"""Modality registry, agent configs, and auto-discovery.

A modality is a package under modalities/ that exposes a module-level MODALITY. Agents
are JSON files in <modality>/agents/. Everything is validated on load so a bad config
produces a readable error instead of failing mid-run.
"""
import importlib
import json
import os
import pkgutil
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.tools import get_tools

HARD_MAX_ITERATIONS = 20
HARD_MAX_INITIAL_FRAMES = 32


@dataclass
class Modality:
    name: str
    label: str
    description: str
    path: str                                       # package dir; agents/ lives under it
    detect: Callable[[Dict], bool]                  # does this session belong here?
    initial_messages: Callable[..., List[Dict]]     # (agent, ctx, params) -> messages
    mock_scripts: Dict[str, Callable] = field(default_factory=dict)
    priority: int = 0                               # higher is checked first in routing
    ui: Dict = field(default_factory=dict)          # hints for the frontend (panel name, ...)


@dataclass
class AgentConfig:
    name: str
    modality: str
    system_prompt: str
    tools: List[str]
    description: str = ""
    kind: str = "followup"          # "steps" fills model_steps/assessment on the turn
    model: Optional[str] = None     # default model key; a request may override it
    max_iterations: int = 6
    stop_when: str = "no_tool_calls"    # or "tool:<name>"
    initial_frames: int = 0
    required_output: List[str] = field(default_factory=list)
    mock: str = ""                  # name of a mock script in the modality

    def to_dict(self) -> Dict:
        return asdict(self)


_MODALITIES: Dict[str, Modality] = {}
DISCOVERY_ERRORS: List[Dict] = []


def register_modality(m: Modality):
    _MODALITIES[m.name] = m


def get_modality(name: str) -> Optional[Modality]:
    return _MODALITIES.get(name)


def all_modalities() -> List[Modality]:
    return sorted(_MODALITIES.values(), key=lambda m: -m.priority)


def discover():
    """Import every package under modalities/. A broken pack is recorded in
    DISCOVERY_ERRORS and skipped; it must not take the other modalities down."""
    import modalities

    DISCOVERY_ERRORS.clear()
    for info in pkgutil.iter_modules(modalities.__path__):
        if not info.ispkg:
            continue
        try:
            mod = importlib.import_module(f"modalities.{info.name}")
            register_modality(mod.MODALITY)
        except Exception as e:
            DISCOVERY_ERRORS.append({"modality": info.name, "error": f"{type(e).__name__}: {e}"})


def resolve_modality(session: Dict) -> Optional[Modality]:
    for m in all_modalities():
        if m.detect(session):
            return m
    return None


_FIELDS = {
    "name", "modality", "description", "system_prompt", "tools", "kind", "model",
    "max_iterations", "stop_when", "initial_frames", "required_output", "mock",
}


def validate_agent(raw: Dict, modality: Modality) -> Tuple[Optional[AgentConfig], List[str]]:
    errors: List[str] = []
    if not isinstance(raw, dict):
        return None, ["agent config must be a JSON object"]

    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        errors.append(f"unknown field(s): {', '.join(unknown)}")

    for key in ("name", "system_prompt"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            errors.append(f"'{key}' is required and must be a non-empty string")

    if raw.get("modality", modality.name) != modality.name:
        errors.append(f"'modality' is '{raw['modality']}' but this agent is in '{modality.name}'")

    tools = raw.get("tools")
    available = get_tools(modality.name)
    if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
        errors.append("'tools' must be a non-empty list of tool names")
        tools = []
    else:
        bad = [t for t in tools if t not in available]
        if bad:
            errors.append(f"unknown tool(s) for '{modality.name}': {', '.join(bad)} "
                          f"(available: {', '.join(sorted(available))})")

    max_it = raw.get("max_iterations", 6)
    if not isinstance(max_it, int) or isinstance(max_it, bool) or not 1 <= max_it <= HARD_MAX_ITERATIONS:
        errors.append(f"'max_iterations' must be an integer in 1..{HARD_MAX_ITERATIONS}")

    frames = raw.get("initial_frames", 0)
    if not isinstance(frames, int) or isinstance(frames, bool) or not 0 <= frames <= HARD_MAX_INITIAL_FRAMES:
        errors.append(f"'initial_frames' must be an integer in 0..{HARD_MAX_INITIAL_FRAMES}")

    stop = raw.get("stop_when", "no_tool_calls")
    if stop != "no_tool_calls":
        if not (isinstance(stop, str) and stop.startswith("tool:") and stop[5:] in tools):
            errors.append("'stop_when' must be 'no_tool_calls' or 'tool:<name>' with <name> "
                          "listed in 'tools'")

    required = raw.get("required_output", [])
    if not isinstance(required, list) or not all(isinstance(k, str) for k in required):
        errors.append("'required_output' must be a list of output keys")

    mock = raw.get("mock", "")
    if mock and mock not in modality.mock_scripts:
        errors.append(f"unknown mock script '{mock}' "
                      f"(available: {', '.join(sorted(modality.mock_scripts)) or 'none'})")

    if errors:
        return None, errors

    return AgentConfig(
        name=raw["name"].strip(), modality=modality.name,
        system_prompt=raw["system_prompt"], tools=tools,
        description=raw.get("description", ""), kind=raw.get("kind", "followup"),
        model=raw.get("model"), max_iterations=max_it, stop_when=stop,
        initial_frames=frames, required_output=required, mock=mock,
    ), []


def load_agents(modality: Modality) -> Tuple[Dict[str, AgentConfig], List[Dict]]:
    """Read agents from disk on every call, so dropping in a JSON file needs no restart."""
    agents: Dict[str, AgentConfig] = {}
    invalid: List[Dict] = []
    agents_dir = os.path.join(modality.path, "agents")
    if not os.path.isdir(agents_dir):
        return agents, invalid

    for fname in sorted(os.listdir(agents_dir)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(agents_dir, fname)) as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            invalid.append({"file": fname, "errors": [f"could not read JSON: {e}"]})
            continue
        agent, errors = validate_agent(raw, modality)
        if errors:
            invalid.append({"file": fname, "errors": errors})
        elif agent.name in agents:
            invalid.append({"file": fname, "errors": [f"duplicate agent name '{agent.name}'"]})
        else:
            agents[agent.name] = agent
    return agents, invalid
