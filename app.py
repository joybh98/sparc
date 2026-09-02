import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from frames import extract_frames_b64, extract_frames_timed, video_duration
from providers import available_models, query_model, query_steps

load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")
STATIC_DIR = os.path.join(BASE_DIR, "static")

ALLOWED_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
MAX_VIDEO_BYTES = 500 * 1024 * 1024

os.makedirs(SESSIONS_DIR, exist_ok=True)

app = FastAPI(title="SPARC Frontier-Model Eval — Barebones")


class QueryRequest(BaseModel):
    session_id: str
    mode: str = "chat"
    question: str = ""
    step_hint: Optional[str] = None
    models: List[str] = ["gpt"]
    conversation_history: List[Dict] = []
    n_frames: int = 8


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
    return {"models": available_models()}


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


@app.post("/api/query")
def api_query(req: QueryRequest):
    """Single endpoint: given an uploaded clip, either answer a question about it
    (mode=chat) or return a structured surgical-step timeline (mode=steps)."""
    video_path = session_video_path(req.session_id)
    if not os.path.exists(video_path):
        return {"error": "no video uploaded for this session"}

    session = load_session(req.session_id)

    if req.mode == "steps":
        frames_timed = extract_frames_timed(video_path, req.n_frames)
        result = query_steps(req.models[0], frames_timed, req.step_hint,
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
    for model_key in req.models:
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
