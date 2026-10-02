"""Scripted mock for the text modality: checks history (and does arithmetic if asked),
then answers. Stateless: the step is read off the message history."""
import re
from typing import Dict, List

from core.llm import LLMReply, ToolCall, mock_step

_ARITH = re.compile(r"\d[\d\s.()]*(?:[-+*/%][\d\s.()*/+-]*\d)+")


def chat_script(agent, messages: List[Dict], tools) -> LLMReply:
    names = {t["name"] for t in tools}
    question = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")
    plan = []
    if "search_history" in names:
        plan.append(("search_history", "[MOCK] Checking earlier messages first.",
                     {"query": question}))
    arith = _ARITH.search(question)
    if "calculate" in names and arith:
        plan.append(("calculate", "[MOCK] That needs a calculation.",
                     {"expression": arith.group(0).strip()}))

    step = mock_step(messages)
    if step < len(plan):
        name, text, args = plan[step]
        return LLMReply(text=text, tool_calls=[ToolCall(f"mock_{step}", name, args)])

    results = [r["text"] for m in messages if m["role"] == "tool" for r in m["results"]]
    calc = next((t for t in results if " = " in t), None)
    if calc:
        return LLMReply(text=f"[MOCK] {calc}")
    return LLMReply(text=f"[MOCK] On '{question}': no video is loaded yet, so this is a "
                         f"general answer. Upload a clip and I can look at the footage.")
