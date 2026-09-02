import base64
import os
from typing import Dict, List, Tuple

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
