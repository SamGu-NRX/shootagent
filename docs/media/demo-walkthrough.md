# Demo walkthrough video

`demo-walkthrough.mp4` is a silent 6:54 screen recording (1920x1080, H.264, 29 MB) of one real run of the
showcase demo shoot, recorded on 2026-10-28. Nothing in it is a replay or demo data. The four agent runs
were:

| Run | Mode | Cost |
|---|---|---|
| `20261028-024412-128f63` | QC | $0.42 |
| `20261028-024938-3a1589` | chat: "remove the cars and trash bins" | $0.34 |
| `20261028-025329-875708` | chat with a click mask: "put the reference image on this screen" | $0.09 |
| `20261028-025517-ea2725` | reel | $0.06 |

Shoot `c93e4a592c`. Total $0.91.

The video keeps real time except for 27 cuts, 6.4 minutes in total, made only while waiting on the agent
when the trace added nothing for more than 8 seconds. Each cut keeps the first 2.5 s and the last 1.5 s of
the gap, so the run clock in the trace panel visibly jumps at those points. The longest cuts are a 38 s wait
for the first QC checks (0:30), 36 s and 44 s waits on generative removals in the chat run (3:37, 3:41), and
the 56 s reel render (6:22). The pointer is drawn by the recorder because headless screen capture has none.

Recorded by `web/scripts/record-walkthrough.mjs` (`record`, then `cut`).

## Timestamps and narration

One line per segment, written to be read over the video.

| Time | On screen | Narration |
|---|---|---|
| 0:00 | Start screen | ShootAgent checks a real-estate shoot against its exposure brackets and takes edit requests in chat. This demo shoot has four AutoHDR edits and four real customer requests. |
| 0:11 | Run QC pressed | I press Run QC and leave the agent to it. |
| 0:23 | Plan | It writes a plan first, then compares every finished photo with its own brackets. |
| 0:32 | Candidate chips | The checks propose candidates with a confidence. Most are legitimate edits, so the agent has to look at each one. |
| 0:49 | regroup brackets | This photo fused two camera positions. The agent splits it back into two views. |
| 0:57 | First failed check (stop sign) | Every edit gets a self-check: pixels outside the mask, similarity to the brackets, and a vision judge. Here the restored stop sign is cut off and soft, so the check fails. |
| 1:00 | Retry, try 2 of 3 | The loop reverts the edit and the agent tries again with a different mask. |
| 1:20 | First passed check | The second attempt passes, with zero pixels changed outside the mask. The doubled house number above the front door goes through the same fail, retry, pass. |
| 2:21 | Verdicts on 13 candidates | It closes every candidate: three fixed and ten dismissed, each with its own reason. |
| 2:41 | QC finished | The run took about five minutes of real time and cost 42 cents. |
| 2:53 | Stop-sign photo | The finished photo had erased this stop sign. |
| 3:05 | Slider across the sign | Before on the left, after on the right. The sign is back and the rest of the photo is untouched. |
| 3:16 | Chat request typed | Now a customer request across the whole shoot: remove the cars and trash bins. |
| 4:05 | Per-photo results | It edited three photos: the cars on the driveway, the truck on the street, and the trash bin in the bedroom. Every edit passed its check. |
| 4:09 | Not needed, with reasons | The other seven photos say why nothing was done, photo by photo. |
| 4:21 | Truck before/after | Here is the truck it removed from the street. |
| 4:27 | Bedroom TV photo | For one specific object, I shift-click it. |
| 4:30 | Shift-click mask | The clicks become a mask, which goes with my next message. |
| 4:39 | Second request sent | Put the reference image on this screen. |
| 5:09 | Result | Applied. |
| 5:16 | Failed check, then retry | The first try broke the TV mount, and the judge caught it. The agent found a tighter screen-only mask, and the second try passed. |
| 5:28 | TV before/after | The customer's photo is on the screen, and the mount and wall are unchanged. |
| 5:31 | Approving photos | I approve the photos for delivery. |
| 6:12 | Build reel | Build reel: the agent labels the rooms, orders them and renders a 9:16 listing reel. |
| 6:25 | Reel playing | Twenty-four seconds, eight photos, ready to post. |
