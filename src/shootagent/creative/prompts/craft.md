# Craft notes: listing reels that travel

Shared by every creator in the writers' room and by the single-agent control, so both work from the same
knowledge.

## The medium

- A vertical 9:16 reel, 12 to 35 seconds, made only from this shoot's photos. Each beat is one photo with a
  camera move; text, cards, sound effects, music and an optional voice sit on top. There is no video
  footage, nobody on camera, no drone, no stock clips, no licensed songs.
- Most people watch on mute first. The on-screen text must carry the idea on its own.
- The voice is a synthetic text-to-speech voice and sounds like one, not like a person. Prefer strong
  on-screen text. Use voice only when it adds something text cannot: a deadpan narrator, the home talking,
  a dry aside. A flat machine read can help deadpan comedy; it hurts warmth and emotion. Keep lines short.

## The hook (the first 1 to 2 seconds)

Adapted from Reel Studio's script prompt (reel-studio-app, `ai/prompts/writeScript.md`, MIT licence).

- People decide in under 2 seconds. The hook's only job is to make a stranger want the next shot.
- No throat clearing. Never open with "Welcome to", "Just listed", "Check out", an address, a logo or a
  wide establishing shot. Start mid-thought.
- Open a curiosity gap: make the question clear without answering it.
- One idea, one question. A 10-year-old should get it. At most 10 words on screen.
- Be specific: a number, a material, an object, a moment from these photos. "The mirror by the door"
  beats "stunning details".
- Struggle beats success. A confession relates where a brag alienates; an honest limit stated first earns
  trust.
- Use words people actually say or search.
- The first frame works as hard as the words: open on the most arresting crop, not the widest shot.

Shapes to start from, then bend to this home (never paste them as they are):
- The only one: "The one thing in this [room] I'd never change."
- Numbered: "[N] things in this [home] that make no sense until they do."
- Contrarian: "Everyone says [belief about this kind of home]. Watch."
- Confession: "I have a confession about this [room]."
- Curiosity question: "Why is there a [odd detail] in the [room]?"
- Call-out by habit or situation, never by who someone is: "If you [specific habit], look at this [room]."
- Story cold open: "[Time]. [A specific moment in this home]."
- Comparison: "[Option A] or [option B]? Look first."

## Retention: earning every second

- Open loop: the hook asks a question that only the end answers. Hold the payoff back.
- A pattern interrupt every 2 to 3 seconds: a cut, a new line of text, a card, a punch-in, a sound. Nothing
  sits unchanged on screen for more than 3 seconds.
- The payoff must be specific, true and worth the wait. A hook with no payoff is bait, and people
  remember.
- Length follows the idea: 12 to 20 seconds for one joke or one reveal; up to 35 only if a story earns it.
  Cut anything that repeats.
- Loops: when the last frame or line flows back into the first, people watch twice, and rewatches are the
  strongest signal a reel sends.
- Rhythm: fast cuts (0.6 to 1.2 s) build energy; a longer hold (3 to 5 s) after them lands a reveal. A reel
  of identical 3-second shots is a slideshow.
- Order is a choice. The buyer's walk (entry, living room, kitchen, bedroom) is what every listing does;
  use it only when the idea needs it.

## Comment, share and save triggers

- Comments: a question only the viewer can answer, a guess, a choice between two things the home has, a
  detail to find, a confession people relate to. Never "Thoughts?" or "Comment below".
- Sends: a reel gets sent when it reminds someone of one specific person, when it is funny, or when it
  settles or starts a debate. Write the moment someone thinks "they need to see this".
- Saves: a specific idea worth stealing (a storage trick, a material, a layout), or a number to remember.
- Follows: a recognisable voice or format people want more of.

## It is still a listing

A creative reel must still work as a listing. Someone who watched it on mute should know, by the end, what
is for sale and roughly how much: the price and the basics (beds, baths, square feet) land on screen
somewhere, as a `price_tag` or `stat_count_up` card, a `price_reveal` ending, or a line of text. Fold them
into the idea (a reveal, a count, a punchline, the answer to the hook) rather than bolting on a closing card
that kills it. A reel that sells only the decor gets admired and skipped by the buyers who would have
asked about the home ("is this a listing or just decor?").

## What the renderer can do

These are the only values that exist; spell them exactly as written.

Camera move (`motion`), one per beat:
- `push_in`: a slow zoom toward `focus`. Draws the eye to a detail; calm.
- `pull_out`: starts close on `focus` and pulls back to the whole photo. A reveal of where something is.
- `pan_left` / `pan_right`: slides sideways across the photo. Best on landscape photos and wide rooms.
- `tilt_up` / `tilt_down`: slides vertically. Shows height: a pendant, a tall wall, a headboard.
- `punch_in`: a fast, hard zoom onto `focus`. A hit for a punchline, a drop or a reveal.
- `static`: no move. Deadpan, text-heavy beats, a held breath.
- `focus`: [x, y] from 0 to 1 across the photo (0, 0 is top left): where a push, pull or punch lands. Set it
  on the detail, read from the photo itself; [] for none.

Transition (`transition`), how a beat enters from the one before:
- `cut`: a hard cut. Fast and modern.
- `crossfade`: a soft dissolve. Gentle and conventional; the template look.
- `whip`: a fast blurred swipe. Energy, a list moving on.
- `flash`: a white flash frame. A hit, a reveal, a punchline.
- `zoom_through`: zooms into the old shot and out of the new one. Travelling through the home.
- `dip_black`: fades through black. A pause, a time jump, "later".

On-screen text (`text`, at most 10 words per beat; `text_style`; `text_pos` is `top`, `center` or `bottom`):
- `pop_words`: words pop in one at a time, big. Punchy captions.
- `typewriter`: the line types itself on. A note, a diary entry, a confession.
- `bold_center`: a big bold headline in the middle, with optional smaller sublines under it. Split headline
  from subline with a line break, " | " or " · ", or lead with a short kicker before a colon ("Verdict: built
  in"). Statements and hooks.
- `sticker`: a tilted tag slapped on the photo. Asides with attitude.
- `redacted`: the line appears under black censor bars that wipe away to reveal it. Secrets, withholding.
- `label`: a small, quiet tag, like a room name. Information, not drama.
- `countdown`: a big number with the line, for countdowns and ranked lists. Start the text with its number
  ("3 · the bench"); text with no leading number gets a 3-2-1 count before it.
In any style, a word wrapped in asterisks takes the accent colour ("the *arch* glows"); use it on the one word
that matters. Keep text off the detail you are pushing in on, and give people time to read it.

Sound effect (`sfx`, on the hook or a beat): `none`, `whoosh` (a swish on a fast transition), `pop` (text or
a card landing), `riser` (tension building into a reveal), `record_scratch` (a comic stop),
`bass_drop` (a heavy hit on the drop), `click` (a light switch or a shutter), `ding` (a bell: right
answer, price reveal).

Animation card (`card`, at most one per beat; Reel Studio templates). A card covers part of the photo, so
use one only where it carries the idea:
- `price_tag`: a price tag slides in. `value` is the price exactly as listed; `text` an optional line under it.
- `stat_count_up`: a big number counts up. `value` is the number ("640"); `text` a 2 to 6 word label.
- `list_reveal`: items build one by one. `items`: 2 to 3 items of at most 22 characters, or 4 to 5 items of
  at most 12 characters ("Stone kitchen"); a longer list is cut to its first three. `text` an optional title.
- `big_quote`: a sentence of at most 60 characters lands word by word in big type. `text`; `highlight` one
  word from it to colour.
- `typing_chat`: a message types into a chat bubble, and an optional reply lands under it. `text` is the
  message and `value` the reply, each at most 60 characters: a text to a friend and the answer.
- `none`: no card.

Ending (`ending.style`; `ending.text` at most 12 words):
- `end_card`: price, address and a call to action. Conventional.
- `loop`: cuts back to the first shot so the reel loops; write the last line so it flows into the first.
- `question`: a question on screen that invites comments.
- `price_reveal`: the price lands last, as the payoff.
- `punchline`: a final joke or twist line.

Music (`music`): generated, licence-free. `mood` is one of `warm`, `upbeat`, `suspense`, `lofi`,
`cinematic`, `playful`, `luxe`. `bpm` 60 to 150; cuts feel right on the beat (at 120 bpm a beat is 0.5 s).
`drop_beat` is the 0-based index of the beat where the music drops with a hit and a punch-in, or -1
for none.

Voice (`voice`): `none` for a silent, music-only reel, or one of `alloy`, `ash`, `ballad`, `coral`, `echo`,
`fable`, `nova`, `onyx`, `sage`, `shimmer`, `verse`, all synthetic; `direction` says how it should sound.
A voiceover line is at most 20 words and must fit its beat at about 2.6 words per second (a 10-word line
needs a 4-second beat).

Timing: each beat is 0.6 to 6 seconds; all beats together 12 to 35 seconds. The hook text plays over the
start of the first beat, so the first photo has to serve the hook.

## Truth and fair housing

- Claim only what the photos and the listing show. Price, beds, baths and square feet exactly as listed.
- Never invent features, materials, appliances or storage you cannot see; views; light at a time of day
  the photos do not show; sounds, smells or temperature; building amenities, parking or outdoor space;
  anything about the neighbourhood (walkability, cafes, schools, safety, "quiet"); renovations or history;
  brands or prices of objects.
- Furniture, art and decor may be staging. Show them, but never promise they come with the home.
- A playful "you" moment (you drop your keys, you make coffee) is fine if it implies no feature the photos
  do not show.
- Never zoom in on framed personal photos or anything that identifies the people who live there.
- Fair housing: describe the home, never the buyer. No wording about who should or could live there by
  race, colour, religion, sex, gender, sexual orientation, disability, family status (kids, couples,
  singles), national origin, age or source of income: not "perfect for a young couple", "bachelor pad",
  "great for families", "ideal for empty nesters", "man cave". Speak to situations and tastes ("anyone who
  cooks", "first-time buyers"), never to who people are.
- No pressure or bait: no "won't last", no fake urgency, no hook the reel does not pay off.

## What every other listing reel already does

"Just listed" openers; "Welcome home"; "Your dream home awaits"; "This one won't last"; "You won't
believe..."; "Wait till you see..."; stunning, nestled, boasts, move-in ready, luxury living, spacious,
cozy; an establishing shot, then room after room with room labels and crossfades over soft piano; "DM me
for details" or "link in bio" endings; "POV: you just walked into your dream home". People scroll past
these on reflex.
