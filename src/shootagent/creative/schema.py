"""The creative lab's data contract: concepts, reels, evaluations, simulations and the lab state.

Every module in creative/ reads and writes these models, the API serves `LabState` as JSON, and the web
dashboard renders it (web/src/creator/types.ts mirrors this file). Change a field here, change it there.

LLM-facing models (Hook, Beat, Ending, Music, Voice, CardSpec and the drafts the room asks for) use plain types
only: strict tool schemas cannot carry numeric or length limits, so ranges are documented in field
descriptions and enforced by `clamp()` methods after the call.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- reel language: what the renderer can do -----------------------------------------------------------

Motion = Literal["push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down", "punch_in", "static"]
Transition = Literal["cut", "crossfade", "whip", "flash", "zoom_through", "dip_black"]
TextStyle = Literal["pop_words", "typewriter", "bold_center", "sticker", "redacted", "label", "countdown"]
TextPos = Literal["top", "center", "bottom"]
Sfx = Literal["none", "whoosh", "pop", "riser", "record_scratch", "bass_drop", "click", "ding"]
CardKind = Literal["none", "price_tag", "stat_count_up", "list_reveal", "big_quote", "typing_chat"]
EndingStyle = Literal["end_card", "loop", "question", "price_reveal", "punchline"]
MusicMood = Literal["warm", "upbeat", "suspense", "lofi", "cinematic", "playful", "luxe"]
VoiceName = Literal["none", "alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CardSpec(_Strict):
    """A Reel Studio animation card overlaid on a beat (templates vendored from reel-studio-app, MIT)."""

    kind: CardKind = Field(description="price_tag: a price slides in. stat_count_up: a number counts up with a label. "
                           "list_reveal: 2-5 short items build one by one. big_quote: a sentence lands word by word. "
                           "typing_chat: a message types into a chat bubble. none: no card.")
    value: str | None = Field(None, description="price_tag / stat_count_up: the number exactly as shown, e.g. '$489,000', '640'.")
    text: str | None = Field(None, description="Label, quote or chat message, at most 60 characters.")
    items: list[str] | None = Field(None, description="list_reveal: 2-3 items of at most 22 characters, or 4-5 items "
                                    "of at most 12 characters (longer lists stop at three).")
    highlight: str | None = Field(None, description="big_quote: one word to colour.")


class Hook(_Strict):
    """The first 1-2 seconds: what stops the scroll."""

    text: str = Field(description="On-screen hook, at most 10 words. Readable on mute.")
    style: TextStyle = Field(description="How the hook text animates in.")
    voiceover: str | None = Field(None, description="Spoken hook line, at most 14 words, or null for no voice.")
    sfx: Sfx = "none"


class Beat(_Strict):
    """One shot: a photo, a camera move, and what happens on it."""

    view: str = Field(description="View id of the photo, e.g. 'v03'.")
    seconds: float = Field(description="Shot length, 0.6 to 6.0 seconds. Fast cuts are 0.6-1.2 s.")
    motion: Motion
    focus: list[float] | None = Field(None, description="[x, y] in 0..1: the point a push or punch settles on.")
    transition: Transition = Field("cut", description="How this shot enters from the previous one.")
    text: str | None = Field(None, description="On-screen text for this shot, at most 10 words, or null.")
    text_style: TextStyle | None = None
    text_pos: TextPos = "bottom"
    voiceover: str | None = Field(None, description="Line spoken over this shot, at most 20 words, or null.")
    card: CardSpec | None = None
    sfx: Sfx = "none"

    def clamp(self) -> Beat:
        self.seconds = min(6.0, max(0.6, float(self.seconds)))
        if self.focus is not None:
            f = (list(self.focus) + [0.5, 0.5])[:2]
            self.focus = [min(1.0, max(0.0, float(v))) for v in f]
        return self


class Ending(_Strict):
    style: EndingStyle = Field(description="end_card: price, address, call to action. loop: cut back to the first "
                               "shot so the reel loops seamlessly. question: a question that invites comments. "
                               "price_reveal: the price lands last. punchline: a final joke or twist line.")
    text: str = Field(description="Ending line, at most 12 words.")
    voiceover: str | None = None


class Music(_Strict):
    mood: MusicMood
    bpm: int = Field(description="60 to 150.")
    drop_beat: int | None = Field(None, description="Index of the beat where the music drops (a hit and a punch-in), or null.")

    def clamp(self) -> Music:
        self.bpm = min(150, max(60, int(self.bpm)))
        return self


class Voice(_Strict):
    voice: VoiceName = Field("none", description="Text-to-speech voice, or none for a silent, music-only reel.")
    direction: str | None = Field(None, description="How the narrator sounds, e.g. 'deadpan, dry, amused'.")


class ReelConcept(BaseModel):
    """One reel idea, complete enough to render. `id`, `agent`, `round` and `parents` are set by the room."""

    id: str
    agent: str  # AgentCard.id of the creator (or "template" / "single_agent")
    round: int  # 1 pitch, 2 remix, 3 final
    parents: list[str] = []  # concept ids this one builds on or steals from
    title: str
    logline: str
    format: str  # free text: "the house narrates its own listing", "spot the detail", ...
    hook: Hook
    beats: list[Beat]
    ending: Ending
    music: Music
    voice: Voice = Voice()
    caption: str = ""  # post caption
    hashtags: list[str] = []
    audience: str = ""
    rationale: str = ""  # why it stops the scroll
    claims: list[str] = []  # every factual claim the reel makes about the home
    status: Literal["pitched", "revised", "killed", "finalist", "rendered"] = "pitched"

    @property
    def seconds(self) -> float:
        return sum(b.seconds for b in self.beats)


class Critique(BaseModel):
    critic: str
    concept: str
    originality: int  # 1-10
    hook: int
    specificity: int
    truth: int  # 10 = every claim checks out against the photos and listing
    virality: int
    verdict: Literal["greenlight", "revise", "kill"]
    notes: str
    cliche_flags: list[str] = []
    truth_flags: list[str] = []


# ---- the room ---------------------------------------------------------------------------------------------

AgentRole = Literal["showrunner", "creator", "critic", "viewer", "judge", "system"]


class AgentCard(BaseModel):
    id: str
    name: str
    role: AgentRole
    lens: str  # one line: how this agent thinks
    color: str  # hex, for the dashboard
    initials: str
    model: str | None = None


RoomKind = Literal["brief", "pitch", "critique", "remix", "steal", "vote", "greenlight", "note", "system"]


class RoomMessage(BaseModel):
    id: str
    t: float  # seconds since the lab started
    agent: str
    kind: RoomKind
    text: str
    concept: str | None = None
    refs: list[str] = []


class Feature(BaseModel):
    view: str
    feature: str
    why: str


class PropertyBrief(BaseModel):
    summary: str
    hero_features: list[Feature] = []
    audiences: list[str] = []
    facts: list[str] = []  # claims a reel may make
    avoid_claims: list[str] = []  # things the photos do not show
    tone: str = ""


# ---- rendered reels -------------------------------------------------------------------------------------


class TimelineEntry(BaseModel):
    t0: float
    t1: float
    kind: Literal["hook", "beat", "ending"]
    beat: int  # index into concept.beats; -1 for hook and ending
    view: str | None = None
    text: str | None = None
    voiceover: str | None = None
    card: str | None = None


class Reel(BaseModel):
    id: str  # "A", "B", ...
    label: str  # "Template", "Showrunner's pick", ...
    kind: Literal["baseline", "creative", "single_agent"]
    concept: str  # ReelConcept.id
    title: str
    video: str  # path relative to the lab directory
    poster: str
    seconds: float
    timeline: list[TimelineEntry] = []
    voiceover: str | None = None  # the full spoken script, for viewers and judges who cannot hear
    has_voice: bool = False
    music: str = ""  # "upbeat, 124 bpm"
    cards: list[str] = []
    warnings: list[str] = []
    render_ms: int = 0


# ---- evaluation network -------------------------------------------------------------------------------


class ViewerPersona(BaseModel):
    id: str
    name: str
    segment: str  # "first-time buyers, 25-34, browsing at night"
    weight: float  # share of the simulated audience, sums to 1 across viewers
    bio: str
    platform: Literal["instagram", "tiktok"] = "instagram"
    color: str = "#888888"
    initials: str = ""


class Reaction(BaseModel):
    viewer: str
    reel: str
    stopped: bool  # the first 1.5 s stopped the scroll
    first_impression: str
    watch_seconds: float
    completed: bool
    rewatch: int  # 0-100 intent
    like: int
    comment: int
    share: int
    save: int
    follow: int
    inquire: int  # would message the agent about the home
    comment_text: str = ""
    share_note: str = ""  # who they would send it to, and why
    why: str = ""


class JudgeRating(BaseModel):
    judge: str
    reel: str
    originality: int  # 1-10
    surprise: int
    hook: int
    specificity: int
    craft: int
    coherence: int
    truthfulness: int
    creativity: int  # overall, 1-10
    best_moment: str = ""
    weakest_moment: str = ""
    notes: str = ""


class PairwiseVote(BaseModel):
    judge: str
    first: str  # reel id shown first
    second: str
    winner: str  # reel id judged more creative
    reason: str = ""


class NoveltyScore(BaseModel):
    id: str  # concept or reel id
    kind: Literal["concept", "reel"]
    text: str  # what was scored: hook, on-screen text and ending
    cliche_similarity: float  # 0-1, nearest neighbour in the cliche corpus
    nearest_cliche: str
    novelty: float  # 0-1, 1 - cliche_similarity, rescaled
    structure_novelty: float  # 0-1, how far the shot order and devices sit from the standard walkthrough


class MapPoint(BaseModel):
    id: str
    kind: Literal["cliche", "concept", "single_agent", "finalist", "baseline"]
    label: str
    x: float
    y: float
    agent: str | None = None
    round: int | None = None


class CreativityMap(BaseModel):
    method: str  # "all-MiniLM-L6-v2 + PCA" or "char n-gram tf-idf + PCA"
    points: list[MapPoint] = []


class ReelSummary(BaseModel):
    reel: str
    creativity: float  # mean judge creativity, 1-10
    creativity_sd: float
    pairwise_win_rate: float  # 0-1
    novelty: float  # 0-1
    stop_rate: float  # 0-1, weighted by viewer weight
    avg_watch_fraction: float
    completion_rate: float
    like_rate: float  # weighted mean intent 0-1 (not a platform rate; simulate.py maps intents to rates)
    comment_rate: float
    share_rate: float
    save_rate: float
    inquire_rate: float


class Evaluation(BaseModel):
    viewers: list[ViewerPersona] = []
    reactions: list[Reaction] = []
    judges: list[AgentCard] = []
    ratings: list[JudgeRating] = []
    pairwise: list[PairwiseVote] = []
    agreement: dict[str, float] = {}  # {"kendall_w": 0.71, ...}
    novelty: list[NoveltyScore] = []
    map: CreativityMap | None = None
    diversity: dict[str, float] = {}  # {"room": 0.62, "single_agent": 0.41}
    summaries: list[ReelSummary] = []
    verdict: str = ""  # one paragraph: which reel is most creative, which should travel furthest, and why
    notes: list[str] = []  # honest caveats


# ---- simulated launch -----------------------------------------------------------------------------------


class SimPoint(BaseModel):
    hour: float
    views: int
    likes: int
    comments: int
    shares: int
    saves: int
    follows: int
    inquiries: int


class RetentionPoint(BaseModel):
    second: float
    fraction: float  # share of viewers still watching


class SimComment(BaseModel):
    hour: float
    viewer: str
    name: str
    text: str
    likes: int


class Simulation(BaseModel):
    reel: str
    followers: int
    hours: int
    seed: int
    series: list[SimPoint] = []
    retention: list[RetentionPoint] = []
    totals: SimPoint
    comments: list[SimComment] = []
    by_segment: dict[str, int] = {}  # views per viewer persona id
    notes: list[str] = []  # the model's assumptions, shown in the dashboard


# ---- lab ----------------------------------------------------------------------------------------------


class LabOptions(BaseModel):
    finalists: int = 2  # creative reels rendered besides the template baseline
    rounds: int = 2  # 1 = pitch only; 2 = pitch, critique, remix
    creators: list[str] | None = None  # creator ids; None = all
    single_agent: bool = True  # also generate concepts with one agent, for the ablation
    render_single_agent: bool = False  # render the single agent's best concept as a reel too
    voice: bool = True
    seed: int = 7
    followers: int = 8000
    hours: int = 48


PhaseStatus = Literal["pending", "running", "done", "failed", "skipped"]


class LabPhase(BaseModel):
    key: str  # brief, pitch, critique, remix, greenlight, single_agent, render, watch, judge, novelty, simulate
    label: str
    status: PhaseStatus = "pending"
    started: float | None = None  # seconds since the lab started
    ended: float | None = None
    detail: str = ""


class Usage(BaseModel):
    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    by_role: dict[str, float] = {}  # usd per agent role


class LabState(BaseModel):
    id: str
    shoot_id: str
    title: str
    created: str  # ISO time
    status: Literal["running", "done", "failed", "cancelled"] = "running"
    phase: str = ""
    phases: list[LabPhase] = []
    options: LabOptions = LabOptions()
    brief: PropertyBrief | None = None
    agents: list[AgentCard] = []
    feed: list[RoomMessage] = []
    concepts: list[ReelConcept] = []
    critiques: list[Critique] = []
    finalists: list[str] = []  # concept ids, best first
    single_agent: list[str] = []  # concept ids from the one-agent ablation
    reels: list[Reel] = []
    evaluation: Evaluation | None = None
    simulations: list[Simulation] = []
    usage: Usage = Usage()
    elapsed: float = 0.0
    error: str | None = None
