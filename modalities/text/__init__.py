"""Text modality: free-form chat. The fallback when a session has no video."""
import os
from typing import Dict, List

from core.registry import Modality
from core.tools import ToolContext
from modalities.text import tools  # noqa: F401  (registers the text tools)
from modalities.text.mock import chat_script

MEMORY_TURNS = 8


def _memory(session: Dict) -> List[Dict]:
    out = []
    for turn in session.get("turns", []):
        if turn.get("answer") and not turn.get("error"):
            out.append({"role": "user", "text": turn.get("question", "")})
            out.append({"role": "assistant", "text": turn["answer"], "tool_calls": []})
    return out[-2 * MEMORY_TURNS:]


def initial_messages(agent, ctx: ToolContext, params: Dict) -> List[Dict]:
    return _memory(ctx.session) + [
        {"role": "user", "text": params.get("question", ""), "images": []}]


MODALITY = Modality(
    name="text",
    label="Text",
    description="Free-form chat with the model (no video loaded).",
    path=os.path.dirname(os.path.abspath(__file__)),
    detect=lambda session: True,        # fallback; video (priority 10) is checked first
    initial_messages=initial_messages,
    mock_scripts={"chat": chat_script},
    priority=0,
    ui={"panel": "text"},
)
