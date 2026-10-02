import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "sessions", "trajectory.db")

VERDICTS = {"correct", "incorrect", "unsure"}
ROOT_CALLER = "reviewer_ui"

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    call_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_index INTEGER,
    parent_id TEXT,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    caller TEXT NOT NULL,
    started_at TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    inputs_json TEXT,
    outputs_json TEXT,
    error TEXT,
    reviewer_verdict TEXT,
    reviewer_note TEXT,
    marked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_session ON calls(session_id, trace_id, seq);
"""

COLUMNS = ["call_id", "trace_id", "session_id", "turn_index", "parent_id", "seq", "name",
           "caller", "started_at", "latency_ms", "inputs_json", "outputs_json", "error",
           "reviewer_verdict", "reviewer_note", "marked_at"]


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def new_trace(session_id: str, mode: str) -> Dict:
    return {"trace_id": str(uuid.uuid4()), "session_id": session_id, "mode": mode, "calls": []}


@contextmanager
def traced_call(trace: Optional[Dict], name: str, parent: Optional[Dict] = None,
                inputs: Optional[Dict] = None):
    call = {
        "call_id": str(uuid.uuid4()),
        "trace_id": trace["trace_id"] if trace else None,
        "session_id": trace["session_id"] if trace else None,
        "turn_index": None,
        "parent_id": parent["call_id"] if parent else None,
        "seq": len(trace["calls"]) if trace else 0,
        "name": name,
        "caller": parent["name"] if parent else ROOT_CALLER,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "latency_ms": 0,
        "inputs": inputs or {},
        "outputs": {},
        "error": None,
        "reviewer_verdict": None,
        "reviewer_note": None,
        "marked_at": None,
    }
    if trace is not None:
        trace["calls"].append(call)
    start = time.perf_counter()
    try:
        yield call
    except Exception as e:
        call["error"] = str(e)
        raise
    finally:
        call["latency_ms"] = int((time.perf_counter() - start) * 1000)


def persist_trace(trace: Dict, turn_index: int) -> None:
    rows = []
    for call in trace["calls"]:
        call["turn_index"] = turn_index
        rows.append((
            call["call_id"], call["trace_id"], call["session_id"], turn_index,
            call["parent_id"], call["seq"], call["name"], call["caller"],
            call["started_at"], call["latency_ms"],
            json.dumps(call["inputs"], default=str),
            json.dumps(call["outputs"], default=str),
            call["error"], None, None, None,
        ))
    with _connect() as conn:
        conn.executemany(
            f"INSERT INTO calls ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            rows,
        )


def mark_call(call_id: str, verdict: str, note: Optional[str]) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE calls SET reviewer_verdict = ?, reviewer_note = ?, marked_at = ? "
            "WHERE call_id = ?",
            (verdict, note, datetime.now(timezone.utc).isoformat(), call_id),
        )
        return cur.rowcount > 0


def get_trajectory(session_id: str) -> List[Dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM calls WHERE session_id = ? ORDER BY started_at, seq",
            (session_id,),
        ).fetchall()
    calls = []
    for row in rows:
        call = dict(row)
        call["inputs"] = json.loads(call.pop("inputs_json") or "{}")
        call["outputs"] = json.loads(call.pop("outputs_json") or "{}")
        calls.append(call)
    return calls
