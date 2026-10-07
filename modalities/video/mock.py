"""Scripted mock model for the video modality: a short, believable tool-using run, so
the whole loop works without API keys. Stateless: the step is read off the history."""
import re
from typing import Dict, List

from core.llm import LLMReply, ToolCall, mock_step


def _duration(messages: List[Dict]) -> float:
    for m in reversed(messages):
        if m["role"] == "tool":
            for r in m["results"]:
                found = re.search(r"Duration: ([0-9.]+)", r["text"])
                if found:
                    return float(found.group(1))
    return 30.0


def question_text(messages: List[Dict]) -> str:
    return next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")


def _run_plan(plan, tools, messages, final):
    names = {t["name"] for t in tools}
    plan = [(n, text, args) for n, text, args in plan if n in names]
    step = mock_step(messages)
    if step < len(plan):
        name, text, args = plan[step]
        return LLMReply(text=text, tool_calls=[ToolCall(f"mock_{step}", name, args)])
    return final(names)


def steps_script(agent, messages, tools) -> LLMReply:
    dur = _duration(messages)
    mid = round(dur * 0.45, 2)
    plan = [
        ("get_clip_info", "[MOCK] Checking the clip details first.", {}),
        ("sample_frames", "[MOCK] Sampling the early portion more densely.",
         {"start_sec": 0, "end_sec": mid, "n": 4}),
        ("zoom_frame", "[MOCK] Zooming in on the middle of the clip.",
         {"time_sec": mid, "zoom": 2.0}),
    ]

    def final(names):
        if "submit_steps" not in names:
            return LLMReply(text="[MOCK] Done examining the clip.")
        return LLMReply(
            text="[MOCK] I have enough to segment the clip.",
            tool_calls=[ToolCall("mock_submit", "submit_steps", {
                "steps": [
                    {"step_label": "Capsulorhexis", "start_sec": 0.0, "end_sec": mid,
                     "comment": "[MOCK] Tear looks continuous and roughly centered.",
                     "confidence": 0.7,
                     "uncertainty": "[MOCK] Sparse frames; tear completion is not directly visible.",
                     "evidence": [{"time_sec": 0.0, "note": "[MOCK] Cystotome at the anterior capsule."},
                                  {"time_sec": mid, "note": "[MOCK] Circular edge of the tear."}]},
                    {"step_label": "Phacoemulsification", "start_sec": mid, "end_sec": dur,
                     "comment": "[MOCK] Nucleus disassembly stays central.",
                     "confidence": 0.5,
                     "uncertainty": "[MOCK] The boundary with the previous step may be off by seconds.",
                     "evidence": [{"time_sec": dur, "note": "[MOCK] Phaco tip engaged with the nucleus."}]}],
                "assessment": "[MOCK] Two-step read from sampled frames; timings are rough."})])

    return _run_plan(plan, tools, messages, final)


def followup_script(agent, messages, tools) -> LLMReply:
    question = question_text(messages).split("\n\n")[0]
    q = question.lower()
    dur = _duration(messages)
    plan = [("get_prior_analysis", "[MOCK] Let me re-read the earlier analysis.", {})]
    if any(w in q for w in ("mark", "disagree", "compare")):
        plan.append(("get_reviewer_marks", "[MOCK] Checking your marked steps.", {}))
    if any(w in q for w in ("closer", "look", "incision", "zoom")):
        plan.append(("zoom_frame", "[MOCK] Taking a closer look at the frame.",
                     {"time_sec": dur / 2, "zoom": 2.5}))

    def final(names):
        seen = [r["text"].splitlines()[0] for m in messages if m["role"] == "tool"
                for r in m["results"]]
        at = re.search(r"player is at ([0-9.]+)s", question_text(messages))
        prior = next((r["text"] for m in messages if m["role"] == "tool"
                      for r in m["results"] if "Step timeline" in r["text"]), "")
        found = re.search(r"\u2013([0-9.]+)s", prior)
        moment = float(at.group(1)) if at else float(found.group(1)) if found else 0.0
        text = (f"[MOCK] On '{question}': based on {len(seen)} tool result(s), my earlier "
                f"read still holds around {moment:.1f}s. Confidence: medium.")
        if "submit_answer" not in names:
            return LLMReply(text=text)
        return LLMReply(text="[MOCK] I can answer now.", tool_calls=[ToolCall(
            "mock_answer", "submit_answer", {
                "answer": text, "confidence": 0.6,
                "uncertainty": "[MOCK] Sparse frames; the exact boundary is not visible.",
                "evidence": [{"time_sec": moment, "note": "[MOCK] The frame this answer rests on."}]})])

    return _run_plan(plan, tools, messages, final)
