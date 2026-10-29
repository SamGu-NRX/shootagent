"""Deterministic novelty measures: how far a reel's words and structure sit from the usual listing reel.

The judges' creativity scores are opinions; these numbers are not. Nothing here calls a model or knows which
concept is the template: every concept and reel is scored the same way.

Text novelty. A concept's lines (hook text and spoken hook, on-screen texts that are more than a room label,
card text, the ending, the format and the logline) are compared one by one with a corpus of overused
real-estate reel hooks, captions and structural tropes (`corpus/cliches.json` when present, else a built-in
list). Lines are scored separately because one embedding of the whole script blurs into "a home tour": in a
test the template and an original concept landed within 0.01 of each other. Facts are masked first (anything
with a digit, and the listing's address), so "Just listed: Unit 4B, $489,000" is compared as "Just listed".
- `cliche_similarity`: the weighted mean over lines of each line's cosine to its nearest cliche (the hook
  counts double, the ending 1.5x); `nearest_cliche` is the nearest cliche of the most cliched line.
- `novelty = clip(0.1 + 0.8 * (stock - sim) / (stock - fresh), 0, 1)`, where `stock` and `fresh` are the mean
  similarities of two built-in probe sets scored alongside: conventional listing-reel scripts and original,
  specific ones (about other homes). A reel as cliched as an ordinary listing reel scores about 0.1, one as far
  from the corpus as the original probes about 0.9. Anchoring on probes rather than fixed numbers keeps the
  scale meaningful whichever embedding and corpus are in use.

Structure novelty (0-1): distance from the standard walkthrough, a weighted mean of the shot order's
Kendall-tau distance from the showing order in reel/spec.py ROOM_ORDER (0.30), opening on something other than
the standard first room (0.15), the share of shots whose text is more than a room label (0.25), and devices
(0.30, equal parts): voice, animation cards, transitions other than crossfades, and an ending other than the
price-and-address end card (loop, question, price reveal or punchline).

Diversity: mean pairwise cosine distance among the room's round-1 concepts and among the single agent's
concepts at equal n (the larger set is subsampled; mean of seeded draws). Creativity map: PCA (numpy SVD) of
the cliches and every concept to 2-D, scaled to [-1, 1] with the cliches on the left. A concept's vector is the
normalised weighted mean of its line vectors.

Embeddings: sentence-transformers/all-MiniLM-L6-v2 through transformers (mean pooling, CPU), downloaded from
Hugging Face on first use; if that fails, character 3-5-gram TF-IDF fitted on the texts at hand. The method is
recorded in the map.
"""
from __future__ import annotations

import json
import math
import random
import re
import threading
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel

from ..reel.spec import ROOM_ORDER, room_rank
from .schema import CardSpec, CreativityMap, MapPoint, NoveltyScore, Reel, ReelConcept

CLICHES_PATH = Path(__file__).parent / "corpus" / "cliches.json"
MINILM = "sentence-transformers/all-MiniLM-L6-v2"
Method = Literal["auto", "minilm", "tfidf"]
METHOD_NAMES = {"minilm": "all-MiniLM-L6-v2 + PCA", "tfidf": "char n-gram tf-idf + PCA"}
# Which embedding measure() uses when not told; tests set "tfidf" so they never download a model.
DEFAULT_METHOD: Method = "auto"
W_HOOK, W_ENDING = 2.0, 1.5
Line = tuple[str, float]  # (text, weight)

# Overused listing-reel language, for when corpus/cliches.json is missing: hooks, captions and structural tropes
# seen on most agents' Instagram and TikTok listing videos.
FALLBACK_CLICHES: list[dict[str, str]] = [
    *({"text": t, "kind": "hook"} for t in [
        "Just listed!",
        "New listing alert!",
        "Welcome home.",
        "Your dream home awaits.",
        "Stop scrolling if you're looking for a home.",
        "POV: you just found your dream home.",
        "Wait until you see the kitchen.",
        "Wait for it...",
        "Come tour this stunning home with me.",
        "Let me show you around.",
        "You have to see this one.",
        "Is this the best deal in town?",
        "Guess the price of this home.",
        "Would you live here?",
        "Rate this home from 1 to 10.",
        "Here's what $500K gets you right now.",
        "This home won't last long!",
        "If you're looking for a sign, this is it.",
        "Imagine waking up here every morning.",
        "Picture yourself living here.",
        "The one you've been waiting for.",
        "Don't miss this one!",
        "Your search ends here.",
        "Coming soon!",
        "Open house this weekend!",
    ]),
    *({"text": t, "kind": "caption"} for t in [
        "Stunning move-in ready home.",
        "Nestled in the heart of the neighborhood.",
        "Luxury living at its finest.",
        "Open-concept living with natural light galore.",
        "Chef's kitchen with modern finishes.",
        "An entertainer's dream.",
        "Spa-like bathroom retreat.",
        "Bright and airy throughout.",
        "Cozy and charming.",
        "Turnkey and updated throughout.",
        "Pride of ownership shows.",
        "Priced to sell.",
        "Hidden gem.",
        "Perfect starter home for first-time buyers.",
        "Great investment opportunity.",
        "Steps from shopping and dining.",
        "Make this house your home.",
        "Location, location, location.",
        "Must see!",
        "Schedule your private tour today.",
        "DM me for details.",
        "Link in bio for more.",
        "Comment TOUR and I'll send you the details.",
        "Call or text to book a showing.",
        "Under contract in 3 days!",
    ]),
    *({"text": t, "kind": "structure"} for t in [
        "Room-by-room walkthrough with the room name on each shot.",
        "Opens on the front door or exterior, then entry, living room, kitchen, bedroom, bathroom.",
        "Title card: just listed, the address and the price.",
        "Beds, baths and square feet card.",
        "End card with the price, address and schedule a tour.",
        "Slow pans and push-ins with crossfades over upbeat stock music.",
        "Agent walks in and says come with me.",
        "Trending audio with text overlays on each room.",
        "Countdown of the top five features.",
        "Before and after renovation reveal.",
        "Things I love about this home, one per shot.",
    ]),
]

# Calibration probes, scored with the corpus on every run (see the module docstring). Neither set is about the
# demo listing: they anchor the scale, they do not score it. Stock: what ordinary listing reels say.
STOCK_PROBES: list[list[Line]] = [
    [("Just listed!", W_HOOK), ("3 beds, 2 baths, 1,850 sq ft", 1), ("Schedule your private tour today.", W_ENDING),
     ("classic listing walkthrough", 1), ("A room-by-room tour of the home with the price, the stats and a call to book a showing.", 1)],
    [("New listing alert!", W_HOOK), ("Welcome to your next home.", W_HOOK), ("Call today for a private showing.", W_ENDING),
     ("listing tour", 1), ("The agent walks viewers through a bright, updated family home, ending on the price.", 1)],
    [("Welcome home", W_HOOK), ("Open-concept living", 1), ("Updated kitchen", 1), ("Don't miss it. DM me for details.", W_ENDING),
     ("highlights walkthrough set to upbeat music", 1), ("The best features of a move-in ready condo, room by room.", 1)],
    [("Come tour this stunning home with me!", W_HOOK), ("Let's take a look inside.", W_HOOK),
     ("Open house this Saturday. Link in bio.", W_ENDING), ("agent-led home tour", 1),
     ("A walkthrough of a renovated townhouse from the front door to the backyard.", 1)],
]
# Fresh: original, specific scripts, the kind a good creative team writes.
FRESH_PROBES: list[list[Line]] = [
    [("The dog picked this house before we did.", W_HOOK), ("Watch where she goes first.", W_HOOK),
     ("Straight to the window seat.", 1), ("Your move, humans.", W_ENDING), ("a dog's-eye tour at knee height", 1),
     ("The family dog chooses her favourite spot in each room.", 1)],
    [("Every light switch in this loft, ranked.", W_HOOK), ("Number one dims the kitchen from the bathtub.", 1),
     ("Which switch would you fight for?", W_ENDING), ("a deadpan ranking", 1),
     ("A straight-faced countdown of the loft's light switches.", 1)],
    [("Thirteen steps up. Twelve steps down.", W_HOOK), ("Count them yourself.", W_HOOK), ("One of them is a drawer.", 1),
     ("Watch it again and find it.", W_ENDING), ("a puzzle reel", 1), ("A staircase puzzle that rewards a second watch.", 1)],
    [("The 1962 wallpaper has seen things.", W_HOOK), ("It remembers the avocado fridge.", 1),
     ("It would like to retire now.", W_ENDING), ("the wallpaper narrates its own goodbye", 1),
     ("The vintage wallpaper says farewell to the house it lived in.", 1)],
]

STRUCTURE_WEIGHTS = {"order": 0.30, "opener": 0.15, "text": 0.25, "devices": 0.30}
_LABEL_WORDS = ({w for key in ROOM_ORDER for w in key.split()}
                | {"hall", "hallway", "nook", "suite", "closet", "room", "area", "bed", "bath", "half", "powder",
                   "master", "guest", "main", "upstairs", "downstairs", "and", "the", "with", "of", "view"})
_FACT_WORDS = {"sq", "ft", "sqft", "square", "feet", "foot", "bed", "beds", "bd", "bath", "baths", "ba", "br",
               "bedroom", "bedrooms", "bathroom", "bathrooms", "price", "unit", "apt", "suite", "the", "and"}
_CARD_KINDS = {"none", "price_tag", "stat_count_up", "list_reveal", "big_quote", "typing_chat"}


# ---- the cliche corpus -------------------------------------------------------------------------------------


def load_cliches(path: Path = CLICHES_PATH) -> tuple[list[dict[str, str]], str]:
    """(cliches as {"text", "kind", "source"}, where they came from)."""
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = (raw.get("cliches") or raw.get("items") or []) if isinstance(raw, dict) else raw
            out = [{"text": str(i["text"]).strip(), "kind": str(i.get("kind", "")), "source": str(i.get("source", ""))}
                   for i in items if isinstance(i, dict) and str(i.get("text", "")).strip()]
            if out:
                return out, f"{path.name} ({len(out)} entries)"
        except (OSError, ValueError, KeyError, TypeError):
            pass
    fallback = [{**c, "source": "built-in fallback"} for c in FALLBACK_CLICHES]
    return fallback, f"built-in fallback list ({len(fallback)} entries)"


# ---- the lines that are scored ------------------------------------------------------------------------------


def is_label(text: str | None, room: str | None = None) -> bool:
    """True when the text is empty or only names a room ("Kitchen", "Primary Bedroom 2")."""
    words = re.findall(r"[a-z]+|\d+", (text or "").lower())
    vocab = _LABEL_WORDS | set(re.findall(r"[a-z]+", (room or "").lower()))
    return all(w in vocab or w.isdigit() for w in words) and len(words) <= 5


def _card_line(card: CardSpec | None) -> str | None:
    if card is None or card.kind == "none":
        return None
    return " ".join(x for x in [card.value, card.text, *(card.items or [])] if x) or None


def _timeline_card(card: str | None) -> str | None:
    """A timeline's card field may be a bare kind ("price_tag") or "kind: text"; keep only the text."""
    text = re.sub(r"^\s*(" + "|".join(_CARD_KINDS) + r")\b[\s:,-]*", "", card or "")
    return text.strip() or None


def _dedupe(lines: list[tuple[str | None, float]]) -> list[Line]:
    seen, out = set(), []
    for text, w in lines:
        t = re.sub(r"\s+", " ", text or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append((t, w))
    return out


def concept_lines(c: ReelConcept) -> list[Line]:
    """Hook text and spoken hook, on-screen texts beyond room labels, cards, ending, format and logline."""
    lines: list[tuple[str | None, float]] = [(c.hook.text, W_HOOK), (c.hook.voiceover, W_HOOK)]
    for b in c.beats:
        if not is_label(b.text):
            lines.append((b.text, 1.0))
        lines.append((_card_line(b.card), 1.0))
    lines += [(c.ending.text, W_ENDING), (c.ending.voiceover, W_ENDING), (c.format, 1.0), (c.logline, 1.0)]
    return _dedupe(lines)


def reel_lines(reel: Reel, concept: ReelConcept | None) -> list[Line]:
    """The same lines read from what the rendered reel shows (its timeline), plus the concept's format and
    logline. Falls back to the concept when the reel has no timeline."""
    if not reel.timeline:
        return concept_lines(concept) if concept else _dedupe([(reel.title, 1.0)])
    order = {"hook": 0, "beat": 1, "ending": 2}
    lines: list[tuple[str | None, float]] = []
    for e in sorted(reel.timeline, key=lambda e: (order[e.kind], e.t0)):
        w = W_HOOK if e.kind == "hook" else W_ENDING if e.kind == "ending" else 1.0
        if not (e.kind == "beat" and is_label(e.text)):
            lines.append((e.text, w))
        lines.append((_timeline_card(e.card), 1.0))
        if e.kind != "beat":
            lines.append((e.voiceover, w))
    if concept is not None:
        lines += [(concept.format, 1.0), (concept.logline, 1.0)]
    return _dedupe(lines)


def mask_facts(text: str, phrases: list[str] = ()) -> str:
    """The text without the listing's address (or any given phrase) and without tokens containing a digit."""
    t = text
    for p in sorted((p.strip() for p in phrases if p and p.strip()), key=len, reverse=True):
        t = re.sub(re.escape(p), " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\S*\d\S*", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .,:;-|/")


def scorable(text: str) -> bool:
    """Whether a masked line still says something: a word of 3+ letters that is not a fact unit."""
    return any(len(w) >= 3 and w not in _FACT_WORDS for w in re.findall(r"[a-z']+", text.lower()))


# ---- structure ----------------------------------------------------------------------------------------------


def order_distance(ranks: list[int]) -> float:
    """Normalised Kendall-tau distance from the sorted order: the share of discordant pairs among pairs of
    shots from different rooms (0 = the standard showing order, 1 = exactly reversed)."""
    disc = total = 0
    for a, b in combinations(ranks, 2):
        if a != b:
            total += 1
            disc += a > b
    return disc / total if total else 0.0


def _weighted(parts: dict[str, float]) -> float:
    return round(sum(STRUCTURE_WEIGHTS[k] * v for k, v in parts.items()), 3)


def structure_parts(c: ReelConcept, rooms: dict[str, str]) -> dict[str, float]:
    """The four structure components, each 0-1. `rooms` maps view id to room name."""
    beats = c.beats
    ranks = [room_rank(rooms.get(b.view)) for b in beats]
    standard_first = min((room_rank(r) for r in rooms.values()), default=0)
    voice = c.voice.voice != "none" and bool(c.hook.voiceover or c.ending.voiceover or any(b.voiceover for b in beats))
    cards = any(b.card is not None and b.card.kind != "none" for b in beats)
    moves = [b.transition != "crossfade" for b in beats[1:]]
    devices = [float(voice), float(cards), float(np.mean(moves)) if moves else 0.0, float(c.ending.style != "end_card")]
    return {
        "order": order_distance(ranks),
        "opener": float(bool(ranks) and ranks[0] > standard_first),
        "text": float(np.mean([not is_label(b.text, rooms.get(b.view)) for b in beats])) if beats else 0.0,
        "devices": float(np.mean(devices)),
    }


def structure_novelty(c: ReelConcept, rooms: dict[str, str]) -> float:
    return _weighted(structure_parts(c, rooms))


def reel_structure_novelty(reel: Reel, rooms: dict[str, str]) -> float:
    """Structure from the timeline alone, for a reel whose concept is unknown. Transitions are not recorded
    there, so they count as crossfades, and an ending that asks for a tour, call or DM counts as an end card."""
    beats = sorted((e for e in reel.timeline if e.kind == "beat"), key=lambda e: e.t0)
    ranks = [room_rank(rooms.get(e.view or "")) for e in beats]
    standard_first = min((room_rank(r) for r in rooms.values()), default=0)
    ending = next((e for e in reel.timeline if e.kind == "ending"), None)
    devices = [float(reel.has_voice), float(bool(reel.cards) or any(e.card for e in beats)), 0.0,
               float(ending is not None and not re.search(r"\b(tour|call|dm|link|showing)\b", (ending.text or "").lower()))]
    return _weighted({
        "order": order_distance(ranks),
        "opener": float(bool(ranks) and ranks[0] > standard_first),
        "text": float(np.mean([not is_label(e.text, rooms.get(e.view or "")) for e in beats])) if beats else 0.0,
        "devices": float(np.mean(devices)),
    })


# ---- embeddings ---------------------------------------------------------------------------------------------

_minilm: tuple | None = None
_minilm_error: str | None = None
_minilm_lock = threading.Lock()


def _load_minilm():
    """Tokenizer and model, loaded once: from the local Hugging Face cache, else downloaded."""
    global _minilm, _minilm_error
    if _minilm is None and _minilm_error is None:
        try:
            from transformers import AutoModel, AutoTokenizer

            try:
                tok = AutoTokenizer.from_pretrained(MINILM, local_files_only=True)
                model = AutoModel.from_pretrained(MINILM, local_files_only=True)
            except OSError:
                tok, model = AutoTokenizer.from_pretrained(MINILM), AutoModel.from_pretrained(MINILM)
            model.eval()
            _minilm = (tok, model)
        except Exception as e:  # noqa: BLE001 - offline, no torch, a bad download: all fall back to tf-idf
            _minilm_error = f"{type(e).__name__}: {str(e)[:200]}"
    return _minilm


def warm() -> None:
    """Load the embedding model ahead of time, e.g. on a background thread while the panel works."""
    if DEFAULT_METHOD != "tfidf":
        with _minilm_lock:
            _load_minilm()


def minilm_embed(texts: list[str]) -> np.ndarray:
    """Mean-pooled, L2-normalised all-MiniLM-L6-v2 embeddings, (n, 384)."""
    import torch

    with _minilm_lock:
        loaded = _load_minilm()
        if loaded is None:
            raise RuntimeError(f"all-MiniLM-L6-v2 unavailable: {_minilm_error}")
        tok, model = loaded
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), 64):
                batch = tok(texts[i:i + 64], padding=True, truncation=True, max_length=256, return_tensors="pt")
                hidden = model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
                mean = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                out.append(torch.nn.functional.normalize(mean, dim=1).numpy())
    return np.vstack(out).astype(np.float32)


def _char_ngrams(text: str, lo: int = 3, hi: int = 5) -> Counter:
    t = " " + re.sub(r"\s+", " ", text.lower()).strip() + " "
    return Counter(t[i:i + n] for n in range(lo, hi + 1) for i in range(len(t) - n + 1))


def tfidf_embed(texts: list[str]) -> np.ndarray:
    """Character 3-5-gram TF-IDF (sublinear tf, smoothed idf) fitted on `texts`, L2-normalised rows."""
    counts = [_char_ngrams(t) for t in texts]
    vocab: dict[str, int] = {}
    for c in counts:
        for g in c:
            vocab.setdefault(g, len(vocab))
    x = np.zeros((len(texts), max(1, len(vocab))), np.float32)
    for i, c in enumerate(counts):
        for g, k in c.items():
            x[i, vocab[g]] = 1.0 + math.log(k)
    df = (x > 0).sum(0)
    x *= (np.log((1 + len(texts)) / (1 + df)) + 1.0).astype(np.float32)
    return x / np.clip(np.linalg.norm(x, axis=1, keepdims=True), 1e-9, None)


def embed(texts: list[str], method: Method = "auto") -> tuple[np.ndarray, str]:
    """(embeddings, "minilm" or "tfidf"). "auto" tries MiniLM and falls back to tf-idf."""
    if method in ("auto", "minilm"):
        try:
            return minilm_embed(texts), "minilm"
        except Exception:  # noqa: BLE001
            if method == "minilm":
                raise
    return tfidf_embed(texts), "tfidf"


# ---- scores, diversity, map ---------------------------------------------------------------------------------


def rescale(sim: np.ndarray | float, stock: float, fresh: float) -> np.ndarray:
    """Novelty from cliche similarity: 0.1 at the stock probes' similarity, 0.9 at the fresh probes', clipped."""
    span = max(stock - fresh, 1e-6)
    return np.clip(0.1 + 0.8 * (stock - np.asarray(sim, dtype=np.float64)) / span, 0.0, 1.0)


def mean_pairwise_distance(vectors: np.ndarray) -> float:
    n = len(vectors)
    if n < 2:
        return 0.0
    sims = vectors @ vectors.T
    return float(1.0 - sims[np.triu_indices(n, 1)].mean())


def diversity(room: np.ndarray, single: np.ndarray, seed: int = 7, draws: int = 200) -> dict[str, float]:
    """Mean pairwise cosine distance in each set at equal n; the larger set is subsampled (mean of seeded draws).
    A set with fewer than two concepts has no entry."""
    n = min(len(room), len(single))
    if n < 2:
        return {k: round(mean_pairwise_distance(v), 4) for k, v in (("room", room), ("single_agent", single)) if len(v) >= 2}
    rng = random.Random(seed)

    def at_n(vectors: np.ndarray) -> float:
        if len(vectors) == n:
            return mean_pairwise_distance(vectors)
        return float(np.mean([mean_pairwise_distance(vectors[rng.sample(range(len(vectors)), n)]) for _ in range(draws)]))

    return {"room": round(at_n(room), 4), "single_agent": round(at_n(single), 4)}


def pca_2d(x: np.ndarray) -> np.ndarray:
    """Rows of `x` projected on their first two principal components (numpy SVD), (n, 2)."""
    if len(x) < 2:
        return np.zeros((len(x), 2))
    xc = x - x.mean(0, keepdims=True)
    _, _, vt = np.linalg.svd(xc, full_matrices=False)
    out = xc @ vt[:2].T
    return np.pad(out, ((0, 0), (0, 2 - out.shape[1])))


class NoveltyReport(BaseModel):
    scores: list[NoveltyScore]
    diversity: dict[str, float]
    map: CreativityMap
    method: str  # "minilm" or "tfidf"
    corpus: str  # where the cliches came from, and how many
    anchors: dict[str, float]  # the stock and fresh probes' mean cliche similarity


def _map_kind(c: ReelConcept, single_ids: set[str], finalists: set[str], baselines: set[str]) -> str:
    if c.id in baselines or c.agent == "template" or c.id == "template":
        return "baseline"
    if c.id in single_ids or c.agent == "single_agent":
        return "single_agent"
    if c.id in finalists or c.status == "finalist":
        return "finalist"
    return "concept"


class _Scorer:
    """Embeds every line once, then scores scripts (lists of weighted lines) against the corpus."""

    def __init__(self, cliches: list[str], scripts: list[list[Line]], mask: list[str], method: Method):
        self.scripts = [[(t, w, m) for t, w in s if scorable(m := mask_facts(t, mask))] for s in scripts]
        texts = cliches + [m for s in self.scripts for _, _, m in s]
        vectors, self.method = embed(texts, method)
        self.cliches, self.k_vec = cliches, vectors[:len(cliches)]
        line_vec = vectors[len(cliches):]
        self.sims = line_vec @ self.k_vec.T if len(line_vec) else np.zeros((0, len(cliches)))
        self.line_vec, self.offsets = line_vec, np.cumsum([0] + [len(s) for s in self.scripts])

    def score(self, i: int) -> tuple[float, str]:
        """(weighted mean of the lines' nearest-cliche similarity, the most cliched line's nearest cliche)."""
        a, b = self.offsets[i], self.offsets[i + 1]
        if a == b:
            return 0.0, ""
        best = self.sims[a:b].max(1)
        w = np.array([w for _, w, _ in self.scripts[i]])
        worst = int(best.argmax())
        return float((best * w).sum() / w.sum()), self.cliches[int(self.sims[a + worst].argmax())]

    def vector(self, i: int) -> np.ndarray:
        a, b = self.offsets[i], self.offsets[i + 1]
        if a == b:
            return np.zeros(self.k_vec.shape[1], np.float32)
        w = np.array([w for _, w, _ in self.scripts[i]], np.float32)[:, None]
        v = (self.line_vec[a:b] * w).sum(0)
        return v / max(float(np.linalg.norm(v)), 1e-9)


def measure(concepts: list[ReelConcept], reels: list[Reel], single_agent: list[ReelConcept], rooms: dict[str, str],
            mask: list[str] = (), seed: int = 7, method: Method | None = None, cliches_path: Path = CLICHES_PATH) -> NoveltyReport:
    """Novelty for every concept and reel, the room-versus-single-agent diversity, and the creativity map.
    `rooms` maps view id to room name; `mask` lists phrases to drop before embedding (the listing's address)."""
    cliches, source = load_cliches(cliches_path)
    k_texts = [c["text"] for c in cliches]
    by_id: dict[str, ReelConcept] = {}
    for c in [*concepts, *single_agent]:
        by_id.setdefault(c.id, c)
    all_concepts = list(by_id.values())
    c_lines = [concept_lines(c) for c in all_concepts]
    r_lines = [reel_lines(r, by_id.get(r.concept)) for r in reels]
    ns, nf, nc = len(STOCK_PROBES), len(FRESH_PROBES), len(all_concepts)
    sc = _Scorer(k_texts, [*STOCK_PROBES, *FRESH_PROBES, *c_lines, *r_lines], list(mask), method or DEFAULT_METHOD)
    stock = float(np.mean([sc.score(i)[0] for i in range(ns)]))
    fresh = float(np.mean([sc.score(ns + i)[0] for i in range(nf)]))

    def novelty_score(id_: str, kind: str, i: int, lines: list[Line], structure: float) -> NoveltyScore:
        sim, nearest = sc.score(i)
        if not sc.scripts[i]:  # no words beyond facts and room labels: as conventional as the stock probes
            sim = stock
        return NoveltyScore(id=id_, kind=kind, text=" / ".join(t for t, _ in lines), cliche_similarity=round(sim, 4),
                            nearest_cliche=nearest, novelty=round(float(rescale(sim, stock, fresh)), 3),
                            structure_novelty=structure)

    scores = [novelty_score(c.id, "concept", ns + nf + i, c_lines[i], structure_novelty(c, rooms))
              for i, c in enumerate(all_concepts)]
    for i, r in enumerate(reels):
        concept = by_id.get(r.concept)
        structure = structure_novelty(concept, rooms) if concept else reel_structure_novelty(r, rooms)
        scores.append(novelty_score(r.id, "reel", ns + nf + nc + i, r_lines[i], structure))

    single_ids = {c.id for c in single_agent}
    c_vec = np.array([sc.vector(ns + nf + i) for i in range(nc)]).reshape(nc, -1)
    room_idx = [i for i, c in enumerate(all_concepts) if c.round == 1 and _map_kind(c, single_ids, set(), set()) == "concept"]
    single_idx = [i for i, c in enumerate(all_concepts) if c.id in single_ids]
    div = diversity(c_vec[room_idx], c_vec[single_idx], seed=seed)

    finalists = {r.concept for r in reels if r.kind == "creative"}
    baselines = {r.concept for r in reels if r.kind == "baseline"}
    nk = len(k_texts)
    xy = pca_2d(np.vstack([sc.k_vec, c_vec]))
    for axis in (0, 1):  # PCA signs are arbitrary: put the cliches to the left of and below the concepts
        if nk and nc and xy[:nk, axis].mean() > xy[nk:, axis].mean():
            xy[:, axis] *= -1
    xy /= max(float(np.abs(xy).max()), 1e-9)
    points = [MapPoint(id=f"cliche-{i + 1}", kind="cliche", label=k_texts[i][:80], x=round(float(xy[i, 0]), 4),
                       y=round(float(xy[i, 1]), 4)) for i in range(nk)]
    points += [MapPoint(id=c.id, kind=_map_kind(c, single_ids, finalists, baselines), label=c.title,
                        x=round(float(xy[nk + i, 0]), 4), y=round(float(xy[nk + i, 1]), 4), agent=c.agent, round=c.round)
               for i, c in enumerate(all_concepts)]
    return NoveltyReport(scores=scores, diversity=div, map=CreativityMap(method=METHOD_NAMES[sc.method], points=points),
                         method=sc.method, corpus=source, anchors={"stock": round(stock, 4), "fresh": round(fresh, 4)})
