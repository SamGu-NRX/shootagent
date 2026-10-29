# Demo video brief

For whoever cuts the one-minute video. The figures come from the recorded lab that the demo replays
(`20261028-212903-d13318`, in `web/public/mock/creative/lab.json`). If you record a different lab, check them
against the page first.

## The pitch in one line

ShootAgent gives every listing a creative team: ten AI agents pitch, critique and remix reel ideas, an
independent panel of AI viewers and judges scores the results blind, and the best reel is predicted to get
3.1× the views of a typical listing reel.

## How to capture

The walkthrough is a full-screen presenter in the web app: six scenes, about 62 seconds, drawn on a fixed
1920×1080 stage. It has no sound.

**A. Render an MP4 (recommended).** It needs ffmpeg and Playwright's Chromium (`npx playwright install
chromium` in `web/`).

```bash
cd web && npx vite build && npx vite preview --port 5179 --strictPort   # leave this running
node web/scripts/record-present.mjs                                      # from the repo root, in a second terminal
node web/scripts/record-present.mjs --scenes pitch,critique              # only some scenes
```

You get `data/present/present.mp4` (1920×1080, 30 fps, H.264, no audio) and two stills per scene,
`data/present/stills/<scene>-mid.png` and `<scene>-end.png`. A full run takes about two and a half minutes,
because the stills pass plays the walkthrough a second time. `--no-stills` skips it. The capture itself runs
at about 30 fps, so keep `--fps 30`.

**B. Record the screen yourself.** Open Chrome at 1920×1080 (or full screen on a 1080p display) at

```
http://127.0.0.1:5179/?api=mock&present=1&clean=1&autoplay=0#/creator/64170ca0a0
```

and press Space. Keys: Space plays and pauses, → and ← move between scenes, R restarts, C shows the
narration as captions, H shows the controls, Esc leaves. Add `&scene=critique` to start at a scene, or drop
`&clean=1` to keep a faint control bar below the stage. The **Present** button in the dashboard header
opens the same walkthrough.

## Shot list

Each scene starts on its own first frame, so cut on the boundaries. To land on 60 seconds, trim about a
second from the end of the first and last scenes.

| Time | Scene | On screen | Narration |
|---|---|---|---|
| 0:00–0:07 | The problem | A wall of real stock phrases from the cliché corpus. The template reel plays in front of it, and the phrases it repeats light up. Headline: every listing reel sounds the same. | "Every listing reel says the same things: just listed, stunning, schedule a tour. So buyers swipe past in seconds." |
| 0:07–0:17 | The creative team | The showrunner's brief, then six creators working side by side on the real clock of the pitch round. Their twelve pitches land on the concept wall at once. | "So we built a creative team of ten AI agents. Six of them write reel ideas at the same time, each in a different style." |
| 0:17–0:29 | Critique and remix | The critics sweep the wall and stamp real verdicts. Two real critic notes pop up: a cliché and a fact check against the photos. Remixes are drawn from the ideas they borrowed, and the two greenlit concepts move to the centre. | "Three critics review every idea. They cut what's overused, catch claims the photos don't support, and the best ideas get combined." |
| 0:29–0:40 | Before and after | The template reel and the team's reel play side by side. The hook is typed out, and three blind-panel comparisons count up. | "Two ideas make the cut and become finished reels. Same listing, same photos, a very different reel." |
| 0:40–0:50 | Measured | The cliché corpus settles into a cloud. The template drops into it, and the room's ideas land far outside it. A blind jury scores the reels. | "Sixteen AI viewers and judges score every reel without knowing who made it, and we measure how far each one is from 195 overused listing lines." |
| 0:50–1:02 | The payoff | The first 48 hours, predicted: views, buyer inquiries and what the reels cost to make, then the end card. | "In a simulated first 48 hours, the AI team's reel gets 3.1 times more views and 3 buyer messages, for $11.31 of AI time." |

## B-roll

- **The four finished reels** in `web/public/mock/creative/reels/`: 720×1280 H.264 with their own audio, and
  a poster `.jpg` beside each one.
  - `A.mp4`: the template, the reel most listings post ("Just listed", room names, a stats card, "Schedule a
    tour"). 25.1 s, music, no voice.
  - `B.mp4`: the showrunner's pick, "Two Things Out of Place". Hook: "Two things in this flat are out of
    place." 22.5 s.
  - `C.mp4`: the wildcard, "Find the Earbud's Counter". Hook: "One earbud got left behind. Find its counter."
    22.5 s. This is the reel judged most creative and predicted to travel furthest.
  - `S.mp4`: the single-agent control, "The Mirror Has Notes". 33.6 s.
- **The listing photos** at 1600 px in `web/public/mock/creative/views-hd/`: v01 entry hall, v02 kitchen, v03
  living room, v04 entry, v05 dining nook, v06 dining and living, v07 bedroom, v08 second bedroom view.
- **The dashboard** at `/?api=mock#/creator/64170ca0a0`. **Run creative lab** replays the recorded room working
  live, sped up. **Present** opens the walkthrough.
- `docs/media/creative-reels.jpg`: frames from the template and the room's pick, one above the other.

## Music and sound

The walkthrough is silent. Use an instrumental track at about 100 to 120 bpm, with a lift at 0:07, when the
team appears, and a hit at 0:50, the payoff. If you cut to a reel full screen, duck the music under the reel's
own voice.

## Claims we can defend

Every viewer and judge is an AI agent (Claude Opus 5.5), not a person. The launch figures are a seeded
model's prediction from those agents' reactions, not platform data. Say "predicted" or "simulated" for them,
and don't call the viewers real people.

| Claim | Figure | Where it comes from |
|---|---|---|
| The creative team | 10 agents: a showrunner, 6 creators, 3 critics | The lab's agents |
| Ideas | 24 concepts: 12 first pitches and 12 remixes, 6 of which borrow from another creator | The room's concepts |
| Critique | 72 critiques; 16 concepts killed; 20 cliché flags; 50 truth flags (claims or shots that don't match the photos or listing) | The critics' notes |
| Judged creativity | 6.6 out of 10 for the team's reel, 2.6 for the template | 5 AI judges rating blind, unlabelled reels |
| Head to head | The team's reel won 28 of 30 blind match-ups | The judges' pairwise votes, each pair shown in both orders |
| The judges agree | Kendall's W 0.90 (p = 0.004) | The judges' rankings |
| Novelty | 0.95 against 0.27 for the template (3.5×) | Distance from 195 overused real-estate lines, measured with embeddings, not a model's opinion |
| Stopped scrolling | 95% against 59% within 1.5 s | 11 AI viewer agents, weighted by audience segment |
| Watched to the end | 27% against 5% | The same panel |
| Views in 48 hours | 6,277 against 2,018 (3.1×) | Simulated launch: 8,000 followers, 48 h, seed 7 |
| Buyer inquiries | 3 against 0 | The same simulation |
| Shares and saves | 24 and 32, against 3 and 14 | The same simulation |
| Views from buyers | 2,283 against 1,190 | The simulation's views from first-time, local, relocating, investor and downsizer viewers |
| Cost and time | $11.31 in model usage, 173 model calls, 11 min 17 s for the whole lab, including rendering four reels; about $2.83 per finished reel | The lab's usage meter |

If the payoff scene shows a commission, it is an assumption: 2.5% of the demo listing's $489,000 price, or
$12,225. The listing values are demo values. Don't present it as money the reel earned.

Keep two limits in mind if a judge asks. This is one lab on one apartment. In a second lab on the same
apartment, the single agent was judged more creative than the room (7.0), although the room's best reel still
travelled furthest. So don't claim the room always out-creates one strong agent.
