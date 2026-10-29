"""The two baselines the room is measured against: the conventional template reel and a single-agent control.

The template is what most listings post: "Just listed", every room in showing order with a room label,
crossfades, the stats card, warm music, "Schedule a tour". It is built deterministically, with no model call, so
it is the same reel every time.

The control is the room's ablation, and it is meant to be strong: one conversation with the same model and effort,
the same brief, the same photos and the same craft notes (prompts/craft.md, plus prompts/trends.md when it
exists), asked for n concepts that range across very different angles in one go and then, in a second turn, for
its own pick. What it lacks is only the room's process: independent lenses, blind specialist critics, remix and
steal, peer votes and a greenlight. Its concepts pass the same `repair` as the room's.
"""
from __future__ import annotations

from pydantic import Field

from ..config import get_settings
from ..reel.spec import default_spec
from .context import LabContext
from .llm import Conversation, text_block
from .personas import SINGLE_AGENT
from .room import (ConceptDraft, _Draft, concept_rules, craft, materials, norm_id, prompt, repair, shoot_photos,
                   stats_line, trim_words)
from .schema import AgentCard, Beat, CardSpec, Ending, Hook, Music, PropertyBrief, ReelConcept, Voice

TEMPLATE_SECONDS = 22.0  # beats only; with the end card the reel runs about 25 s
TEMPLATE_MAX_BEATS = 10


# ---- the template ------------------------------------------------------------------------------------------


def template_concept(ctx: LabContext) -> ReelConcept:
    """The conventional listing reel, deterministic and model-free: "Just listed" with the address and price, every
    approved photo in showing order (reel/spec.py ROOM_ORDER) with a room label, crossfades, alternating push-ins
    and pans, the beds/baths/sq ft card on beat 2, warm music at 84 bpm, no voice, and a "Schedule a tour" end card."""
    shoot = ctx.store.load(ctx.shoot_id)
    slides = default_spec(shoot).slides
    if len(slides) > TEMPLATE_MAX_BEATS:  # one photo per room first, then second angles, kept in showing order
        firsts, seconds_, seen = [], [], set()
        for i, s in enumerate(slides):
            (seconds_ if s.caption in seen else firsts).append(i)
            seen.add(s.caption)
        slides = [slides[i] for i in sorted((firsts + seconds_)[:TEMPLATE_MAX_BEATS])]
    listing = shoot.listing
    stats = stats_line(listing)
    seconds = round(min(6.0, max(2.2, TEMPLATE_SECONDS / len(slides))), 2)
    pans = ("pan_right", "pan_left")
    beats = []
    for i, s in enumerate(slides):
        card = None
        if i == 1 and stats:
            value = f"{listing.sqft:,}" if listing.sqft else f"{listing.beds:g}" if listing.beds is not None else None
            card = CardSpec(kind="stat_count_up", value=value, text=stats) if value else None
        beats.append(Beat(view=s.view, seconds=seconds, motion="push_in" if i % 2 == 0 else pans[(i // 2) % 2],
                          transition="cut" if i == 0 else "crossfade", text=s.caption, text_style="label",
                          text_pos="bottom", card=card))
    where = " · ".join(x for x in (listing.address, listing.price) if x)
    hook = f"Just listed: {where}" if where else "Just listed"
    if len(hook.split()) > 10:
        hook = f"Just listed · {listing.price}" if listing.price else "Just listed"
    facts = [x for x in (listing.address, listing.price, stats) if x]
    return ReelConcept(
        id="template", agent="template", round=1, title="Just listed",
        logline="The conventional listing reel: every room in showing order with labels, the stats and a call to action.",
        format="conventional listing walkthrough",
        hook=Hook(text=hook, style="bold_center"), beats=beats,
        ending=Ending(style="end_card", text="Schedule a tour"), music=Music(mood="warm", bpm=84), voice=Voice(),
        caption=". ".join(["Just listed", *facts, "Schedule a tour"]) + ".",
        hashtags=["justlisted", "realestate", "newlisting", "hometour", "forsale"],
        audience="buyers browsing new listings",
        rationale="What most listings post: a walkthrough in showing order with room labels and the key numbers.",
        claims=facts,
    )


# ---- the single-agent control -------------------------------------------------------------------------------


class SingleSet(_Draft):
    obvious: list[str] = Field(description="The 3 reels any listing agent would post for this home, a line each. "
                               "You will not make these.")
    concepts: list[ConceptDraft]


class SinglePick(_Draft):
    concept: str = Field(description="The id of the concept you would post, e.g. 's03'.")
    why: str = Field(description="One or two sentences, honest about its risks.")


def single_agent_card() -> AgentCard:
    return SINGLE_AGENT.card(get_settings().creative_model)


def single_agent_concepts(ctx: LabContext, brief: PropertyBrief, n: int = 8) -> tuple[list[ReelConcept], str]:
    """The control: one agent writes `n` concepts in one pass (ids s01, s02, ...), then picks its best. Returns the
    concepts that survive `repair` and the id of its pick."""
    ctx.progress("single_agent", f"One agent writing {n} concepts in one pass")
    shoot, photos = shoot_photos(ctx)
    by_view = {p.id: p for p in photos}
    rules, voice_rule = concept_rules(ctx)
    convo = Conversation(ctx, SINGLE_AGENT.id, "single_agent", prompt("single_agent", craft=craft(with_trends=True)),
                         max_tokens=32000)
    out = convo.ask([*materials(brief, shoot.listing, photos),
                     text_block(prompt("single_pitch", n=str(n), rules=rules, voice_rule=voice_rule))], SingleSet)
    concepts: list[ReelConcept] = []
    for draft in out.concepts[:n]:
        concept, notes = repair(draft, by_view, shoot.listing)
        if concept is None:
            ctx.emit(SINGLE_AGENT.id, "note", f'"{draft.title}" was not usable: {notes[-1]}.')
            continue
        concept.id, concept.agent, concept.round = f"s{len(concepts) + 1:02d}", SINGLE_AGENT.id, 1
        concepts.append(concept)
        ctx.emit(SINGLE_AGENT.id, "pitch", f'{concept.title}. Hook: "{concept.hook.text}"', concept=concept.id)
    if not concepts:
        raise RuntimeError("The single agent wrote no usable concept.")
    ctx.publish("concepts", [c.model_copy(deep=True) for c in concepts])  # its pick changes no status
    listing ="\n".join(f'{c.id} "{c.title}": {c.format}; hook "{c.hook.text}"' for c in concepts)
    pick = convo.ask(prompt("single_pick", concepts=listing), SinglePick)
    chosen = norm_id(pick.concept, prefix="s")
    if chosen not in {c.id for c in concepts}:
        chosen = concepts[0].id
    title = next(c.title for c in concepts if c.id == chosen)
    ctx.emit(SINGLE_AGENT.id, "note", f'My pick: {chosen}, "{title}". {trim_words(pick.why, 60) or ""}'.strip(),
             concept=chosen)
    ctx.progress("single_agent", f"{len(concepts)} concepts; its pick: {chosen}")
    return concepts, chosen
