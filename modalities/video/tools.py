"""Video tools. Importing this module registers them under the 'video' modality."""
import os
from typing import Dict, List

from core.tools import Image, ToolContext, ToolResult, tool
from frames import extract_frame_at, extract_frames_range, video_info

MAX_SAMPLE_FRAMES = 12
MAX_ZOOM = 4.0


def video_path(ctx: ToolContext) -> str:
    return os.path.join(ctx.session_dir, "video.mp4")


def _duration(ctx: ToolContext) -> float:
    return video_info(video_path(ctx)).get("duration_sec", 0.0)


def _fmt_steps(steps: List[Dict]) -> str:
    return "\n".join(f"- {s.get('step_label', '?')}: {s.get('start_sec')}s–{s.get('end_sec')}s"
                     f" — {s.get('comment', '')}" for s in steps)


@tool("video", "get_clip_info", "Get the clip's duration, frame rate and resolution.")
def get_clip_info(ctx):
    info = video_info(video_path(ctx))
    if not info:
        return ToolResult(text="No video found for this session.", is_error=True)
    return ToolResult(
        text=(f"Duration: {info['duration_sec']} s\nFPS: {info['fps']}\n"
              f"Resolution: {info['width']}x{info['height']}\nFrames: {info['frame_count']}"),
        data=info)


@tool("video", "sample_frames",
      "Fetch evenly spaced frames from a time range of the clip, each tagged with its "
      "timestamp. Use it to inspect a specific part of the surgery more densely.",
      {"type": "object", "required": ["start_sec", "end_sec"], "properties": {
          "start_sec": {"type": "number", "description": "Range start in seconds."},
          "end_sec": {"type": "number", "description": "Range end in seconds."},
          "n": {"type": "integer",
                "description": f"Number of frames (1-{MAX_SAMPLE_FRAMES}, default 6)."}}})
def sample_frames(ctx, start_sec, end_sec, n=6):
    duration = _duration(ctx)
    start, end = max(0.0, start_sec), min(end_sec, duration)
    if end < start:
        return ToolResult(text=f"Empty range: clip is {duration}s long.", is_error=True)
    n = max(1, min(n, MAX_SAMPLE_FRAMES))
    frames = extract_frames_range(video_path(ctx), start, end, n)
    if not frames:
        return ToolResult(text="Could not read frames for that range.", is_error=True)
    stamps = ", ".join(f"{f['time_sec']}s" for f in frames)
    return ToolResult(
        text=f"{len(frames)} frames from {start}s to {end}s, in order, at: {stamps}.",
        images=[Image(f["b64"], {"time_sec": f["time_sec"]}) for f in frames],
        data={"times": [f["time_sec"] for f in frames]},
        ui={"seek_to": frames[0]["time_sec"]})


@tool("video", "zoom_frame",
      "Fetch one frame at a given time, optionally magnified around a point, to inspect "
      "fine detail such as instruments or the capsule edge.",
      {"type": "object", "required": ["time_sec"], "properties": {
          "time_sec": {"type": "number", "description": "Time of the frame in seconds."},
          "zoom": {"type": "number", "description": f"Magnification 1-{MAX_ZOOM:g} (default 2)."},
          "center_x": {"type": "number", "description": "Zoom center, 0 (left) to 1 (right)."},
          "center_y": {"type": "number", "description": "Zoom center, 0 (top) to 1 (bottom)."}}})
def zoom_frame(ctx, time_sec, zoom=2.0, center_x=0.5, center_y=0.5):
    zoom = max(1.0, min(zoom, MAX_ZOOM))
    cx, cy = min(max(center_x, 0.0), 1.0), min(max(center_y, 0.0), 1.0)
    frame = extract_frame_at(video_path(ctx), time_sec, zoom, cx, cy)
    if not frame:
        return ToolResult(text=f"Could not read a frame at {time_sec}s.", is_error=True)
    return ToolResult(
        text=f"Frame at {frame['time_sec']}s, zoom {zoom:g}x around ({cx:.2f}, {cy:.2f}).",
        images=[Image(frame["b64"], {"time_sec": frame["time_sec"], "zoom": zoom,
                                     "cx": cx, "cy": cy})],
        ui={"seek_to": frame["time_sec"]})


@tool("video", "submit_steps",
      "Deliver the final surgical step timeline. Call this once you are done examining "
      "the clip. Steps must be chronological and lie within the clip duration.",
      {"type": "object", "required": ["steps", "assessment"], "properties": {
          "steps": {"type": "array", "description": "Ordered steps. Besides the label, "
                    "times and comment, each carries your calibrated confidence, what is "
                    "uncertain, and the frames you relied on.",
                    "items": {"type": "object", "properties": {
                        "step_label": {"type": "string"}, "start_sec": {"type": "number"},
                        "end_sec": {"type": "number"}, "comment": {"type": "string"},
                        "confidence": {"type": "number", "description":
                                       "Probability 0-1 that this label and time range are right."},
                        "uncertainty": {"type": "string", "description":
                                        "What is unclear, or what would resolve it. Empty if nothing."},
                        "evidence": {"type": "array", "description":
                                     "Frames that support this step.", "items": {
                                         "type": "object", "required": ["time_sec", "note"],
                                         "properties": {"time_sec": {"type": "number"},
                                                        "note": {"type": "string", "description":
                                                                 "What is visible there."}}}}},
                        "required": ["step_label", "start_sec", "end_sec", "confidence",
                                     "evidence"]}},
          "assessment": {"type": "string", "description": "Brief overall read of the clip."}}})
def submit_steps(ctx, steps, assessment):
    duration = _duration(ctx)
    problems, prev_start = [], -1.0
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            problems.append(f"step {i} is not an object")
            continue
        label, a, b = s.get("step_label"), s.get("start_sec"), s.get("end_sec")
        if not isinstance(label, str) or not label.strip():
            problems.append(f"step {i} needs a step_label")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)):
            problems.append(f"step {i} needs numeric start_sec and end_sec")
            continue
        if not 0 <= a <= b <= duration + 0.01:
            problems.append(f"step {i} ({a}-{b}s) must satisfy 0 <= start <= end <= {duration}")
        if a < prev_start:
            problems.append(f"step {i} starts before the previous step")
        prev_start = a
        conf = s.get("confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not 0 <= conf <= 1:
            problems.append(f"step {i} needs a confidence between 0 and 1")
        ev = s.get("evidence")
        if not isinstance(ev, list):
            problems.append(f"step {i} needs an evidence list (may be empty)")
        else:
            for e in ev:
                t = e.get("time_sec") if isinstance(e, dict) else None
                if not isinstance(t, (int, float)) or isinstance(t, bool) \
                        or not 0 <= t <= duration + 0.01:
                    problems.append(f"step {i} evidence needs time_sec within [0, {duration}]")
                    break
    if not steps:
        problems.append("steps is empty")
    if problems:
        return ToolResult(text="Rejected: " + "; ".join(problems) + ". Fix and call again.",
                          is_error=True)
    steps = [{**s, "uncertainty": str(s.get("uncertainty") or ""),
              "evidence": [{"time_sec": e["time_sec"], "note": str(e.get("note") or "")}
                           for e in s["evidence"]]} for s in steps]
    return ToolResult(text=f"Submitted {len(steps)} steps.",
                      data={"steps": steps, "assessment": assessment},
                      ui={"steps": steps})


@tool("video", "get_prior_analysis",
      "Read the most recent step timeline and overall assessment produced for this clip.")
def get_prior_analysis(ctx):
    for turn in reversed(ctx.session.get("turns", [])):
        if turn.get("type") == "steps" and not turn.get("error") and turn.get("model_steps"):
            return ToolResult(
                text=(f"Step timeline (turn {turn['turn_index']}, model {turn.get('model')}):\n"
                      f"{_fmt_steps(turn['model_steps'])}\nAssessment: {turn.get('assessment', '')}"),
                data={"steps": turn["model_steps"], "assessment": turn.get("assessment", "")})
    return ToolResult(text="No step analysis has been run for this clip yet.")


@tool("video", "get_reviewer_marks",
      "Read the step segments the human reviewer marked on this clip.")
def get_reviewer_marks(ctx):
    marks = ctx.session.get("marks", [])
    if not marks:
        return ToolResult(text="The reviewer has not marked any steps.")
    lines = [f"- {m['step_label']}: {m['start_sec']}s–{m['end_sec']}s"
             + (f" — {m['note']}" if m.get("note") else "") for m in marks]
    return ToolResult(text="Reviewer marks:\n" + "\n".join(lines), data={"marks": marks})


@tool("video", "ask_user",
      "Ask the reviewer one clarifying question instead of guessing. Ends your turn.",
      {"type": "object", "required": ["question"],
       "properties": {"question": {"type": "string"}}})
def ask_user(ctx, question):
    return ToolResult(text=question, data={"text": question, "needs_reply": True},
                      end_turn=True)
