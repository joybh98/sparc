"""Flagging an answer: verdict + reason, correction, session record, preference export."""
import json
import sqlite3
import unittest

from tests.helpers import force_mock_env
from tests.test_video_agents import Base

force_mock_env()

from core import feedback  # noqa: E402


class FeedbackBase(Base):
    def setUp(self):
        super().setUp()
        self.upload()
        self.query(agent="default")                                   # turn 0: steps
        self.answer = self.query(agent="followup", question="Is the tear controlled?",
                                 player_time_sec=1.5)["turn"]          # turn 1: answer

    def flag(self, rating, turn_index=1, **kw):
        return self.client.post("/api/answers/feedback", json={
            "session_id": "s1", "turn_index": turn_index, "rating": rating, **kw}).json()

    def examples(self, **params):
        return self.client.get("/api/preferences", params=params).json()["examples"]

    def run_row(self, turn_index=1):
        calls = self.client.get("/api/sessions/s1/trajectory").json()["calls"]
        return next(c for c in calls if c["call_id"] == f"s1:{turn_index}:run")


class FlagTests(FeedbackBase):
    def test_thumbs_down_with_reason_and_correction_is_stored_everywhere(self):
        out = self.flag("down", reason="wrong step", correction="It is a capsulorhexis.")
        self.assertTrue(out["ok"])

        row = self.run_row()
        self.assertEqual((row["reviewer_verdict"], row["reviewer_note"]), ("incorrect", "wrong step"))
        turn = self.client.get("/api/sessions/s1").json()["turns"][1]
        self.assertEqual(turn["feedback"]["rating"], "down")
        self.assertEqual(turn["feedback"]["correction"], "It is a capsulorhexis.")
        self.assertEqual(turn["answer"], self.answer["answer"])    # the original is kept
        db = sqlite3.connect(self.tmp.name + "/trajectory.db")
        self.assertEqual(db.execute("select text from corrections").fetchall(),
                         [("It is a capsulorhexis.",)])

    def test_thumbs_up_is_a_correct_verdict_and_drops_any_correction(self):
        self.flag("down", reason="x", correction="y")
        self.flag("up")
        self.assertEqual(self.run_row()["reviewer_verdict"], "correct")
        self.assertIsNone(self.run_row()["reviewer_note"])
        self.assertIsNone(self.client.get("/api/sessions/s1").json()["turns"][1]["feedback"]["correction"])
        self.assertIsNone(self.examples()[0]["correction"])

    def test_clearing_removes_the_flag_the_correction_and_the_example(self):
        self.flag("down", reason="x", correction="y")
        self.assertTrue(self.flag(None)["ok"])
        self.assertIsNone(self.run_row()["reviewer_verdict"])
        self.assertNotIn("feedback", self.client.get("/api/sessions/s1").json()["turns"][1])
        self.assertEqual(self.examples(), [])

    def test_blank_text_is_dropped_and_long_text_is_capped(self):
        self.flag("down", reason="   ", correction="")
        self.assertIsNone(self.run_row()["reviewer_note"])
        self.flag("down", reason="r" * 900, correction="c" * 9000)
        fb = self.client.get("/api/sessions/s1").json()["turns"][1]["feedback"]
        self.assertEqual((len(fb["reason"]), len(fb["correction"])),
                         (feedback.MAX_REASON, feedback.MAX_CORRECTION))

    def test_rejects_bad_requests(self):
        self.assertIn("error", self.flag("meh"))
        self.assertIn("error", self.flag("down", turn_index=0))      # a steps turn
        self.assertIn("error", self.flag("down", turn_index=99))
        self.assertIn("error", self.client.post("/api/answers/feedback", json={
            "session_id": "nope", "turn_index": 0, "rating": "up"}).json())

    def test_tool_call_marks_are_independent_of_the_answer_flag(self):
        self.flag("down")
        tool = next(c for c in self.client.get("/api/sessions/s1/trajectory").json()["calls"]
                    if c["call_id"].startswith("s1:1:") and c["kind"] == "tool")
        self.assertIsNone(tool["reviewer_verdict"])


class PreferenceExportTests(FeedbackBase):
    def test_down_with_correction_yields_a_pair_and_a_negative_label(self):
        self.flag("down", reason="wrong step", correction="It is a capsulorhexis.")
        (ex,) = self.examples()
        self.assertEqual(ex["pair"], {"chosen": "It is a capsulorhexis.",
                                      "rejected": self.answer["answer"]})
        self.assertFalse(ex["label"])
        self.assertEqual((ex["rating"], ex["reason"]), ("down", "wrong step"))
        self.assertEqual(ex["prompt"]["question"], "Is the tear controlled?")
        self.assertEqual(ex["prompt"]["player_time_sec"], 1.5)
        self.assertEqual(ex["model_confidence"], 0.6)
        self.assertTrue(ex["is_mock"])

    def test_down_without_correction_is_label_only(self):
        self.flag("down", reason="vague")
        (ex,) = self.examples()
        self.assertIsNone(ex["pair"])
        self.assertFalse(ex["label"])

    def test_up_is_a_positive_label(self):
        self.flag("up")
        (ex,) = self.examples()
        self.assertTrue(ex["label"])
        self.assertIsNone(ex["pair"])

    def test_history_holds_only_earlier_answers(self):
        second = self.query(agent="followup", question="And the second step?")["turn"]
        self.flag("down", turn_index=second["turn_index"], correction="c")
        (ex,) = self.examples()
        self.assertEqual(ex["prompt"]["history"],
                         [{"question": "Is the tear controlled?", "answer": self.answer["answer"]}])

    def test_jsonl_and_all_sessions(self):
        self.flag("up")
        text = self.client.get("/api/preferences", params={"format": "jsonl"}).text
        lines = [json.loads(l) for l in text.splitlines()]
        self.assertEqual([l["example_id"] for l in lines], ["s1:1:run"])
        self.assertEqual(self.examples(session_id="other"), [])

    def test_steps_turn_verdicts_are_not_exported(self):
        self.client.post("/api/calls/mark", json={"call_id": "s1:0:run", "verdict": "incorrect"})
        self.assertEqual(self.examples(), [])


class MemoryTests(FeedbackBase):
    def replayed(self):
        from modalities.video import _memory
        return _memory(app_module_session())[-1]["text"]

    def test_next_question_sees_the_flag_and_the_correction(self):
        self.flag("down", reason="wrong step", correction="It is a capsulorhexis.")
        text = self.replayed()
        self.assertTrue(text.startswith(self.answer["answer"]))
        self.assertIn("flagged as incorrect", text)
        self.assertIn("Reason: wrong step", text)
        self.assertIn("It is a capsulorhexis.", text)

    def test_approved_or_unflagged_answers_replay_unchanged(self):
        self.assertEqual(self.replayed(), self.answer["answer"])
        self.flag("up")
        self.assertEqual(self.replayed(), self.answer["answer"])

    def test_text_modality_replays_the_flag_too(self):
        from modalities.text import _memory
        self.flag("down", correction="fix")
        self.assertIn("flagged as incorrect", _memory(app_module_session())[-1]["text"])


def app_module_session():
    import app as app_module
    return app_module.load_session("s1")


if __name__ == "__main__":
    unittest.main()
