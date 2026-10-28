import numpy as np
import pytest

from shootagent.tools import _ops


@pytest.fixture
def fake_ops(monkeypatch):
    """Deterministic stand-ins for imaging/models so the loop is tested without GPUs or network.
    `verdicts` is the queue of judge answers ('pass'/'fail'); empty means pass."""
    verdicts: list[str] = []

    def exposure(img, ev, mask=None):  # gain in linear light, like a real exposure change
        lin = (img.astype(np.float32) / 255.0) ** 2.2 * 2**ev
        out = np.clip(lin ** (1 / 2.2) * 255 + 0.5, 0, 255).astype(np.uint8)
        return out if mask is None else np.where(mask[..., None], out, img)

    def judge(before, after, mask, intent, reference=None):
        v = verdicts.pop(0) if verdicts else "pass"
        if isinstance(v, dict):  # a full verdict with sub-checks
            return v, {"model": "claude-sonnet-5-5", "input_tokens": 500, "output_tokens": 50}
        return (
            {"verdict": v, "reason": f"judge said {v}", "issues": []},
            {"model": "claude-sonnet-5-5", "input_tokens": 500, "output_tokens": 50},
        )

    monkeypatch.setattr(_ops, "exposure", exposure)
    monkeypatch.setattr(
        _ops, "composite", lambda before, after, mask: np.where(mask[..., None], after, before)
    )
    monkeypatch.setattr(_ops, "judge", judge)
    return verdicts
