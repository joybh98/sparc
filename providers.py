import base64
import json
import os
import random
import time
from typing import Dict, List, Optional

from trajectory import traced_call


SYSTEM_PROMPT = (
    "You are assisting an expert reviewer in evaluating a cataract surgery video clip. "
    "You are given a sequence of sampled frames from the clip (in temporal order) and a "
    "question from the reviewer. Answer the question directly, cite which part of the "
    "sequence (early/mid/late, or a rough frame index) your answer relies on, and state "
    "your confidence as high, medium, or low. If the question is ambiguous or you need "
    "more context to answer well, you may ask a single clarifying question back to the "
    "reviewer instead of guessing. Be concise."
)


STEP_SYSTEM_PROMPT = (
    "You are assisting an expert reviewer in evaluating a cataract surgery video clip. "
    "You are given frames sampled from the clip in temporal order, each tagged with its "
    "timestamp in seconds, plus the clip's total duration. Segment the clip into the "
    "ordered surgical steps you can identify. Respond with a single JSON object of the "
    "form {\"steps\": [{\"step_label\": str, \"start_sec\": number, \"end_sec\": number, "
    "\"comment\": str, \"confidence\": number, \"uncertainty\": str, \"evidence\": "
    "[{\"frame\": int, \"note\": str}]}], \"assessment\": str}. Every start_sec and end_sec "
    "must lie within [0, duration] and steps must be in chronological order. 'comment' is a "
    "short note on technique or quality for that step. 'confidence' is your calibrated "
    "probability in [0, 1] that this step label and its time range are correct. "
    "'uncertainty' states what is unclear or what additional information would resolve it "
    "(empty string if nothing). 'evidence' lists the frames you relied on: 'frame' is the "
    "1-based frame number exactly as listed in the frame timestamps, and 'note' says what "
    "is visible in that frame that supports the step. 'assessment' is a brief overall read "
    "of the clip. Base timing estimates on the frame timestamps. Return only the JSON."
)


class ModelResponse:
    def __init__(self, model: str, provider: str, text: str, latency_ms: int,
                 error: Optional[str] = None):
        self.model = model
        self.provider = provider
        self.text = text
        self.latency_ms = latency_ms
        self.error = error

    def to_dict(self):
        return {
            "model": self.model,
            "provider": self.provider,
            "text": self.text,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


def _history_text(conversation_history: List[Dict]) -> str:
    lines = []
    for turn in conversation_history:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        lines.append(f"{role.upper()}: {content}")
    return "\n".join(lines)


def _frame_timing_text(frames_timed: List[Dict]) -> str:
    duration = frames_timed[-1]["time_sec"] if frames_timed else 0.0
    lines = [f"Total duration: {duration:.2f}s", "Frame timestamps:"]
    for i, f in enumerate(frames_timed):
        lines.append(f"  frame {i + 1}: t={f['time_sec']:.2f}s")
    return "\n".join(lines)


def _mock_call(question: str, n_frames: int) -> str:
    time.sleep(0.3 + random.random() * 0.4)
    templates = [
        f"[MOCK] Based on the {n_frames} sampled frames, my read on '{question}' is that "
        f"the technique looks broadly consistent with standard practice, though I can't "
        f"be fully certain from sparse frames alone. Confidence: medium.",
        f"[MOCK] Could you clarify whether you're asking about the early or late portion "
        f"of the clip? I want to point at the right frame range before answering "
        f"'{question}'.",
    ]
    return random.choice(templates)


def _mock_steps(frames_timed: List[Dict], step_hint: Optional[str]) -> Dict:
    time.sleep(0.3 + random.random() * 0.4)
    duration = frames_timed[-1]["time_sec"] if frames_timed else 30.0
    n = len(frames_timed)
    mid = round(duration * 0.45, 2)
    hint = f" (reviewer expected: {step_hint})" if step_hint else ""
    return {
        "steps": [
            {
                "step_label": "Capsulorhexis",
                "start_sec": 0.0,
                "end_sec": mid,
                "comment": f"[MOCK] Tear looks continuous and roughly centered{hint}.",
                "confidence": 0.7,
                "uncertainty": "[MOCK] Sparse frames; tear completion point is not directly visible.",
                "evidence": [
                    {"frame": 1, "note": "[MOCK] Cystotome visible at the anterior capsule."},
                    {"frame": max(1, n // 2), "note": "[MOCK] Circular edge of the tear."},
                ],
            },
            {
                "step_label": "Phacoemulsification",
                "start_sec": mid,
                "end_sec": duration,
                "comment": "[MOCK] Nucleus disassembly stays central; no obvious posterior capsule contact.",
                "confidence": 0.5,
                "uncertainty": "[MOCK] Boundary with the previous step could be off by several seconds.",
                "evidence": [
                    {"frame": max(1, n), "note": "[MOCK] Phaco tip engaged with the nucleus."},
                ],
            },
        ],
        "assessment": "[MOCK] Two-step read from sparse frames; timings are rough estimates only.",
    }


def _normalize_steps(steps, frames_timed: List[Dict]) -> List[Dict]:
    n = len(frames_timed)
    out = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        evidence = []
        for e in step.get("evidence") or []:
            if not isinstance(e, dict):
                continue
            try:
                frame = int(e.get("frame"))
            except (TypeError, ValueError):
                continue
            if 1 <= frame <= n:
                evidence.append({
                    "frame": frame,
                    "time_sec": frames_timed[frame - 1]["time_sec"],
                    "note": str(e.get("note") or ""),
                })
        try:
            confidence = max(0.0, min(1.0, float(step.get("confidence"))))
        except (TypeError, ValueError):
            confidence = None
        out.append({**step, "confidence": confidence,
                    "uncertainty": str(step.get("uncertainty") or ""),
                    "evidence": evidence})
    return out


def _openai_generate(system, prompt, images_b64, history, model_name, want_json,
                     api_key, base_url=None):
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    content = [{"type": "text", "text": prompt}]
    for b in images_b64:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}"}})

    messages = [{"role": "system", "content": system}]
    for turn in history:
        messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})
    messages.append({"role": "user", "content": content})

    kwargs = {"model": model_name, "messages": messages}
    if want_json:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


def _anthropic_generate(system, prompt, images_b64, history, model_name, want_json,
                        api_key, base_url=None):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    content = [{"type": "text", "text": prompt}]
    for b in images_b64:
        content.append({"type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b}})

    messages = [{"role": t.get("role", "user"), "content": t.get("content", "")} for t in history]
    messages.append({"role": "user", "content": content})

    resp = client.messages.create(model=model_name, max_tokens=2048, system=system,
                                   messages=messages)
    return "".join(b.text for b in resp.content if b.type == "text")


def _google_generate(system, prompt, images_b64, history, model_name, want_json,
                     api_key, base_url=None):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    parts = []
    hist = _history_text(history)
    if hist:
        parts.append(types.Part.from_text(text=f"Conversation so far:\n{hist}"))
    parts.append(types.Part.from_text(text=prompt))
    for b in images_b64:
        parts.append(types.Part.from_bytes(data=base64.b64decode(b), mime_type="image/jpeg"))

    cfg = {"system_instruction": system}
    if want_json:
        cfg["response_mime_type"] = "application/json"
    resp = client.models.generate_content(
        model=model_name,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(**cfg),
    )
    return resp.text


PROVIDERS: Dict[str, Dict] = {
    "gpt": {"provider": "openai", "key_env": "OPENAI_API_KEY",
            "model_env": "OPENAI_MODEL", "default_model": "gpt-4o",
            "generate": _openai_generate},
    "claude": {"provider": "anthropic", "key_env": "ANTHROPIC_API_KEY",
               "model_env": "ANTHROPIC_MODEL", "default_model": "claude-3-5-sonnet-latest",
               "generate": _anthropic_generate},
    "gemini": {"provider": "google", "key_env": "GOOGLE_API_KEY",
               "model_env": "GOOGLE_MODEL", "default_model": "gemini-1.5-flash",
               "generate": _google_generate},
    "qwen": {"provider": "qwen", "key_env": "DASHSCOPE_API_KEY",
             "model_env": "QWEN_MODEL", "default_model": "qwen-vl-max",
             "generate": _openai_generate,
             "base_url_env": "QWEN_BASE_URL",
             "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"},
}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def available_models() -> List[Dict]:
    return [
        {
            "key": key,
            "provider": p["provider"],
            "model_name": _env(p["model_env"], p.get("default_model", key)),
            "has_key": bool(_env(p["key_env"])),
        }
        for key, p in PROVIDERS.items()
    ]


def _resolve(model_key: str):
    p = PROVIDERS[model_key]
    model_name = _env(p["model_env"], p.get("default_model", model_key))
    api_key = _env(p["key_env"])
    base_url = _env(p.get("base_url_env", ""), p.get("base_url", "")) or None
    return p, model_name, api_key, base_url


def query_model(model_key: str, question: str, frames_b64: List[str],
                 conversation_history: List[Dict], trace: Optional[Dict] = None,
                 parent: Optional[Dict] = None) -> ModelResponse:
    start = time.time()

    if model_key not in PROVIDERS:
        return ModelResponse(model_key, "unknown", "", 0, error=f"Unknown model key '{model_key}'")

    p, model_name, api_key, base_url = _resolve(model_key)

    try:
        with traced_call(trace, "model_generate", parent,
                         {"model": model_name, "provider": p["provider"],
                          "n_images": len(frames_b64), "mock": not api_key,
                          "question": question}) as gen:
            if not api_key:
                text = _mock_call(question, len(frames_b64))
                provider_label = f"{p['provider']} (mock — no {p['key_env']} set)"
            else:
                text = p["generate"](SYSTEM_PROMPT, f"Question: {question}", frames_b64,
                                      conversation_history, model_name, False,
                                      api_key=api_key, base_url=base_url)
                provider_label = p["provider"]
            gen["outputs"] = {"raw_text": text}
        return ModelResponse(model_name, provider_label, text, int((time.time() - start) * 1000))
    except Exception as e:
        return ModelResponse(model_name, p["provider"], "", int((time.time() - start) * 1000),
                              error=str(e))


def query_steps(model_key: str, frames_timed: List[Dict], step_hint: Optional[str],
                 conversation_history: List[Dict], trace: Optional[Dict] = None,
                 parent: Optional[Dict] = None) -> Dict:
    start = time.time()

    base = {"steps": [], "assessment": "", "model": model_key, "provider": "unknown",
            "latency_ms": 0, "error": None}

    if model_key not in PROVIDERS:
        base["error"] = f"Unknown model key '{model_key}'"
        return base

    p, model_name, api_key, base_url = _resolve(model_key)
    base["model"] = model_name
    base["provider"] = p["provider"]

    try:
        with traced_call(trace, "model_generate", parent,
                         {"model": model_name, "provider": p["provider"],
                          "n_images": len(frames_timed), "mock": not api_key,
                          "step_hint": step_hint}) as gen:
            if not api_key:
                raw = json.dumps(_mock_steps(frames_timed, step_hint))
                base["provider"] = f"{p['provider']} (mock — no {p['key_env']} set)"
            else:
                prompt = _frame_timing_text(frames_timed)
                if step_hint:
                    prompt += f"\n\nReviewer expects this clip to contain: {step_hint}"
                images = [f["b64"] for f in frames_timed]
                raw = p["generate"](STEP_SYSTEM_PROMPT, prompt, images, conversation_history,
                                     model_name, True, api_key=api_key, base_url=base_url)
            gen["outputs"] = {"raw_text": raw}

        with traced_call(trace, "parse_steps", parent, {"n_frames": len(frames_timed)}) as parse:
            result = json.loads(raw)
            base["steps"] = _normalize_steps(result.get("steps", []), frames_timed)
            base["assessment"] = result.get("assessment", "")
            parse["outputs"] = {
                "n_steps": len(base["steps"]),
                "steps": [{"step_label": s.get("step_label"),
                           "confidence": s["confidence"],
                           "n_evidence": len(s["evidence"])} for s in base["steps"]],
            }
    except json.JSONDecodeError as e:
        base["error"] = f"model did not return valid JSON: {e}"
    except Exception as e:
        base["error"] = str(e)

    base["latency_ms"] = int((time.time() - start) * 1000)
    return base
