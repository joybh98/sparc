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


class AnswerEvidenceTests(Base):
    def setUp(self):
        super().setUp()
        self.upload()
        self.query(agent="default")

    def ask(self, **kw):
        return self.query(agent="followup", question="What is happening here?", **kw)["turn"]

    def test_answer_carries_confidence_uncertainty_and_evidence(self):
        turn = self.ask()
        self.assertIsNone(turn["error"])
        self.assertEqual(turn["stop_reason"], "tool:submit_answer")
        self.assertEqual(turn["confidence"], 0.6)
        self.assertTrue(turn["uncertainty"])
        self.assertEqual(len(turn["evidence"]), 1)
        self.assertIn("[MOCK]", turn["answer"])

    def test_player_position_reaches_the_agent_and_is_stored(self):
        turn = self.ask(player_time_sec=2.5)
        self.assertEqual(turn["player_time_sec"], 2.5)
        self.assertEqual(turn["evidence"][0]["time_sec"], 2.5)
        self.assertIn("around 2.5s", turn["answer"])

    def test_player_position_is_clamped_to_the_clip(self):
        self.assertEqual(self.ask(player_time_sec=999)["player_time_sec"], 4.0)
        self.assertEqual(self.ask(player_time_sec=-3)["player_time_sec"], 0.0)

    def test_no_player_position_means_none_stored(self):
        self.assertNotIn("player_time_sec", self.ask())

    def test_player_frame_is_attached_to_the_first_message(self):
        from modalities.video import initial_messages
        from core import registry
        agent = registry.load_agents(registry.get_modality("video"))[0]["followup"]
        ctx = ToolContext(session_id="s1", session={"turns": []},
                          session_dir=f"{self.tmp.name}/s1")
        with_pos = initial_messages(agent, ctx, {"question": "q", "player_time_sec": 1.0})[-1]
        without = initial_messages(agent, ctx, {"question": "q"})[-1]
        self.assertEqual(len(with_pos["images"]), 1)
        self.assertIn("player is at 1.00s", with_pos["text"])
        self.assertEqual(without["images"], [])

    def test_steps_agent_ignores_player_position(self):
        turn = self.query(agent="default", player_time_sec=1.0)["turn"]
        self.assertNotIn("player_time_sec", turn)

    def test_submit_answer_validation(self):
        ctx = ToolContext(session_id="s1", session={}, session_dir=f"{self.tmp.name}/s1")
        tool = get_tools("video")["submit_answer"]
        ok = {"answer": "a", "confidence": 0.5, "evidence": [{"time_sec": 1, "note": "n"}]}
        self.assertFalse(run_tool(tool, ctx, ok).is_error)
        for bad in ({**ok, "confidence": 2}, {**ok, "answer": " "},
                    {**ok, "evidence": [{"time_sec": 99, "note": ""}]}, {**ok, "evidence": 1}):
            self.assertTrue(run_tool(tool, ctx, bad).is_error, bad)


class EvidenceTableTests(Base):
    def rows(self):
        return self.client.get("/api/sessions/s1/evidence").json()["evidence"]

    def test_steps_and_answers_are_flattened_into_the_table(self):
        self.upload()
        self.query(agent="default")
        steps = self.rows()
        self.assertEqual([r["source"] for r in steps], ["step"] * 3)
        self.assertEqual([r["step_label"] for r in steps],
                         ["Capsulorhexis", "Capsulorhexis", "Phacoemulsification"])
        self.assertEqual(steps[0]["confidence"], 0.7)

        self.query(agent="followup", question="q", player_time_sec=1.5)
        answer = [r for r in self.rows() if r["source"] == "answer"]
        self.assertEqual(len(answer), 1)
        self.assertEqual((answer[0]["turn_index"], answer[0]["time_sec"]), (1, 1.5))

    def test_evidence_joins_to_the_delivering_call_and_its_verdict(self):
        self.upload()
        self.query(agent="default")
        calls = {c["call_id"]: c for c in
                 self.client.get("/api/sessions/s1/trajectory").json()["calls"]}
        row = self.rows()[0]
        self.assertEqual(calls[row["call_id"]]["name"], "submit_steps")
        self.client.post("/api/calls/mark", json={"call_id": row["call_id"], "verdict": "incorrect"})
        import sqlite3
        db = sqlite3.connect(self.tmp.name + "/trajectory.db")
        n = db.execute("SELECT count(*) FROM evidence e JOIN calls c USING (call_id) "
                       "WHERE c.reviewer_verdict = 'incorrect'").fetchone()[0]
        self.assertEqual(n, 3)

    def test_uncited_answer_keeps_its_confidence(self):
        from core.trajectory import evidence_rows
        turn = {"trace": [{"type": "tool_call", "name": "submit_answer", "seq": 3,
                           "iteration": 1, "id": "x"}],
                "confidence": 0.4, "uncertainty": "u", "evidence": []}
        rows = evidence_rows("s", 0, turn)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["confidence"], rows[0]["time_sec"]), (0.4, None))

    def test_a_rejected_submit_stores_nothing(self):
        from core.trajectory import evidence_rows
        trace = [{"type": "tool_call", "name": "submit_steps", "seq": 2, "iteration": 1, "id": "x"},
                 {"type": "tool_result", "name": "submit_steps", "iteration": 1, "id": "x",
                  "is_error": True}]
        self.assertEqual(evidence_rows("s", 0, {"trace": trace, "model_steps": []}), [])

    def test_rerecording_a_turn_replaces_its_rows(self):
        from core.trajectory import record_turn
        self.upload()
        turn = self.query(agent="default")["turn"]
        record_turn(self.tmp.name + "/trajectory.db", "s1", 0, turn)
        self.assertEqual(len(self.rows()), 3)


if __name__ == "__main__":
    unittest.main()
