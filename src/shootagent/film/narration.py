"""Narration: the TTS request for a script, script checks, and where each segment falls in the audio.

One TTS request covers a whole narration in one language (Gemini's free tier allows few requests a day,
and one take keeps the voice even), so the audio comes back as one file. Each shot must play while its
segment is spoken, so the segment boundaries are found afterwards: the audio is transcribed with word
timestamps and the script's words are matched to the heard words in order.
"""
from __future__ import annotations

import difflib
import re

from ..config import Settings
from ..store import Listing
from .media import SpeechRequest

# The voice direction sent with every narration. Only the voice (Sulafat) was compared in the lab; this
# wording was not, and the lab's own style prompt was not recorded.
STYLE = ("Warm, unhurried real-estate narration, like a friendly listing agent giving a private tour. "
         "Natural pauses between sentences.")

# Speaking rates for the length check, words per second. Measured on the lab's two Sulafat takes on
# explore/media-tests: E1 English, 88 words in 32.6 s (2.7/s); E2 Spanish, 89 words in 39.7 s (2.2/s).
WORDS_PER_SECOND = {"en": 2.7, "es": 2.2}
TARGET_SECONDS = (28.0, 45.0)  # the brief asks for about 30-40 s; the bounds allow one sentence either way
# A shot that holds longer than this reads as a slideshow. The minimum length is lowered to what the planned
# shots fill, so a short plan is never padded (the demo's first script stuffed the listing facts into its last
# line to reach 28 s, and the game room held for about 8 s).
MAX_SHOT_SECONDS = 4.5
PLAN_FILL = 0.8  # the narration may run this much shorter than the planned shot lengths add up to
# The first segment is the hook. 3 s is 8 English words at the measured rate, so the hook's point is made
# inside the first 2 s. Translations keep the segment structure and are not held to it.
HOOK_MAX_SECONDS = 3.0


def speech_request(settings: Settings, language: str, text: str) -> SpeechRequest:
    return SpeechRequest(model=settings.tts_model, voice=settings.tts_voice, style=STYLE, language=language,
                         text=text.strip())


def estimated_seconds(text: str, language: str) -> float:
    return len(_tokens(text)) / WORDS_PER_SECOND.get(language, 2.4)


# Listing facts a script may state only when the listing fields give them. Visual facts ("three tall
# windows") come from the photos and are the agent's to describe; counts of rooms, sizes and prices are not
# visible in photos and are the usual invention.
_NUM = r"(\d+(?:[.,]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|dos|tres|cuatro|cinco|seis)"  # not uno/una: Spanish articles
FACT_PATTERNS = [
    (re.compile(_NUM + r"[\s-]*(bed|bedroom|bedrooms|beds|rec[aá]maras?|habitaciones?|dormitorios?)\b", re.I), "beds"),
    (re.compile(_NUM + r"[\s-]*(bath|baths|bathroom|bathrooms|ba[nñ]os?)\b", re.I), "baths"),
    (re.compile(r"\b\d[\d,.]*\s*(sq\.?\s*ft|square\s+feet|sqft|pies\s+cuadrados|m2|metros\s+cuadrados)", re.I), "sqft"),
    (re.compile(r"(\$|US\$|€)\s*\d|\b\d[\d,.]*\s*(dollars|d[oó]lares|k\b|million|mill[oó]n)", re.I), "price"),
]


def unsupported_facts(text: str, listing: Listing) -> list[str]:
    """Claims about beds, baths, size or price that the listing fields do not supply."""
    have = {"beds": listing.beds is not None, "baths": listing.baths is not None, "sqft": listing.sqft is not None,
            "price": bool((listing.price or "").strip())}
    out = []
    for pattern, field in FACT_PATTERNS:
        for m in pattern.finditer(text):
            if not have[field]:
                out.append(f"'{m.group(0)}' ({field} is not in the listing)")
    return out


def script_problems(segments: list[str], language: str, listing: Listing, n_shots: int, hook: bool = True,
                    shot_seconds: list[float] | None = None) -> list[str]:
    """Reasons a script cannot be narrated as written; empty when it can. `hook=False` for translations, which
    keep the first script's structure and are held to neither the hook nor the per-shot length.
    `shot_seconds`, the planned shot lengths, lower the minimum to what the shots fill."""
    problems = []
    low = TARGET_SECONDS[0]
    if shot_seconds:
        low = min(low, PLAN_FILL * sum(shot_seconds))
    if len(segments) != n_shots:
        problems.append(f"write exactly one segment per shot: {n_shots} shots, {len(segments)} segments")
    if any(not s.strip() for s in segments):
        problems.append("every segment needs words; a silent shot drifts out of sync with the narration")
    text = " ".join(segments)
    secs = estimated_seconds(text, language)
    if not low <= secs <= TARGET_SECONDS[1]:
        problems.append(f"the script reads in about {secs:.0f} s; aim for {low:.0f}-{TARGET_SECONDS[1]:.0f} s "
                        f"(about {WORDS_PER_SECOND.get(language, 2.4):g} words per second); never pad a line to reach it")
    if hook and segments and estimated_seconds(segments[0], language) > HOOK_MAX_SECONDS:
        limit = int(HOOK_MAX_SECONDS * WORDS_PER_SECOND.get(language, 2.4))
        problems.append(f"the first segment is the hook and must make its point in about 2 s: at most {limit} words")
    if hook:
        rate = WORDS_PER_SECOND.get(language, 2.4)
        long = [i for i, s in enumerate(segments) if estimated_seconds(s, language) > MAX_SHOT_SECONDS]
        if long:
            problems.append(f"segments {long} read longer than a shot should hold ({MAX_SHOT_SECONDS:g} s, about "
                            f"{int(MAX_SHOT_SECONDS * rate)} words): shorten them; listing facts are on the cards")
    problems += [f"remove {f}" for f in unsupported_facts(text, listing)]
    return problems


# ---- timing -------------------------------------------------------------------------------------------------

def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def segment_starts(segments: list[str], words: list[dict], total: float) -> list[float]:
    """Start time (s) of each segment in the narration audio.

    `words` are transcript words in order, each {"w": text, "s": start, "e": end}. The script's tokens are
    matched to the heard tokens with difflib; a segment starts at the first heard word matched to one of its
    tokens. A segment with no matched token (the recogniser heard "3" for "three", say) is placed between its
    neighbours in proportion to its length. The first segment always starts at 0, so the picture covers the
    breath before the first word."""
    seg_tok = [_tokens(s) for s in segments]
    script, owner = [], []
    for i, toks in enumerate(seg_tok):
        script += toks
        owner += [i] * len(toks)
    heard, heard_word = [], []
    for j, w in enumerate(words):
        for t in _tokens(w.get("w", "")):
            heard.append(t)
            heard_word.append(j)
    match: dict[int, int] = {}
    sm = difflib.SequenceMatcher(None, script, heard, autojunk=False)
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            match[blk.a + k] = heard_word[blk.b + k]
    starts: list[float | None] = []
    pos = 0
    for toks in seg_tok:
        hit = next((match[pos + k] for k in range(len(toks)) if pos + k in match), None)
        starts.append(float(words[hit]["s"]) if hit is not None else None)
        pos += len(toks)
    starts[0] = 0.0
    # Fill unmatched segments by length between the known neighbours; keep the order strictly increasing.
    lengths = [max(1, len(t)) for t in seg_tok]
    i = 0
    while i < len(starts):
        if starts[i] is not None:
            i += 1
            continue
        j = i
        while j < len(starts) and starts[j] is None:
            j += 1
        # Segments i-1 .. j-1 share the time from lo (segment i-1's start) to hi (segment j's start).
        lo = starts[i - 1]
        hi = starts[j] if j < len(starts) else total
        run = lengths[i - 1:j]
        for k in range(i, j):
            starts[k] = lo + (hi - lo) * sum(run[: k - i + 1]) / sum(run)
        i = j
    out = [float(s) for s in starts]
    for k in range(1, len(out)):
        out[k] = max(out[k], out[k - 1] + 0.5)  # a mismatch must not give a shot no screen time
    return [min(s, max(0.0, total - 0.5 * (len(out) - k))) for k, s in enumerate(out)]


def proportional_starts(segments: list[str], total: float) -> list[float]:
    """Segment starts by word count, for when no transcript is available."""
    lengths = [max(1, len(_tokens(s))) for s in segments]
    starts, acc = [], 0
    for n in lengths:
        starts.append(total * acc / sum(lengths))
        acc += n
    return starts
