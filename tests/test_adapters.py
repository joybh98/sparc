"""Provider message translation (pure functions, no network)."""
import base64
import json
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from core import llm
from core.llm import anthropic_messages, gemini_contents, openai_messages

B64 = base64.b64encode(b"\xff\xd8fakejpeg").decode()

HISTORY = [
    {"role": "user", "text": "q", "images": [B64]},
    {"role": "assistant", "text": "looking", "tool_calls": [
        {"id": "c1", "name": "zoom_frame", "args": {"time_sec": 1.0}},
        {"id": "c2", "name": "get_clip_info", "args": {}}]},
    {"role": "tool", "results": [
        {"id": "c1", "name": "zoom_frame", "text": "frame", "images": [B64]},
        {"id": "c2", "name": "get_clip_info", "text": "Duration: 4 s", "images": []}]},
]


class AdapterTests(unittest.TestCase):
    def test_openai(self):
        out = openai_messages("sys", HISTORY)
        self.assertEqual([m["role"] for m in out],
                         ["system", "user", "assistant", "tool", "tool", "user"])
        calls = out[2]["tool_calls"]
        self.assertEqual(calls[0]["function"]["name"], "zoom_frame")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"time_sec": 1.0})
        self.assertEqual([m["tool_call_id"] for m in out[3:5]], ["c1", "c2"])
        # tool results are text-only; the image rides in a following user turn
        self.assertTrue(out[5]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_openai_assistant_without_text(self):
        out = openai_messages("s", [{"role": "assistant", "text": "", "tool_calls": [
            {"id": "c", "name": "n", "args": {}}]}])
        self.assertIsNone(out[1]["content"])

    def test_anthropic(self):
        out = anthropic_messages(HISTORY)
        self.assertEqual([m["role"] for m in out], ["user", "assistant", "user"])
        self.assertEqual([b["type"] for b in out[1]["content"]], ["text", "tool_use", "tool_use"])
        results = out[2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["c1", "c2"])
        self.assertEqual([b["type"] for b in results[0]["content"]], ["text", "image"])

    def test_gemini(self):
        out = gemini_contents(HISTORY)
        self.assertEqual([c.role for c in out], ["user", "model", "user"])
        calls = [p.function_call.name for p in out[1].parts if p.function_call]
        self.assertEqual(calls, ["zoom_frame", "get_clip_info"])
        responses = [p.function_response.name for p in out[2].parts if p.function_response]
        self.assertEqual(responses, ["zoom_frame", "get_clip_info"])
        self.assertTrue(any(p.inline_data for p in out[2].parts))


TOOLS = [{"name": "zoom_frame", "description": "d",
          "parameters": {"type": "object", "properties": {}}}]


class ParseTests(unittest.TestCase):
    """Fake SDK clients: check what we send and how we parse what comes back."""

    def test_openai_parse_and_request(self):
        seen = {}

        class Completions:
            def create(self, **kw):
                seen.update(kw)
                msg = NS(content="hi", tool_calls=[NS(id="c9", function=NS(
                    name="zoom_frame", arguments='{"time_sec": 2}'))])
                return NS(choices=[NS(message=msg)],
                          usage=NS(prompt_tokens=5, completion_tokens=3))

        fake = NS(chat=NS(completions=Completions()))
        with mock.patch("openai.OpenAI", lambda **kw: fake):
            reply = llm._openai_chat("sys", HISTORY, TOOLS, "m", "k", None)
        self.assertEqual(seen["tools"][0]["function"]["name"], "zoom_frame")
        self.assertEqual(reply.text, "hi")
        self.assertEqual((reply.tool_calls[0].id, reply.tool_calls[0].args),
                         ("c9", {"time_sec": 2}))
        self.assertEqual(reply.usage, {"input_tokens": 5, "output_tokens": 3})

    def test_openai_bad_arguments_do_not_crash(self):
        self.assertEqual(llm._parse_args("{not json"), {"_raw": "{not json"})
        self.assertEqual(llm._parse_args(""), {})

    def test_anthropic_parse_and_request(self):
        seen = {}

        class Messages:
            def create(self, **kw):
                seen.update(kw)
                return NS(content=[NS(type="text", text="hm"),
                                   NS(type="tool_use", id="t1", name="zoom_frame",
                                      input={"time_sec": 1})],
                          usage=NS(input_tokens=7, output_tokens=2))

        with mock.patch("anthropic.Anthropic", lambda **kw: NS(messages=Messages())):
            reply = llm._anthropic_chat("sys", HISTORY, TOOLS, "m", "k", None)
        self.assertEqual(seen["tools"][0]["input_schema"], TOOLS[0]["parameters"])
        self.assertEqual(seen["system"], "sys")
        self.assertEqual(reply.text, "hm")
        self.assertEqual(reply.tool_calls[0].args, {"time_sec": 1})

    def test_google_parse_and_request(self):
        seen = {}

        class Models:
            def generate_content(self, **kw):
                seen.update(kw)
                parts = [NS(function_call=NS(id=None, name="zoom_frame", args={"time_sec": 3}),
                            text=None),
                         NS(function_call=None, text="ok")]
                return NS(candidates=[NS(content=NS(parts=parts))],
                          usage_metadata=NS(prompt_token_count=4, candidates_token_count=1))

        import google.genai as genai
        with mock.patch.object(genai, "Client", lambda **kw: NS(models=Models())):
            reply = llm._google_chat("sys", HISTORY, TOOLS, "m", "k", None)
        decl = seen["config"].tools[0].function_declarations[0]
        self.assertEqual(decl.name, "zoom_frame")
        self.assertEqual(reply.tool_calls[0].id, "call_0")
        self.assertEqual(reply.tool_calls[0].args, {"time_sec": 3})
        self.assertEqual(reply.text, "ok")


if __name__ == "__main__":
    unittest.main()
