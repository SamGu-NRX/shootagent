// The creative lab's data contract, mirroring src/shootagent/creative/schema.py field for field.
// Pydantic serialises every field (defaults included), so fields with defaults are required here too.
// Change a field there, change it here.

// ---- reel language: what the renderer can do ------------------------------------------------------

export type Motion = "push_in" | "pull_out" | "pan_left" | "pan_right" | "tilt_up" | "tilt_down" | "punch_in" | "static";
export type Transition = "cut" | "crossfade" | "whip" | "flash" | "zoom_through" | "dip_black";
export type TextStyle = "pop_words" | "typewriter" | "bold_center" | "sticker" | "redacted" | "label" | "countdown";
export type TextPos = "top" | "center" | "bottom";
export type Sfx = "none" | "whoosh" | "pop" | "riser" | "record_scratch" | "bass_drop" | "click" | "ding";
export type CardKind = "none" | "price_tag" | "stat_count_up" | "list_reveal" | "big_quote" | "typing_chat";
export type EndingStyle = "end_card" | "loop" | "question" | "price_reveal" | "punchline";
export type MusicMood = "warm" | "upbeat" | "suspense" | "lofi" | "cinematic" | "playful" | "luxe";
export type VoiceName =
  | "none"
  | "alloy"
  | "ash"
  | "ballad"
  | "coral"
  | "echo"
  | "fable"
  | "nova"
  | "onyx"
  | "sage"
  | "shimmer"
  | "verse";

/** A Reel Studio animation card overlaid on a beat. */
export interface CardSpec {
  kind: CardKind;
  value: string | null;
  text: string | null;
  items: string[] | null;
  highlight: string | null;
}

/** The first 1-2 seconds: what stops the scroll. */
export interface Hook {
  text: string;
  style: TextStyle;
  voiceover: string | null;
  sfx: Sfx;
}

/** One shot: a photo, a camera move, and what happens on it. */
export interface Beat {
  view: string;
  seconds: number;
  motion: Motion;
  /** [x, y] in 0..1 */
  focus: number[] | null;
  transition: Transition;
  text: string | null;
  text_style: TextStyle | null;
  text_pos: TextPos;
  voiceover: string | null;
  card: CardSpec | null;
  sfx: Sfx;
}

export interface Ending {
  style: EndingStyle;
  text: string;
  voiceover: string | null;
}

export interface Music {
  mood: MusicMood;
  bpm: number;
  drop_beat: number | null;
}

export interface Voice {
  voice: VoiceName;
  direction: string | null;
}

export type ConceptStatus = "pitched" | "revised" | "killed" | "finalist" | "rendered";

/** One reel idea, complete enough to render. (schema.py's `seconds` is a property, not serialised: see conceptSeconds.) */
export interface ReelConcept {
  id: string;
  /** AgentCard.id of the creator (or "template" / "single_agent") */
  agent: string;
  /** 1 pitch, 2 remix, 3 final */
  round: number;
  /** concept ids this one builds on or steals from */
  parents: string[];
  title: string;
  logline: string;
  format: string;
  hook: Hook;
  beats: Beat[];
  ending: Ending;
  music: Music;
  voice: Voice;
  caption: string;
  hashtags: string[];
  audience: string;
  rationale: string;
  claims: string[];
  status: ConceptStatus;
}

export type CritiqueVerdict = "greenlight" | "revise" | "kill";

export interface Critique {
  critic: string;
  concept: string;
  originality: number;
  hook: number;
  specificity: number;
  /** 10 = every claim checks out against the photos and listing */
  truth: number;
  virality: number;
  verdict: CritiqueVerdict;
  notes: string;
  cliche_flags: string[];
  truth_flags: string[];
}

// ---- the room -------------------------------------------------------------------------------------

export type AgentRole = "showrunner" | "creator" | "critic" | "viewer" | "judge" | "system";

export interface AgentCard {
  id: string;
  name: string;
  role: AgentRole;
  /** one line: how this agent thinks */
  lens: string;
  /** hex */
  color: string;
  initials: string;
  model: string | null;
}

export type RoomKind = "brief" | "pitch" | "critique" | "remix" | "steal" | "vote" | "greenlight" | "note" | "system";

export interface RoomMessage {
  id: string;
  /** seconds since the lab started */
  t: number;
  agent: string;
  kind: RoomKind;
  text: string;
  concept: string | null;
  refs: string[];
}

export interface Feature {
  view: string;
  feature: string;
  why: string;
}

export interface PropertyBrief {
  summary: string;
  hero_features: Feature[];
  audiences: string[];
  /** claims a reel may make */
  facts: string[];
  /** things the photos do not show */
  avoid_claims: string[];
  tone: string;
}

// ---- rendered reels -------------------------------------------------------------------------------

export interface TimelineEntry {
  t0: number;
  t1: number;
  kind: "hook" | "beat" | "ending";
  /** index into concept.beats; -1 for hook and ending */
  beat: number;
  view: string | null;
  text: string | null;
  voiceover: string | null;
  card: string | null;
}

export type ReelKind = "baseline" | "creative" | "single_agent";

export interface Reel {
  /** "A", "B", ... */
  id: string;
  label: string;
  kind: ReelKind;
  /** ReelConcept.id */
  concept: string;
  title: string;
  /** path relative to the lab directory */
  video: string;
  poster: string;
  seconds: number;
  timeline: TimelineEntry[];
  /** the full spoken script */
  voiceover: string | null;
  has_voice: boolean;
  /** "upbeat, 124 bpm" */
  music: string;
  cards: string[];
  warnings: string[];
  render_ms: number;
}

// ---- evaluation network ---------------------------------------------------------------------------

export interface ViewerPersona {
  id: string;
  name: string;
  segment: string;
  /** share of the simulated audience, sums to 1 across viewers */
  weight: number;
  bio: string;
  platform: "instagram" | "tiktok";
  color: string;
  initials: string;
}

export interface Reaction {
  viewer: string;
  reel: string;
  /** the first 1.5 s stopped the scroll */
  stopped: boolean;
  first_impression: string;
  watch_seconds: number;
  completed: boolean;
  /** intents, 0-100 */
  rewatch: number;
  like: number;
  comment: number;
  share: number;
  save: number;
  follow: number;
  /** would message the agent about the home */
  inquire: number;
  comment_text: string;
  /** who they would send it to, and why */
  share_note: string;
  why: string;
}

export interface JudgeRating {
  judge: string;
  reel: string;
  /** 1-10 */
  originality: number;
  surprise: number;
  hook: number;
  specificity: number;
  craft: number;
  coherence: number;
  truthfulness: number;
  /** overall, 1-10 */
  creativity: number;
  best_moment: string;
  weakest_moment: string;
  notes: string;
}

export interface PairwiseVote {
  judge: string;
  /** reel id shown first */
  first: string;
  second: string;
  /** reel id judged more creative */
  winner: string;
  reason: string;
}

export interface NoveltyScore {
  /** concept or reel id */
  id: string;
  kind: "concept" | "reel";
  /** what was scored: hook, on-screen text and ending */
  text: string;
  /** 0-1, nearest neighbour in the cliche corpus */
  cliche_similarity: number;
  nearest_cliche: string;
  /** 0-1, 1 - cliche_similarity, rescaled */
  novelty: number;
  /** 0-1, how far the shot order and devices sit from the standard walkthrough */
  structure_novelty: number;
}

export type MapPointKind = "cliche" | "concept" | "single_agent" | "finalist" | "baseline";

export interface MapPoint {
  id: string;
  kind: MapPointKind;
  label: string;
  x: number;
  y: number;
  agent: string | null;
  round: number | null;
}

export interface CreativityMap {
  /** "all-MiniLM-L6-v2 + PCA" or "char n-gram tf-idf + PCA" */
  method: string;
  points: MapPoint[];
}

export interface ReelSummary {
  reel: string;
  /** mean judge creativity, 1-10 */
  creativity: number;
  creativity_sd: number;
  /** 0-1 */
  pairwise_win_rate: number;
  /** 0-1 */
  novelty: number;
  /** 0-1, weighted by viewer weight */
  stop_rate: number;
  avg_watch_fraction: number;
  completion_rate: number;
  /** weighted mean intent 0-1 (not a platform rate) */
  like_rate: number;
  comment_rate: number;
  share_rate: number;
  save_rate: number;
  inquire_rate: number;
}

export interface Evaluation {
  viewers: ViewerPersona[];
  reactions: Reaction[];
  judges: AgentCard[];
  ratings: JudgeRating[];
  pairwise: PairwiseVote[];
  /** {"kendall_w": 0.71, ...} */
  agreement: Record<string, number>;
  novelty: NoveltyScore[];
  map: CreativityMap | null;
  /** {"room": 0.62, "single_agent": 0.41} */
  diversity: Record<string, number>;
  summaries: ReelSummary[];
  /** one paragraph: which reel is most creative, which should travel furthest, and why */
  verdict: string;
  /** honest caveats */
  notes: string[];
}

// ---- simulated launch -----------------------------------------------------------------------------

export interface SimPoint {
  hour: number;
  views: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  follows: number;
  inquiries: number;
}

export interface RetentionPoint {
  second: number;
  /** share of viewers still watching */
  fraction: number;
}

export interface SimComment {
  hour: number;
  viewer: string;
  name: string;
  text: string;
  likes: number;
}

export interface Simulation {
  reel: string;
  followers: number;
  hours: number;
  seed: number;
  series: SimPoint[];
  retention: RetentionPoint[];
  totals: SimPoint;
  comments: SimComment[];
  /** views per viewer persona id */
  by_segment: Record<string, number>;
  /** the model's assumptions */
  notes: string[];
}

// ---- lab ------------------------------------------------------------------------------------------

export interface LabOptions {
  /** creative reels rendered besides the template baseline */
  finalists: number;
  /** 1 = pitch only; 2 = pitch, critique, remix */
  rounds: number;
  /** creator ids; null = all */
  creators: string[] | null;
  single_agent: boolean;
  render_single_agent: boolean;
  voice: boolean;
  seed: number;
  followers: number;
  hours: number;
}

export type PhaseStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface LabPhase {
  /** brief, pitch, critique, remix, greenlight, single_agent, render, watch, judge, novelty, verdict, simulate */
  key: string;
  label: string;
  status: PhaseStatus;
  /** seconds since the lab started */
  started: number | null;
  ended: number | null;
  detail: string;
}

export interface Usage {
  usd: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
  /** usd per agent role */
  by_role: Record<string, number>;
}

export type LabStatus = "running" | "done" | "failed" | "cancelled";

export interface LabState {
  id: string;
  shoot_id: string;
  title: string;
  /** ISO time */
  created: string;
  status: LabStatus;
  phase: string;
  phases: LabPhase[];
  options: LabOptions;
  brief: PropertyBrief | null;
  agents: AgentCard[];
  feed: RoomMessage[];
  concepts: ReelConcept[];
  critiques: Critique[];
  /** concept ids, best first */
  finalists: string[];
  /** concept ids from the one-agent ablation */
  single_agent: string[];
  reels: Reel[];
  evaluation: Evaluation | null;
  simulations: Simulation[];
  usage: Usage;
  elapsed: number;
  error: string | null;
}

// ---- API shapes (the routes in api/app.py, not schema.py) -----------------------------------------

/** GET /api/shoots/{id}/creative rows */
export interface LabSummary {
  id: string;
  created: string;
  status: LabStatus;
  phase: string;
  /** rendered reel count */
  reels: number;
  usd: number;
}

export interface SimulateBody {
  followers: number;
  hours: number;
  seed: number;
}

export const DEFAULT_OPTIONS: LabOptions = {
  finalists: 2,
  rounds: 2,
  creators: null,
  single_agent: true,
  render_single_agent: false,
  voice: true,
  seed: 7,
  followers: 8000,
  hours: 48,
};
