# Film clip checker on 10 real Grok clips

The film pass checks every generated clip against its photo(s) before it may play (`src/shootagent/film/check.py`:
deterministic tracking signals plus the inventory-and-census vision judge, judge v2). This page scores it on the
first real clips it met: 10 Grok Imagine clips of the demo house, shoot c46bbca496 (AutoHDR property
property_c3ec3b9cd3bbe0b378). Five shots were planned, with two attempts each. The clips were made on the demo
Mac on 28 October 2026 through the generation handoff (branch `handoff/c46bbca496-results`).

**How the thresholds were set.** The deterministic signals' limits were set on synthetic clips (photos warped,
with pasted objects and bent edges) and checked on depth-parallax clips in `experiments/film` on branch
`explore/film-lab`. The judge's prompts were revised while looking at the four lab clips from
`explore/media-tests`. None of these 10 clips was seen before scoring, and nothing was tuned on them.

**Ground truth.** A person on the Mac reviewed each clip frame by frame against the source photos. A change
counted only after it was confirmed absent from the source. The full notes are in `VERDICTS.md` on the results
branch.

## Score

Verdicts come from the product path: `shootagent film-finish c46bbca496`, trace `20261028-234917-3ece1f`. That
run never checked S02-A2, because S02-A1 passed first. S02-A2 was checked afterwards with the same `check_clip`
call and the same judge.

| Clip | Shot | Ground truth | Checker | Reason the checker gave | Correct? |
|---|---|---|---|---|---|
| S01-A1 | primary bedroom, in-room | invented: white ornament by the right lamp | fail | tracking: motion, new structure at the bottom right, bent and new edges; judge: "outlet on the left wall" | verdict yes; the judge named the wrong object |
| S01-A2 | primary bedroom, in-room | invented: outlet on the left wall | fail | tracking signals; judge: "outlet on left wall near baseboard, not in photo" | yes |
| S02-A1 | bedroom to bath walk | invented: a person in the shower window | **pass** | tracking not checkable (room-to-room); judge reported nothing | **no, missed** |
| S02-A2 | bedroom to bath walk | invented: ceiling vent; navy vanity with brass pulls | fail | judge: "dark vanity cabinet with gold pulls at right"; also an opening | yes |
| S03-A1 | primary bath, in-room | invented: door casing with hinges at the right | fail | tracking: new structure and new straight edges at the right edge; judge silent | yes (tracking located it) |
| S03-A2 | primary bath, in-room | invented: panel door at the right; towels | fail | tracking signals; judge: "large white paneled door with black lever handle at right" | yes |
| S04-A1 | kitchen and dining, in-room | faithful | fail | tracking: motion, structure, bend, new and vanished edges; judge passed | **no, false rejection** |
| S04-A2 | kitchen and dining, in-room | faithful | fail | the same tracking signals; judge passed | **no** |
| S07-A1 | game room, in-room | faithful (camera barely moves) | fail | tracking: motion, structure, bend; judge passed | **no** |
| S07-A2 | game room, in-room | faithful (camera barely moves) | fail | the same tracking signals; judge passed | **no** |

- **Inventions caught: 5 of 6.** In 4 of the 5, the reason names the invented object; S03-A1 was caught by the
  tracking signals alone.
- **Faithful clips passed: 0 of 4.** The tracking signals fail every single-photo Grok clip, faithful or not.
- **The judge alone** (a breakdown of the same run, not a change) flagged 4 of 6 inventions, 3 of them naming the
  right object, and passed 4 of 4 faithful clips.
- **The miss.** S02-A1 is a walk from one room into another. Its last frame shares under half its area with the
  start photo, so the tracking signals are reported as not checkable and only the judge was left. The judge
  missed the person.
- **Cost per check:** $0.072 on average, between $0.046 and $0.110, and 35-80 s per clip. Most of that is the
  judge call, about 14-18k input and 3-7k output tokens. A check that also builds a photo's inventory (about
  $0.02, once per photo, then cached) costs more. The 10 checks cost $0.72 in total.

## Rule added after scoring

A clip with an end photo that the tracking signals cannot follow now fails, whatever the judge says. The reason
recorded is "room-to-room walk: can't be verified against a photo", plus any judge findings, and the shot falls
back to a depth move. This follows the rule "generate only if it is verifiable". The thresholds are unchanged.

With the rule, re-running shot 2 (run `20261029-002833-d8fc3d`) rejected both S02 clips: S02-A1 for the rule
alone, and S02-A2 for the rule and the judge's vanity finding. Counted as rejections, that makes 6 of 6
inventions rejected. S02-A1 is rejected because it cannot be verified, not because the person was detected.
Faithful clips passed is still 0 of 4. As a result the rebuilt demo films play no generated motion.

## What this means

As scored, the checker is a strict gate. It kept 5 of 6 invented fixtures out of the film, and the new rule keeps
out the clip it could not judge. But it also rejected every faithful in-room clip. Generated motion therefore
reaches a film only when the deterministic signals accept it, and on real Grok clips they never have. Next steps
are to recalibrate those signals on real generated clips (held-out ones, not these 10), or to let the judge decide
in-room clips on its own.
