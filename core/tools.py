"""Tool contract and registry.

A tool is a plain function registered under a modality:

    @tool("video", "zoom_frame", "Zoom into a frame.", {...json schema...})
    def zoom_frame(ctx, time_sec, zoom=2.0): ...
        return ToolResult(text="...", images=[Image(b64, ref)])

Agent configs may only reference registered tools, so a config file can never run
arbitrary code.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolContext:
    session_id: str
    session: Dict
    session_dir: str
    # scratch space shared by the tools and modality hooks within one agent run
    state: Dict = field(default_factory=dict)


@dataclass
class Image:
    b64: str
    # small JSON-safe descriptor stored in the trace instead of the bytes, e.g.
    # {"time_sec": 3.2, "zoom": 2.0}; the UI re-renders it on demand
    ref: Dict = field(default_factory=dict)


@dataclass
class ToolResult:
    text: str = ""
    images: List[Image] = field(default_factory=list)
    data: Optional[Dict] = None      # structured payload; becomes the run output on a stop
    ui: Optional[Dict] = None        # rendering hint for the trace panel
    is_error: bool = False
    end_turn: bool = False           # stop the loop after this tool batch


@dataclass
class ToolSpec:
    name: str
    modality: str
    description: str
    parameters: Dict
    fn: Callable[..., ToolResult]

    def schema(self) -> Dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


_TOOLS: Dict[str, Dict[str, ToolSpec]] = {}


def tool(modality: str, name: str, description: str, parameters: Optional[Dict] = None):
    def decorator(fn):
        registry = _TOOLS.setdefault(modality, {})
        if name in registry:
            raise ValueError(f"tool '{name}' already registered for modality '{modality}'")
        registry[name] = ToolSpec(
            name=name, modality=modality, description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            fn=fn)
        return fn
    return decorator


def get_tools(modality: str) -> Dict[str, ToolSpec]:
    return _TOOLS.get(modality, {})


_JSON_TYPES = {
    "string": (str,), "boolean": (bool,), "array": (list,), "object": (dict,),
    "integer": (int,), "number": (int, float),
}


def check_args(schema: Dict, args: Any) -> Optional[str]:
    """Light JSON-schema check (required, unknown keys, simple types). Returns an error
    message the model can act on, or None."""
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    props = schema.get("properties", {})
    missing = [k for k in schema.get("required", []) if k not in args]
    if missing:
        return f"missing required argument(s): {', '.join(missing)}"
    unknown = [k for k in args if k not in props]
    if unknown:
        return f"unknown argument(s): {', '.join(unknown)}"
    for key, value in args.items():
        expected = _JSON_TYPES.get(props[key].get("type"))
        if expected is None:
            continue
        if isinstance(value, bool) and props[key]["type"] in ("integer", "number"):
            return f"argument '{key}' must be {props[key]['type']}"
        if not isinstance(value, expected):
            return f"argument '{key}' must be {props[key]['type']}"
    return None


def run_tool(spec: ToolSpec, ctx: ToolContext, args: Dict) -> ToolResult:
    """Run a tool; failures come back as is_error results so the model can recover."""
    err = check_args(spec.parameters, args)
    if err:
        return ToolResult(text=f"Invalid call to {spec.name}: {err}", is_error=True)
    try:
        result = spec.fn(ctx, **args)
    except Exception as e:  # tool bugs shouldn't kill the whole run
        return ToolResult(text=f"{spec.name} failed: {e}", is_error=True)
    return result if isinstance(result, ToolResult) else ToolResult(text=str(result))
