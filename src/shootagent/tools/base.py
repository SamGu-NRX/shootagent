"""The agent's tool contract.

The agent never touches pixels or writes code. It calls tools from this registry; each tool validates
its input with a pydantic model, reads one image version, and either returns information (perception
tools) or writes a new version (edit tools). Pixels outside an edit's mask must stay byte-identical,
which `imaging.composite` enforces and the self-check measures.

Register a tool with::

    class ExposureIn(ToolInput):
        view: str = Field(description="View id")
        ev: float = Field(ge=-3, le=3, description="Stops to add")

    @tool("adjust_exposure", "Brighten or darken a photo or a masked region.", kind="edit")
    def adjust_exposure(ctx: ToolContext, args: ExposureIn) -> ToolResult: ...
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ..config import Settings
from ..store import ShootStore
from ..trace import TraceRun

ToolKind = Literal["perceive", "edit", "check", "shoot"]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class ToolResult:
    summary: str  # one or two plain sentences; shown in the trace and sent to the model
    data: dict[str, Any] = field(default_factory=dict)  # structured values sent to the model as JSON
    images: list[str] = field(default_factory=list)  # blob names to show the model (and the UI)
    version: str | None = None  # new version id, for edit tools
    mask: str | None = None  # new mask id, for mask tools
    ok: bool = True


@dataclass
class ToolContext:
    store: ShootStore
    shoot_id: str
    trace: TraceRun
    settings: Settings
    call_id: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    kind: ToolKind
    input_model: type[ToolInput]
    handler: Callable[[ToolContext, Any], ToolResult]

    def anthropic_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, kind: ToolKind = "edit"):
    def deco(fn: Callable[[ToolContext, Any], ToolResult]):
        input_model = fn.__annotations__.get("args")
        if isinstance(input_model, str):  # postponed annotations
            input_model = fn.__globals__[input_model]
        if not (isinstance(input_model, type) and issubclass(input_model, ToolInput)):
            raise TypeError(f"tool {name}: annotate the second parameter as `args: <ToolInput subclass>`")
        if name in REGISTRY:
            raise ValueError(f"tool {name!r} registered twice")
        REGISTRY[name] = ToolSpec(name, description, kind, input_model, fn)
        return fn

    return deco


def run_tool(ctx: ToolContext, name: str, raw_input: dict[str, Any], view: str | None = None) -> ToolResult:
    """Validate, execute and trace one tool call. Errors become a failed ToolResult the model can read."""
    spec = REGISTRY.get(name)
    ctx.trace.emit("tool_call", view=view or raw_input.get("view"), call_id=ctx.call_id, tool=name, input=raw_input)
    started = time.perf_counter()
    try:
        if spec is None:
            raise KeyError(f"unknown tool {name!r}; available: {', '.join(sorted(REGISTRY))}")
        args = spec.input_model.model_validate(raw_input)
        result = spec.handler(ctx, args)
    except ValidationError as e:
        result = ToolResult(summary=f"Invalid input for {name}: {e.errors(include_url=False)}", ok=False)
    except Exception as e:  # surfaced to the model so it can choose another approach
        result = ToolResult(
            summary=f"{name} failed: {type(e).__name__}: {e}",
            data={"traceback": traceback.format_exc(limit=4)},
            ok=False,
        )
    ms = int((time.perf_counter() - started) * 1000)
    ctx.trace.emit(
        "tool_result",
        view=view or raw_input.get("view"),
        call_id=ctx.call_id,
        tool=name,
        ok=result.ok,
        summary=result.summary,
        version=result.version,
        mask=result.mask,
        images=result.images,
        data={k: v for k, v in result.data.items() if k != "traceback"},
        ms=ms,
    )
    return result
