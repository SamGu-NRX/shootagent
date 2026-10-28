# Media experiments: Grok Imagine walkthrough clips and Gemini 3.8 Flash TTS

Run on the demo MacBook on 28 October 2026, on branch `explore/media-tests`. This is an experiment, not product
code. It answers two questions for ShootAgent's next phase:

- Can Grok Imagine animate one real listing's finished photos without changing the house?
- Is Gemini TTS on the free key good enough for listing narration in English and Spanish?

## Setup

- Photos: the finished images (`current_image`, `edited.jpg`) of property `property_c3ec3b9cd3bbe0b378` in the
  AutoHDR defect set, cases `defect_train_0054` to `0060`. These come from a defect dataset, so a photo may carry
  its labelled defect. The copies the clips were made from are git-ignored (`lab/inputs/`). They appear only on
  the contact sheets.
- Tools: `tools/film.py`, `tools/tts.py` and `tools/listen.py` are unmodified copies from
  `~/Programming Projects/spurs-film/tools/`. They need no path edits because they resolve their project as the
  parent of `tools/`. Two helpers are new:
  - `tools/grok_usage.py` reads the Grok weekly allowance without spending any. It opens `grok dashboard` in a
    pseudo-terminal and types `/usage`. With no session open, that modal shows only the account limit and makes
    no model call.
  - `tools/sheet.py` builds the contact sheets.

## Experiment 1: Grok Imagine

All four clips are 6 s at 720p: 1280x720, 720x1280, 768x1168 and 1168x768. The `video` clips keep the source
photo's aspect ratio, so the portrait T3 is 768x1168, not 720x1280. None of the calls returned an error.

The allowance column comes from the "Weekly limit (SuperGrok)" gauge. It rounds to whole percent and updates late:
T3's cost appeared only after T4 had started. The settled readings went 0% before T1 to 7% after all four clips,
so expect about 2% of the weekly allowance per 6 s clip.

| Clip | Mode, aspect | Sources | Generation | Gauge before → after |
|---|---|---|---|---|
| [T1](shots/T1/ref-8b56910d4c9a.mp4) | `ref`, 16:9 | first 0054, last 0055 | 59.1 s | 0% → 1% |
| [T2](shots/T2/ref-1dc6c6948ff1.mp4) | `ref`, 9:16 | first 0057, last 0058 | 65.2 s | 1% → 3% |
| [T3](shots/T3/video-fd8792a87044.mp4) | `video` | 0058 | 42.0 s | 3% → 3% (later 5%) |
| [T4](shots/T4/video-2a84f10fca79.mp4) | `video` | 0060 | 40.9 s | 5% → 5% (settled 7%) |

The verdicts below come from the contact sheets in `sheets/`, plus full-resolution frames at the transitions.
They answer two questions: does the clip change the house, and do straight lines bend?

- **T1 (bedroom to bath):** the clip invents a second door leaf in the bedroom's right-hand doorway (about 3.0 s)
  and sends that doorway straight into the shower. In 0054, that doorway opens onto a hallway, and the bath is
  through the centre opening. A large foreground door then sweeps past before the frame settles on 0055. The
  bedroom's bed, bench, art and windows are kept.
  Straight lines stay straight; nothing bends.
- **T2 (dining windows to chandelier corner):** the three windows, table, chairs, chandelier and deck door are
  kept. One thing is invented: from about 1.8 s, a white deck stair and railing appear outside the right-hand
  window, where 0057 shows open field. The model borrowed them from the deck door in 0058.
  Window frames stay straight. Only the chandelier's beads shift slightly.
- **T3 (orbit around the dining table):** the door, window, table, chairs and chandelier are kept. As the camera
  moves left it reveals a second window, which matches the real three-window wall in 0057. It also adds a double
  light-switch plate beside the door (from 3.6 s) that 0058 does not show.
  Lines stay straight. The move reads as a sideways slide rather than an orbit.
- **T4 (dolly toward the round table):** this clip has the largest invention. From 1.2 s, a cluster of four cone
  pendant lights hangs over the round table; 0060 has only the three teardrop pendants over the bar. The wooden
  house ornament on the table also reshapes into a faceted block (3.6 s).
  The table, ceiling beam and frames stay straight; nothing bends.

**Takeaway:** none of the four clips bends lines. Three of the four add a fixture or architectural element:

- T1 adds a door leaf.
- T2 adds an exterior stair.
- T4 adds a pendant cluster.

The fourth clip, T3, adds only a light switch. A listing video cannot show any of these unchecked, because an
added door or light misrepresents the property. If clips like these ship, every clip needs a human check against
its photos.

## Experiment 2: Gemini 3.8 Flash TTS

Both narrations use voice **Sulafat**, the one the docs' prebuilt voice list describes as "Warm". The style
prompt was the brief's text. Loudness, LRA and true peak come from ffmpeg `ebur128`.

| File | Language | Duration | Integrated | LRA | True peak |
|---|---|---|---|---|---|
| [E1](audio/E1-en-Sulafat-be7b613035.m4a) | English | 32.6 s | -16.9 LUFS | 2.4 LU | -1.3 dBFS |
| [E2](audio/E2-es-Sulafat-6c8f5855ce.m4a) | Spanish | 39.7 s | -18.5 LUFS | 2.6 LU | -0.3 dBFS |

E2's true peak of -0.3 dBFS leaves almost no headroom. Both files need a master pass, such as the Spurs
`master.sh` (-15 LUFS, then a limiter), before they go under video.

The listening notes below are one Gemini Flash listener's answers to the brief's question. Full answers are in
`audio/*.listen.md`.

- **E1 (English):**
  - No mispronunciations; "chandelier", "suite", "frameless" and "nook" are all fine. The accent reads as
    standard American.
  - The listener calls it fitting for a luxury listing but recognisably synthetic. The pauses in the opening line
    are uniform, and the list of features at 0:06 to 0:10 is "metronomic".
  - "Wood bead chandelier" comes out flat, and the closing "Book your tour today" drops abruptly in pitch and
    warmth.
- **E2 (Spanish):**
  - No mispronunciations; the loanwords *suite* and *spa* sound natural. The listener calls it "100% native",
    with a Mexican or neutral Latin American accent and *seseo*.
  - The feature list in the first half sounds slightly staccato.
  - Part of that accent judgement rests on vocabulary: *recámara* and *regadera* were in the brief's script, so
    they say nothing about the voice itself.

### E3: voice replication

No request was made. The voice-replication docs (`ai.google.dev/gemini-api/docs/voice-replication`) make every
replication request (`POST /v1beta/voices`) carry two recordings from the same adult speaker:

- a 10 to 30 s `source_audio` clip;
- a `consent_audio` clip of that speaker reciting "I am the owner of this voice and I consent to Google using
  this voice to create a synthetic voice model." Both must come from the same microphone in the same room,
  because the service runs a speaker-verification check.

A LibriVox reading cannot supply that consent, and faking it would misstate consent to Google. So the sample was
not downloaded.

Free-tier access to `/v1beta/voices` is undocumented: neither the replication page nor the pricing page mentions
it. TTS itself is "Free of charge" on the Standard tier for both `gemini-3.8-flash-tts` and
`gemini-3.8-flash-lite-tts`.

The legitimate route is a recording of a willing speaker, such as Sam or a hired voice, who records the consent
line in one of the 30 supported locales.

## Requests used

- **Grok:** 4 Imagine calls (T1 to T4), all ok, and 7% of the weekly allowance, which resets 2 November at 22:08.
  The allowance reads cost nothing.
- **Gemini:**
  - TTS: 2 requests (E1, E2), out of the 3 allowed.
  - Listening: 2 passes. The E1 pass needed three HTTP requests: `gemini-3.8-flash` and `gemini-3.7-flash`
    returned 503 before `gemini-3.5-flash` answered.
  - Voice replication: 0 requests.

## Reproduce

Commands run from `lab/`. Outputs are cached by a fingerprint of their inputs, so rerunning costs nothing.

```bash
uv run --no-project --with pyte python tools/grok_usage.py      # allowance, no spend
python3 -I tools/film.py log                                    # the four calls with their arguments
uv run --no-project --with pillow python tools/sheet.py --clip shots/T1/ref-8b56910d4c9a.mp4 \
  --source inputs/0054.jpg --source inputs/0055.jpg --out sheets/T1.jpg
python3 -I tools/tts.py --voice Sulafat --style "<style>" --text-file audio/text/E1-en.txt --out audio
```

`shots/log.jsonl` records each Grok call's exact prompt and arguments. `audio/text/` holds the two scripts.
