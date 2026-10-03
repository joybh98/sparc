"""Step evidence/confidence validation and the SQLite trajectory + per-call marks."""
import unittest

from tests.helpers import force_mock_env
from tests.test_video_agents import Base

force_mock_env()

from core.tools import ToolContext, get_tools, run_tool  # noqa: E402


class StepFieldTests(Base):
    def submit(self, steps):
        self.upload()
        ctx = ToolContext(session_id="s1", session={}, session_dir=f"{self.tmp.name}/s1")
        return run_tool(get_tools("video")["submit_steps"], ctx,
                        {"steps": steps, "assessment": "a"})

    def step(self, **kw):
        s = {"step_label": "X", "start_sec": 0, "end_sec": 2, "confidence": 0.6,
             "evidence": [{"time_sec": 1, "note": "n"}]}
        s.update(kw)
        return s

    def test_valid_step_gets_defaults_and_keeps_evidence(self):
        r = self.submit([self.step()])
        self.assertFalse(r.is_error)
        out = r.data["steps"][0]
        self.assertEqual(out["uncertainty"], "")
        self.assertEqual(out["evidence"], [{"time_sec": 1, "note": "n"}])

    def test_rejects_bad_confidence_and_evidence(self):
        for bad in (self.step(confidence=1.5), self.step(confidence=None),
                    self.step(evidence="x"), self.step(evidence=[{"time_sec": 99, "note": ""}])):
            self.assertTrue(self.submit([bad]).is_error, bad)


class TrajectoryTests(Base):
    def run_analysis(self):
        self.upload()
        return self.query(agent="default")["turn"]

    def test_turn_is_stored_with_caller_links(self):
        turn = self.run_analysis()
        step = turn["model_steps"][0]
        self.assertEqual(step["confidence"], 0.7)
        self.assertTrue(step["evidence"] and step["uncertainty"])

        calls = self.client.get("/api/sessions/s1/trajectory").json()["calls"]
        by_id = {c["call_id"]: c for c in calls}
        root = by_id["s1:0:run"]
        self.assertEqual(root["kind"], "run")
        tools = [c for c in calls if c["kind"] == "tool"]
        self.assertEqual([t["name"] for t in tools],
                         ["get_clip_info", "sample_frames", "zoom_frame", "submit_steps"])
        for t in tools:
            self.assertEqual(by_id[t["parent_id"]]["kind"], "model_call")
            self.assertEqual(by_id[by_id[t["parent_id"]]["parent_id"]]["call_id"], root["call_id"])
        sample = next(t for t in tools if t["name"] == "sample_frames")
        self.assertEqual(sample["args"]["n"], 4)
        self.assertGreater(sample["latency_ms"], -1)

    def test_call_ids_match_trace_events(self):
        turn = self.run_analysis()
        call = next(e for e in turn["trace"] if e["type"] == "tool_call")
        calls = self.client.get("/api/sessions/s1/trajectory").json()["calls"]
        self.assertIn(f"s1:0:{call['seq']}", [c["call_id"] for c in calls])

    def test_mark_set_change_clear_and_reject(self):
        self.run_analysis()
        cid = next(c["call_id"] for c in
                   self.client.get("/api/sessions/s1/trajectory").json()["calls"]
                   if c["kind"] == "tool")

        def verdict():
            return next(c for c in self.client.get("/api/sessions/s1/trajectory").json()["calls"]
                        if c["call_id"] == cid)["reviewer_verdict"]

        post = lambda **kw: self.client.post("/api/calls/mark", json={"call_id": cid, **kw}).json()
        self.assertEqual(post(verdict="incorrect", note="wrong frame"), {"ok": True})
        self.assertEqual(verdict(), "incorrect")
        post(verdict="correct")
        self.assertEqual(verdict(), "correct")
        post(verdict=None)
        self.assertIsNone(verdict())
        self.assertIn("error", post(verdict="bad"))
        self.assertIn("error", self.client.post(
            "/api/calls/mark", json={"call_id": "nope", "verdict": "correct"}).json())

    def test_followup_turn_is_also_recorded(self):
        self.run_analysis()
        self.query(agent="followup", question="Which step are you least certain about?")
        calls = self.client.get("/api/sessions/s1/trajectory").json()["calls"]
        self.assertIn("s1:1:run", [c["call_id"] for c in calls])


if __name__ == "__main__":
    unittest.main()
