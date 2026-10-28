"""Tamper-evident edit records: a hash chain per photo, from the camera's brackets to the published JPEG.

Each entry is a small JSON object serialised canonically (sorted keys, no spaces, UTF-8) and hashed with
SHA-256; its "prev" field holds the previous entry's hash. Images are referenced by blob name and the SHA-256
of their bytes. Change one byte of an image, reorder two edits or rewrite a disclosure, and the chain no longer
verifies. The entry text that was hashed travels with the hash (`body`), so a browser can check the chain
without reproducing Python's JSON formatting.

The chain proves integrity (nothing changed since publication), not identity: anyone can build a chain.
Signing it (C2PA) is the next step.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ..store import ChainEntry


def _clean(x: Any) -> Any:
    """Make numbers serialise identically everywhere: no NaN, at most 6 decimals, integral floats as ints."""
    if isinstance(x, bool) or x is None or isinstance(x, (str, int)):
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            return None
        r = round(x, 6)
        return int(r) if r == int(r) else r
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return str(x)


def canonical(obj: dict) -> str:
    return json.dumps(_clean(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class Chain:
    """Builder: `add(body)` links the body to the previous entry and hashes it."""

    def __init__(self):
        self.entries: list[ChainEntry] = []

    def add(self, body: dict) -> ChainEntry:
        body = {**body, "prev": self.entries[-1].hash if self.entries else None}
        text = canonical(body)
        entry = ChainEntry(hash=sha256_text(text), body=text)
        self.entries.append(entry)
        return entry

    @property
    def head(self) -> str | None:
        return self.entries[-1].hash if self.entries else None


def root_hash(last_hashes: Iterable[str]) -> str:
    """One hash over every photo's chain head, in showing order: the publication's fingerprint."""
    return sha256_text("\n".join(last_hashes))


def image_refs(body: dict) -> list[tuple[str, str]]:
    """(blob, sha256) pairs an entry vouches for, wherever they sit in it."""
    out: list[tuple[str, str]] = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("blob"), str) and isinstance(node.get("sha256"), str):
                out.append((node["blob"], node["sha256"]))
            for k, v in node.items():
                if k.endswith("_sha256") and isinstance(v, str):
                    blob = node.get(k[: -len("_sha256")] + "_blob")
                    if isinstance(blob, str):
                        out.append((blob, v))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(body)
    return out


def verify_chain(entries: list[ChainEntry] | list[dict], digest: Callable[[str], str | None] | None = None) -> dict:
    """Check every hash and link; with `digest(blob) -> sha256 or None`, also the images each entry names.

    Returns {ok, entries, images_checked, problems: [str]}."""
    problems: list[str] = []
    prev: str | None = None
    checked = 0
    for i, e in enumerate(entries):
        e = e if isinstance(e, dict) else e.model_dump()
        if sha256_text(e["body"]) != e["hash"]:
            problems.append(f"entry {i + 1}: its text does not match its hash")
        try:
            body = json.loads(e["body"])
        except json.JSONDecodeError:
            problems.append(f"entry {i + 1}: not valid JSON")
            continue
        if body.get("prev") != prev:
            problems.append(f"entry {i + 1}: does not link to the entry before it")
        prev = e["hash"]
        if digest is not None:
            for blob, want in image_refs(body):
                got = digest(blob)
                if got is None:
                    continue
                checked += 1
                if got != want:
                    problems.append(f"entry {i + 1}: image {blob} has changed since it was recorded")
    return {"ok": not problems, "entries": len(entries), "images_checked": checked, "problems": problems}
