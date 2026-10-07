"""Contract + end-to-end tests for the video modality and the /api endpoints. Everything
runs on the mock provider with a generated clip and a temp sessions dir."""
import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from tests.helpers import force_mock_env, make_video

force_mock_env()

import app as app_module  # noqa: E402
from core import registry  # noqa: E402
from core.llm import LLMReply, ToolCall  # noqa: E402
from core.loop import run_agent  # noqa: E402
from core.tools import ToolContext, get_tools  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        force_mock_env()
        self.tmp = tempfile.TemporaryDirectory()
        patches = [
            mock.patch.object(app_module, "SESSIONS_DIR", self.tmp.name),
            mock.patch.object(app_module, "reload_env", lambda: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(app_module.app)

    def upload(self, sid="s1"):
        clip = os.path.join(self.tmp.name, "in.mp4")
        make_video(clip, seconds=4, fps=10)
        with open(clip, "rb") as f:
            r = self.client.post("/api/upload", files={"file": ("clip.mp4", f, "video/mp4")},
                                 data={"session_id": sid}).json()
        self.assertEqual(r["session_id"], sid)
        return r

    def query(self, **kw):
        body = {"session_id": "s1", "mode": "agent", "models": ["gpt"]}
        body.update(kw)
        return self.client.post("/api/query", json=body).json()


class ContractTests(Base):
    """Anything dropped into modalities/ must satisfy these."""

    def test_discovery_has_no_errors(self):
        self.assertEqual(registry.DISCOVERY_ERRORS, [])
        self.assertIn("video", [m.name for m in registry.all_modalities()])

    def test_every_tool_has_a_valid_schema(self):
        for m in registry.all_modalities():
            for name, spec in get_tools(m.name).items():
                self.assertEqual(spec.parameters.get("type"), "object", name)
                self.assertTrue(spec.description, name)
                required = spec.parameters.get("required", [])
                props = spec.parameters.get("properties", {})
                self.assertTrue(set(required) <= set(props), name)
                json.dumps(spec.schema())

    def test_every_agent_file_is_valid_and_runs_on_the_mock(self):
        self.upload()
        for m in registry.all_modalities():
            agents, invalid = registry.load_agents(m)
            self.assertEqual(invalid, [])
            self.assertTrue(agents)
            for agent in agents.values():
                session = app_module.load_session("s1")
                ctx = ToolContext("s1", session, os.path.join(self.tmp.name, "s1"))
                res = run_agent(agent, m, "gpt", ctx,
                                {"question": "How does the capsulorhexis look?", "n_frames": 4})
                self.assertIsNone(res.error, f"{agent.name}: {res.error}")
                self.assertEqual(res.trace[-1]["type"], "final")
                self.assertIn("tool_call", [e["type"] for e in res.trace])
                # trace must be JSON-safe and carry refs, not image bytes
                self.assertLess(len(json.dumps(res.trace)), 20000)


class ApiTests(Base):
    def test_agents_endpoint(self):
        self.upload()
        data = self.client.get("/api/agents", params={"session_id": "s1"}).json()
        self.assertEqual(data["active"], "video")
        video = next(m for m in data["modalities"] if m["name"] == "video")
        self.assertEqual({a["name"] for a in video["agents"]}, {"default", "followup"})
        self.assertIn("submit_steps", {t["name"] for t in video["tools"]})
        self.assertEqual(video["invalid"], [])

    def test_steps_agent_turn_matches_legacy_shape_and_has_trace(self):
        self.upload()
        out = self.query(agent="default")
        turn = out["turn"]
        self.assertIsNone(turn["error"])
        self.assertEqual(turn["type"], "steps")
        self.assertEqual(turn["stop_reason"], "tool:submit_steps")
        self.assertEqual(len(turn["model_steps"]), 2)
        self.assertTrue(turn["assessment"])
        self.assertEqual(turn["n_frames_sent"], 8)
        self.assertIn("mock", turn["provider"])
        kinds = [e["type"] for e in turn["trace"]]
        for k in ("model_call", "tool_call", "tool_result", "final"):
            self.assertIn(k, kinds)
        zoom = next(e for e in turn["trace"]
                    if e["type"] == "tool_result" and e["name"] == "zoom_frame")
        self.assertIn("zoom", zoom["images"][0])
        saved = self.client.get("/api/sessions/s1").json()
        self.assertEqual(saved["turns"][0]["trace"], turn["trace"])

    def test_followup_uses_prior_analysis_and_threads_memory(self):
        self.upload()
        self.query(agent="default")
        self.client.post("/api/mark", json={"session_id": "s1", "step_label": "Phaco",
                                            "start_sec": 1, "end_sec": 3})
        first = self.query(agent="followup",
                           question="Where do you disagree with my marks? Look closer.")["turn"]
        self.assertIsNone(first["error"])
        self.assertEqual(first["type"], "followup")
        self.assertEqual(first["parent_turn"], 0)
        called = [e["name"] for e in first["trace"] if e["type"] == "tool_call"]
        self.assertEqual(called, ["get_prior_analysis", "get_reviewer_marks", "zoom_frame",
                                  "submit_answer"])
        prior = next(e for e in first["trace"]
                     if e["type"] == "tool_result" and e["name"] == "get_prior_analysis")
        self.assertIn("Capsulorhexis", prior["text"])
        self.assertTrue(first["answer"])

        second = self.query(agent="followup", question="And the second step?")["turn"]
        self.assertEqual(second["turn_index"], 2)
        # replayed memory must not make the mock skip its tool calls
        self.assertEqual([e["name"] for e in second["trace"] if e["type"] == "tool_call"],
                         ["get_prior_analysis", "submit_answer"])
        # memory: the first follow-up's Q&A is replayed into the second run's context
        agents, _ = registry.load_agents(registry.get_modality("video"))
        session = app_module.load_session("s1")
        ctx = ToolContext("s1", session, os.path.join(self.tmp.name, "s1"))
        msgs = registry.get_modality("video").initial_messages(
            agents["followup"], ctx, {"question": "q"})
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "assistant", "user"])

    def test_ask_user_ends_turn_with_needs_reply(self):
        self.upload()
        cfg = {"name": "asker", "system_prompt": "ask", "tools": ["ask_user"],
               "kind": "followup", "mock": "followup"}
        ask = lambda agent, messages, tools: LLMReply(  # noqa: E731
            tool_calls=[ToolCall("c1", "ask_user", {"question": "Early or late portion?"})])
        with mock.patch.dict(registry.get_modality("video").mock_scripts, {"followup": ask}):
            turn = self.query(agent_config=cfg, question="Is it okay?")["turn"]
        self.assertTrue(turn["needs_reply"])
        self.assertEqual(turn["answer"], "Early or late portion?")
        self.assertEqual(turn["stop_reason"], "tool:ask_user")

    def test_inline_config_is_validated_and_snapshotted(self):
        self.upload()
        bad = self.query(agent_config={"name": "x", "system_prompt": "p", "tools": ["nope"]})
        self.assertIn("unknown tool", bad["error"])
        good = self.query(agent_config={"name": "mine", "system_prompt": "p",
                                        "tools": ["get_clip_info"], "mock": "followup"},
                          question="hello")["turn"]
        self.assertEqual(good["agent_config"]["name"], "mine")
        self.assertIsNone(good["error"])

    def test_errors(self):
        # a session with no video is a text session: video agents don't exist there
        self.assertIn("unknown agent 'default' for text",
                      self.query(session_id="nobody", agent="default")["error"])
        self.upload()
        self.assertIn("unknown agent", self.query(agent="ghost")["error"])
        self.assertIn("needs a question", self.query(agent="followup")["error"])

    def test_max_iterations_surfaces_as_turn_error(self):
        self.upload()
        cfg = {"name": "short", "system_prompt": "p", "kind": "steps", "mock": "steps",
               "tools": ["get_clip_info", "sample_frames", "zoom_frame", "submit_steps"],
               "stop_when": "tool:submit_steps", "required_output": ["steps"],
               "max_iterations": 2}
        turn = self.query(agent_config=cfg)["turn"]
        self.assertEqual(turn["stop_reason"], "max_iterations")
        self.assertIn("max_iterations", turn["error"])
        self.assertEqual(turn["model_steps"], [])

    def test_frame_endpoint(self):
        self.upload()
        r = self.client.get("/api/sessions/s1/frame", params={"t": 1.5, "zoom": 2})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.content[:2], b"\xff\xd8")
        self.assertEqual(self.client.get("/api/sessions/none/frame", params={"t": 1}).status_code, 404)

    def test_legacy_modes_still_work(self):
        self.upload()
        steps = self.client.post("/api/query", json={"session_id": "s1", "mode": "steps"}).json()
        self.assertEqual(steps["turn"]["type"], "steps")
        self.assertEqual(len(steps["turn"]["model_steps"]), 2)
        chat = self.client.post("/api/query", json={"session_id": "s1", "mode": "chat",
                                                    "question": "hi", "models": ["gpt", "claude"]}).json()
        self.assertEqual(len(chat["turn"]["responses"]), 2)


class TextModalityTests(Base):
    def test_agents_endpoint_routes_by_session(self):
        self.assertEqual(self.client.get("/api/agents", params={"session_id": "t1"}).json()["active"],
                         "text")
        self.upload("t1")
        self.assertEqual(self.client.get("/api/agents", params={"session_id": "t1"}).json()["active"],
                         "video")

    def test_text_chat_turn_with_trace(self):
        turn = self.query(session_id="t1", agent="chat", question="What is 12.5 * 4 + 3?")["turn"]
        self.assertIsNone(turn["error"])
        self.assertEqual(turn["type"], "chat")
        self.assertEqual(turn["modality"], "text")
        called = [e["name"] for e in turn["trace"] if e["type"] == "tool_call"]
        self.assertEqual(called, ["search_history", "calculate"])
        self.assertIn("= 53.0", turn["answer"])

    def test_text_memory_and_search_history(self):
        self.query(session_id="t1", agent="chat", question="Remember that my favourite step is capsulorhexis")
        second = self.query(session_id="t1", agent="chat", question="What step did I say was my favourite?")["turn"]
        hist = next(e for e in second["trace"]
                    if e["type"] == "tool_result" and e["name"] == "search_history")
        self.assertIn("capsulorhexis", hist["text"])
        self.assertEqual(second["turn_index"], 1)

    def test_chat_before_upload_carries_into_video_followups(self):
        self.query(session_id="t1", agent="chat", question="Hello, I will upload a clip soon")
        self.upload("t1")
        video = registry.get_modality("video")
        agents, _ = registry.load_agents(video)
        ctx = ToolContext("t1", app_module.load_session("t1"), os.path.join(self.tmp.name, "t1"))
        msgs = video.initial_messages(agents["followup"], ctx, {"question": "now?"})
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user"])
        self.assertIn("upload a clip soon", msgs[0]["text"])
        # and the upload kept the earlier turn
        self.assertEqual(len(app_module.load_session("t1")["turns"]), 1)

    def test_calculate_tool_is_safe(self):
        from modalities.text.tools import calculate
        ctx = ToolContext("s", {}, "/x")
        self.assertEqual(calculate(ctx, "2 + 3 * 4").data["value"], 14)
        for bad in ("__import__('os').system('x')", "2 ** 9999", "1/0", "abs(-1)", "a + 1"):
            self.assertTrue(calculate(ctx, bad).is_error, bad)


class StreamingTests(Base):
    def poll(self, run_id):
        import time
        after, events = 0, []
        for _ in range(100):
            r = self.client.get(f"/api/runs/{run_id}", params={"after": after}).json()
            events += r["events"]
            if r["events"]:
                after = r["events"][-1]["seq"]
            if r["status"] != "running":
                return r, events
            time.sleep(0.05)
        self.fail("run did not finish")

    def test_streamed_analysis_matches_sync_result(self):
        self.upload()
        start = self.query(agent="default", stream=True)
        self.assertIn("run_id", start)
        done, events = self.poll(start["run_id"])
        self.assertEqual(done["status"], "done")
        turn = done["turn"]
        self.assertEqual(events, turn["trace"])             # no events lost or duplicated
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(len(turn["model_steps"]), 2)
        saved = self.client.get("/api/sessions/s1").json()
        self.assertEqual(saved["turns"][0]["trace"], turn["trace"])

    def test_poll_returns_only_new_events(self):
        self.upload()
        run_id = self.query(agent="default", stream=True)["run_id"]
        self.poll(run_id)
        tail = self.client.get(f"/api/runs/{run_id}", params={"after": 5}).json()
        self.assertTrue(all(e["seq"] > 5 for e in tail["events"]))
        self.assertEqual(self.client.get("/api/runs/nope").status_code, 404)

    def test_validation_errors_are_immediate_not_streamed(self):
        self.upload()
        out = self.query(agent="ghost", stream=True)
        self.assertIn("unknown agent", out["error"])
        self.assertNotIn("run_id", out)

    def test_failure_inside_a_run_surfaces_as_error_status(self):
        self.upload()
        video = registry.get_modality("video")
        with mock.patch.object(video, "initial_messages", side_effect=RuntimeError("boom")):
            run_id = self.query(agent="default", stream=True)["run_id"]
            done, _ = self.poll(run_id)
        self.assertEqual(done["status"], "error")
        self.assertIn("boom", done["error"])

    def test_concurrent_runs_keep_distinct_turn_indexes(self):
        self.upload()
        ids = [self.query(agent="default", stream=True)["run_id"] for _ in range(3)]
        turns = [self.poll(i)[0]["turn"] for i in ids]
        self.assertEqual(sorted(t["turn_index"] for t in turns), [0, 1, 2])
        self.assertEqual(len(app_module.load_session("s1")["turns"]), 3)


if __name__ == "__main__":
    unittest.main()
