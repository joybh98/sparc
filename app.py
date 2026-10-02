import base64
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import registry
from core.loop import run_agent
from core.tools import ToolContext, get_tools
from frames import (extract_frame_at, extract_frames_b64, extract_frames_timed,
                    video_duration)
from providers import available_models, query_model, query_steps

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def reload_env():
    load_dotenv(ENV_PATH, override=True)


reload_env()

SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
STATIC_DIR = os.path.join(BASE_DIR, "static")

ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
MAX_VIDEO_BYTES = 500 * 1024 * 1024

os.makedirs(SESSIONS_DIR, exist_ok=True)

registry.discover()

app = FastAPI(title="SPARC Frontier-Model Eval — Barebones")


class QueryRequest(BaseModel):
    session_id: str
    mode: str = "chat"
    question: str = ""
    step_hint: Optional[str] = None
    models: List[str] = []
    conversation_history: List[Dict] = []
    n_frames: int = 8
    # mode="agent": pick a saved agent by name, or pass a full config inline
    agent: Optional[str] = None
    agent_config: Optional[Dict] = None
    stream: bool = False    # agent mode: return a run_id immediately; poll /api/runs/<id>


class MarkRequest(BaseModel):
    session_id: str
    step_label: str
    start_sec: float
    end_sec: float
    note: Optional[str] = None


def session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def session_video_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, session_id, "video.mp4")


def load_session(session_id: str) -> Dict:
    path = session_path(session_id)
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "video": None,
        "turns": [],
        "marks": [],
    }


def save_session(session: Dict):
    with open(session_path(session["session_id"]), "w") as f:
        json.dump(session, f, indent=2)


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/models")
def api_models():
    reload_env()
    return {"models": available_models()}


@app.get("/api/agents")
def api_agents(session_id: Optional[str] = None):
    """Everything the UI needs to drive agents: per modality, its agents (re-read from
    disk), its tools, and any config files that failed validation."""
    active = None
    if session_id:
        m = registry.resolve_modality(load_session(session_id))
        active = m.name if m else None
    modalities = []
    for m in registry.all_modalities():
        agents, invalid = registry.load_agents(m)
        modalities.append({
            "name": m.name, "label": m.label, "description": m.description, "ui": m.ui,
            "agents": [a.to_dict() for a in agents.values()],
            "tools": [t.schema() for t in get_tools(m.name).values()],
            "invalid": invalid,
        })
    return {"active": active, "modalities": modalities,
            "errors": registry.DISCOVERY_ERRORS}


@app.get("/api/sessions/{session_id}/frame")
def api_session_frame(session_id: str, t: float, zoom: float = 1.0, cx: float = 0.5,
                      cy: float = 0.5, w: int = 480):
    """Re-render a frame a trace refers to (trace stores refs, not image bytes)."""
    frame = extract_frame_at(session_video_path(session_id), t,
                             min(max(zoom, 1.0), 4.0), min(max(cx, 0.0), 1.0),
                             min(max(cy, 0.0), 1.0), max_side=min(max(w, 64), 1280))
    if not frame:
        raise HTTPException(status_code=404, detail="frame not available")
    return Response(content=base64.b64decode(frame["b64"]), media_type="image/jpeg")


@app.post("/api/upload")
def api_upload(file: UploadFile, session_id: Optional[str] = Form(None)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_VIDEO_EXTS:
        return {"error": f"unsupported video type '{ext}'"}

    sid = session_id or str(uuid.uuid4())
    session_dir = os.path.join(SESSIONS_DIR, sid)
    os.makedirs(session_dir, exist_ok=True)
    dest = os.path.join(session_dir, "video.mp4")

    size = 0
    with open(dest, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_VIDEO_BYTES:
                out.close()
                os.remove(dest)
                return {"error": "video exceeds 500 MB limit"}
            out.write(chunk)

    session = load_session(sid)
    session["video"] = "video.mp4"
    save_session(session)

    return {
        "session_id": sid,
        "video_url": f"/api/sessions/{sid}/video",
        "duration_sec": video_duration(dest),
    }


@app.get("/api/sessions/{session_id}/video")
def api_session_video(session_id: str):
    path = session_video_path(session_id)
    if not os.path.exists(path):
        return {"error": "no video uploaded for this session"}
    return FileResponse(path)


class AgentRun:
    """A background agent run whose trace events can be polled while it executes."""

    def __init__(self):
        self.id = uuid.uuid4().hex
        self.created = time.time()
        self.lock = threading.Lock()
        self.events: List[Dict] = []
        self.status = "running"          # running | done | error
        self.turn: Optional[Dict] = None
        self.error: Optional[str] = None

    def add_event(self, event: Dict):
        with self.lock:
            self.events.append(event)

    def finish(self, turn: Optional[Dict], error: Optional[str]):
        with self.lock:
            self.turn, self.error = turn, error
            self.status = "done" if error is None else "error"


RUNS: Dict[str, AgentRun] = {}
RUN_TTL_SEC = 3600
_session_locks: Dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def session_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())


def prepare_agent_run(req: QueryRequest) -> Dict:
    """Resolve modality, agent and model. Returns {"error": ...} or the run inputs."""
    session = load_session(req.session_id)
    modality = registry.resolve_modality(session)
    if modality is None:
        return {"error": "no modality available for this session"}

    if req.agent_config is not None:
        agent, errors = registry.validate_agent(req.agent_config, modality)
        if errors:
            return {"error": "invalid agent config: " + "; ".join(errors)}
    else:
        agents, _ = registry.load_agents(modality)
        name = req.agent or ("default" if "default" in agents else next(iter(agents), ""))
        agent = agents.get(name)
        if agent is None:
            return {"error": f"unknown agent '{name}' for {modality.name} "
                             f"(available: {', '.join(agents) or 'none'})"}

    if agent.kind != "steps" and not req.question.strip():
        return {"error": f"agent '{agent.name}' needs a question"}

    return {
        "modality": modality, "agent": agent,
        "model_key": (req.models[0] if req.models else None) or agent.model or "gpt",
        "ctx": ToolContext(session_id=req.session_id, session=session,
                           session_dir=os.path.join(SESSIONS_DIR, req.session_id)),
        "params": {"question": req.question, "step_hint": req.step_hint,
                   "n_frames": req.n_frames},
    }


def execute_agent_run(prep: Dict, on_event=None) -> Dict:
    """Run the agent loop, then save the turn. Returns the turn."""
    agent, modality, ctx = prep["agent"], prep["modality"], prep["ctx"]
    result = run_agent(agent, modality, prep["model_key"], ctx, prep["params"],
                       on_event=on_event)

    turn = {
        "type": agent.kind,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "modality": modality.name,
        "agent": agent.name,
        "agent_config": agent.to_dict(),
        "n_frames_sent": ctx.state.get("n_frames_sent", 0),
        "model": result.model or prep["model_key"],
        "provider": result.provider or "unknown",
        "latency_ms": result.latency_ms,
        "iterations": result.iterations,
        "stop_reason": result.stop_reason,
        "output": result.output,
        "trace": result.trace,
        "error": result.error,
    }
    if agent.kind == "steps":
        turn["model_steps"] = result.output.get("steps", [])
        turn["assessment"] = result.output.get("assessment", "")
    else:
        turn["question"] = prep["params"]["question"]
        turn["answer"] = result.output.get("text", "")
        turn["needs_reply"] = bool(result.output.get("needs_reply"))

    # Re-read under a lock: a chat turn and an analysis run can finish at the same time.
    with session_lock(ctx.session_id):
        session = load_session(ctx.session_id)
        turn["turn_index"] = len(session["turns"])
        if agent.kind != "steps":
            turn["parent_turn"] = next((t["turn_index"] for t in reversed(session["turns"])
                                        if t.get("type") == "steps"), None)
        session["turns"].append(turn)
        save_session(session)
    return turn


def start_agent_run(prep: Dict) -> AgentRun:
    for rid in [r.id for r in RUNS.values()
                if r.status != "running" and time.time() - r.created > RUN_TTL_SEC]:
        RUNS.pop(rid, None)
    run = AgentRun()
    RUNS[run.id] = run

    def work():
        try:
            run.finish(execute_agent_run(prep, on_event=run.add_event), None)
        except Exception as e:  # e.g. the modality failed to build its first message
            run.finish(None, f"{type(e).__name__}: {e}")

    threading.Thread(target=work, daemon=True).start()
    return run


def run_agent_turn(req: QueryRequest) -> Dict:
    prep = prepare_agent_run(req)
    if "error" in prep:
        return prep
    if req.stream:
        return {"session_id": req.session_id, "run_id": start_agent_run(prep).id}
    return {"session_id": req.session_id, "turn": execute_agent_run(prep)}


@app.get("/api/runs/{run_id}")
def api_run(run_id: str, after: int = 0):
    """Poll a streaming agent run: trace events with seq > after, plus status/turn."""
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    with run.lock:
        return {"status": run.status, "error": run.error, "turn": run.turn,
                "events": [e for e in run.events if e["seq"] > after]}


@app.post("/api/query")
def api_query(req: QueryRequest):
    """Single endpoint: given an uploaded clip, either answer a question about it
    (mode=chat), return a structured surgical-step timeline (mode=steps), or run a
    configurable tool-using agent loop with a full trace (mode=agent)."""
    reload_env()
    if req.mode == "agent":
        return run_agent_turn(req)
    video_path = session_video_path(req.session_id)
    if not os.path.exists(video_path):
        return {"error": "no video uploaded for this session"}

    session = load_session(req.session_id)

    if req.mode == "steps":
        frames_timed = extract_frames_timed(video_path, req.n_frames)
        result = query_steps((req.models or ["gpt"])[0], frames_timed, req.step_hint,
                              req.conversation_history)
        turn = {
            "turn_index": len(session["turns"]),
            "type": "steps",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "n_frames_sent": len(frames_timed),
            "model": result["model"],
            "provider": result["provider"],
            "latency_ms": result["latency_ms"],
            "model_steps": result["steps"],
            "assessment": result["assessment"],
            "error": result["error"],
        }
        session["turns"].append(turn)
        save_session(session)
        return {"session_id": req.session_id, "turn": turn}

    frames_b64 = extract_frames_b64(video_path, req.n_frames)
    results = []
    for model_key in req.models or ["gpt"]:
        resp = query_model(model_key, req.question, frames_b64, req.conversation_history)
        results.append(resp.to_dict())

    turn = {
        "turn_index": len(session["turns"]),
        "type": "chat",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question": req.question,
        "n_frames_sent": len(frames_b64),
        "responses": results,
    }
    session["turns"].append(turn)
    save_session(session)

    return {"session_id": req.session_id, "turn": turn}


@app.post("/api/mark")
def api_mark(req: MarkRequest):
    session = load_session(req.session_id)
    session["marks"].append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "step_label": req.step_label,
        "start_sec": req.start_sec,
        "end_sec": req.end_sec,
        "note": req.note,
    })
    save_session(session)
    return {"ok": True, "marks": session["marks"]}


@app.get("/api/sessions/{session_id}")
def api_get_session(session_id: str):
    return load_session(session_id)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
