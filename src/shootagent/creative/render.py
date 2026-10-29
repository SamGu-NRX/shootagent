"""Render a ReelConcept as a finished 9:16 reel: the lab's renderer for the template, the finalists and the control.

The work is in studio/ (see studio/engine.py); this module is the contract lab.py codes against:

    reel = render_concept(ctx, concept, "B", "Showrunner's pick", "creative")
    # ctx.lab_dir/reels/B.mp4 and B.jpg (a designed cover); reel.timeline says what is on screen when

Width, height and fps can be overridden for quick renders (tests use 270x480 at 10 fps).
"""
from __future__ import annotations

from .context import LabContext
from .schema import Reel, ReelConcept
from .studio.engine import Job, Settings, render


def render_concept(ctx: LabContext, concept: ReelConcept, reel_id: str, label: str, kind: str, voice: bool = True,
                   *, width: int = 1080, height: int = 1920, fps: int = 30) -> Reel:
    """Writes ctx.lab_dir/reels/<reel_id>.mp4 and <reel_id>.jpg; Reel.video and Reel.poster are relative to
    ctx.lab_dir. Deterministic: the same concept renders the same video."""
    ctx.progress("render", f"Reel {reel_id}: {concept.title}")
    cfg = Settings(width=int(width) // 2 * 2, height=int(height) // 2 * 2, fps=int(fps))
    reel = render(Job(ctx, concept, reel_id, label, kind, voice, cfg))
    ctx.progress("render", f"Reel {reel_id} rendered in {reel.render_ms / 1000:.0f} s")
    return reel
