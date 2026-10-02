import base64
import os
from typing import Dict, List, Optional, Tuple

import cv2


def _sample(video_path: str, n_frames: int) -> List[Tuple[int, float, str]]:
    if not os.path.exists(video_path):
        return []

    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if total <= 0:
        cap.release()
        return []

    n_frames = max(1, min(n_frames, total))
    indices = [int(round(i * (total - 1) / max(1, n_frames - 1))) for i in range(n_frames)]

    out: List[Tuple[int, float, str]] = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            continue
        time_sec = idx / fps if fps > 0 else 0.0
        out.append((idx, round(time_sec, 2), base64.b64encode(buf.tobytes()).decode("utf-8")))

    cap.release()
    return out


def extract_frames_b64(video_path: str, n_frames: int = 8) -> List[str]:
    return [b64 for _, _, b64 in _sample(video_path, n_frames)]


def extract_frames_timed(video_path: str, n_frames: int = 8) -> List[Dict]:
    return [{"time_sec": t, "b64": b64} for _, t, b64 in _sample(video_path, n_frames)]


def video_duration(video_path: str) -> float:
    if not os.path.exists(video_path):
        return 0.0
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    cap.release()
    return round(total / fps, 2) if fps > 0 and total > 0 else 0.0


def _encode(frame, max_side: int = 0):
    if max_side:
        h, w = frame.shape[:2]
        scale = max_side / max(h, w)
        if scale < 1:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return base64.b64encode(buf.tobytes()).decode("utf-8") if ok else None


def _frame_at(cap, fps: float, total: int, time_sec: float, zoom: float, cx: float,
              cy: float, max_side: int) -> Optional[Dict]:
    idx = min(max(int(round(time_sec * fps)), 0), total - 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    if not ok:
        return None
    if zoom > 1.0:
        h, w = frame.shape[:2]
        cw, ch = w / zoom, h / zoom
        x0 = int(min(max(cx * w - cw / 2, 0), w - cw))
        y0 = int(min(max(cy * h - ch / 2, 0), h - ch))
        frame = frame[y0:y0 + int(ch), x0:x0 + int(cw)]
    b64 = _encode(frame, max_side)
    return {"time_sec": round(idx / fps, 2), "b64": b64} if b64 else None


def video_info(video_path: str) -> Dict:
    if not os.path.exists(video_path):
        return {}
    cap = cv2.VideoCapture(video_path)
    info = {
        "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        "fps": round(cap.get(cv2.CAP_PROP_FPS) or 0.0, 2),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    cap.release()
    info["duration_sec"] = (round(info["frame_count"] / info["fps"], 2)
                            if info["fps"] > 0 and info["frame_count"] > 0 else 0.0)
    return info


def extract_frame_at(video_path: str, time_sec: float, zoom: float = 1.0, cx: float = 0.5,
                     cy: float = 0.5, max_side: int = 1024) -> Optional[Dict]:
    """One frame at time_sec, optionally center-cropped by zoom around (cx, cy) in [0, 1]."""
    if not os.path.exists(video_path):
        return None
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    try:
        if total <= 0 or fps <= 0:
            return None
        return _frame_at(cap, fps, total, time_sec, zoom, cx, cy, max_side)
    finally:
        cap.release()


def extract_frames_range(video_path: str, start_sec: float, end_sec: float, n: int,
                         max_side: int = 1024) -> List[Dict]:
    """n frames evenly spaced over [start_sec, end_sec], each tagged with its timestamp."""
    if not os.path.exists(video_path):
        return []
    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    try:
        if total <= 0 or fps <= 0:
            return []
        n = max(1, n)
        times = ([start_sec] if n == 1 else
                 [start_sec + i * (end_sec - start_sec) / (n - 1) for i in range(n)])
        frames = [_frame_at(cap, fps, total, t, 1.0, 0.5, 0.5, max_side) for t in times]
        return [f for f in frames if f]
    finally:
        cap.release()
