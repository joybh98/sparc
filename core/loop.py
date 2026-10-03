"""The agent loop: call the model, run the tools it asks for, feed results back, repeat.

Every step is recorded as a trace event. The trace is JSON-safe and image-free (tool
images are stored as small refs), so it can be saved straight into the session file.

Event types: model_call, tool_call, tool_result, nudge, error, final.
"""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from core.llm import LLMReply, call_llm
from core.registry import AgentConfig, Modality
from core.tools import ToolContext, ToolResult, get_tools, run_tool

MAX_TEXT = 2000
MAX_DATA = 4000
STEPS_NUDGE = "You have not finished yet. Call the {tool} tool to deliver your result."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[:limit] + f"… [{len(text) - limit} more chars]"


def _clip_data(data: Optional[Dict]) -> Optional[Dict]:
    if data is None:
        return None
    raw = json.dumps(data, default=str)
    return data if len(raw) <= MAX_DATA else {"_truncated": raw[:MAX_DATA]}


@dataclass
class RunResult:
    output: Dict = field(default_factory=dict)
    stop_reason: str = ""
    error: Optional[str] = None
    trace: List[Dict] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    iterations: int = 0


def _default_mock(agent: AgentConfig, messages: List[Dict], tools: List[Dict]) -> LLMReply:
    question = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")
    return LLMReply(text=f"[MOCK] No mock script for agent '{agent.name}'. "
                         f"Received: {_clip(question, 200)}")


def run_agent(agent: AgentConfig, modality: Modality, model_key: str, ctx: ToolContext,
              params: Dict, llm: Callable = call_llm,
              on_event: Optional[Callable[[Dict], None]] = None) -> RunResult:
    """on_event, if given, is called with each trace event as it is recorded (used to
    stream a live trace to the UI)."""
    started = time.time()
    registry = get_tools(modality.name)
    specs = [registry[n] for n in agent.tools]
    schemas = [s.schema() for s in specs]
    messages = modality.initial_messages(agent, ctx, params)
    messages[-1]["run_start"] = True    # lets stateless mock scripts tell memory from this run
    mock_script = modality.mock_scripts.get(agent.mock, _default_mock)

    res = RunResult()
    seq = 0

    def emit(type_: str, **fields):
        nonlocal seq
        seq += 1
        event = {"seq": seq, "type": type_, "timestamp": _now(), **fields}
        res.trace.append(event)
        if on_event:
            on_event(event)

    last_text = ""
    nudged = False
    stop_tool = agent.stop_when[5:] if agent.stop_when.startswith("tool:") else None

    for iteration in range(1, agent.max_iterations + 1):
        res.iterations = iteration
        t0 = time.time()
        try:
            reply, info = llm(model_key, agent.system_prompt, messages, schemas,
                              mock=lambda: mock_script(agent, messages, schemas))
        except Exception as e:
            emit("error", iteration=iteration, stage="model_call", message=str(e))
            res.stop_reason, res.error = "error", str(e)
            break

        res.model, res.provider = info["model"], info["provider"]
        last_text = reply.text or last_text
        emit("model_call", iteration=iteration, model=info["model"], provider=info["provider"],
             latency_ms=int((time.time() - t0) * 1000), text=_clip(reply.text),
             n_tool_calls=len(reply.tool_calls), usage=reply.usage)
        messages.append({"role": "assistant", "text": reply.text,
                         "tool_calls": [{"id": c.id, "name": c.name, "args": c.args}
                                        for c in reply.tool_calls]})

        if not reply.tool_calls:
            if stop_tool and not nudged:
                nudged = True
                text = STEPS_NUDGE.format(tool=stop_tool)
                emit("nudge", iteration=iteration, text=text)
                messages.append({"role": "user", "text": text})
                continue
            res.stop_reason = "no_tool_calls"
            res.output = {"text": reply.text}
            break

        results, ended = [], None
        for call in reply.tool_calls:
            emit("tool_call", iteration=iteration, id=call.id, name=call.name, args=call.args)
            spec = registry.get(call.name)
            t1 = time.time()
            if spec is None or call.name not in agent.tools:
                result = ToolResult(text=f"Unknown tool '{call.name}'. "
                                         f"Available: {', '.join(agent.tools)}", is_error=True)
            else:
                result = run_tool(spec, ctx, call.args)
            emit("tool_result", iteration=iteration, id=call.id, name=call.name,
                 latency_ms=int((time.time() - t1) * 1000), text=_clip(result.text),
                 data=_clip_data(result.data), images=[i.ref for i in result.images],
                 ui=result.ui, is_error=result.is_error)
            results.append({"id": call.id, "name": call.name, "text": result.text,
                            "images": [i.b64 for i in result.images]})
            if not result.is_error and ended is None and (
                    result.end_turn or call.name == stop_tool):
                ended = (call.name, result)

        messages.append({"role": "tool", "results": results})
        if ended:
            name, result = ended
            res.stop_reason = f"tool:{name}"
            res.output = dict(result.data or {})
            if result.text and "text" not in res.output:
                res.output["text"] = result.text
            break
    else:
        res.stop_reason = "max_iterations"
        res.output = {"text": last_text}
        res.error = f"agent hit max_iterations ({agent.max_iterations}) without finishing"

    if not res.error:
        missing = [k for k in agent.required_output if k not in res.output]
        if missing:
            res.error = f"agent finished without required output: {', '.join(missing)}"

    res.latency_ms = int((time.time() - started) * 1000)
    emit("final", iterations=res.iterations, stop_reason=res.stop_reason,
         latency_ms=res.latency_ms, error=res.error, output_keys=sorted(res.output))
    return res
