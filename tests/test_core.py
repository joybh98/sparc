import unittest

from core.llm import LLMReply, ToolCall
from core.loop import run_agent
from core.registry import AgentConfig, Modality, validate_agent
from core.tools import ToolContext, ToolResult, check_args, get_tools, run_tool, tool


@tool("unit", "echo", "Echo text.",
      {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}})
def echo(ctx, text):
    return ToolResult(text=text)


@tool("unit", "boom", "Always fails.")
def boom(ctx):
    raise RuntimeError("kaput")


@tool("unit", "finish", "Deliver the result.",
      {"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}})
def finish(ctx, value):
    if value < 0:
        return ToolResult(text="value must be >= 0", is_error=True)
    return ToolResult(text="done", data={"value": value})


@tool("unit", "stop_now", "Ends the turn.")
def stop_now(ctx):
    return ToolResult(text="bye", data={"text": "bye"}, end_turn=True)


UNIT = Modality(
    name="unit", label="Unit", description="", path="/nonexistent",
    detect=lambda s: True,
    initial_messages=lambda agent, ctx, params: [{"role": "user", "text": "go"}],
    mock_scripts={"m": lambda agent, messages, tools: LLMReply(text="mock")},
)


def agent(**kw):
    base = dict(name="a", modality="unit", system_prompt="sys",
                tools=["echo", "boom", "finish", "stop_now"])
    base.update(kw)
    return AgentConfig(**base)


def scripted(replies):
    """A fake llm that plays back the given replies in order."""
    it = iter(replies)

    def llm(model_key, system, messages, tools, mock=None):
        return next(it), {"model": "fake", "provider": "fake", "mock": False}
    return llm


def ctx():
    return ToolContext(session_id="s", session={}, session_dir="/nonexistent")


def call(name, **args):
    return ToolCall(f"id_{name}", name, args)


class ToolTests(unittest.TestCase):
    def test_check_args(self):
        schema = get_tools("unit")["finish"].parameters
        self.assertIsNone(check_args(schema, {"value": 3}))
        self.assertIn("missing", check_args(schema, {}))
        self.assertIn("unknown", check_args(schema, {"value": 1, "x": 2}))
        self.assertIn("integer", check_args(schema, {"value": "3"}))
        self.assertIn("integer", check_args(schema, {"value": True}))
        self.assertIsNone(check_args({"properties": {"n": {"type": "number"}}}, {"n": 2}))

    def test_run_tool_wraps_failures(self):
        r = run_tool(get_tools("unit")["boom"], ctx(), {})
        self.assertTrue(r.is_error)
        self.assertIn("kaput", r.text)

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValueError):
            tool("unit", "echo", "dup")(lambda ctx: None)


class ValidateAgentTests(unittest.TestCase):
    def valid(self, **kw):
        raw = {"name": "x", "system_prompt": "p", "tools": ["echo"]}
        raw.update(kw)
        return validate_agent(raw, UNIT)

    def test_valid(self):
        cfg, errs = self.valid()
        self.assertEqual(errs, [])
        self.assertEqual(cfg.max_iterations, 6)

    def test_rejects_unknown_tool_and_field_and_stop(self):
        _, errs = self.valid(tools=["nope"])
        self.assertTrue(any("unknown tool" in e for e in errs))
        _, errs = self.valid(tool=["echo"])
        self.assertTrue(any("unknown field" in e for e in errs))
        _, errs = self.valid(stop_when="tool:finish")
        self.assertTrue(any("stop_when" in e for e in errs))
        _, errs = self.valid(max_iterations=999)
        self.assertTrue(any("max_iterations" in e for e in errs))
        _, errs = self.valid(mock="ghost")
        self.assertTrue(any("mock" in e for e in errs))
        _, errs = self.valid(modality="other")
        self.assertTrue(any("modality" in e for e in errs))


class LoopTests(unittest.TestCase):
    def run_loop(self, cfg, replies):
        return run_agent(cfg, UNIT, "gpt", ctx(), {}, llm=scripted(replies))

    def test_tool_roundtrip_then_text(self):
        res = self.run_loop(agent(), [
            LLMReply("thinking", [call("echo", text="hi")]),
            LLMReply("all done")])
        self.assertEqual(res.stop_reason, "no_tool_calls")
        self.assertEqual(res.output, {"text": "all done"})
        types = [e["type"] for e in res.trace]
        self.assertEqual(types, ["model_call", "tool_call", "tool_result", "model_call", "final"])
        self.assertEqual([e["seq"] for e in res.trace], [1, 2, 3, 4, 5])

    def test_stop_when_tool_ends_loop_with_output(self):
        res = self.run_loop(agent(stop_when="tool:finish", required_output=["value"]), [
            LLMReply("", [call("finish", value=7)])])
        self.assertEqual(res.stop_reason, "tool:finish")
        self.assertEqual(res.output["value"], 7)
        self.assertIsNone(res.error)

    def test_rejected_submit_lets_model_retry(self):
        res = self.run_loop(agent(stop_when="tool:finish"), [
            LLMReply("", [call("finish", value=-1)]),
            LLMReply("", [call("finish", value=2)])])
        results = [e for e in res.trace if e["type"] == "tool_result"]
        self.assertTrue(results[0]["is_error"])
        self.assertEqual(res.output["value"], 2)

    def test_nudge_once_when_model_skips_stop_tool(self):
        res = self.run_loop(agent(stop_when="tool:finish"), [
            LLMReply("I think I'm done"),
            LLMReply("", [call("finish", value=1)])])
        self.assertIn("nudge", [e["type"] for e in res.trace])
        self.assertEqual(res.stop_reason, "tool:finish")

    def test_no_stop_tool_after_nudge_reports_missing_output(self):
        res = self.run_loop(agent(stop_when="tool:finish", required_output=["value"]), [
            LLMReply("done?"), LLMReply("really done")])
        self.assertEqual(res.stop_reason, "no_tool_calls")
        self.assertIn("required output", res.error)

    def test_end_turn_tool_stops_even_with_default_stop(self):
        res = self.run_loop(agent(), [LLMReply("", [call("stop_now")])])
        self.assertEqual(res.stop_reason, "tool:stop_now")
        self.assertEqual(res.output["text"], "bye")

    def test_max_iterations(self):
        res = self.run_loop(agent(max_iterations=2), [
            LLMReply("a", [call("echo", text="1")]),
            LLMReply("b", [call("echo", text="2")]),
            LLMReply("never reached")])
        self.assertEqual(res.stop_reason, "max_iterations")
        self.assertIn("max_iterations", res.error)
        self.assertEqual(res.output["text"], "b")

    def test_unknown_or_unlisted_tool_is_an_error_result(self):
        res = self.run_loop(agent(tools=["echo"]), [
            LLMReply("", [call("boom")]),
            LLMReply("ok")])
        r = next(e for e in res.trace if e["type"] == "tool_result")
        self.assertTrue(r["is_error"])
        self.assertIn("Unknown tool", r["text"])

    def test_model_failure_is_traced(self):
        def llm(*a, **k):
            raise RuntimeError("api down")
        res = run_agent(agent(), UNIT, "gpt", ctx(), {}, llm=llm)
        self.assertEqual(res.stop_reason, "error")
        self.assertEqual(res.error, "api down")
        self.assertEqual(res.trace[0]["type"], "error")
        self.assertEqual(res.trace[-1]["type"], "final")

    def test_batch_runs_all_calls_even_when_one_ends_turn(self):
        res = self.run_loop(agent(), [
            LLMReply("", [call("stop_now"), call("echo", text="x")])])
        results = [e for e in res.trace if e["type"] == "tool_result"]
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
