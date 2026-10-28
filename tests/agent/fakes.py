"""Scripted Anthropic client and a tiny on-disk shoot for agent and API tests."""

from __future__ import annotations

import itertools
import threading

import numpy as np
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from shootagent.config import Settings
from shootagent.store import ShootStore, View

_ids = itertools.count(1)


def tool(name: str, **inp) -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=inp)


def reply(*blocks, text: str | None = None, stop: str | None = None, usage=(1000, 100)) -> Message:
    content = ([TextBlock(type="text", text=text)] if text else []) + list(blocks)
    stop = stop or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    return Message(
        id=f"msg_{next(_ids)}",
        type="message",
        role="assistant",
        model="claude-sonnet-5-5",
        content=content,
        stop_reason=stop,
        stop_sequence=None,
        usage=Usage(input_tokens=usage[0], output_tokens=usage[1]),
    )


class ScriptedClient:
    """Returns the scripted replies in order and records every request. `gate`, when set, blocks the first call."""

    def __init__(self, replies, gate: threading.Event | None = None):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.gate = gate
        self.messages = self

    def create(self, **kwargs):
        if self.gate is not None and not self.requests:
            self.gate.wait(10)
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot: the loop appends
        if not self.replies:
            return reply(text="Done.")
        return self.replies.pop(0)


def make_shoot(root, n_views: int = 2, size=(48, 64)) -> tuple[ShootStore, str]:
    store = ShootStore(root)
    shoot = store.create("Test shoot", shoot_id="s1")
    rng = np.random.default_rng(0)
    views = [View(id=f"v{i:02d}") for i in range(1, n_views + 1)]
    store.mutate(shoot.id, lambda s: s.views.extend(views))
    for v in views:
        img = rng.integers(40, 160, (*size, 3), dtype=np.uint8)
        fin = store.put_image(shoot.id, img)
        store.mutate(shoot.id, lambda s, b=fin, vid=v.id: setattr(s.view(vid), "finished", b))
        store.add_version(shoot.id, v.id, img, op="ingest")
    return store, shoot.id


def settings(tmp_path, **kw) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, anthropic_api_key="test", **kw)
