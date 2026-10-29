"""Fair-housing wording check for listing copy.

The Fair Housing Act (and California's FEHA, which adds source of income among others) bars advertising that
states a preference for or against buyers by race, colour, religion, sex, disability, familial status or
national origin. Copy should describe the property, not who should live in it. This is a word-list lint with
suggested rewrites, the way MLS systems and brokerages screen remarks; it is not legal advice.

`avoid` terms state or imply a preference about people; `review` terms are commonly flagged and usually have a
plainer alternative.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

Severity = str  # "avoid" | "review"


@dataclass
class Flag:
    phrase: str  # the words found
    start: int
    end: int
    severity: Severity
    suggestion: str
    why: str


_RULES: list[tuple[re.Pattern, Severity, str, str]] = [
    (re.compile(r"\b(?:perfect|ideal|great|wonderful|made)\s+for\s+(?:a\s+)?(?:young\s+)?(?:families|family|kids|children|"
                r"couples?|singles?|retirees|seniors|students|professionals|newlyweds|empty[- ]nesters|bachelors?|"
                r"christians?|a\s+growing\s+family)\b", re.I),
     "avoid", "Describe the space instead (\"three bedrooms near the park\").",
     "Says who should live there (familial status, age, religion)."),
    (re.compile(r"\bfamily[- ]friendly\b|\bfamily\s+neighbou?rhood\b|\bkid[- ]friendly\b", re.I),
     "avoid", "Name the feature (\"fenced yard\", \"cul-de-sac\").", "Signals a preference for families with children."),
    (re.compile(r"\b(?:no|not\s+suitable\s+for)\s+(?:kids|children|families|students)\b|\badults?\s+only\b|"
                r"\bsingles?\s+only\b|\bmature\s+(?:couple|person|adults?|individuals?)\b|\bempty[- ]nesters?\b|"
                r"\bbachelor\s+pad\b", re.I),
     "avoid", "Remove it, or describe the home (\"low-maintenance single-level\").", "Excludes people by familial status or age."),
    (re.compile(r"\b(?:christian|catholic|jewish|muslim|hindu|buddhist|mormon)\s+(?:home|family|neighbou?rhood|community|area)\b|"
                r"\bnear\s+(?:the\s+|a\s+)?(?:church|synagogue|mosque|temple)\b", re.I),
     "avoid", "Name distances to amenities neutrally, or leave places of worship out.", "Implies a religious preference."),
    (re.compile(r"\b(?:exclusive|restricted|private)\s+(?:community|neighbou?rhood|area|enclave)\b", re.I),
     "review", "\"Gated community\" or \"quiet street\", if true.", "Can read as excluding people."),
    (re.compile(r"\b(?:white|black|hispanic|latino|asian|caucasian|ethnic|integrated|segregated|traditional)\s+"
                r"(?:neighbou?rhood|community|area|families|people|residents)\b|\bminorit(?:y|ies)\b", re.I),
     "avoid", "Remove it.", "Refers to race, colour or national origin."),
    (re.compile(r"\b(?:handicap(?:ped)?|crippled|wheelchair[- ]bound)\b", re.I),
     "review", "\"Accessible\", \"wheelchair accessible\", \"step-free entry\".", "Describe accessibility features, not people."),
    (re.compile(r"\b(?:able[- ]bodied|physically\s+fit|no\s+wheelchairs?)\b", re.I),
     "avoid", "Remove it.", "Excludes people with disabilities."),
    (re.compile(r"\bsafe\s+(?:neighbou?rhood|area|community|street|location)\b|\bcrime[- ]free\b", re.I),
     "review", "Describe specific features (\"quiet cul-de-sac\", \"gated entry\").", "Subjective safety claims can read as steering."),
    (re.compile(r"\bmaster\s+(?:bed(?:room)?|suite|bath(?:room)?|closet|retreat)s?\b", re.I),
     "review", "\"Primary bedroom\", \"primary suite\".", "Many MLSs now ask for \"primary\" instead of \"master\"."),
    (re.compile(r"\bman\s+cave\b|\bshe[- ]shed\b", re.I),
     "review", "\"Bonus room\", \"den\", \"studio\".", "Gendered wording; describe the room."),
    (re.compile(r"\bmother[- ]in[- ]law\s+(?:suite|unit|apartment|quarters)\b|\bnanny\s+(?:suite|quarters)\b", re.I),
     "review", "\"Guest suite\", \"accessory dwelling unit\".", "Familial wording; describe the space."),
    (re.compile(r"\bno\s+(?:section\s*8|housing\s+vouchers?|vouchers?)\b", re.I),
     "avoid", "Remove it.", "Source-of-income discrimination is unlawful in California."),
    (re.compile(r"\benglish[- ]speaking\b|\bamerican\s+family\b|\bimmigrants?\b|\bforeigners?\b", re.I),
     "avoid", "Remove it.", "Refers to national origin."),
    (re.compile(r"\bstudents?\s+welcome\b|\bseniors?\s+(?:only|community|living)\b", re.I),
     "review", "Only for housing that qualifies as 55+; otherwise remove.", "States an age or occupancy preference."),
]


def lint(text: str) -> list[Flag]:
    flags: list[Flag] = []
    for pattern, severity, suggestion, why in _RULES:
        for m in pattern.finditer(text or ""):
            flags.append(Flag(m.group(0), m.start(), m.end(), severity, suggestion, why))
    flags.sort(key=lambda f: f.start)
    return flags


def lint_dict(text: str) -> dict:
    flags = lint(text)
    return {
        "ok": not any(f.severity == "avoid" for f in flags),
        "flags": [asdict(f) for f in flags],
        "note": "A wording check against common fair-housing guidance, not legal advice.",
    }
