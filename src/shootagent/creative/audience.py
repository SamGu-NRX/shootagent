"""The audience panel: viewer agents who meet a rendered reel the way people really scroll.

Each viewer is a Claude agent (the audience model at low effort) playing one slice of the people a listing reel
reaches on Instagram or TikTok, split by need and habit, not by protected class: casual scrollers (the largest
share), first-time buyers, renters who dream, design fans, active local buyers, investors, relocators buying
sight-unseen, peers in the trade, downsizers, humour-first Gen Z and curious neighbours. Weights are each
segment's assumed share of the audience and drive every weighted rate in the evaluation.

A viewer sees the reel as a timed sequence of frames (0, 0.5, 1.0 and 1.5 s, where most people decide, then
every 1.5-2 s), each introduced by its time, the text on screen and the words spoken at that moment, plus the
music, a note that the narrator is a synthetic voice, and the post caption. It is not told who made the reel or
why. The prompt describes how feeds behave (most reels lose most people within 1.5 s, intents are rare) and
the answers are made consistent afterwards: nobody who scrolled away keeps watching, likes or shares.

The frames and text are the same for every viewer of a reel, so they come first and the persona last; one
`max_tokens=0` call writes them to the prompt cache and the whole panel reads them from there (`Prewarmer`).
"""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import BaseModel, Field

from .context import LabContext
from .llm import TOOL, ask_once, image_block, pmap, strict_schema, text_block
from .schema import Reaction, Reel, ViewerPersona

VIEW_LONG_EDGE = 640  # frames go to the viewers at about 360x640
JPEG_QUALITY = 80
CACHE = {"type": "ephemeral"}


def default_viewers() -> list[ViewerPersona]:
    """The panel: 11 segments of a real-estate reel audience, weights summing to 1."""
    p = ViewerPersona
    return [
        p(id="v_scroller", name="Jess Romero", weight=0.24, color="#8E8E93", initials="JR",
          segment="impatient casual scroller, not house hunting, thumbing through Reels on a break",
          bio="Scrolls hundreds of reels a day, on breaks and in bed. Not looking for a home. The thumb moves in under "
              "a second unless something is funny, strange or beautiful enough to buy two more seconds. Has seen a "
              "thousand 'just listed' videos and no longer registers them. Never contacts agents."),
        p(id="v_genz", name="Kai Brooks", weight=0.11, platform="tiktok", color="#FF2D55", initials="KB",
          segment="humour-first Gen Z scroller on TikTok, 19-24, watches for jokes and weirdness",
          bio="Lives on the For You page. Stops for a joke, a weird detail, a trend done well or something unhinged in "
              "a good way. Spots ads and realtor-speak instantly and skips them. Comments jokes, sends funny ones to "
              "the group chat, cannot afford a home and knows it."),
        p(id="v_first_buyer", name="Priya Nair", weight=0.12, color="#34C759", initials="PN",
          segment="first-time buyer, 27-34, saving for a down payment, browsing listings at night",
          bio="Has a savings target and a pre-approval in progress. Looks for the price, the size and whether a place "
              "feels like a real home she could afford. Tired of glossy listings that hide the price. Saves practical "
              "places to show her partner and asks questions in the comments."),
        p(id="v_renter", name="Marcus Bell", weight=0.10, color="#AF52DE", initials="MB",
          segment="renter who watches home tours as escapism, 24-35",
          bio="Rents a small beige apartment and watches tours to imagine a better one. Stops for a beautiful room, a "
              "cosy detail or a clever small-space idea. Saves inspiration and sends dreamy places to his partner with "
              "'one day'. Not buying any time soon."),
        p(id="v_design", name="Elena Ruiz", weight=0.10, color="#FF9500", initials="ER",
          segment="interiors and design enthusiast who follows designers and stylists",
          bio="Notices materials, light, colour and styling before anything else. Stops for a distinctive object or a "
              "well-composed shot, saves rooms for reference, and is put off by bad staging, cheap filters and sloppy "
              "edits. Follows accounts with taste. Does not care about the price."),
        p(id="v_local_buyer", name="Sam Whitaker", weight=0.08, color="#007AFF", initials="SW",
          segment="local buyer actively searching, pre-approved, listing alerts on",
          bio="Wants the facts fast: price, beds and baths, size, layout, condition. Impatient with anything that delays "
              "them, but stops at once when a home might fit. Messages the agent when it matches and saves listings to "
              "compare."),
        p(id="v_investor", name="Victor Lane", weight=0.05, color="#5856D6", initials="VL",
          segment="small investor who owns two rentals and screens deals",
          bio="Thinks in price per square foot, likely rent and resale. Skeptical of marketing language, respects "
              "specific numbers, ignores lifestyle fluff. Saves or inquires only when the numbers look interesting."),
        p(id="v_relocator", name="Hannah Okoye", weight=0.05, color="#30B0C7", initials="HO",
          segment="relocating for a new job, may buy sight-unseen from another city",
          bio="Cannot visit in person, so needs to understand the real layout, size and condition from video. Values "
              "clear, honest shots of every room and distrusts anything that hides them. Sends promising homes to her "
              "partner and messages agents with questions."),
        p(id="v_peer", name="Diego Alvarez", weight=0.06, color="#A2845E", initials="DA",
          segment="real-estate agent and listing videographer who studies other agents' reels",
          bio="Watches competitors' reels for hooks and editing ideas. Judges craft hard and rarely likes another "
              "agent's post, but stops for a genuinely new idea and saves it to steal. Follows accounts that keep "
              "doing something different."),
        p(id="v_downsizer", name="Linda Carver", weight=0.05, color="#64D2FF", initials="LC",
          segment="downsizer in her sixties selling the family home for something smaller and easier",
          bio="Watches more patiently than most. Wants to see the bedroom, kitchen and storage clearly and to know the "
              "price. Fast cuts, slang and shouty text put her off; a calm, clear tour keeps her. Shares good finds "
              "with her daughter."),
        p(id="v_neighbor", name="Tom Fischer", weight=0.04, color="#FFCC00", initials="TF",
          segment="curious local, not buying, wants to know what homes nearby look like and cost",
          bio="Watches local listings to see inside and to know the price. Stops for a number or a surprising "
              "interior, comments on prices, and shows a neighbour when something surprises him."),
    ]


VIEWER_SYSTEM = """You simulate one specific person scrolling Instagram Reels or TikTok on their phone. You will see one vertical video as a sequence of still frames, each introduced by its time, the text on screen and the words spoken from that moment, then a description of the sound and the post caption, and finally who you are. Answer as that person, honestly: what their thumb would do, not what a polite survey respondent would say.

How real feeds behave:
- The feed is endless and most reels lose most people within 1.5 seconds. On an ordinary listing reel about two in three viewers are gone by then, and fewer than one in five watch a 20-second reel to the end. A reel that truly earns attention keeps more; a dull one keeps fewer.
- People stop only for a real reason that matters to them: a frame that surprises, a line that makes them curious or laugh, a number they care about, a home they can picture themselves in. "Nicely made" is not a reason. "Just listed", room names, slow pans and an agent's end card look like every other listing post.
- Decide in order, as it happens. Look at the frames up to 1.5 s and decide whether you stop as if you had not seen the rest. If you stop, keep going frame by frame and leave the moment it stops paying off (a slow stretch, a repeat, an obvious ad). Nothing after the moment you leave exists for you.
- Intentions are stingy. Each is the honest chance, 0-100, that you would really do it: like means tapping the heart, share means sending it to one specific person, save means you will come back to it, follow means you want more from this account, inquire means messaging the agent about this home. Most reels get none of these from most people: 0-5 is no, 20-40 is maybe, above 60 only when you clearly would.
- You see frames, not motion, and you cannot hear: the sound and the spoken words are given as text. A frame's line gives the words on screen; how big and readable they are, and whether you would notice them at all, you see in the frame. The narrator, when there is one, is a synthetic text-to-speech voice; react to it the way you would to an AI voice.
Do not grade the reel or give advice. Say what you would do and think, briefly, in your own words."""


class ViewerAnswer(BaseModel):
    """What one viewer does with one reel (the model's answer; `Reaction` adds ids and consistency)."""

    first_impression: str = Field(description="What goes through your head in the first second, in your own voice, under 15 words.")
    stopped: bool = Field(description="True only if the first 1.5 seconds actually stopped your thumb.")
    watch_seconds: float = Field(description="Seconds you watch before scrolling on: from 0.3 up to the reel's length (the full length means you watched to the end).")
    completed: bool = Field(description="True if you watched to the end.")
    rewatch: int = Field(description="0-100: chance you let it loop and watch it again.")
    like: int = Field(description="0-100: chance you tap like.")
    comment: int = Field(description="0-100: chance you leave a comment.")
    share: int = Field(description="0-100: chance you send it to a specific person.")
    save: int = Field(description="0-100: chance you save it.")
    follow: int = Field(description="0-100: chance you follow the account that posted it.")
    inquire: int = Field(description="0-100: chance you message the agent about this home.")
    comment_text: str = Field(description="The comment you would actually type, in your own voice, or empty.")
    share_note: str = Field(description="Who you would send it to and why, or empty.")
    why: str = Field(description="One short sentence, under 25 words: the moment that made you stay or leave, and why.")


# ---- frames -------------------------------------------------------------------------------------------------


@dataclass
class Frame:
    t: float
    image: np.ndarray  # RGB uint8, long edge VIEW_LONG_EDGE
    line: str  # "2.5 s | on screen: ... | voice: ..."


def frame_times(seconds: float) -> list[float]:
    """0, 0.5, 1.0 and 1.5 s, then evenly to the end, 1.5-2 s apart: 12-16 frames for a 10-30 s reel."""
    end = max(0.0, seconds - 0.1)
    early = [t for t in (0.0, 0.5, 1.0, 1.5) if t <= end]
    rest = end - 1.5
    if rest <= 0.4:
        return early
    n = int(min(12, max(8, round(rest / 1.75))))
    n = max(1, min(n, int(rest / 0.4)))
    return early + [round(1.5 + rest * k / n, 2) for k in range(1, n + 1)]


def read_frames(path: Path, times: list[float], long_edge: int = VIEW_LONG_EDGE) -> list[tuple[float, np.ndarray]]:
    """RGB frames at `times` (seconds), decoded in one sequential pass (seeking is unreliable in some codecs)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open the reel video {path}.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    targets = sorted((max(0, round(t * fps)), t) for t in times)
    out: list[tuple[float, np.ndarray]] = []
    k, i, last = 0, 0, None
    try:
        while i < len(targets) and cap.grab():
            if k == targets[i][0]:
                ok, bgr = cap.retrieve()
                if ok:
                    h, w = bgr.shape[:2]
                    s = min(1.0, long_edge / max(h, w))
                    small = cv2.resize(bgr, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
                    last = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                while i < len(targets) and targets[i][0] == k:
                    if last is not None:
                        out.append((targets[i][1], last))
                    i += 1
            k += 1
    finally:
        cap.release()
    if last is None:
        raise RuntimeError(f"No frames could be read from {path}.")
    out += [(t, last) for _, t in targets[i:]]  # times past the last frame show the last frame
    return out


def moment(reel: Reel, t: float) -> tuple[list[str], list[str]]:
    """(texts on screen, lines being spoken) at time t, from the reel's timeline."""
    t = min(t, max(0.0, reel.seconds - 0.01))
    order = {"hook": 0, "beat": 1, "ending": 2}
    active = sorted((e for e in reel.timeline if e.t0 - 1e-6 <= t < e.t1), key=lambda e: order[e.kind])
    texts = [e.text.strip() for e in active if e.text and e.text.strip()]
    texts += [f"[card: {e.card.strip()}]" for e in active if e.card and e.card.strip()]
    spoken = [e.voiceover.strip() for e in active if e.voiceover and e.voiceover.strip()] if reel.has_voice else []
    return texts, spoken


def reel_frames(reel: Reel, video_path: Path | str, long_edge: int = VIEW_LONG_EDGE) -> list[Frame]:
    """The reel as the panel sees it: timed frames, each with its on-screen text and spoken words."""
    frames, prev_spoken = [], None
    for t, img in read_frames(Path(video_path), frame_times(reel.seconds), long_edge):
        texts, spoken = moment(reel, t)
        parts = [f"{t:.1f} s", "on screen: " + " / ".join(f'"{x}"' for x in texts) if texts else "no text on screen"]
        if spoken:
            parts.append("voice: (same line continues)" if spoken == prev_spoken else "voice: " + " ".join(f'"{s}"' for s in spoken))
        elif reel.has_voice:
            parts.append("no one speaking")
        prev_spoken = spoken
        frames.append(Frame(t=t, image=img, line=" | ".join(parts)))
    return frames


def sound_text(reel: Reel, caption: str | None) -> str:
    music = reel.music or "background music"
    if reel.has_voice:
        voice = "A synthetic text-to-speech narrator speaks the voice lines shown above."
        if reel.voiceover and not any(e.voiceover for e in reel.timeline):
            voice += f' The full script: "{reel.voiceover.strip()}"'
    else:
        voice = "No voiceover: music only."
    cap = f'Post caption: "{caption.strip()}"' if caption and caption.strip() else "No post caption."
    return f"Sound: {music}. {voice}\n{cap}\nThe reel loops until the viewer scrolls on."


def reel_blocks(reel: Reel, frames: list[Frame], caption: str | None, name: str = "The reel",
                long_edge: int = VIEW_LONG_EDGE, cache: bool = True) -> list[dict]:
    """Content blocks for one reel: a header, then each frame's line and image, then sound and caption. The last
    block carries a cache breakpoint so every agent shown this reel reads it from the prompt cache."""
    blocks = [text_block(f"{name}: a vertical 9:16 video, {reel.seconds:.1f} s long, shown as {len(frames)} frames in "
                         "order. Each frame's line gives its time, the text on screen and what is being said from that moment.")]
    for f in frames:
        blocks += [text_block(f.line), image_block(f.image, long_edge=long_edge, quality=JPEG_QUALITY)]
    blocks.append({**text_block(sound_text(reel, caption)), **({"cache_control": CACHE} if cache else {})})
    return blocks


# ---- the shared-prefix cache --------------------------------------------------------------------------------


class Prewarmer:
    """Writes a shared request prefix (tools, system, the reel blocks) to the prompt cache once, just before the
    calls that share it. A cache entry is readable only once a response has started, so N parallel calls on a
    cold prefix would each pay to write it; a `max_tokens=0` call writes it in a second or two and returns no
    output. The request mirrors llm.Conversation exactly (same tool, system, tool_choice and effort): any
    difference would key a different cache entry. A failure only costs the saving, never the call."""

    def __init__(self, ctx: LabContext):
        self.ctx = ctx
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._done: set[str] = set()
        self.failures: list[str] = []

    def ensure(self, key: str, role: str, system: str, schema: type[BaseModel] | dict, shared: list[dict],
               model: str, effort: str) -> None:
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            if key in self._done:
                return
            self._done.add(key)
            tool = {"name": TOOL, "description": "Submit your answer.", "input_schema": strict_schema(schema), "strict": True}
            try:
                resp = self.ctx.client.messages.create(
                    model=model, max_tokens=0, system=[{"type": "text", "text": system, "cache_control": CACHE}],
                    tools=[tool], tool_choice={"type": "auto"}, output_config={"effort": effort},
                    messages=[{"role": "user", "content": [*shared, text_block("(cache warm-up)")]}],
                )
                self.ctx.usage.add(role, model, resp.usage)
            except Exception as e:  # noqa: BLE001 - the real calls still run, uncached
                self.failures.append(f"{key}: {type(e).__name__}: {str(e)[:160]}")


# ---- watching -----------------------------------------------------------------------------------------------


def persona_text(v: ViewerPersona, seconds: float) -> str:
    app = "TikTok" if v.platform == "tiktok" else "Instagram Reels"
    return (f"You are {v.name}: {v.segment}. {v.bio}\n"
            f"You are on {app} and this {seconds:.1f}-second reel just came up in your feed. What do you do? "
            "Decide in order, as it plays.")


def to_reaction(a: ViewerAnswer, viewer: ViewerPersona, reel: Reel) -> Reaction:
    """The viewer's answer made consistent: under 1.5 s means not stopped and no actions; finishing means
    watching the full length; only finishers rewatch."""
    length = max(reel.seconds, 0.1)
    watch = min(max(float(a.watch_seconds), 0.0), length)
    stopped = bool(a.stopped) and watch >= min(1.5, length)
    if not stopped:
        watch = min(watch, 1.5)
    completed = stopped and (bool(a.completed) or watch >= 0.97 * length)
    if completed:
        watch = length

    def intent(x: int, ok: bool = True) -> int:
        return int(min(100, max(0, int(x)))) if ok else 0

    return Reaction(
        viewer=viewer.id, reel=reel.id, stopped=stopped, first_impression=a.first_impression.strip(),
        watch_seconds=round(watch, 2), completed=completed, rewatch=intent(a.rewatch, completed),
        like=intent(a.like, stopped), comment=intent(a.comment, stopped), share=intent(a.share, stopped),
        save=intent(a.save, stopped), follow=intent(a.follow, stopped), inquire=intent(a.inquire, stopped),
        comment_text=a.comment_text.strip() if stopped else "", share_note=a.share_note.strip() if stopped else "",
        why=a.why.strip(),
    )


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip(" ,.;:") + "…"


def brief(text: str, limit: int = 170) -> str:
    """The first sentence of `text` (or as much as fits in `limit` characters), for a feed line. A stop inside an
    open quote or parenthesis ('lines ("wavy. On purpose")') does not end the sentence."""
    text = " ".join(text.split())
    for m in re.finditer(r"[.!?][\"')\]]*(?=\s+[A-Z\"(]|$)", text):
        first = text[:m.end()]
        if first.count("(") == first.count(")") and first.count('"') % 2 == 0:
            return _clip(first if len(first) <= limit else text, limit)
    return _clip(text, limit)


def reaction_note(r: Reaction, seconds: float) -> str:
    """The feed line, in the viewer's own voice: "Scrolled at 0.8 s: another 'just listed'."."""
    if not r.stopped:
        return _clip(f"Scrolled at {r.watch_seconds:.1f} s: {r.first_impression}", 160)
    if not r.completed:
        return f"Stopped, gone at {r.watch_seconds:.0f} of {seconds:.0f} s. {brief(r.why, 150)}"
    extra = f' Commenting: "{_clip(r.comment_text, 80)}"' if r.comment >= 50 and r.comment_text else ""
    if not extra and r.share >= 50 and r.share_note:
        extra = f" Sending it: {_clip(r.share_note, 80)}"
    return f"Watched to the end. {brief(r.why, 150)}{extra}"


def watch_one(ctx: LabContext, viewer: ViewerPersona, reel: Reel, shared: list[dict]) -> Reaction:
    """One viewer, one reel: a fresh agent sees the shared reel blocks, then who it is."""
    answer = ask_once(ctx, viewer.id, "viewer", VIEWER_SYSTEM, [*shared, text_block(persona_text(viewer, reel.seconds))],
                      ViewerAnswer, model=ctx.settings.audience_model, effort=ctx.settings.audience_effort, max_tokens=8000)
    return to_reaction(answer, viewer, reel)


def watch(ctx: LabContext, reel: Reel, video_path: Path | str, viewers: list[ViewerPersona], caption: str | None = None,
          frames: list[Frame] | None = None, prewarm: Prewarmer | None = None,
          on_reaction: Callable[[Reaction], Any] | None = None, on_error: Callable[[ViewerPersona, Exception], Any] | None = None,
          ) -> list[Reaction]:
    """Every viewer watches one reel, in parallel. Each reaction is posted to the feed as it arrives.
    A viewer whose call fails is dropped and reported to `on_error`; without `on_error` the failure propagates."""
    frames = frames if frames is not None else reel_frames(reel, video_path)
    shared = reel_blocks(reel, frames, caption)
    warm = prewarm or Prewarmer(ctx)

    def run(v: ViewerPersona) -> Reaction:
        warm.ensure(f"watch:{reel.id}", "viewer", VIEWER_SYSTEM, ViewerAnswer, shared,
                    ctx.settings.audience_model, ctx.settings.audience_effort)
        r = watch_one(ctx, v, reel, shared)
        ctx.emit(v.id, "note", reaction_note(r, reel.seconds), concept=reel.concept)
        if on_reaction:
            on_reaction(r)
        return r

    return [r for r in pmap(ctx, run, viewers, on_error=on_error) if r is not None]
