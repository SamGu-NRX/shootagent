"""Structured Claude calls for the creative lab: one-shot asks and agents with their own memory.

Answers come back through one strict client tool (`respond`) whose input schema is the answer's schema; Claude
Opus 5.5 and Sonnet 5.5 reject forced tool_choice, so the call uses `auto` and the prompt names the tool (the
same pattern as models/vision.py). Sampling parameters (temperature, top_p) are rejected by the API, so
variety has to come from the agents themselves: different lenses, briefs and rounds.

    convo = Conversation(ctx, "contrarian", role="creator", system=SYSTEM)
    pitch = convo.ask([text_block(brief), image_block(sheet)], PitchSet)   # a pydantic model or a JSON schema
    again = convo.ask("The critics said ... Revise.", PitchSet)           # same agent, remembers its pitch
"""
from __future__ import annotations

import base64
import copy
import io
import json
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

import numpy as np
from PIL import Image
from pydantic import BaseModel, ValidationError

from .context import LabContext

TOOL = "respond"
T = TypeVar("T")
_DROP = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength",
         "pattern", "minItems", "maxItems", "uniqueItems", "default", "title", "examples", "prefixItems"}


def strict_schema(model: type[BaseModel] | dict) -> dict:
    """A JSON schema the strict tool accepts: every object closed and fully required, refs inlined, no numeric or
    length constraints (validate those after the call). Optional fields stay nullable."""
    raw = model.model_json_schema() if isinstance(model, type) else copy.deepcopy(model)
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
        # Keywords are dropped from schema nodes only; a property may itself be called "title" or "default".
        out = {k: ({p: walk(s) for p, s in v.items()} if k == "properties" else walk(v))
               for k, v in node.items() if k not in _DROP}
        if out.get("type") == "object" or "properties" in out:
            out["type"] = "object"
            props = out.get("properties", {})
            out["properties"] = props
            out["required"] = list(props)
            out["additionalProperties"] = False
        if out.get("type") == "array" and "items" not in out:
            out["items"] = {"type": "string"}
        return out

    return walk(raw)


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(img: np.ndarray | Image.Image, long_edge: int = 1568, quality: int = 85) -> dict:
    """RGB uint8 array or PIL image as a base64 JPEG content block, downscaled to `long_edge`."""
    pil = Image.fromarray(img) if isinstance(img, np.ndarray) else img
    pil = pil.convert("RGB")
    s = min(1.0, long_edge / max(pil.size))
    if s < 1.0:
        pil = pil.resize((max(1, round(pil.width * s)), max(1, round(pil.height * s))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=quality)
    data = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _unescape(value: Any) -> Any:
    """Undo quotes the model JSON-escaped twice (11 of 162 feed lines in one lab read `the \\"Watch.\\" hook`).
    Answers are prose, so a literal backslash before a quote is never meant."""
    if isinstance(value, str):
        return value.replace('\\"', '"')
    if isinstance(value, list):
        return [_unescape(v) for v in value]
    if isinstance(value, dict):
        return {k: _unescape(v) for k, v in value.items()}
    return value


def _content(content: str | list[dict]) -> list[dict]:
    return [text_block(content)] if isinstance(content, str) else list(content)


class Conversation:
    """One agent with its own memory: every `ask` appends to the same message history (append-only, so cached
    prefixes and thinking blocks stay valid)."""

    def __init__(self, ctx: LabContext, agent_id: str, role: str, system: str, model: str | None = None,
                 effort: str | None = None, max_tokens: int = 16000):
        self.ctx, self.agent_id, self.role = ctx, agent_id, role
        self.model = model or ctx.settings.creative_model
        self.effort = effort or ctx.settings.creative_effort
        self.max_tokens = max_tokens
        self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self.messages: list[dict] = []
        self._pending_tool_id: str | None = None  # the last `respond` call, answered at the start of the next turn

    def ask(self, content: str | list[dict], schema: type[BaseModel] | dict, instruction: str | None = None,
            retries: int = 2) -> Any:
        """Send `content`, get the answer as `schema` (a validated model instance, or a dict for a raw schema)."""
        tool = {"name": TOOL, "description": "Submit your answer.", "input_schema": strict_schema(schema), "strict": True}
        blocks = _content(content) + [text_block(instruction or f"Give your answer only by calling the `{TOOL}` tool.")]
        self._user(blocks, first_cached=not self.messages)
        for attempt in range(retries + 1):
            self.ctx.check_cancel()
            resp = self.ctx.client.messages.create(
                model=self.model, max_tokens=self.max_tokens, system=self.system, tools=[tool],
                tool_choice={"type": "auto"}, messages=self.messages, output_config={"effort": self.effort},
            )
            self.ctx.usage.add(self.role, self.model, resp.usage)
            self.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                raise RuntimeError(f"{self.agent_id}: the model declined ({getattr(resp, 'stop_details', None)})")
            call = next((b for b in resp.content if b.type == "tool_use" and b.name == TOOL), None)
            if call is None:
                if resp.stop_reason == "max_tokens":
                    raise RuntimeError(f"{self.agent_id}: hit max_tokens ({self.max_tokens}) before answering")
                self._user([text_block(f"Call the `{TOOL}` tool with your answer now.")])
                continue
            self._pending_tool_id = call.id
            data = _unescape(dict(call.input))
            if isinstance(schema, dict):
                return data
            try:
                return schema.model_validate(data)
            except ValidationError as e:
                if attempt == retries:
                    raise
                self._user([text_block(f"That answer did not validate: {str(e)[:1500]}. Call `{TOOL}` again with a corrected answer.")],
                           tool_error=True)
        raise RuntimeError(f"{self.agent_id}: no `{TOOL}` call after {retries + 1} tries")

    def _user(self, blocks: list[dict], first_cached: bool = False, tool_error: bool = False) -> None:
        content: list[dict] = []
        if self._pending_tool_id:
            content.append({"type": "tool_result", "tool_use_id": self._pending_tool_id,
                            "content": "Rejected, see below." if tool_error else "Received.", "is_error": tool_error})
            self._pending_tool_id = None
        content += blocks
        if first_cached:
            # Cache the opening (brief, photos): every later turn of this agent reuses it.
            content[-2 if len(content) > 1 else -1] = {**content[-2 if len(content) > 1 else -1], "cache_control": {"type": "ephemeral"}}
        self.messages.append({"role": "user", "content": content})


def ask_once(ctx: LabContext, agent_id: str, role: str, system: str, content: str | list[dict],
             schema: type[BaseModel] | dict, model: str | None = None, effort: str | None = None,
             max_tokens: int = 16000) -> Any:
    """A single structured question to a fresh agent."""
    return Conversation(ctx, agent_id, role, system, model=model, effort=effort, max_tokens=max_tokens).ask(content, schema)


def pmap(ctx: LabContext, fn: Callable[[T], Any], items: Iterable[T], workers: int | None = None,
         on_error: Callable[[T, Exception], Any] | None = None) -> list[Any]:
    """Run `fn` over `items` on threads (model calls overlap), keeping order. An exception propagates unless
    `on_error` turns it into a result (return None to drop the item)."""
    items = list(items)
    if not items:
        return []

    def run(item: T) -> Any:
        ctx.check_cancel()
        try:
            return fn(item)
        except Exception as e:  # noqa: BLE001 - reported or re-raised below
            if on_error is None or type(e).__name__ == "Cancelled":
                raise
            return on_error(item, e)

    with ThreadPoolExecutor(max_workers=max(1, min(len(items), workers or ctx.settings.creative_workers))) as pool:
        return list(pool.map(run, items))


def dumps(obj: Any) -> str:
    """Compact JSON for prompts (pydantic models included)."""
    if isinstance(obj, BaseModel):
        return obj.model_dump_json(exclude_none=True)
    if isinstance(obj, list) and obj and isinstance(obj[0], BaseModel):
        return "[" + ",".join(o.model_dump_json(exclude_none=True) for o in obj) + "]"
    return json.dumps(obj, ensure_ascii=False, default=str)
