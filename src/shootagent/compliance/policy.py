"""Edit policy for listing photos: what may change, what must be disclosed, and what is refused.

Three sources shape it:

* California AB 723 (Business and Professions Code section 10140.8, in force since 1 January 2026). An image
  that adds, removes or materially changes physical features of the property is "digitally altered": it must
  say so and be paired with the unaltered original. Exposure, colour correction, cropping, straightening,
  sharpening and noise reduction are not alterations.
* NAR Code of Ethics, Article 12: advertising must present a true picture of the property.
* MLS photo rules: no edits that misrepresent the property's condition, surroundings or features.

The classifier is deterministic, so the same request always gets the same answer, the agent loop can enforce
it on every edit, and a declined request costs nothing. It is a guard rail, not legal advice.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal

Verdict = Literal["decline", "disclose", "overlay", "exempt", "unclear"]

# Worst first: a request's verdict is the worst of its parts.
SEVERITY: dict[str, int] = {"decline": 4, "disclose": 3, "overlay": 2, "exempt": 1, "unclear": 0}

TRUE_PICTURE = "Listing photos must show the property as it is (NAR Code of Ethics, Article 12; MLS photo rules)."


@dataclass
class PolicyItem:
    text: str  # the part of the request it judged, e.g. "remove the power lines"
    verdict: Verdict
    category: str  # surroundings, condition, view, feature, structure, proportions, removal, ...
    reason: str  # one plain sentence a customer can read


@dataclass
class PolicyResult:
    verdict: Verdict
    items: list[PolicyItem] = field(default_factory=list)

    @property
    def declined(self) -> list[PolicyItem]:
        return [i for i in self.items if i.verdict == "decline"]

    def to_dict(self) -> dict:
        return {"verdict": self.verdict, "items": [asdict(i) for i in self.items]}


# ---- lexicons ---------------------------------------------------------------------------------------------

_REMOVE = (
    r"remove|removing|removed|erase|erasing|delete|deleting|take\s+out|take\s+away|get\s+rid\s+of|eliminate|"
    r"clear(?:\s+away|\s+out)?|declutter|de-clutter|edit\s+out|photoshop\s+out|clone\s+out|hide|hiding|cover(?:\s+up)?|"
    r"conceal|mask\s+out|crop\s+out"
)
_ADD = r"add|adding|insert|place|put|include|stage|furnish|install|render|create|build|show"
_REPAIR = r"fix|fixing|repair|repairing|patch|patching|retouch|touch\s+up|smooth(?:\s+out)?|clean\s+up|cleanup|clean|mend|fill\s+in"
_CHANGE = (
    r"change|changing|replace|replacing|swap|turn|make|paint|repaint|recolou?r|colou?r|use|match|convert|update|"
    r"refresh|improve|enhance|green|redo|transform"
)
_ADJUST_VERB = r"brighten|darken|lighten|warm|cool|straighten|crop|sharpen|denoise|level|correct|balance|expose"
_VERB = re.compile(rf"\b(?:{_REMOVE}|{_ADD}|{_REPAIR}|{_CHANGE}|{_ADJUST_VERB})\b", re.I)

_SURROUNDINGS = re.compile(
    r"\b(?:power|utility|electric(?:al)?\s+utility|telephone|phone|overhead|transmission|high[- ]voltage)\s+"
    r"(?:lines?|wires?|cables?)\b|"
    r"\b(?:utility|power|telephone|phone|telegraph|electric(?:al)?|light)\s+poles?\b|"
    r"\b(?:cell(?:ular|phone)?|radio|transmission|water|communications?)\s+towers?\b|"
    r"\b(?:substations?|high[- ]?ways?|freeways?|interstates?|overpass(?:es)?|railroads?|railways?|"
    r"(?:train|rail(?:road|way)?)\s+tracks?|billboards?|landfills?|wind\s+turbines?|power\s+plants?)\b|"
    r"\bneighbou?r(?:'s|s'|s)?\s+(?:house|home|building|garage|roof|wall|property|structure|shed)s?\b|"
    r"\b(?:house|home|building|apartment|condo)s?\s+next\s+door\b|"
    r"\badjacent\s+(?:house|home|building|structure)s?\b",
    re.I,
)
_CONDITION = re.compile(
    r"\bcrack(?:s|ed|ing)?\b|\bwater\s+(?:stains?|damage|marks?|spots?)\b|\bmou?ld\b|\bmildew\b|"
    r"\b(?:dry\s+)?rot(?:ten|ting)?\b|\bleak(?:s|ing|y)?\b|\bholes?\s+in\b|\bdents?\b|"
    r"\b(?:water|storm|fire|roof|wall|ceiling|floor|siding|hail|flood|smoke|termite|structural)\s+damage\b|"
    r"\bdamage\s+(?:to|on|in)\b|"
    r"\bpeeling\b|\bchipped\b|\bbroken\s+(?:windows?|glass|tiles?|fences?|gutters?|steps?|doors?|boards?|siding)\b|"
    r"\bmissing\s+(?:shingles?|tiles?|siding|boards?)\b|\brust\s+(?:stains?|marks?|on\s+the)\b|"
    r"\brusted\s+(?:gutters?|railings?|fences?|roof|pipes?)\b|\btermites?\b|\bsagging\b|"
    r"\b(?:ceiling|wall|roof)\s+stains?\b|\bstains?\s+on\s+the\s+(?:ceiling|walls?|roof)\b|"
    r"\bfoundation\s+(?:issues?|problems?|damage)\b|\bpotholes?\b|\bstructural\b",
    re.I,
)
_VIEW = re.compile(
    r"\b(?:an?\s+|the\s+)?(?:ocean|sea|lake|river|bay|beach|mountain|city|skyline|golf(?:\s+course)?|canyon|valley|"
    r"harbou?r)\s+views?\b|"
    r"\b(?:replace|replacing|swap|swapping|change|changing|fake|invent)\s+(?:the\s+|a\s+)?(?:window\s+)?views?\b",
    re.I,
)
NOT_STRUCT = '(?!\\s+(?:mats?|stops?|hangers?|wreaths?|signs?|decor|decals?|stickers?|clings?|art|clocks?|hangings?|posters?|pictures?|lights?|sconces?|shelf|shelves|mounts?|tvs?|outlets?|plates?|vents?|treatments?|coverings?|curtains?|blinds?|screens?|reflections?|glare|clutter|items?|contents?|magnets?|handles?|knobs?|hardware|ac\\b|a/c|air\\s+condition\\w*|units?|fans?|heaters?|tools?|toys?|accessories|deck|decks|side|sides|area|areas|edge|edges|furniture|chairs?|loungers?|towels?|floats?|cover|covers))'
_ARTICLES = r"(?:(?:a|an|the|some|new|nice|big|small|large|beautiful|modern|another|second|extra|real|working|built-in|in-ground|above-ground)\s+){0,3}"
# _ADD without "stage" and "furnish": staging puts furniture in a space the property has ("stage the bedroom",
# "furnish the patio"), it never adds the space, so it is disclosed as staging below rather than declined here.
_ADD_FEATURE = r"add|adding|insert|place|put|include|install|render|create|build|show"
_FEATURE_ADD = re.compile(
    rf"\b(?:{_ADD_FEATURE})\s+{_ARTICLES}(?:swimming\s+pools?|pools?|hot\s+tubs?|jacuzzis?|spas?|decks?|patios?|balcon(?:y|ies)|"
    r"porch(?:es)?|fireplaces?|chimneys?|windows?|skylights?|doors?|garages?|rooms?|bedrooms?|bathrooms?|"
    r"extensions?|(?:second|2nd|third)\s+(?:story|storey|floor)|fences?|gazebos?|pergolas?|sheds?|"
    r"(?:tennis|basketball|pickleball)\s+courts?|driveways?|solar\s+panels?|staircases?|kitchen\s+islands?)\b"
    + NOT_STRUCT,
    re.I,
)
# "declutter the garage" and "clear the counter" name the container, not the thing removed.
_REMOVE_THING = (
    r"remove|removing|removed|erase|erasing|delete|deleting|take\s+out|take\s+away|get\s+rid\s+of|eliminate|"
    r"edit\s+out|photoshop\s+out|clone\s+out|hide|hiding|cover\s+up|conceal|mask\s+out|crop\s+out"
)
_STRUCTURE_REMOVE = re.compile(
    rf"\b(?:{_REMOVE_THING})\s+(?:(?:the|a|an|that|this|these|those|all|some|both|old|ugly|left|right|front|back|"
    r"interior|small|big|large|half|partial|support)\s+){0,3}(?:walls?|columns?|pillars?|beams?|stairs?|staircases?|"
    r"windows?|doors?|doorways?|fireplaces?|chimneys?|built-?ins?|cabinets?|cabinetry|(?:kitchen\s+)?islands?|"
    r"countertops?|radiators?|garages?|decks?|porch(?:es)?|balcon(?:y|ies)|roofs?|railings?)\b"
    + NOT_STRUCT,
    re.I,
)
_PROPORTIONS = re.compile(
    r"\b(?:make|making)\s+(?:(?:the|this|it|room|space|yard|lot|kitchen|bedroom|bathroom|house|home|garden|"
    r"backyard|living\s+room)\s+){0,3}(?:look\s+|seem\s+|appear\s+|feel\s+)?(?:much\s+|a\s+lot\s+|way\s+)?"
    r"(?:bigger|larger|more\s+spacious|wider|taller|longer|deeper|roomier)\b|\bstretch\b|\bwiden\s+the\b",
    re.I,
)

_GEAR = re.compile(
    r"\b(?:tripods?|camera(?:\s+gear)?|photographers?|light\s+stands?|flash(?:es)?|(?:my|the\s+photographer'?s?)\s+reflections?|"
    r"reflection\s+of\s+(?:me|the\s+photographer|the\s+camera))\b",
    re.I,
)
_OVERLAY = re.compile(
    r"\b(?:watermarks?|logos?|captions?|text\s+overlay|overlay|lot\s+lines?|property\s+lines?|"
    r"boundar(?:y|ies)|outline\s+(?:the\s+)?(?:lot|property)|arrows?|sign\s+riders?)\b",
    re.I,
)
_SKY = re.compile(r"\b(?:sky|skies|clouds?|cloudy|overcast)\b", re.I)
_TWILIGHT = re.compile(r"\b(?:twilight|dusk|sunset|night\s+(?:shot|photo|look|scene)|evening\s+look)\b", re.I)
_LAWN = re.compile(r"\b(?:lawn|grass|turf|yard)\b.*\b(?:green(?:er)?|fill|fix|patchy|brown|dead|dry|lush)\b|"
                   r"\b(?:green(?:er)?|lush)\b.*\b(?:lawn|grass|turf)\b", re.I)
_FIRE = re.compile(r"\b(?:fire|fires|flames?|fire\s+in\s+the\s+fireplace|light\s+the\s+fireplace)\b", re.I)
_SCREEN = re.compile(r"\b(?:tv|tvs|television|screen|monitor)\b", re.I)
_STAGING = re.compile(r"\b(?:virtual(?:ly)?\s+stag\w*|stag(?:e|ing)\b|furnish\w*|furniture)\b", re.I)
_COLOUR_WORDS = (
    r"white|off-white|cream|ivory|beige|tan|grey|gray|greige|taupe|black|charcoal|navy|blue|green|sage|olive|"
    r"yellow|gold|orange|red|pink|purple|brown|walnut|oak|espresso|natural"
)
_COLOUR = re.compile(
    rf"#[0-9a-f]{{3}}(?:[0-9a-f]{{3}})?\b|\bpaint(?:ed|ing)?\b|\brepaint\w*\b|\brecolou?r\w*\b|"
    rf"\bcolou?r\b|\bstain(?:ed)?\s+(?:the\s+)?(?:floors?|cabinets?|deck|wood)\b|"
    rf"\b(?:{_COLOUR_WORDS})\s+(?:walls?|floors?|flooring|cabinets?|doors?|trim|ceiling|siding|shutters?|countertops?)\b|"
    rf"\b(?:walls?|floors?|cabinets?|doors?|trim|ceiling|siding|shutters?)\s+(?:to\s+)?(?:{_COLOUR_WORDS})\b",
    re.I,
)
_ADJUST = re.compile(
    r"\b(?:bright(?:en|er|ness)?|lighten|lighter|darken|darker|exposure|expose|white\s+balance|warm(?:er|th)?|"
    r"cool(?:er)?|colou?r\s+correct\w*|vibran(?:t|ce)|saturat\w*|contrast|straighten\w*|level|crop|sharpen\w*|"
    r"sharper|denois\w*|noise|grain|hdr|tone|tones|highlights?|shadows?|clarity|dull|dim|cast|tint|vertical(?:s)?|"
    r"perspective|exposure)\b",
    re.I,
)
_REMOVE_VERB = re.compile(rf"\b(?:{_REMOVE})\b", re.I)
_ADD_VERB = re.compile(rf"\b(?:{_ADD})\b", re.I)
_REPAIR_VERB = re.compile(rf"\b(?:{_REPAIR})\b", re.I)
_CHANGE_VERB = re.compile(rf"\b(?:{_CHANGE})\b", re.I)

_SENTENCES = re.compile(r"[.;!?\n:]+|\(|\)")
_SPLIT = re.compile(r",\s*(?:and|or|then|but|also|plus)?\s*|\s+(?:and|&|then|also|plus|but|while|as\s+well\s+as)\s+", re.I)
# A part that opens with a preposition is context for the last object ("in front of the garage"), not a new one.
_PREPOSITION = re.compile(
    r"(?:in|on|at|behind|beside|next|near|along|under|over|by|of|for|with|from|to|between|inside|outside|around|"
    r"across|above|below|against|beyond|past|toward|towards|where|which|that|so)\b",
    re.I,
)
_KEEP = re.compile(
    r"\b(?:keep|keeping|preserve|preserving|retain|retaining|leave|leaving|maintain|maintaining|protect|"
    r"do\s+not|don'?t|must\s+stay|should\s+stay|stays?|intact|unchanged|untouched|as[- ]is|in\s+place|"
    r"except|unless|only\s+if)\b",
    re.I,
)

REASONS = {
    "surroundings": "Power lines, poles, towers, roads and neighbouring buildings are part of the property's "
    "surroundings; removing them misrepresents the property. " + TRUE_PICTURE,
    "condition": "Hiding damage or wear (cracks, stains, leaks, mould, rot) misrepresents the property's condition. "
    + TRUE_PICTURE,
    "view": "Changing or inventing the view misrepresents the property's location. " + TRUE_PICTURE,
    "feature": "Adding a feature the property does not have (a pool, a room, a fireplace) misrepresents it. "
    + TRUE_PICTURE,
    "structure": "Removing walls, windows, doors or built-ins changes the property's structure in the photo. "
    + TRUE_PICTURE,
    "proportions": "Stretching or enlarging a space misrepresents its size. " + TRUE_PICTURE,
    "gear": "The photographer's equipment is not part of the property; removing it is not an alteration.",
    "overlay": "Text and graphics laid over the photo (watermarks, lot lines) are not alterations of the property; "
    "lot lines should be marked approximate.",
    "sky": "A replaced or enhanced sky changes the scene; the photo is marked digitally altered (AB 723).",
    "twilight": "A day-to-twilight conversion changes the scene; the photo is marked digitally altered (AB 723).",
    "lawn": "Greening or filling a lawn changes how the property looks; the photo is marked digitally altered (AB 723).",
    "fire": "A fire added to a real fireplace is a common enhancement; the photo is marked digitally altered (AB 723).",
    "screen": "Replacing what a TV or monitor shows is a minor change; the photo is still marked digitally "
    "altered (AB 723).",
    "staging": "Virtual staging must be disclosed: the photo is marked virtually staged (AB 723).",
    "colour": "Changing paint, flooring or cabinet colour changes physical features; the photo is marked "
    "digitally altered (AB 723).",
    "removal": "Removing objects from the scene is a digital alteration under AB 723; the photo is marked as "
    "altered and paired with the unaltered original.",
    "addition": "Adding objects to the scene is a digital alteration under AB 723; the photo is marked as altered "
    "and paired with the unaltered original.",
    "change": "Changing what the scene shows is a digital alteration under AB 723; the photo is marked as altered.",
    "adjustment": "Exposure, colour correction, straightening, cropping and sharpening are not alterations under "
    "AB 723; no disclosure is needed.",
    "keep": "Asks to keep part of the scene as it is; nothing to disclose.",
    "unclear": "Not recognised as a specific edit; the agent decides from the photo and discloses any alteration.",
}

DECLINE_CATEGORIES = ("surroundings", "condition", "view", "feature", "structure", "proportions")


_BETWEEN = re.compile(r"\b(between\s+(?:the\s+)?[\w'-]+(?:\s+[\w'-]+)?)\s+and\s+", re.I)


def _clauses(text: str) -> list[str]:
    """Split a request into parts. A verb carries into the short list items right after it ("remove the bins and
    the cars" -> "remove the bins", "remove the cars"), never across sentences, never into a long phrase (context
    such as "in front of the neighbour's garage"), and a keep clause ("keep the windows, doors and trim intact")
    stops it. "between the kitchen and the dining room" stays whole."""
    text = _BETWEEN.sub(lambda m: m.group(1) + " \u2227 ", text or "")
    out: list[str] = []
    for sentence in _SENTENCES.split(text):
        verb = ""
        for raw in _SPLIT.split(sentence):
            p = raw.strip(" -:\t\"'").replace("\u2227", "and")
            if len(p) < 2:
                continue
            if _KEEP.search(p):
                verb = "keep"
                out.append(p)
                continue
            m = _VERB.search(p)
            if m:
                verb = m.group(0)
                out.append(p)
            elif verb and len(p.split()) <= 6 and not _PREPOSITION.match(p):
                out.append(f"{verb} {p}")
            else:
                verb = ""
                out.append(p)
    return out


def classify_clause(clause: str) -> PolicyItem:
    c = clause.strip()
    removing = bool(_REMOVE_VERB.search(c))
    repairing = bool(_REPAIR_VERB.search(c))
    changing = bool(_CHANGE_VERB.search(c))
    adding = bool(_ADD_VERB.search(c))
    editing = removing or repairing or changing or adding

    def item(verdict: Verdict, category: str) -> PolicyItem:
        return PolicyItem(text=c, verdict=verdict, category=category, reason=REASONS[category])

    if _KEEP.search(c) and not re.match(rf"\s*(?:{_REMOVE}|{_ADD})\b", c, re.I):
        return item("exempt", "keep")

    # Refusals first: a request that would misrepresent the property is declined whatever else it asks.
    if _SURROUNDINGS.search(c) and (removing or repairing or changing):
        return item("decline", "surroundings")
    if _CONDITION.search(c) and (removing or repairing or changing) and not _GEAR.search(c):
        return item("decline", "condition")
    if _VIEW.search(c) and editing:
        return item("decline", "view")
    if _FEATURE_ADD.search(c):
        return item("decline", "feature")
    if _STRUCTURE_REMOVE.search(c):
        return item("decline", "structure")
    if _PROPORTIONS.search(c):
        return item("decline", "proportions")

    if _GEAR.search(c) and removing:
        return item("exempt", "gear")
    if _OVERLAY.search(c):
        return item("overlay", "overlay")
    if _STAGING.search(c) and (adding or changing or "stag" in c.lower()):
        return item("disclose", "staging")
    if _TWILIGHT.search(c):
        return item("disclose", "twilight")
    if _SKY.search(c) and (changing or adding or removing or re.search(r"\bblue\b", c, re.I)):
        return item("disclose", "sky")
    if _LAWN.search(c):
        return item("disclose", "lawn")
    if _FIRE.search(c.lower().replace("fireplace", "")) or re.search(r"\blight\s+the\s+fireplace\b", c, re.I):
        return item("disclose", "fire")
    if _SCREEN.search(c) and (adding or changing or "image" in c.lower() or "picture" in c.lower()):
        return item("disclose", "screen")
    if _COLOUR.search(c) and not _ADJUST.search(c.replace("colour correct", "").replace("color correct", "")):
        return item("disclose", "colour")
    if _COLOUR.search(c) and (changing or adding):
        return item("disclose", "colour")
    if removing:
        return item("disclose", "removal")
    if _ADJUST.search(c):
        return item("exempt", "adjustment")
    if adding:
        return item("disclose", "addition")
    if changing or repairing:
        return item("disclose", "change")
    return item("unclear", "unclear")


def classify_request(text: str) -> PolicyResult:
    """Judge a customer's request part by part. The verdict is the worst part: decline > disclose > overlay >
    exempt > unclear."""
    items = [classify_clause(c) for c in _clauses(text)]
    if not items:
        return PolicyResult("unclear", [])
    worst = max(items, key=lambda i: SEVERITY[i.verdict]).verdict
    return PolicyResult(worst, items)


# Words in an edit's intent that say the edit hides something. "gone" and "without" are left out on purpose:
# removal intents use them for the removed object ("the car is gone and the cracked driveway looks natural").
_HIDING = re.compile(
    r"\b(?:hide|hides|hidden|hiding|cover|covers|covered|covering|conceal\w*|no\s+longer\s+(?:visible|shows?)|"
    r"fix(?:es|ed)?|repair(?:s|ed)?|patch(?:es|ed)?|smooth(?:ed)?\s+over|invisible)\b",
    re.I,
)


def check_edit(tool: str, args: dict) -> PolicyItem | None:
    """The loop's guard on one edit tool call in chat mode: a decline item when the call would misrepresent the
    property, else None. It judges what the call removes (`what`) or draws (`prompt`), plus an intent that says
    it hides damage, so a model that missed the policy in the request still cannot make the edit. Context in the
    intent ("the car in front of the neighbour's house is gone") is not judged as the object."""
    intent = str(args.get("intent") or "")
    texts: list[str] = []
    if tool == "remove_object":
        texts = [f"remove {args.get('what') or ''}"]
    elif tool == "generative_fill":
        prompt = str(args.get("prompt") or "")
        texts = [f"make it show {prompt}", f"add {prompt}"]
    for text in texts:
        hit = classify_clause(text)
        if hit.verdict == "decline":
            return PolicyItem(text, "decline", hit.category, hit.reason)
    if tool in ("remove_object", "generative_fill", "recolor_region", "place_on_screen", "adjust_exposure",
                "adjust_white_balance") and _CONDITION.search(intent) and _HIDING.search(intent):
        return PolicyItem(intent, "decline", "condition", REASONS["condition"])
    return None
