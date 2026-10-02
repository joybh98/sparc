"""Provider-neutral tool-calling LLM adapter.

The loop speaks one message format; each provider adapter translates it.

  {"role": "user",      "text": str, "images": [b64]}
  {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool",      "results": [{"id", "name", "text", "images": [b64]}]}

With no API key the call is served by a scripted mock instead, matching the rest of
the app's mock-first behavior.
"""
import base64
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from providers import PROVIDERS, _resolve

MAX_TOKENS = 4096


@dataclass
class ToolCall:
    id: str
    name: str
    args: Dict


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Dict = field(default_factory=dict)


def mock_step(messages: List[Dict]) -> int:
    """How many assistant turns this run has taken so far. Mock scripts are stateless and
    use it to pick their next move; replayed chat memory before the run's first message
    (flagged run_start by the loop) must not count."""
    start = next((i for i, m in enumerate(messages) if m.get("run_start")), 0)
    return sum(1 for m in messages[start:] if m["role"] == "assistant")


def _parse_args(raw) -> Dict:
    if isinstance(raw, dict):
        return raw
    try:
        args = json.loads(raw or "{}")
        return args if isinstance(args, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}


# ---- OpenAI-compatible (gpt, qwen) -------------------------------------------------

def _img_url(b64: str) -> Dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def openai_messages(system: str, messages: List[Dict]) -> List[Dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": m["text"]}]
                        + [_img_url(b) for b in m.get("images", [])]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("text") or None}
            if m.get("tool_calls"):
                msg["tool_calls"] = [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                    for c in m["tool_calls"]]
            out.append(msg)
        else:  # tool results; the API only allows text there, so images follow as a user turn
            images = []
            for r in m["results"]:
                out.append({"role": "tool", "tool_call_id": r["id"],
                            "content": r["text"] or "(no output)"})
                images.extend(r.get("images", []))
            if images:
                out.append({"role": "user", "content": [
                    {"type": "text", "text": "Images returned by the tool call(s) above, in order:"}
                ] + [_img_url(b) for b in images]})
    return out


def _openai_chat(system, messages, tools, model_name, api_key, base_url) -> LLMReply:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs = {"model": model_name, "messages": openai_messages(system, messages)}
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    calls = [ToolCall(c.id, c.function.name, _parse_args(c.function.arguments))
             for c in (msg.tool_calls or [])]
    usage = {}
    if getattr(resp, "usage", None):
        usage = {"input_tokens": resp.usage.prompt_tokens,
                 "output_tokens": resp.usage.completion_tokens}
    return LLMReply(msg.content or "", calls, usage)


# ---- Anthropic -----------------------------------------------------------------------

def _anthropic_img(b64: str) -> Dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


def anthropic_messages(messages: List[Dict]) -> List[Dict]:
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": m["text"]}]
                        + [_anthropic_img(b) for b in m.get("images", [])]})
        elif m["role"] == "assistant":
            blocks = []
            if m.get("text"):
                blocks.append({"type": "text", "text": m["text"]})
            for c in m.get("tool_calls", []):
                blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                               "input": c["args"]})
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r["id"],
                 "content": [{"type": "text", "text": r["text"] or "(no output)"}]
                 + [_anthropic_img(b) for b in r.get("images", [])]}
                for r in m["results"]]})
    return out


def _anthropic_chat(system, messages, tools, model_name, api_key, base_url) -> LLMReply:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    kwargs = {"model": model_name, "max_tokens": MAX_TOKENS, "system": system,
              "messages": anthropic_messages(messages)}
    if tools:
        kwargs["tools"] = [{"name": t["name"], "description": t["description"],
                            "input_schema": t["parameters"]} for t in tools]
    resp = client.messages.create(**kwargs)
    text = "".join(b.text for b in resp.content if b.type == "text")
    calls = [ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else {})
             for b in resp.content if b.type == "tool_use"]
    usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
    return LLMReply(text, calls, usage)


# ---- Google Gemini -------------------------------------------------------------------

def gemini_contents(messages: List[Dict]):
    from google.genai import types

    def img(b64):
        return types.Part.from_bytes(data=base64.b64decode(b64), mime_type="image/jpeg")

    out = []
    for m in messages:
        if m["role"] == "user":
            parts = [types.Part.from_text(text=m["text"])] + [img(b) for b in m.get("images", [])]
            out.append(types.Content(role="user", parts=parts))
        elif m["role"] == "assistant":
            parts = []
            if m.get("text"):
                parts.append(types.Part.from_text(text=m["text"]))
            for c in m.get("tool_calls", []):
                parts.append(types.Part.from_function_call(name=c["name"], args=c["args"]))
            out.append(types.Content(role="model", parts=parts))
        else:
            parts, images = [], []
            for r in m["results"]:
                parts.append(types.Part.from_function_response(
                    name=r["name"], response={"result": r["text"] or "(no output)"}))
                images.extend(r.get("images", []))
            parts.extend(img(b) for b in images)
            out.append(types.Content(role="user", parts=parts))
    return out


def _google_chat(system, messages, tools, model_name, api_key, base_url) -> LLMReply:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    cfg = {"system_instruction": system}
    if tools:
        cfg["tools"] = [types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t["description"],
                                      parameters_json_schema=t["parameters"])
            for t in tools])]
    resp = client.models.generate_content(
        model=model_name, contents=gemini_contents(messages),
        config=types.GenerateContentConfig(**cfg))

    text, calls = "", []
    parts = (resp.candidates[0].content.parts or []) if resp.candidates else []
    for i, part in enumerate(parts):
        if part.function_call:
            calls.append(ToolCall(part.function_call.id or f"call_{i}", part.function_call.name,
                                  dict(part.function_call.args or {})))
        elif part.text:
            text += part.text
    meta = getattr(resp, "usage_metadata", None)
    usage = ({"input_tokens": meta.prompt_token_count, "output_tokens": meta.candidates_token_count}
             if meta else {})
    return LLMReply(text, calls, usage)


_CHAT = {"openai": _openai_chat, "qwen": _openai_chat, "anthropic": _anthropic_chat,
         "google": _google_chat}


def call_llm(model_key: str, system: str, messages: List[Dict], tools: List[Dict],
             mock: Optional[Callable[[], LLMReply]] = None) -> Tuple[LLMReply, Dict]:
    """Returns (reply, info). `mock` is used when the provider has no API key."""
    if model_key not in PROVIDERS:
        raise ValueError(f"Unknown model key '{model_key}'")
    p, model_name, api_key, base_url = _resolve(model_key)

    if not api_key:
        if mock is None:
            raise RuntimeError(f"no {p['key_env']} set and no mock available")
        return mock(), {"model": model_name, "mock": True,
                        "provider": f"{p['provider']} (mock — no {p['key_env']} set)"}

    reply = _CHAT[p["provider"]](system, messages, tools, model_name, api_key, base_url)
    return reply, {"model": model_name, "provider": p["provider"], "mock": False}
