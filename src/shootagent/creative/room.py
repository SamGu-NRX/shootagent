"""The writers' room: a showrunner, six creators and three critics invent listing reels for one home.

One agent asked for "eight diverse concepts" tends to write the obvious reel in eight costumes. The room is built
to diverge first and select after. Creators with different lenses (personas.py) pitch independently, each in its
own conversation with its own memory. Critics with narrow briefs (templates, truth, the feed) score every concept
blind: ids only, in a shuffled order per critic. Each creator then revises its strongest concept against the notes
and steals one idea from another creator's concept to make something new; the critics re-check round 2 while the
creators vote for someone else's work; the showrunner greenlights finalists that are original, likely to travel,
different from each other and free of truth flags. The control in baseline.py gets the same brief, photos and
craft notes in one conversation, so what the lab compares is the process, not the prompt.

Every concept passes `repair` before it reaches the wall: beats must use this shoot's photos, lengths must suit
the renderer, and every voiceover line must fit its beat.

    brief = write_brief(ctx)          # the showrunner studies every photo
    result = run_room(ctx, brief)     # pitch, critique, remix and steal, re-check and vote, greenlight
"""
from __future__ import annotations

import random
import re
import statistics
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field

from ..store import Listing, Shoot
from ..tools._common import contact_sheet, resize_long
from .context import LabContext
from .llm import TOOL, Conversation, image_block, pmap, strict_schema, text_block
from .personas import CRITICS, SHOWRUNNER, Persona, creators
from .schema import (AgentCard, Beat, CardKind, CardSpec, Critique, Ending, EndingStyle, Feature, Hook, Motion, Music,
                     MusicMood, PropertyBrief, ReelConcept, Sfx, TextPos, TextStyle, Transition, Voice, VoiceName)

PROMPTS = Path(__file__).parent / "prompts"
WORDS_PER_SECOND = 2.6  # the synthetic voice's pace; a voiceover line must fit its beat at this rate
VOICE_PAD = 0.2  # seconds of air after a line
MIN_BEATS, MAX_BEATS = 3, 24
MIN_SECONDS, MAX_SECONDS = 12.0, 35.0  # all beats together
BEAT_MIN, BEAT_MAX = 0.6, 6.0  # Beat.clamp's range
MAX_PHOTOS = 8  # photos shown one by one (besides the contact sheet) to every agent that writes or checks claims
BRIEF_PHOTOS = 10  # photos the showrunner studies one by one for the brief


# ---- prompts ----------------------------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _read(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


def prompt(file: str, /, **values: str) -> str:
    """prompts/<file>.md with each {{key}} replaced."""
    text = _read(file)
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def craft(with_trends: bool = False) -> str:
    """The shared craft notes; with the current-format research (prompts/trends.md) when that file exists. It is
    another engineer's file and optional, so it is read at run time and never required."""
    text = _read("craft")
    if with_trends:
        try:
            trends = (PROMPTS / "trends.md").read_text(encoding="utf-8").strip()
        except OSError:
            trends = ""
        if trends:
            text += "\n\n# Current formats on the platforms (research notes)\n\n" + trends
    return text


# The system prompt is the same for every creator (and for every critic); who the agent is comes in its first
# message, after the shared brief and photos. Six parallel pitches then share one cached opening instead of
# each writing its own copy of the photos (see `_prewarm`).


def creator_system() -> str:
    return prompt("creator", craft=craft(with_trends=True))


def persona_text(p: Persona) -> str:
    return prompt("persona", name=p.name, lens=p.lens, philosophy=p.philosophy, signature=p.signature,
                  never="; ".join(p.never) + ".")


def critic_system() -> str:
    return prompt("critic", craft=craft())


def critic_persona_text(p: Persona) -> str:
    return prompt("critic_persona", name=p.name if p.name.startswith("The ") else f"the {p.name}", lens=p.lens,
                  checks=p.checks)


def concept_rules(ctx: LabContext) -> tuple[str, str]:
    """The per-concept rules (shared word for word by the room and the single-agent control) and the voice rule."""
    voice = "" if ctx.options.voice else "\n\nThis lab renders without a voice: set voice to none and write no voiceover."
    return prompt("concept_rules"), voice


# ---- photos -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Photo:
    id: str
    room: str
    orientation: str  # portrait, landscape or square
    path: str  # the head version's file


@lru_cache(maxsize=64)
def _assets(path: str) -> tuple[np.ndarray, dict]:
    """A 480 px thumbnail for contact sheets and a 1024 px JPEG block for the model, from one decode."""
    with Image.open(path) as im:
        img = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    return resize_long(img, 480), image_block(img, long_edge=1024)


@lru_cache(maxsize=8)
def _sheet(photos: tuple[Photo, ...]) -> dict:
    return image_block(contact_sheet([(f"{p.id} {p.room}", _assets(p.path)[0]) for p in photos]), long_edge=1568)


def shoot_photos(ctx: LabContext) -> tuple[Shoot, list[Photo]]:
    """The photos the room works from: the approved views (every view with a photo when none is approved)."""
    shoot = ctx.store.load(ctx.shoot_id)
    views = [v for v in shoot.views if v.head]
    views = [v for v in views if v.approved] or views
    if not views:
        raise ValueError("The shoot has no photos for the writers' room to work from.")
    photos = []
    for v in views:
        path = str(ctx.store.blob_path(ctx.shoot_id, v.head_version.blob))
        h, w = _assets(path)[0].shape[:2]
        orientation = "portrait" if h > 1.05 * w else "landscape" if w > 1.05 * h else "square"
        photos.append(Photo(v.id, (v.room or v.label or "room").strip().lower(), orientation, path))
    return shoot, photos


def _first(photos: list[Photo], ids: list[str], limit: int) -> list[Photo]:
    """`photos` with the ones named in `ids` first (in that order), at most `limit`."""
    by_id = {p.id: p for p in photos}
    lead = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
    return (lead + [p for p in photos if p not in lead])[:limit]


def photo_blocks(photos: list[Photo]) -> list[dict]:
    blocks = []
    for p in photos:
        blocks += [text_block(f"{p.id} · {p.room}"), _assets(p.path)[1]]
    return blocks


def materials(brief: PropertyBrief, listing: Listing, photos: list[Photo], individual: bool = True) -> list[dict]:
    """The opening every writing or checking agent gets: the brief, the contact sheet and the photos (hero views
    first). The single-agent control gets exactly this too."""
    blocks = [text_block(brief_text(brief, listing, photos)),
              text_block("Contact sheet: every photo, labelled with its view id and room."), _sheet(tuple(photos))]
    if individual:
        blocks += photo_blocks(_first(photos, [f.view for f in brief.hero_features], MAX_PHOTOS))
    return blocks


def _prewarm(ctx: LabContext, role: str, system: str, opening: list[dict], schema: type[BaseModel]) -> None:
    """Write a shared opening (system prompt, brief, photos) to the prompt cache with one `max_tokens: 0` request,
    so the agents that start from it in parallel read it instead of each writing it. The request mirrors
    llm.Conversation's (tool, effort, tool_choice); a mismatch only costs the cache hit."""
    tool = {"name": TOOL, "description": "Submit your answer.", "input_schema": strict_schema(schema), "strict": True}
    model = ctx.settings.creative_model
    try:
        resp = ctx.client.messages.create(
            model=model, max_tokens=0, system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[tool], tool_choice={"type": "auto"}, messages=[{"role": "user", "content": opening}],
            output_config={"effort": ctx.settings.creative_effort})
    except Exception:  # noqa: BLE001 - an optimisation: the agents still run, each writing its own cache
        return
    ctx.usage.add(role, model, resp.usage)


def _cached(block: dict) -> dict:
    """Mark the end of a turn for prompt caching, so the agent's next turn reads this one from the cache."""
    return {**block, "cache_control": {"type": "ephemeral"}}


# ---- text the agents read ---------------------------------------------------------------------------------


def _num(x: float) -> str:
    return f"{x:g}"


def stats_line(listing: Listing) -> str:
    """'1 bed · 1 bath · 640 sq ft' from whatever the listing has."""
    bits = []
    if listing.beds is not None:
        bits.append(f"{_num(listing.beds)} bed" + ("" if listing.beds == 1 else "s"))
    if listing.baths is not None:
        bits.append(f"{_num(listing.baths)} bath" + ("" if listing.baths == 1 else "s"))
    if listing.sqft:
        bits.append(f"{listing.sqft:,} sq ft")
    return " · ".join(bits)


def listing_text(listing: Listing) -> str:
    bits = [b for b in (listing.address, listing.price, stats_line(listing)) if b]
    text = " · ".join(bits) or "no listing details entered"
    return text + (f'. Headline: "{listing.headline}"' if listing.headline else "")


def brief_text(brief: PropertyBrief, listing: Listing, photos: list[Photo]) -> str:
    lines = ["THE HOME", f"Listing: {listing_text(listing)}", "", f"Summary: {brief.summary}"]
    if brief.tone:
        lines.append(f"Tone: {brief.tone}")
    sections = [("Hero details (view: detail; what it can do in a reel)",
                 [f"{f.view}: {f.feature}; {f.why}" for f in brief.hero_features]),
                ("Facts a reel may state", brief.facts), ("Never claim", brief.avoid_claims),
                ("Audiences", brief.audiences),
                ("Photos in this shoot (use only these view ids)", [f"{p.id}: {p.room} ({p.orientation})" for p in photos])]
    for head, items in sections:
        if items:
            lines += ["", head + ":"] + [f"- {x}" for x in items]
    return "\n".join(lines)


def _card_text(c: CardSpec) -> str:
    if c.kind in ("price_tag", "stat_count_up"):
        return f"{c.kind} {c.value}" + (f' "{c.text}"' if c.text else "")
    if c.kind == "list_reveal":
        return "list_reveal " + (f'"{c.text}": ' if c.text else "") + " / ".join(c.items or [])
    if c.kind == "big_quote":
        return f'big_quote "{c.text}"' + (f" (highlight {c.highlight})" if c.highlight else "")
    if c.kind == "typing_chat":
        return f'typing_chat "{c.text}"' + (f' -> reply "{c.value}"' if c.value else "")
    return f'{c.kind} "{c.text}"'


def _beat_text(b: Beat) -> str:
    move = b.motion + (f" on ({b.focus[0]:.2f}, {b.focus[1]:.2f})" if b.focus else "")
    parts = [f"{b.view} {b.seconds:.1f}s {move}, {b.transition} in"]
    if b.text:
        parts.append(f'"{b.text}" [{b.text_style or "pop_words"}, {b.text_pos}]')
    if b.voiceover:
        parts.append(f'vo "{b.voiceover}"')
    if b.card:
        parts.append("card " + _card_text(b.card))
    if b.sfx != "none":
        parts.append(f"sfx {b.sfx}")
    return " | ".join(parts)


def script(c: ReelConcept, lineage: str = "") -> str:
    """A concept as a readable shot list: what critics, voters and the showrunner read. No author and no rationale,
    by design: they judge the reel a viewer would see, not the creator's case for it."""
    music = f"{c.music.mood} {c.music.bpm} bpm" + (
        f", drop on beat {c.music.drop_beat + 1}" if c.music.drop_beat is not None else "")
    voice = c.voice.voice + (f" ({c.voice.direction})" if c.voice.direction and c.voice.voice != "none" else "")
    lines = [f'{c.id} "{c.title}" | {c.format} | {c.seconds:.1f} s | music {music} | voice {voice}']
    if lineage:
        lines.append(f"  {lineage}")
    lines += [f"  logline: {c.logline}",
              f'  hook "{c.hook.text}" [{c.hook.style}]' + (f' | vo "{c.hook.voiceover}"' if c.hook.voiceover else "")
              + (f" | sfx {c.hook.sfx}" if c.hook.sfx != "none" else "")]
    lines += [f"  {i}. {_beat_text(b)}" for i, b in enumerate(c.beats, 1)]
    lines.append(f'  ending [{c.ending.style}] "{c.ending.text}"' + (f' | vo "{c.ending.voiceover}"' if c.ending.voiceover else ""))
    tags = " ".join("#" + h for h in c.hashtags)
    lines.append(f"  caption: {c.caption} {tags}".rstrip())
    if c.claims:
        lines.append("  claims: " + "; ".join(c.claims))
    return "\n".join(lines)


def outline(c: ReelConcept, lineage: str = "") -> str:
    """A concept's idea and words without the camera work: what creators read when they steal and when they vote."""
    beats = []
    for b in c.beats:
        bits = [b.view] + ([f'"{b.text}"'] if b.text else []) + ([f"card {_card_text(b.card)}"] if b.card else [])
        beats.append(" ".join(bits + ([f'vo "{b.voiceover}"'] if b.voiceover else [])))
    lines = [f'{c.id} "{c.title}" | {c.format} | {c.seconds:.0f} s | music {c.music.mood} | voice {c.voice.voice}']
    lines += [f"  {lineage}"] if lineage else []
    lines += [f"  logline: {c.logline}", f'  hook "{c.hook.text}"' + (f' | vo "{c.hook.voiceover}"' if c.hook.voiceover else ""),
              "  beats: " + " / ".join(beats), f'  ending [{c.ending.style}] "{c.ending.text}"']
    return "\n".join(lines)


_SIZE = re.compile(r"\b\d+(?:\.\d+)?\s*-?\s*(?:bed|bath|br\b|ba\b)|\b\d[\d,]*\s*(?:sq\.?\s*ft|square\s+f(?:ee|oo)t)")


def basics_on_screen(c: ReelConcept, listing: Listing) -> tuple[bool, bool]:
    """Whether someone watching on mute sees the price, and the basics (beds, baths or square feet): a reel that
    shows neither reads as decor, not a listing. Cards, on-screen text, the hook and the ending count; the caption
    does not."""
    cards = [b.card for b in c.beats if b.card]
    screen = " ".join([c.hook.text, c.ending.text, *(b.text or "" for b in c.beats),
                       *(f"{k.value or ''} {k.text or ''} {' '.join(k.items or [])}" for k in cards)]).lower()
    flat = screen.replace(",", "")
    digits = re.sub(r"\D", "", listing.price or "")
    price = (c.ending.style in ("price_reveal", "end_card") or any(k.kind == "price_tag" for k in cards)
             or bool(digits and re.search(r"\$\s*" + digits[:3], flat)))
    size = (any(k.kind == "stat_count_up" for k in cards) or bool(_SIZE.search(screen))
            or bool(listing.sqft and re.search(rf"\b{listing.sqft}\b", flat)))
    return price, size


# ---- what the agents answer with --------------------------------------------------------------------------


class _Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")


# The concept drafts mirror Hook, Beat, CardSpec, Ending, Music and Voice without a single nullable field: "none" is
# an empty string, an empty list or -1. The API compiles every strict tool schema into a grammar with a size cap,
# and one concept written with the contract's nullable fields (13 anyOf unions) sits right at that cap; any list of
# concepts, or two extra fields, is refused ("The compiled grammar is too large"). `repair` converts back.


class CardDraft(_Draft):
    kind: CardKind = Field(description="price_tag, stat_count_up, list_reveal, big_quote, typing_chat, or none for no card.")
    value: str = Field(description="price_tag / stat_count_up: the number exactly as shown, e.g. '$489,000', '640'. "
                       "typing_chat: the reply bubble, or empty for none. Empty otherwise.")
    text: str = Field(description="Label, quote or chat message, at most 60 characters. Empty if unused.")
    items: list[str] = Field(description="list_reveal: 2-5 items of at most 24 characters each. Empty otherwise.")
    highlight: str = Field(description="big_quote: one word from the text to colour. Empty otherwise.")


class BeatDraft(_Draft):
    view: str = Field(description="View id of the photo, e.g. 'v03'.")
    seconds: float = Field(description="Shot length, 0.6 to 6.0 seconds. Fast cuts are 0.6-1.2 s.")
    motion: Motion
    focus: list[float] = Field(description="[x, y] in 0..1: the point a push, pull or punch lands on. [] for none.")
    transition: Transition = Field(description="How this shot enters from the previous one.")
    text: str = Field(description="On-screen text for this shot, at most 10 words. Empty for none.")
    text_style: TextStyle = Field(description="How the text animates in (ignored when there is no text).")
    text_pos: TextPos
    voiceover: str = Field(description="Line spoken over this shot, at most 20 words. Empty for none.")
    card: CardDraft = Field(description="An animation card over this shot; kind none for no card.")
    sfx: Sfx


class HookDraft(_Draft):
    text: str = Field(description="On-screen hook, at most 10 words. Readable on mute.")
    style: TextStyle = Field(description="How the hook text animates in.")
    voiceover: str = Field(description="Spoken hook line, at most 14 words. Empty for no voice.")
    sfx: Sfx


class EndingDraft(_Draft):
    style: EndingStyle
    text: str = Field(description="Ending line, at most 12 words.")
    voiceover: str = Field(description="Spoken ending line. Empty for none.")


class MusicDraft(_Draft):
    mood: MusicMood
    bpm: int = Field(description="60 to 150.")
    drop_beat: int = Field(description="0-based index of the beat where the music drops (a hit and a punch-in); "
                           "-1 for none.")


class VoiceDraft(_Draft):
    voice: VoiceName = Field(description="Text-to-speech voice, or none for a silent, music-only reel.")
    direction: str = Field(description="How the narrator sounds, e.g. 'deadpan, dry, amused'. Empty for none.")


class ConceptDraft(_Draft):
    """A concept as a creator writes it. The room sets id, agent, round, parents and status."""

    title: str = Field(description="Working title, at most 6 words.")
    format: str = Field(description="The format in a few words, e.g. 'the home narrates its own listing', 'spot the detail'.")
    logline: str = Field(description="One sentence: what happens and why it is worth watching.")
    rationale: str = Field(description="At most two sentences: why it stops the scroll and holds, and the detail "
                           "only this home has that it is built on (view ids).")
    audience: str = Field(description="Who it is for, by situation or taste, never by protected traits.")
    hook: HookDraft
    beats: list[BeatDraft] = Field(description="The shots in order: usually 5 to 12, 12 to 35 seconds in all.")
    ending: EndingDraft
    music: MusicDraft
    voice: VoiceDraft
    caption: str = Field(description="The post caption, at most two sentences.")
    hashtags: list[str] = Field(description="3 to 6 hashtags, without '#'.")
    claims: list[str] = Field(description="Every factual claim the reel makes about the home: on screen, in voice, "
                              "on cards and in the caption.")


class PitchDraft(ConceptDraft):
    pitch: str = Field(description="What you say to the room as you pin it up: the title and the hook in one or two "
                       "sentences, in your voice.")


class PitchSet(_Draft):
    obvious: list[str] = Field(description="The 3 reels any listing agent would post for this home, a line each. "
                               "You will not make these.")
    angles: list[str] = Field(description="5 rough ideas of your own, a line each.")
    concepts: list[PitchDraft] = Field(description="Exactly 2 complete concepts.")


class RemixMove(_Draft):
    move: Literal["revise", "steal"]
    source: str = Field(description="revise: the id of your concept. steal: the id of the concept you take from "
                        "(not yours).")
    own: str = Field(description="steal: the id of your concept it builds on. revise: empty.")
    concept: ConceptDraft
    note: str = Field(description="What you say to the room, in your voice: what changed and which critique it "
                      "answers, or what you took from which id and what you do with it.")


class RemixSet(_Draft):
    moves: list[RemixMove] = Field(description="Exactly two moves: one revise, one steal.")


class CritiqueDraft(_Draft):
    concept: str = Field(description="The concept id, e.g. 'c04'.")
    originality: int = Field(description="1 to 10.")
    hook: int = Field(description="1 to 10.")
    specificity: int = Field(description="1 to 10.")
    truth: int = Field(description="1 to 10.")
    virality: int = Field(description="1 to 10.")
    verdict: Literal["greenlight", "revise", "kill"]
    notes: str = Field(description="One to three pointed sentences: what works, what to cut, the fix.")
    cliche_flags: list[str] = Field(description="Exact templated phrases or devices; empty if none.")
    truth_flags: list[str] = Field(description="Unsupported claims or fair-housing problems, each with why; empty if none.")


class CritiqueList(_Draft):
    critiques: list[CritiqueDraft]


class CriticReview(CritiqueList):
    summary: str = Field(description="Two or three sentences to the room about the wall, naming ids, in your voice.")
    strongest: str = Field(description="The id of the strongest concept.")
    strongest_why: str = Field(description="One pointed sentence.")
    weakest: str = Field(description="The id of the weakest concept.")
    weakest_why: str = Field(description="One pointed sentence.")


class VoteDraft(_Draft):
    concept: str = Field(description="The id you vote for.")
    why: str = Field(description="One line, in your voice.")


class PickDraft(_Draft):
    concept: str
    reason: str


class GreenlightDraft(_Draft):
    picks: list[PickDraft] = Field(description="The finalists, best first.")
    passed_over: str = Field(description="One sentence on the strongest concept you did not pick, and why.")


class BriefDraft(_Draft):
    summary: str
    hero_features: list[Feature]
    facts: list[str]
    avoid_claims: list[str]
    audiences: list[str]
    tone: str
    note_to_room: str


# ---- repair: every concept is checked and fixed before it reaches the wall --------------------------------

_DANGLING = {"a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "with", "for", "at", "by", "from", "your",
             "my", "its", "is", "are", "that"}


def _words(s: str | None) -> int:
    """Words a viewer reads; separators such as '|' and '·' do not count."""
    return sum(1 for w in (s or "").split() if any(ch.isalnum() for ch in w))


def trim_words(s: str | None, n: int) -> str | None:
    """At most `n` words, cut at a word boundary without a dangling 'the' or 'and'; None for blank text. Line
    breaks survive (bold_center sets the lines after the first as sublines)."""
    lines = [" ".join(line.split()) for line in (s or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    out, left = [], n
    for line in lines:
        words = line.split(" ")
        if _words(line) <= left:
            out.append(line)
            left -= _words(line)
            continue
        keep, count = [], 0
        for w in words:
            count += any(ch.isalnum() for ch in w)
            if count > left:
                break
            keep.append(w)
        while len(keep) > 1 and keep[-1].lower().strip(",;:.-*") in _DANGLING:
            keep.pop()
        if keep:
            out.append(" ".join(keep).rstrip(",;:-|·"))
        break
    return "\n".join(out).strip() or None


def _trim_chars(s: str | None, n: int) -> str | None:
    s = " ".join((s or "").split())
    if not s:
        return None
    if len(s) <= n:
        return s
    cut = s[:n]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(",;:- ")


def match_view(raw: str, photos: dict[str, Photo]) -> str | None:
    """The shoot's view id for what a model wrote: 'v03', 'V3', '3', 'view 3', or a room only one view shows."""
    s = " ".join((raw or "").lower().split())
    if s in photos:
        return s
    m = re.fullmatch(r"(?:view\s*)?v?\s*0*(\d{1,3})", s)
    if m:
        hits = [vid for vid in photos if re.fullmatch(rf"v0*{m.group(1)}", vid)]
        if len(hits) == 1:
            return hits[0]
    rooms = [vid for vid, p in photos.items() if p.room == s]
    return rooms[0] if len(rooms) == 1 else None


def norm_id(raw: str | None, prefix: str = "c") -> str | None:
    """'C7', 'c07', '#c07' or '7' as 'c07'."""
    m = re.search(rf"(?i)(?:^|[^a-z]){prefix}?\s*0*(\d{{1,3}})\b", f" {raw or ''}")
    return f"{prefix}{int(m.group(1)):02d}" if m else None


def _beat(d: BeatDraft) -> Beat:
    card = None
    if d.card.kind != "none":
        card = CardSpec(kind=d.card.kind, value=d.card.value or None, text=d.card.text or None,
                        items=d.card.items or None, highlight=d.card.highlight or None)
    return Beat(view=d.view, seconds=d.seconds, motion=d.motion, focus=list(d.focus) if len(d.focus) >= 2 else None,
                transition=d.transition, text=d.text or None, text_style=d.text_style if d.text.strip() else None,
                text_pos=d.text_pos, voiceover=d.voiceover or None, card=card, sfx=d.sfx)


def _repair_card(card: CardSpec | None, listing: Listing) -> tuple[CardSpec | None, str | None]:
    """A card the renderer can draw, with only the fields its kind uses; None (and why) when it cannot be saved."""
    if card is None or card.kind == "none":
        return None, None
    text, value = _trim_chars(card.text, 60), (card.value or "").strip() or None
    if card.kind == "price_tag":
        value = value or listing.price
        return (CardSpec(kind="price_tag", value=value, text=text), None) if value else (None, "a price tag had no price")
    if card.kind == "stat_count_up":
        if not value or not re.search(r"\d", value):
            return None, "a count-up card had no number"
        return CardSpec(kind="stat_count_up", value=value, text=text), None
    if card.kind == "list_reveal":
        items = [i for i in (_trim_chars(x, 24) for x in (card.items or [])) if i][:5]
        if len(items) < 2:
            return None, "a list card had fewer than 2 items"
        return CardSpec(kind="list_reveal", items=items, text=text), None
    if not text:
        return None, f"a {card.kind} card had no text"
    if card.kind == "big_quote":
        hl = (card.highlight or "").strip()
        return CardSpec(kind="big_quote", text=text, highlight=hl if hl and hl.lower() in text.lower() else None), None
    if card.kind == "typing_chat":  # the renderer reads the reply from value (or items[0])
        reply = _trim_chars(value or next(iter(card.items or []), None), 60)
        return CardSpec(kind="typing_chat", text=text, value=reply), None
    return CardSpec(kind=card.kind, text=text), None


def _card_seconds(c: CardSpec) -> float:
    """Time a card needs to play out on screen."""
    if c.kind == "list_reveal":
        return 1.0 + 0.5 * len(c.items or [])
    if c.kind == "big_quote":
        return 1.0 + 0.3 * _words(c.text)
    if c.kind == "typing_chat":
        return 1.2 + (len(c.text or "") + len(c.value or "")) / 25
    return 2.2


def _voice_seconds(line: str | None) -> float:
    return _words(line) / WORDS_PER_SECOND + VOICE_PAD if line else 0.0


def _min_seconds(b: Beat) -> float:
    """The shortest this beat can be: its voiceover spoken, its text read, its card played."""
    need = max(BEAT_MIN, _voice_seconds(b.voiceover))
    if b.text:
        need = max(need, 0.4 + 0.22 * _words(b.text))
    if b.card:
        need = max(need, _card_seconds(b.card))
    return min(BEAT_MAX, need)


def _fit_length(beats: list[Beat], notes: list[str]) -> None:
    """Lengthen beats their content does not fit, then bring the total into 12-35 s."""
    longest_line = int((BEAT_MAX - VOICE_PAD) * WORDS_PER_SECOND)
    for b in beats:
        if _words(b.voiceover) > longest_line:
            b.voiceover = trim_words(b.voiceover, longest_line)
            notes.append(f"cut a voiceover line on {b.view} to {longest_line} words")
        need = _min_seconds(b)
        if b.seconds < need - 1e-6:
            b.seconds = need
    total, goal = sum(b.seconds for b in beats), None
    if total > MAX_SECONDS:
        goal = MAX_SECONDS
        mins = [_min_seconds(b) for b in beats]
        slack = sum(b.seconds - m for b, m in zip(beats, mins))
        if slack >= total - MAX_SECONDS:
            k = (total - MAX_SECONDS) / slack
            for b, m in zip(beats, mins):
                b.seconds -= (b.seconds - m) * k
        else:  # even the minimums run long: scale everything and cut the lines that no longer fit
            k = MAX_SECONDS / total
            for b in beats:
                b.seconds = max(BEAT_MIN, b.seconds * k)
                if _voice_seconds(b.voiceover) > b.seconds:
                    b.voiceover = trim_words(b.voiceover, max(1, int((b.seconds - VOICE_PAD) * WORDS_PER_SECOND)))
        notes.append(f"shortened from {total:.1f} s to {MAX_SECONDS:.0f} s")
    elif total < MIN_SECONDS:
        goal = MIN_SECONDS
        room = sum(BEAT_MAX - b.seconds for b in beats)
        k = min(1.0, (MIN_SECONDS - total) / room) if room > 0 else 0.0
        for b in beats:
            b.seconds += (BEAT_MAX - b.seconds) * k
        notes.append(f"lengthened from {total:.1f} s to {MIN_SECONDS:.0f} s")
    for b in beats:
        b.seconds = round(b.seconds, 2)
    if goal is not None:  # rounding to centiseconds leaves a few hundredths over or under the goal
        off = round(goal - sum(b.seconds for b in beats), 2)
        fits = [b for b in beats if BEAT_MIN <= b.seconds + off <= BEAT_MAX]
        if off and fits:
            b = max(fits, key=lambda x: x.seconds)
            b.seconds = round(b.seconds + off, 2)


def repair(draft: ConceptDraft, photos: dict[str, Photo], listing: Listing) -> tuple[ReelConcept | None, list[str]]:
    """A renderable concept from a draft, and what was fixed; (None, reasons) when it cannot be saved.

    Beats must name a photo of this shoot (fixable spellings are mapped; the rest are dropped, and fewer than
    three left rejects the concept); seconds and focus are clamped; text, voiceover and card fields are trimmed to
    what the renderer shows; every voiceover line fits its beat at 2.6 words a second; the beats add up to
    12-35 s; music is clamped and the drop beat follows the beats that survived."""
    notes: list[str] = []
    beats: list[Beat] = []
    index: dict[int, int] = {}
    for i, raw in enumerate(draft.beats):
        view = match_view(raw.view, photos)
        if view is None:
            notes.append(f"dropped beat {i + 1}: no photo {raw.view!r} in this shoot")
            continue
        b = _beat(raw)
        if view != b.view:
            notes.append(f"beat {i + 1}: {b.view!r} is {view}")
        b.view = view
        b.clamp()
        b.text = trim_words(b.text, 10)
        b.text_style = (b.text_style or "pop_words") if b.text else None
        b.voiceover = trim_words(b.voiceover, 20)
        b.card, why = _repair_card(b.card, listing)
        if why:
            notes.append(f"beat {i + 1}: dropped the card ({why})")
        index[i] = len(beats)
        beats.append(b)
    if len(beats) < MIN_BEATS:
        return None, notes + [f"only {len(beats)} beats use this shoot's photos; a reel needs {MIN_BEATS}"]
    if len(beats) > MAX_BEATS:
        notes.append(f"kept the first {MAX_BEATS} of {len(beats)} beats")
        beats = beats[:MAX_BEATS]

    h, e = draft.hook, draft.ending
    hook = Hook(text=trim_words(h.text, 10) or trim_words(draft.title, 10) or "Look closer", style=h.style,
                voiceover=trim_words(h.voiceover, 14), sfx=h.sfx)
    ending = Ending(style=e.style, text=trim_words(e.text, 12) or trim_words(draft.title, 12) or "",
                    voiceover=trim_words(e.voiceover, 20))

    voice = Voice(voice=draft.voice.voice, direction=draft.voice.direction.strip() or None)
    spoken = bool(hook.voiceover or ending.voiceover or any(b.voiceover for b in beats))
    if voice.voice == "none" and spoken:
        hook.voiceover = ending.voiceover = None
        for b in beats:
            b.voiceover = None
        notes.append("voice is none, so the voiceover lines were dropped")
    elif voice.voice != "none" and not spoken:
        voice = Voice()
    _fit_length(beats, notes)

    drop = index.get(draft.music.drop_beat)  # the drop follows its beat; gone if that beat was dropped
    music = Music(mood=draft.music.mood, bpm=draft.music.bpm,
                  drop_beat=drop if drop is not None and drop < len(beats) else None).clamp()
    tags = [t.strip().lstrip("#").replace(" ", "") for t in draft.hashtags]
    concept = ReelConcept(
        id="", agent="", round=1, title=_trim_chars(draft.title, 80) or hook.text, logline=draft.logline.strip(),
        format=draft.format.strip() or "untitled format", hook=hook, beats=beats, ending=ending, music=music,
        voice=voice, caption=draft.caption.strip(), hashtags=list(dict.fromkeys(t for t in tags if t))[:8],
        audience=draft.audience.strip(), rationale=draft.rationale.strip(),
        claims=[c.strip() for c in draft.claims if c and c.strip()])
    return concept, notes


# ---- the room ---------------------------------------------------------------------------------------------


class RoomResult(BaseModel):
    agents: list[AgentCard]
    concepts: list[ReelConcept]
    critiques: list[Critique]
    finalists: list[str]


def room_agents(ctx: LabContext) -> list[AgentCard]:
    """The showrunner, the creators this lab seats (ctx.options.creators) and the critics."""
    model = ctx.settings.creative_model
    return [p.card(model) for p in [SHOWRUNNER, *creators(ctx.options.creators), *CRITICS]]


def write_brief(ctx: LabContext) -> PropertyBrief:
    """The showrunner studies every photo (a labelled contact sheet, then each photo at 1024 px) and the listing,
    and writes the brief the room works from: hero details by view, facts, claims to avoid, audiences, tone."""
    shoot, photos = shoot_photos(ctx)
    ctx.progress("brief", f"Showrunner studying {len(photos)} photos and the listing")
    by_view = {p.id: p for p in photos}
    rooms = list(dict.fromkeys(p.room for p in photos))
    studied = _first(photos, [next(p.id for p in photos if p.room == r) for r in rooms], BRIEF_PHOTOS)
    content = [text_block("Contact sheet: every photo, labelled with its view id and room."), _sheet(tuple(photos)),
               *photo_blocks([p for p in photos if p in studied]),
               text_block(prompt("brief", listing=listing_text(shoot.listing),
                                 views="\n".join(f"- {p.id}: {p.room}, {p.orientation}" for p in photos)))]
    draft = Conversation(ctx, SHOWRUNNER.id, "showrunner", prompt("showrunner", craft=craft())).ask(content, BriefDraft)
    features = []
    for f in draft.hero_features:
        view = match_view(f.view, by_view)
        if view:
            features.append(Feature(view=view, feature=f.feature.strip(), why=f.why.strip()))
    brief = PropertyBrief(summary=draft.summary.strip(), hero_features=features, audiences=_clean(draft.audiences),
                          facts=_clean(draft.facts), avoid_claims=_clean(draft.avoid_claims), tone=draft.tone.strip())
    ctx.emit(SHOWRUNNER.id, "brief", draft.note_to_room.strip() or brief.summary)
    ctx.progress("brief", f"{len(features)} hero details, {len(brief.facts)} facts, "
                          f"{len(brief.avoid_claims)} claims off limits")
    return brief


def _clean(items: list[str]) -> list[str]:
    return [x.strip() for x in items if x and x.strip()]


def _score(x: int) -> int:
    return min(10, max(1, int(x)))


def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


class _Room:
    """The room's shared state: the wall of concepts, the critiques, the votes and each agent's conversation."""

    def __init__(self, ctx: LabContext, brief: PropertyBrief):
        self.ctx, self.brief = ctx, brief
        shoot, self.photos = shoot_photos(ctx)
        self.listing = shoot.listing
        self.by_view = {p.id: p for p in self.photos}
        self.creators = creators(ctx.options.creators)
        if not self.creators:
            raise ValueError(f"No known creators in {ctx.options.creators}.")
        self.names = {p.id: p.name for p in [SHOWRUNNER, *self.creators, *CRITICS]}
        self.wall: dict[str, ReelConcept] = {}
        self.critiques: list[Critique] = []
        self.latest: dict[str, list[Critique]] = {}  # concept id -> critiques from its latest review
        self.votes: list[tuple[str, str, str]] = []  # voter, concept, why
        self.convos: dict[str, Conversation] = {}
        self.rules, self.voice_rule = concept_rules(ctx)
        # Openings end on a cache breakpoint: every creator shares `opening`, every critic `overview` (the brief and
        # the contact sheet; the Fact Checker's photos come after it) and the showrunner reuses `overview` too.
        self.opening = materials(brief, self.listing, self.photos)
        self.opening[-1] = _cached(self.opening[-1])
        self.overview = materials(brief, self.listing, self.photos, individual=False)
        self.overview[-1] = _cached(self.overview[-1])
        self._lock = threading.Lock()

    # -- bookkeeping --
    def pin(self, items: list[tuple[ReelConcept, str, int, list[str]]]) -> list[ReelConcept]:
        """Give concepts the next ids (c01, c02, ...) and put them on the wall."""
        with self._lock:
            for concept, agent, round_no, parents in items:
                concept.id, concept.agent, concept.round, concept.parents = f"c{len(self.wall) + 1:02d}", agent, round_no, parents
                self.wall[concept.id] = concept
        return [c for c, *_ in items]

    def publish(self, concepts: list[ReelConcept]) -> None:
        """Hand concepts to the lab as they land (snapshots: the room keeps changing statuses), so the dashboard
        shows the cards live and a cancelled run keeps them."""
        self.ctx.publish("concepts", [c.model_copy(deep=True) for c in concepts])

    def round(self, n: int) -> list[ReelConcept]:
        return [c for c in self.wall.values() if c.round == n]

    def lineage(self, c: ReelConcept) -> str:
        if c.round < 2 or not c.parents:
            return ""
        if len(c.parents) == 1 and self.wall.get(c.parents[0]) and self.wall[c.parents[0]].agent == c.agent:
            return f"revision of {c.parents[0]}"
        return "new: takes from " + " and builds on ".join(c.parents)

    def flags(self, cid: str) -> list[str]:
        return [f for k in self.latest.get(cid, []) for f in k.truth_flags]

    def score(self, cid: str) -> float:
        ks = self.latest.get(cid, [])
        crit = _mean([0.35 * k.originality + 0.15 * k.hook + 0.2 * k.specificity + 0.2 * k.virality + 0.1 * k.truth
                      for k in ks])
        return crit + 0.5 * sum(1 for _, v, _ in self.votes if v == cid)

    def system(self, text: str) -> None:
        self.ctx.emit("system", "system", text)

    def failed(self, what: str) -> Callable[[Any, Exception], None]:
        def on_error(item: Any, e: Exception) -> None:
            p = item[1] if isinstance(item, tuple) else item
            name = getattr(p, "name", str(p))
            self.system(f"{name} could not {what}: {type(e).__name__}: {str(e)[:200]}")
            return None
        return on_error

    def accept(self, draft: ConceptDraft, who: Persona) -> ReelConcept | None:
        concept, notes = repair(draft, self.by_view, self.listing)
        if concept is None:
            self.system(f"{who.name}'s \"{draft.title}\" did not make the wall: {notes[-1]}.")
        return concept

    def critique_lines(self, cid: str) -> list[str]:
        out = []
        for k in self.latest.get(cid, []):
            s = (f"{self.names.get(k.critic, k.critic)}: originality {k.originality}, hook {k.hook}, specificity "
                 f"{k.specificity}, truth {k.truth}, virality {k.virality}; {k.verdict}. {k.notes}")
            if k.cliche_flags:
                s += " Cliche flags: " + "; ".join(k.cliche_flags) + "."
            if k.truth_flags:
                s += " Truth flags: " + "; ".join(k.truth_flags) + "."
            out.append("  - " + s)
        return out

    def shuffled(self, concepts: list[ReelConcept], salt: int) -> list[ReelConcept]:
        order = list(concepts)
        random.Random(self.ctx.options.seed * 1000 + salt).shuffle(order)
        return order

    # -- phases --
    def pitch(self) -> None:
        ctx = self.ctx
        ctx.progress("pitch", f"{len(self.creators)} creators pitching 2 concepts each, independently")

        system = creator_system()
        if len(self.creators) > 1:
            _prewarm(ctx, "creator", system, self.opening, PitchSet)
        ask = prompt("pitch", rules=self.rules, voice_rule=self.voice_rule)

        def one(p: Persona) -> None:
            convo = Conversation(ctx, p.id, "creator", system, max_tokens=24000)
            out = convo.ask([*self.opening, text_block(persona_text(p) + "\n\n" + ask)], PitchSet)
            self.convos[p.id] = convo  # a creator whose pitch failed sits out the later rounds
            kept = [(c, d.pitch) for d in out.concepts[:2] if (c := self.accept(d, p)) is not None]
            self.pin([(c, p.id, 1, []) for c, _ in kept])
            self.publish([c for c, _ in kept])
            for c, line in kept:
                ctx.emit(p.id, "pitch", line.strip() or f'{c.title}. Hook: "{c.hook.text}"', concept=c.id)

        pmap(ctx, one, self.creators, on_error=self.failed("pitch"))
        if not self.wall:
            raise RuntimeError("No creator pinned a usable concept.")
        ctx.progress("pitch", f"{len(self.wall)} concepts on the wall")

    def critique(self, concepts: list[ReelConcept], round_no: int) -> None:
        ctx, ids = self.ctx, ", ".join(c.id for c in concepts)
        system = critic_system()
        if round_no == 1:
            _prewarm(ctx, "critic", system, self.overview, CriticReview)

        def one(item: tuple[int, Persona]) -> list[Critique]:
            i, critic = item
            order = self.shuffled(concepts, 10 * round_no + i)
            wall = "\n\n".join(script(c, self.lineage(c)) for c in order)
            convo = self.convos.get(critic.id)
            if convo is None:
                convo = Conversation(ctx, critic.id, "critic", system, max_tokens=24000)
                photos = photo_blocks(_first(self.photos, [f.view for f in self.brief.hero_features], MAX_PHOTOS))
                opening = [*self.overview, *(photos if critic.id == "fact_checker" else [])]  # truth needs the photos
                ask = prompt("critique", count=str(len(concepts)), wall=wall, ids=ids)
                content = [*opening, text_block(critic_persona_text(critic) + "\n\n" + ask)]
            else:
                content = [text_block(prompt("critique2", wall=wall, ids=ids))]
            review = convo.ask(content, CriticReview)
            self.convos[critic.id] = convo
            got = self._critiques(critic, review.critiques, concepts)
            missing = [c.id for c in concepts if c.id not in got]
            if missing:
                more = convo.ask(f"You did not review {', '.join(missing)}. Review those now, one critique each.",
                                 CritiqueList)
                got.update(self._critiques(critic, more.critiques, [c for c in concepts if c.id in missing]))
            ctx.publish("critiques", [k.model_copy(deep=True) for k in got.values()])
            self._announce(critic, review, got)
            return list(got.values())

        results = pmap(ctx, one, list(enumerate(CRITICS)), on_error=self.failed(f"review round {round_no}"))
        with self._lock:
            fresh = [k for ks in results if ks for k in ks]
            self.critiques += fresh
            for c in concepts:
                self.latest[c.id] = [k for k in fresh if k.concept == c.id]

    def _critiques(self, critic: Persona, drafts: list[CritiqueDraft], concepts: list[ReelConcept]) -> dict[str, Critique]:
        wanted, got = {c.id for c in concepts}, {}
        for d in drafts:
            cid = norm_id(d.concept)
            if cid in wanted and cid not in got:
                got[cid] = Critique(critic=critic.id, concept=cid, originality=_score(d.originality), hook=_score(d.hook),
                                    specificity=_score(d.specificity), truth=_score(d.truth), virality=_score(d.virality),
                                    verdict=d.verdict, notes=d.notes.strip(),
                                    cliche_flags=[f.strip() for f in d.cliche_flags if f.strip()],
                                    truth_flags=[f.strip() for f in d.truth_flags if f.strip()])
        return got

    def _announce(self, critic: Persona, review: CriticReview, got: dict[str, Critique]) -> None:
        mentioned = [i for i in dict.fromkeys(norm_id(m) for m in re.findall(r"(?i)\bc\d{1,3}\b", review.summary)) if i in got]
        self.ctx.emit(critic.id, "critique", review.summary, refs=mentioned)
        for label, raw, why in (("Strongest", review.strongest, review.strongest_why),
                                ("Weakest", review.weakest, review.weakest_why)):
            cid = norm_id(raw)
            if cid in got and why.strip():
                self.ctx.emit(critic.id, "critique", f"{label}: {cid}. {why.strip()}", concept=cid)

    def deal(self, round1: list[ReelConcept]) -> dict[str, list[str]]:
        """The concepts each creator may steal from: one strong and one weak, from other creators, and no concept
        dealt twice while any is left. Left to choose, creators raid the wall's favourite (in the first live run
        four of six stole the same line from the top-scored concept); dealt, the steals cover the whole wall."""
        ranked = sorted(round1, key=lambda c: (-self.score(c.id), c.id))
        taken: Counter[str] = Counter()
        hands: dict[str, list[str]] = {}
        for p in self.creators:
            pool = [c for c in ranked if c.agent != p.id]
            if not pool or all(c.agent != p.id for c in round1):
                continue
            strong = min(pool, key=lambda c: (taken[c.id], ranked.index(c)))
            rest = [c for c in pool if c is not strong]
            hands[p.id] = [strong.id] + ([min(rest, key=lambda c: (taken[c.id], -ranked.index(c))).id] if rest else [])
            taken.update(hands[p.id])
        return hands

    def remix(self) -> None:
        ctx = self.ctx
        round1 = self.round(1)
        ctx.progress("remix", f"{len(self.creators)} creators revising against the notes and stealing one idea each")
        ids1 = {c.id for c in round1}
        hands = self.deal(round1)

        def one(p: Persona) -> None:
            convo, own = self.convos.get(p.id), [c.id for c in round1 if c.agent == p.id]
            if convo is None or not own:
                return
            hand = hands.get(p.id, [])
            # Its own concepts in full, everyone else's as outlines: enough to steal an idea from.
            wall = "\n\n".join("\n".join([script(c) if c.id in own else outline(c), *self.critique_lines(c.id)])
                               for c in round1)
            ask = prompt("remix", own=" and ".join(own), deal=" and ".join(hand) or "any concept that is not yours",
                         wall=wall, rules=self.rules, voice_rule=self.voice_rule)
            out = convo.ask([_cached(text_block(ask))], RemixSet)
            for move in out.moves[:2]:
                source, base = norm_id(move.source), norm_id(move.own)
                if move.move == "revise":
                    if source not in own:
                        source = max(own, key=self.score)
                    parents = [source]
                else:
                    if source not in ids1 or source in own:  # the id the note names, a dealt one first
                        named = [i for i in (norm_id(m) for m in re.findall(r"(?i)\bc\d{1,3}\b", move.note))
                                 if i in ids1 and i not in own]
                        source = next((i for i in named if i in hand), named[0] if named else None)
                    parents = [x for x in (source, base if base in own else None) if x]
                concept = self.accept(move.concept, p)
                if concept is None:
                    continue
                self.pin([(concept, p.id, 2, parents)])
                if move.move == "revise":
                    self.wall[source].status = "revised"
                self.publish([concept] + ([self.wall[source]] if move.move == "revise" else []))
                kind = "remix" if move.move == "revise" else "steal"
                ctx.emit(p.id, kind, move.note.strip() or f"{kind}: {concept.title}", concept=concept.id, refs=parents)

        pmap(ctx, one, self.creators, on_error=self.failed("remix"))
        ctx.progress("remix", f"{len(self.round(2))} round-2 concepts on the wall")

    def _vote(self, p: Persona, candidates: list[ReelConcept], salt: int) -> None:
        convo = self.convos.get(p.id)
        allowed = [c.id for c in candidates if c.agent != p.id]
        if convo is None or not allowed:
            return
        own = [c.id for c in candidates if c.agent == p.id]
        wall = "\n\n".join(outline(c, self.lineage(c)) for c in self.shuffled(candidates, 500 + salt))
        out = convo.ask(prompt("vote", own=" and ".join(own) or "none", wall=wall), VoteDraft)
        pick = norm_id(out.concept)
        if pick not in allowed:
            out = convo.ask(f"{out.concept} is not a concept you can vote for. Vote for one of {', '.join(allowed)}.",
                            VoteDraft)
            pick = norm_id(out.concept)
            if pick not in allowed:
                return
        with self._lock:
            self.votes.append((p.id, pick, out.why.strip()))
        self.ctx.emit(p.id, "vote", f"Vote: {pick}. {out.why.strip()}", concept=pick)

    def recheck_and_vote(self, candidates: list[ReelConcept], recheck: bool) -> None:
        """Round 2's critiques and the creators' votes, side by side: neither needs the other."""
        jobs: list[Callable[[], None]] = [lambda c=c, i=i: self._vote(c, candidates, i) for i, c in enumerate(self.creators)]
        if recheck:
            jobs.insert(0, lambda: self.critique(candidates, 2))
        pmap(self.ctx, lambda job: job(), jobs, workers=len(jobs), on_error=lambda job, e: self.system(
            f"A round-2 review or vote failed: {type(e).__name__}: {str(e)[:200]}"))

    def greenlight(self, candidates: list[ReelConcept]) -> list[str]:
        ctx = self.ctx
        n = max(0, ctx.options.finalists)
        ctx.progress("greenlight", f"Showrunner choosing {n} finalists from {len(candidates)} concepts")
        eligible = [c.id for c in candidates if not self.flags(c.id)]
        picks: list[tuple[str, str]] = []
        passed = ""
        if n and len(candidates) > n:
            try:
                picks, passed = self._ask_showrunner(candidates, eligible, n)
            except Exception as e:  # noqa: BLE001 - the room still ends with finalists, from the scores
                if type(e).__name__ == "Cancelled":
                    raise
                self.system(f"The showrunner could not decide ({type(e).__name__}); finalists come from the scores.")
        chosen = [cid for cid, _ in picks]
        # Short of picks (no decision, or invalid ids): fill from the scores, unflagged concepts first, and a creator
        # not picked yet before a second concept from the same creator.
        ranked = sorted(candidates, key=lambda c: (len(self.flags(c.id)), -self.score(c.id), c.id))
        for repeat in (False, True):
            for c in ranked:
                if len(chosen) < n and c.id not in chosen and (repeat or c.agent not in {self.wall[x].agent for x in chosen}):
                    chosen.append(c.id)
                    picks.append((c.id, "Picked on the critics' scores and the votes."))
        for c in self.wall.values():
            if c.id in chosen:
                c.status = "finalist"
            elif c.status != "revised":
                c.status = "killed"
        self.publish(sorted(self.wall.values(), key=lambda c: c.id))
        ctx.publish("finalists", list(chosen))
        for i, (cid, reason) in enumerate(picks):
            label = "Greenlit" if i == 0 else "Wildcard" if i == 1 else "Also greenlit"
            ctx.emit(SHOWRUNNER.id, "greenlight", f'{label}: {cid}, "{self.wall[cid].title}". {reason}', concept=cid)
        if passed:
            refs = [i for i in dict.fromkeys(norm_id(m) for m in re.findall(r"(?i)\bc\d{1,3}\b", passed)) if i in self.wall]
            ctx.emit(SHOWRUNNER.id, "greenlight", passed, refs=refs)
        ctx.progress("greenlight", "Finalists: " + ", ".join(chosen) if chosen else "No finalists")
        return chosen

    def _ask_showrunner(self, candidates: list[ReelConcept], eligible: list[str], n: int) -> tuple[list[tuple[str, str]], str]:
        wall = "\n\n".join(script(c, self.lineage(c)) for c in candidates)
        board = ["SCORES (mean of the critics), VERDICTS, FLAGS AND VOTES"]
        for c in candidates:
            ks, votes = self.latest.get(c.id, []), [(v, w) for v, cid, w in self.votes if cid == c.id]
            means = ", ".join(f"{d} {_mean([getattr(k, d) for k in ks]):.1f}"
                              for d in ("originality", "hook", "specificity", "truth", "virality"))
            tag = "eligible" if c.id in eligible else "INELIGIBLE: unresolved truth flags"
            price, size = basics_on_screen(c, self.listing)
            board.append(f"{c.id} [{tag}] {means}; {len(votes)} vote{'s' if len(votes) != 1 else ''}; price on "
                         f"screen: {'yes' if price else 'NO'}; beds/baths/size on screen: {'yes' if size else 'NO'}")
            board += self.critique_lines(c.id)
            board += [f"  - vote from the {self.names.get(v, v)}: {w}" for v, w in votes]
        which = "round-2" if candidates[0].round == 2 else "round-1"
        wildcard = (" The second is the wildcard: the boldest original idea that is still true, as different from your"
                    " pick as the room allows.") if n >= 2 else ""
        ask = prompt("greenlight", which=which, wall=wall, scoreboard="\n".join(board), n=str(n), wildcard=wildcard)
        convo = Conversation(self.ctx, SHOWRUNNER.id, "showrunner", prompt("showrunner", craft=craft()))
        out = convo.ask([*self.overview, text_block(ask)], GreenlightDraft)
        allowed = set(eligible) if len(eligible) >= n else {c.id for c in candidates}
        picks: list[tuple[str, str]] = []
        for p in out.picks:
            cid = norm_id(p.concept)
            if cid in allowed and cid not in {x for x, _ in picks}:
                picks.append((cid, p.reason.strip()))
        return picks[:n], out.passed_over.strip()


def run_room(ctx: LabContext, brief: PropertyBrief) -> RoomResult:
    """Pitch, critique, remix and steal, re-check and vote, greenlight. Reports the pitch, critique, remix and
    greenlight phases; with ctx.options.rounds < 2 there is no remix and the room votes on round 1."""
    room = _Room(ctx, brief)
    room.pitch()
    round1 = room.round(1)
    ctx.progress("critique", f"{len(CRITICS)} critics reviewing {len(round1)} concepts blind")
    room.critique(round1, 1)
    ctx.progress("critique", f"{len(room.critiques)} critiques on the wall")
    candidates = round1
    if ctx.options.rounds >= 2:
        room.remix()
        candidates = room.round(2) or round1
    recheck = candidates is not round1
    ctx.progress("greenlight", ("Critics re-checking round 2 while the creators vote" if recheck
                                else "Creators voting"))
    room.recheck_and_vote(candidates, recheck)
    finalists = room.greenlight(candidates)
    return RoomResult(agents=room_agents(ctx), concepts=sorted(room.wall.values(), key=lambda c: c.id),
                      critiques=room.critiques, finalists=finalists)
