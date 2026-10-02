import os
import tempfile

import cv2
import numpy as np

KEY_VARS = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "DASHSCOPE_API_KEY"]


def force_mock_env():
    """Clear provider keys so every call takes the mock path (and stays offline)."""
    for k in KEY_VARS:
        os.environ.pop(k, None)


def make_video(path: str, seconds: int = 4, fps: int = 10, size=(160, 120)):
    w, h = size
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(seconds * fps):
        frame = np.full((h, w, 3), (i * 3) % 255, dtype=np.uint8)
        cv2.putText(frame, str(i), (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        writer.write(frame)
    writer.release()


def make_session_dir():
    tmp = tempfile.TemporaryDirectory()
    return tmp
