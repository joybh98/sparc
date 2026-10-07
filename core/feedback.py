"""Reviewer feedback on a Q&A answer, and the training examples derived from it.

A flag is a verdict on the turn's `run` row in the trajectory store (the same mechanism as
step tagging: 👎 = incorrect, 👍 = correct, reason in the row's note), plus an optional
correction (what the answer should have said). Both are also written onto the turn in the
session JSON, so the record itself is corrected. Preference examples are never stored: they
are derived from those two on read, so editing a flag changes the export."""
from typing import Dict, List, Optional

from core.trajectory import call_id, get_corrections, get_run_marks

RATINGS = {"up": "correct", "down": "incorrect"}
VERDICT_RATING = {v: k for k, v in RATINGS.items()}
ANSWER_TYPES = ("followup", "chat")
MAX_REASON = 500
MAX_CORRECTION = 4000
HISTORY_TURNS = 6


def clean(text: Optional[str], limit: int) -> Optional[str]:
    text = (text or "").strip()
    return text[:limit] or None


def run_call_id(session_id: str, turn_index: int) -> str:
    return call_id(session_id, turn_index, "run")


def memory_note(turn: Dict) -> str:
    """Appended to a flagged answer when it is replayed as chat history, so the model does
    not repeat a mistake the reviewer already corrected."""
    fb = turn.get("feedback") or {}
    if fb.get("rating") != "down":
        return ""
    note = "\n\n[Reviewer feedback: this answer was flagged as incorrect."
    if fb.get("reason"):
        note += f" Reason: {fb['reason']}."
    if fb.get("correction"):
        note += f" The reviewer says the correct answer is: {fb['correction']}"
    return note + "]"


def _history(turns: List[Dict], before: int) -> List[Dict]:
    prior = [{"question": t.get("question", ""), "answer": t["answer"]}
             for t in turns[:before]
             if t.get("type") in ANSWER_TYPES and t.get("answer") and not t.get("error")]
    return prior[-HISTORY_TURNS:]


def build_examples(sessions: List[Dict], db_path: str) -> List[Dict]:
    """One example per flagged answer.

    `label` is always set (True = the reviewer approved it). `pair` is set when the reviewer
    flagged it AND wrote a correction: chosen = their correction, rejected = the model's
    answer. `is_mock` lets a training filter drop scripted-mock answers."""
    out = []
    for session in sessions:
        sid = session["session_id"]
        marks = {m["call_id"]: m for m in get_run_marks(db_path, sid)}
        corrections = get_corrections(db_path, sid)
        turns = session.get("turns", [])
        for turn in turns:
            cid = run_call_id(sid, turn.get("turn_index", -1))
            mark = marks.get(cid)
            if mark is None or turn.get("type") not in ANSWER_TYPES or not turn.get("answer"):
                continue
            rating = VERDICT_RATING.get(mark["reviewer_verdict"])
            if rating is None:
                continue
            correction = corrections.get(cid)
            out.append({
                "example_id": cid,
                "marked_at": mark["marked_at"],
                "session_id": sid,
                "turn_index": turn["turn_index"],
                "turn_type": turn["type"],
                "agent": turn.get("agent"),
                "model": turn.get("model"),
                "is_mock": "mock" in (turn.get("provider") or ""),
                "prompt": {"question": turn.get("question", ""),
                           "history": _history(turns, turn["turn_index"]),
                           "player_time_sec": turn.get("player_time_sec")},
                "response": turn["answer"],
                "model_confidence": turn.get("confidence"),
                "model_evidence": turn.get("evidence"),
                "rating": rating,
                "label": rating == "up",
                "reason": mark["reviewer_note"],
                "correction": correction,
                "pair": ({"chosen": correction, "rejected": turn["answer"]}
                         if rating == "down" and correction else None),
            })
    return sorted(out, key=lambda e: (e["session_id"], e["turn_index"]))
