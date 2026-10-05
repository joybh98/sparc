"""SQLite store for agent trajectories: one row per run, model call and tool call, linked by
parent_id, so "which call invoked what" is queryable and the reviewer can mark each call.

The session JSON keeps the full trace; this is the flat, queryable copy. A call_id is
deterministic ("<session>:<turn>:<trace seq>", or ":run" for the root), so the UI can
address a trace event without a round trip."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

VERDICTS = {"correct", "incorrect", "unsure"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    parent_id TEXT,
    seq INTEGER NOT NULL,
    iteration INTEGER,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    caller TEXT NOT NULL,
    started_at TEXT,
    latency_ms INTEGER,
    args_json TEXT,
    result_text TEXT,
    result_json TEXT,
    is_error INTEGER NOT NULL DEFAULT 0,
    reviewer_verdict TEXT,
    reviewer_note TEXT,
    marked_at TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    source TEXT NOT NULL,
    step_index INTEGER,
    step_label TEXT,
    confidence REAL,
    uncertainty TEXT,
    time_sec REAL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS corrections (
    call_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_call ON evidence(call_id);
CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id, turn_index, seq);
"""


def call_id(session_id: str, turn_index: int, seq) -> str:
    return f"{session_id}:{turn_index}:{seq}"


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _dumps(value) -> str:
    return json.dumps(value, default=str)


def turn_rows(session_id: str, turn_index: int, turn: Dict) -> List[Dict]:
    """Flatten a saved turn's trace into call rows: run -> model_call -> tool."""
    agent = turn.get("agent") or turn.get("type") or "agent"
    root = call_id(session_id, turn_index, "run")
    rows = [{
        "call_id": root, "parent_id": None, "seq": 0, "iteration": None, "kind": "run",
        "name": agent, "caller": "reviewer_ui", "started_at": turn.get("timestamp"),
        "latency_ms": turn.get("latency_ms"),
        "args": {"type": turn.get("type"), "model": turn.get("model"),
                 "question": turn.get("question"),
                 "player_time_sec": turn.get("player_time_sec")},
        "result_text": turn.get("error") or "", "result": {"stop_reason": turn.get("stop_reason")},
        "is_error": bool(turn.get("error")),
    }]

    model_ids: Dict[int, str] = {}
    results = {}
    for e in turn.get("trace", []):
        if e["type"] == "tool_result":
            results.setdefault((e.get("iteration"), e.get("id")), e)

    for e in turn.get("trace", []):
        if e["type"] == "model_call":
            cid = call_id(session_id, turn_index, e["seq"])
            model_ids[e["iteration"]] = cid
            rows.append({
                "call_id": cid, "parent_id": root, "seq": e["seq"], "iteration": e["iteration"],
                "kind": "model_call", "name": e.get("model") or "model",
                "caller": f"run:{agent}", "started_at": e["timestamp"],
                "latency_ms": e.get("latency_ms"),
                "args": {"n_tool_calls": e.get("n_tool_calls"), "usage": e.get("usage")},
                "result_text": e.get("text") or "", "result": None, "is_error": False,
            })
        elif e["type"] == "tool_call":
            res = results.get((e.get("iteration"), e.get("id"))) or {}
            parent = model_ids.get(e.get("iteration"), root)
            rows.append({
                "call_id": call_id(session_id, turn_index, e["seq"]), "parent_id": parent,
                "seq": e["seq"], "iteration": e.get("iteration"), "kind": "tool",
                "name": e["name"], "caller": f"model_call#{e.get('iteration')}",
                "started_at": e["timestamp"], "latency_ms": res.get("latency_ms"),
                "args": e.get("args"), "result_text": res.get("text") or "",
                "result": {"data": res.get("data"), "images": res.get("images"),
                           "ui": res.get("ui")},
                "is_error": bool(res.get("is_error")),
            })
        elif e["type"] == "error":
            rows.append({
                "call_id": call_id(session_id, turn_index, e["seq"]), "parent_id": root,
                "seq": e["seq"], "iteration": e.get("iteration"), "kind": "error",
                "name": e.get("stage") or "error", "caller": f"run:{agent}",
                "started_at": e["timestamp"], "latency_ms": None, "args": None,
                "result_text": e.get("message") or "", "result": None, "is_error": True,
            })
    return rows


DELIVERY_TOOLS = {"submit_steps": "step", "submit_answer": "answer"}


def evidence_rows(session_id: str, turn_index: int, turn: Dict) -> List[Dict]:
    """One row per cited frame, tied to the tool call that delivered it. A step or answer
    that cites nothing still gets one row (time_sec NULL) so its confidence is kept."""
    trace = turn.get("trace", [])
    failed = {(e.get("iteration"), e.get("id")) for e in trace
              if e["type"] == "tool_result" and e.get("is_error")}
    delivered = [e for e in trace if e["type"] == "tool_call" and e["name"] in DELIVERY_TOOLS
                 and (e.get("iteration"), e.get("id")) not in failed]
    if not delivered:
        return []
    call = delivered[-1]
    cid, source = call_id(session_id, turn_index, call["seq"]), DELIVERY_TOOLS[call["name"]]
    if source == "step":
        items = [(i, s.get("step_label"), s) for i, s in enumerate(turn.get("model_steps") or [])]
    else:
        items = [(None, None, turn)]
    rows = []
    for idx, label, item in items:
        base = {"call_id": cid, "source": source, "step_index": idx, "step_label": label,
                "confidence": item.get("confidence"), "uncertainty": item.get("uncertainty")}
        cited = item.get("evidence") or [{"time_sec": None, "note": None}]
        rows += [{**base, "time_sec": e.get("time_sec"), "note": e.get("note")} for e in cited]
    return rows


def record_turn(db_path: str, session_id: str, turn_index: int, turn: Dict) -> None:
    rows = turn_rows(session_id, turn_index, turn)
    ev = evidence_rows(session_id, turn_index, turn)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM evidence WHERE session_id = ? AND turn_index = ?",
                     (session_id, turn_index))
        conn.executemany(
            "INSERT INTO evidence (call_id, session_id, turn_index, source, step_index, "
            "step_label, confidence, uncertainty, time_sec, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(r["call_id"], session_id, turn_index, r["source"], r["step_index"],
              r["step_label"], r["confidence"], r["uncertainty"], r["time_sec"], r["note"])
             for r in ev])
        conn.executemany(
            "INSERT OR REPLACE INTO calls (call_id, session_id, turn_index, parent_id, seq, "
            "iteration, kind, name, caller, started_at, latency_ms, args_json, result_text, "
            "result_json, is_error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["call_id"], session_id, turn_index, r["parent_id"], r["seq"], r["iteration"],
              r["kind"], r["name"], r["caller"], r["started_at"], r["latency_ms"],
              _dumps(r["args"]), r["result_text"], _dumps(r["result"]), int(r["is_error"]))
             for r in rows])


def mark_call(db_path: str, cid: str, verdict: Optional[str], note: Optional[str]) -> bool:
    """verdict=None clears the mark."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE calls SET reviewer_verdict = ?, reviewer_note = ?, marked_at = ? "
            "WHERE call_id = ?",
            (verdict, note if verdict else None,
             datetime.now(timezone.utc).isoformat() if verdict else None, cid))
        return cur.rowcount > 0


def set_correction(db_path: str, cid: str, session_id: str, turn_index: int,
                   text: Optional[str]) -> None:
    """What the reviewer says the answer should have been. Empty text removes it."""
    with _connect(db_path) as conn:
        if text:
            conn.execute("INSERT OR REPLACE INTO corrections (call_id, session_id, turn_index, "
                         "text, created_at) VALUES (?,?,?,?,?)",
                         (cid, session_id, turn_index, text,
                          datetime.now(timezone.utc).isoformat()))
        else:
            conn.execute("DELETE FROM corrections WHERE call_id = ?", (cid,))


def get_corrections(db_path: str, session_id: str) -> Dict[str, str]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT call_id, text FROM corrections WHERE session_id = ?",
                            (session_id,)).fetchall()
    return {r["call_id"]: r["text"] for r in rows}


def get_run_marks(db_path: str, session_id: str) -> List[Dict]:
    """Run rows (one per turn) the reviewer has given a verdict."""
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT call_id, turn_index, reviewer_verdict, reviewer_note, "
                            "marked_at FROM calls WHERE session_id = ? AND kind = 'run' "
                            "AND reviewer_verdict IS NOT NULL", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def get_evidence(db_path: str, session_id: str) -> List[Dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM evidence WHERE session_id = ? "
                            "ORDER BY turn_index, id", (session_id,)).fetchall()
    return [dict(r) for r in rows]


def get_trajectory(db_path: str, session_id: str) -> List[Dict]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM calls WHERE session_id = ? "
                            "ORDER BY turn_index, seq", (session_id,)).fetchall()
    out = []
    for row in rows:
        c = dict(row)
        c["args"] = json.loads(c.pop("args_json") or "null")
        c["result"] = json.loads(c.pop("result_json") or "null")
        c["is_error"] = bool(c["is_error"])
        out.append(c)
    return out
