# Human verdicts for handoff c46bbca496 (run 28 October 2026 on the demo Mac)

These are ground truth for scoring the automatic checker. Each clip got two passes:

- its contact sheet in `sheets/<id>.jpg`: the source photo(s), then frames at 0, 1.2, 2.4, 3.6, 4.8 and 5.9 s;
- full-resolution frames at 2.0, 4.0 and 5.9 s set beside the source, plus extra frames wherever something
  changed.

A change counts only after it was checked against the source photo. Three candidates were rejected that way:

- **S02, the switch plate and baseboard outlet beside the bedroom doorway:** both are in `0ec2944b2bc17d16.jpg`.
- **S01-A2, the bedside lamp's clear glass base:** the same lamp, with a glass base, is in `0ec2944b2bc17d16.jpg`.
- **S04, the double switch on the right wall and the dark baseboard fixture under the right window:** both are in
  `c81611f3838c25d8.jpg`.

**Run.** `python3 run_grok.py` made all 10 clips and both narrations with no failures. Every clip's arguments
matched grok's session log. The Grok weekly allowance went from 7% at 22:40 to 26% at 22:51, plus any late
update. A final reading at 23:06 still showed 26%, so the batch used 19 points.

**Aspect ratio.** Every request asked for `aspect_ratio: 9:16`, but the eight single-first-frame clips (S01, S03,
S04, S07) came back **1168x784, landscape 3:2**, the source photos' shape. Only the S02 clips, which have both a
first and a last frame, are 720x1280 portrait. An importer that expects 9:16 needs to crop these or treat them as
a mismatch.

## Clips

Times are seconds into the 6.04 s clip. "Left" and "right" are as seen in the frame.

### S01: dolly toward the bed and the three windows (source `84fb92bc79c23d05.jpg`)

**S01-A1** (`clips/940c24291708e56b.mp4`, 40.4 s)

- Kept the three back windows, the right-wall window, both canvases, the dresser, round mirror, tree, bench,
  lamp, the outlet under the window and the prairie view.
- **One addition:** a small white ornament on the right nightstand beside the lamp, from about 4.0 s and clear by
  5.9 s.
- **Lines:** straight. The camera also lowers slightly as it moves in.

**S01-A2** (`clips/0e55b54d679d7a7c.mp4`, 41.6 s)

- Same room kept: windows, view, art, bed, tree and dresser.
- **One addition:** a wall outlet low on the left wall, just right of the dresser. It appears at about 4.0 s and
  is at the baseboard below and left of the tree at 5.9 s. The source shows blank wall there.
- **Lines:** straight.

### S02: walk from the foot of the bed through the doorway into the bath (sources `0ec2944b2bc17d16.jpg` → `247f14cdd5443bd0.jpg`)

**S02-A1** (`clips/078f3fa50053c6aa.mp4`, 61.0 s)

- The route is correct: it enters the bath through the centre opening, past the vanity and the freestanding tub.
- It ends on a 9:16 crop of the shower. The white door and the right-hand doorway of `247f…` fall outside the crop;
  they were not removed.
- **One invention: a person.** From about 4.8 s to the end, a clear head-and-shoulders human silhouette stands in
  the shower window. `247f…` has only a faint dark sliver at that window's lower-left edge, probably a
  reflection, and the clip turns it into a figure.
- **Lines:** straight.

**S02-A2** (`clips/c119d5a4eeba39be.mp4`, 59.4 s)

- **Invents a rectangular ceiling vent** above the shower. It is visible through the doorway from about 2.4 s and
  clearest at 4.4 s; `247f…` shows only a recessed light there.
- **Invents a dark navy vanity cabinet with brass pulls** at the right edge, right next to the shower, from about
  4.4 s to the end. In `247f…` that spot is a white wall; the navy vanity is in the far room through the
  right-hand doorway.
- **Lines:** straight.

### S03: sideways glide past the freestanding tub (source `e10e582ea184abf1.jpg`)

**S03-A1** (`clips/cf81653e6384fa94.mp4`, 37.9 s)

- Kept both windows, both backlit mirrors and vanities, both sconces, the tub and filler, the towel bar and ring,
  and the walk-in closet doorway.
- **One reshape:** at the far right edge, the source's textured wall corner becomes a smooth door casing with
  black hinges, from about 2.0 s.
- **Lines:** straight. Wide-angle perspective at the left edge is unchanged from the source.

**S03-A2** (`clips/e44da3ca0ccb5bed.mp4`, 40.1 s)

- **Invents a white panel door** with black hinges and a lever handle at the right edge. It appears at about
  2.4 s and is a full door leaf by 4.0 s, where the source has a textured wall corner.
- Also adds folded towels on a closet shelf at about 4.0 s.
- Otherwise faithful. **Lines:** straight.

### S04: dolly past the kitchen island toward the dining table and windows (source `c81611f3838c25d8.jpg`)

**S04-A1** (`clips/51f77f01ada51163.mp4`, 46.5 s)

- Faithful: the four windows and their prairie, fence and house view, the beaded chandelier, globe pendant,
  table, chairs, island, stools, vase and the switch beside it.
- Nothing invented or removed at full resolution.
- **Lines:** straight.

**S04-A2** (`clips/d342234b26da40f2.mp4`, 34.9 s)

- Faithful. The same elements are kept, and the right-wall double switch and the baseboard fixture match the
  source.
- **Lines:** straight.

### S07: dolly toward the round table, bar and three pendants in view (source `dcd8fa54fae70ab1.jpg`)

**S07-A1** (`clips/5581c73a16c8e88a.mp4`, 40.0 s)

- Faithful: still exactly three bar pendants (the lab's T4 had added a pendant cluster here), plus the art pair,
  map, round mirror, bar cart, stools, sofa and the wooden house ornament, unchanged.
- The camera barely moves: a slight push, not a real dolly.
- **Lines:** straight.

**S07-A2** (`clips/1149d973c8ab5ce1.mp4`, 40.5 s)

- Nearly identical to A1 and faithful. Nothing is invented or removed, and the motion is again minimal.
- **Lines:** straight.

## Summary

| Shot | A1 | A2 |
|---|---|---|
| S01 | small nightstand ornament added | outlet added on the left wall |
| S02 | **person in the shower window** | **ceiling vent and navy vanity added** |
| S03 | wall corner reshaped into a door casing | **full door added** at the right edge |
| S04 | faithful | faithful |
| S07 | faithful (barely moves) | faithful (barely moves) |

No clip bends straight lines.

## Narrations

One listening pass each with `listen.py`, both answered by `gemini-3.7-flash`. The full answers are in
`listen/`. Loudness comes from ffmpeg `ebur128`.

**TTS-en** (`audio/f4e18f1303cc118e.wav`, 10.3 s to generate): 37.0 s, -19.2 LUFS, true peak -1.6 dBFS.

- **Pronunciation:** none wrong, including "suite", "freestanding soaking tub", "chandelier" and the price. The
  accent reads as standard American.
- **Naturalness:** the listener calls it natural and warm. The sentence-by-sentence pauses suit a room-by-room
  tour.
- **Robotic spots:** only a slight TTS cadence in the closing specs ("Three bedrooms, two baths…") at about
  0:31–0:34.

**TTS-es** (`audio/b7fc9364eb241416.wav`, 11.7 s to generate): 39.6 s, -16.4 LUFS, true peak **+2.1 dBFS**. It
will clip on export, so gain it down or limit it.

- **Pronunciation and accent:** none wrong; the listener calls it completely native. The accent is neutral Latin
  American (Mexican or international media standard) with *seseo*.
- **The price was read correctly:** "$549,000" came out as "quinientos cuarenta y nueve mil dólares".
- **Rated less natural than the English, with three causes:**
  - the pause between every sentence is uniform and rigid, so it sounds like slide captions read aloud;
  - "Pasa por la puerta abierta al baño principal" (0:08–0:10) has a flat pitch contour;
  - the closing specs sound monotone.
- **Wording:** "bajo una lámpara de cuentas" (0:21) reads as a literal translation of "beaded chandelier". The
  listener suggests "lámpara colgante decorativa".
