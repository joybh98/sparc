"""Video modality: sessions with an uploaded clip. Analysis and follow-up questions run
as tool-using agent loops over sampled frames."""
import os
from typing import Dict, List

from core.registry import Modality
from core.tools import ToolContext
from frames import extract_frame_at, extract_frames_timed, video_info
from modalities.video import tools  # noqa: F401  (registers the video tools)
from modalities.video.mock import followup_script, steps_script
from modalities.video.tools import video_path

MEMORY_TURNS = 6
HARD_MAX_FRAMES = 32


def _frame_text(frames: List[Dict]) -> str:
    return "Frame timestamps (in order):\n" + "\n".join(
        f"  frame {i + 1}: t={f['time_sec']:.2f}s" for i, f in enumerate(frames))


def _memory(session: Dict) -> List[Dict]:
    """Prior Q&A as plain chat turns (including text-chat from before the clip was
    uploaded), so a follow-up has the whole thread."""
    out = []
    for turn in session.get("turns", []):
        if turn.get("type") in ("followup", "chat") and turn.get("answer") \
                and not turn.get("error"):
            out.append({"role": "user", "text": turn.get("question", "")})
            out.append({"role": "assistant", "text": turn["answer"], "tool_calls": []})
    return out[-2 * MEMORY_TURNS:]


def initial_messages(agent, ctx: ToolContext, params: Dict) -> List[Dict]:
    info = video_info(video_path(ctx))
    duration = info.get("duration_sec", 0.0)
    n_frames = 0
    if agent.initial_frames:
        n_frames = min(params.get("n_frames") or agent.initial_frames, HARD_MAX_FRAMES)
    frames = extract_frames_timed(video_path(ctx), n_frames) if n_frames else []
    ctx.state["n_frames_sent"] = len(frames)

    if agent.kind == "steps":
        text = f"Segment this clip into surgical steps. Total duration: {duration:.2f}s."
        if params.get("step_hint"):
            text += f"\nReviewer expects this clip to contain: {params['step_hint']}"
        messages: List[Dict] = []
    else:
        text = params.get("question", "")
        messages = _memory(ctx.session)
    images = [f["b64"] for f in frames]
    if frames:
        text += "\n\n" + _frame_text(frames)
    at = params.get("player_time_sec")
    if agent.kind != "steps" and at is not None:
        at = round(min(max(float(at), 0.0), duration), 2)
        frame = extract_frame_at(video_path(ctx), at)
        if frame:
            ctx.state["player_time_sec"] = at
            images.append(frame["b64"])
            text += (f"\n\nThe reviewer's video player is at {at:.2f}s and the question is "
                     f"about that moment. The frame at {at:.2f}s is attached.")
    messages.append({"role": "user", "text": text, "images": images})
    return messages


MODALITY = Modality(
    name="video",
    label="Video",
    description="Surgical video clips: step segmentation and follow-up questions.",
    path=os.path.dirname(os.path.abspath(__file__)),
    detect=lambda session: bool(session.get("video")),
    initial_messages=initial_messages,
    mock_scripts={"steps": steps_script, "followup": followup_script},
    priority=10,
    ui={"panel": "video"},
)
