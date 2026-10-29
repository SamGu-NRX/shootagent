"""The generate, check, regenerate-once, fall-back policy for one shot, and the trace it leaves."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from shootagent.film import shots
from shootagent.film.check import ClipCheck
from shootagent.film.media import ClipRequest, DepthRequest, MediaError, MediaResult, fingerprint
from shootagent.film.spec import Shot
from shootagent.trace import NullTrace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import make_shoot, settings  # noqa: E402


class FakeMedia:
    """Makes a placeholder file per request; `refuse_retry` makes regenerations unavailable (replay misses)."""

    def __init__(self, root: Path, refuse: str | None = None):
        self.root, self.refuse, self.requests = root, refuse, []

    def backend_name(self, kind):
        return "fake"

    def make(self, req, photos=None):
        self.requests.append(req)
        if isinstance(req, ClipRequest) and self.refuse == "all":
            raise MediaError("no recorded clip for this request", "not_cached")
        fp = fingerprint(req)
        path = self.root / f"{fp}.mp4"
        path.write_bytes(b"clip")
        return MediaResult(fp, path, "fake", cached=False)


def _verdict(passed: bool):
    judge = {"pass": passed, "changes": [] if passed else
             [{"object": "cluster of pendant lights", "change": "appeared", "first_frame": "t=1.2s", "where": "top"}]}

    def fake_check(clip, start, end=None, judge_fn=None, **kw):
        return ClipCheck(passed=passed, reasons=[] if passed else ["judge (high confidence): pendant lights appeared"],
                         signals={"judge": judge}, sheet=np.zeros((16, 16, 3), np.uint8))

    return fake_check


@pytest.fixture
def film(tmp_path, monkeypatch):
    store, sid = make_shoot(tmp_path / "shoots")
    trace = NullTrace()

    def build(passed: bool, refuse: str | None = None):
        monkeypatch.setattr(shots, "check_clip", _verdict(passed))
        media = FakeMedia(tmp_path, refuse)
        ctx = shots.FilmContext(store, sid, trace, settings(tmp_path), media, judge=None)
        return ctx, media, trace

    return build


GENERATED = Shot(view="v01", motion="generated", camera="Slow steady dolly forward toward the round table",
                 aspect="source", label="Game room")


def _kinds(trace):
    return [(e["kind"], e["data"].get("next")) for e in trace.events if e["kind"] in ("check", "retry", "shot")]


def test_a_clip_rejected_twice_is_regenerated_once_naming_the_failure_then_falls_back_to_depth(film):
    """Fails if a rejected generated clip plays, if more than one regeneration is made, if the regeneration
    prompt does not name what was wrong, or if the trace does not show check, retry, check, retry, shot."""
    ctx, media, trace = film(passed=False)
    out = shots.prepare_shot(ctx, 0, GENERATED)

    clips = [r for r in media.requests if isinstance(r, ClipRequest)]
    assert len(clips) == 2
    assert "pendant lights appeared" in clips[1].prompt and "pendant" not in clips[0].prompt
    assert isinstance(media.requests[-1], DepthRequest)
    assert out.source == "depth" and out.fell_back and out.clips == [fingerprint(media.requests[-1])]
    assert [a.passed for a in out.attempts] == [False, False]
    assert _kinds(trace) == [
        ("check", None), ("retry", "regenerate with the failure named in the prompt"),
        ("check", None), ("retry", "depth fallback"), ("shot", None),
    ]


def test_a_backend_that_cannot_serve_the_clip_falls_back_without_a_regeneration(film):
    """Fails if a replay miss or a signed-out grok is retried (it cannot succeed) or leaves the shot with no clip."""
    ctx, media, trace = film(passed=False, refuse="all")
    out = shots.prepare_shot(ctx, 0, GENERATED)
    assert sum(isinstance(r, ClipRequest) for r in media.requests) == 1
    assert out.source == "depth" and out.fell_back
    assert _kinds(trace) == [("retry", "depth fallback"), ("shot", None)]


def test_a_clip_that_passes_its_check_plays(film):
    """Fails if a clip that passed the check is replaced or regenerated."""
    ctx, media, _ = film(passed=True)
    out = shots.prepare_shot(ctx, 0, GENERATED)
    assert out.source == "generated" and not out.fell_back
    assert len(media.requests) == 1


def test_a_clip_the_checker_cannot_read_never_plays(film, monkeypatch):
    """Fails if a corrupt generated clip (the checker raises) plays, or leaves its shot with no clip at all."""
    ctx, media, _ = film(passed=True)

    def unreadable(*a, **k):
        raise ValueError("clip.mp4 decoded no frames")

    monkeypatch.setattr(shots, "check_clip", unreadable)
    out = shots.prepare_shot(ctx, 0, GENERATED)
    assert out.source == "depth" and out.fell_back
    assert "could not be checked" in out.attempts[0].reasons[0]
