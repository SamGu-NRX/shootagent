# Listing film music: Lyria in the Gemini app (28 October 2026)

All tracks came from the Gemini app's Create music tool, with Length "Short" and Vocals "Instrumental". This is
not the paid Lyria API. The account was `sgu07966@gmail.com` (Gemini Pro), not `sp4rklabs@gmail.com` as
`HANDOFF-media-tools.md` says:

- On sp4rklabs, Gemini had never been set up in this browser and opened its first-run terms and data-sharing
  dialog. That choice is Sam's, so it was left alone.
- sgu07966 holds the Spurs film's music chat ("Cinematic Sports Anthem Creation"), so the Spurs music was made
  there.

The tracks are in the Gemini chat "Real Estate Listing Track Creation".

**Measurements.** Loudness and true peak come from ffmpeg `ebur128`.

- **Tempo:** comes from `lab/tools/tempo.py`, which autocorrelates the onset envelope. At its frame rate,
  neighbouring estimates are about 4 BPM apart, so 103.4 and 107.7 both mean about 105.
- **Shape:** the RMS level per second gives each track's shape.
- **Listening:** one Gemini Flash pass per track, using the brief's question. The full answers are in
  `*.listen.md`.

| File | Title from Gemini | Duration | Integrated | True peak | Tempo |
|---|---|---|---|---|---|
| `01-modern-listing-open-plan-afternoon.mp3` | Open Plan Afternoon | 64.8 s | -11.5 LUFS | +0.5 dBFS | ~105 BPM |
| `01b-modern-listing-first-light-on-oak.mp3` | First Light on Oak | 66.7 s | -12.3 LUFS | +1.0 dBFS | ~105 BPM |
| `02-cinematic-calm-the-morning-foyer.mp3` | The Morning Foyer | 63.8 s | -11.4 LUFS | +0.1 dBFS | ~76 (loose pulse) |

All three are mastered loud and two have true-peak overs. Gain them down and limit them before they go under a
voiceover. The Spurs `master.sh` targets -15 LUFS; a music bed sits well below that again.

## 1. Modern listing

**Prompt, take 1:**

> Modern listing: an instrumental background track, no vocals, about 60 seconds. Warm, bright, modern
> acoustic-pop with acoustic guitar, light percussion and a clear steady beat around 104 BPM. Uplifting but not
> cheesy, suitable under a real-estate listing voiceover. Ends cleanly on a resolved final chord, no fade-out cut.

**Take 1 (Open Plan Afternoon).** The listener (`gemini-3.5-flash`) heard wordless "mmm" vocal chops at about
0:02–0:05, 0:22–0:25 and 0:48–0:51. That counts as vocals, so the track was regenerated once.

- **Beat and swells:** the beat enters at 0:03–0:04. The first swell, with full bass and percussion, comes at
  0:22, and a second peak at 0:41. The level data put the loudest second at 0:49.
- **Ending:** a clean ring-out on a guitar chord at 0:57–1:01, decaying to silence at 1:04.
- **Artifacts:** none heard.

**Prompt, take 2:**

> Regenerate the first track (Modern listing) as a new take: strictly instrumental, with no human voice of any
> kind, no humming, no 'mmm' or 'ooh' vocal chops, no choir. … around 104 BPM, about 60 seconds. … Ends cleanly
> on a resolved final chord.

**Take 2 (First Light on Oak).** The listener (`gemini-3.7-flash`) still heard wordless "ooh/aah" vocal pads,
now confined to 0:27–0:45. The first 27 seconds are purely instrumental.

- **Beat and swells:** the beat enters at 0:08–0:09, and the peak lift comes at 0:27–0:28.
- **Ending:** a breakdown at 0:56, then a guitar strum and reverb tail that dies out by 1:05.
- **Artifacts:** none heard.

**Verdict.** Neither take is fully free of voice, even with the Instrumental setting and an explicit prompt.
Take 2 has the longest clean stretch, 0:00–0:27. For a 30–40 s reel, use one of these:

- take 2's first 27 s, then the outro;
- take 2 with the chops ducked;
- the Cinematic calm track.

## 2. Cinematic calm

**Prompt:**

> New, separate track (not a variation of the last one). Cinematic calm: an instrumental background track, no
> vocals, about 60 seconds. Slow ambient piano with soft strings, spacious and calm, a gentle swell in the
> middle, and a resolved ending on a held final chord that rings out naturally. Suitable under a real-estate
> listing voiceover.

**The Morning Foyer.** Used as is; no regeneration was needed. The listener (`gemini-3.5-flash`):

- **Vocals:** none.
- **Artifacts:** none.
- **Shape:** soft piano at 0:00–0:15, strings join at 0:15–0:25, and the peak swell is at 0:26–0:35. The level
  data put the loudest second at 0:29.
- **Ending:** it resolves to solo piano and decays naturally at 0:55–1:02.
- **Suggested cut:** play 0:00 to about 0:40 so the voiceover closes as the music winds down after the swell.
