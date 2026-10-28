# Submission kit

Text to paste into the hackathon forms, and the Loom shot list. Numbers come from the README's Results
section; update both together if a rerun changes them.

## Write-up (265 words)

**Problem.** AutoHDR edits about one in ten US real-estate listings with AI. When an edit goes wrong, a
person finds it: two rooms fused into one photo, a lamp erased along with the tripod, a blue wall turned
grey. Then the customer asks for more ("remove the cars", "use #C8C2B7 on the floors") and someone does
that by hand too.

**Who it helps.** Real-estate photographers and editing teams who deliver dozens of photos per listing
and fix complaints one at a time.

**Solution.** ShootAgent is a Claude agent that works on the whole shoot through typed tools. Its QC pass
fuses each view's exposure brackets into a reference and compares the finished photo against it. Claude
judges each disagreement from side-by-side crops, then fixes real defects by restoring from the brackets,
regrouping them, or recolouring. Customers make chat requests, and the agent decides per photo whether
each one applies. It masks with Grounding DINO and SAM 2.1 and edits through a mask, so pixels outside the
edit stay byte-identical. After every edit the loop runs a check: outside-mask delta, a region metric, and
a vision judge. A failed check reverts the edit and forces a different approach. Approved photos become a
9:16 listing reel. Every plan, tool call, check and retry streams to a live trace.

**Impact.** On AutoHDR's 63 defect cases it fixed all 21 grouping and ghosting errors, and 31 of 59
defects overall, without editing any clean photo. It costs about $0.07 and under a minute per photo. Seven
saved customer requests passed from chat, including removals of cars, bins and wires.

## Loom

The fastest path: narrate over [docs/media/demo-walkthrough.mp4](media/demo-walkthrough.mp4), a silent 6:54
recording of one real showcase run (QC, two chat requests, reel; $0.91 of agent runs). The narration lines
and timestamps are in [docs/media/demo-walkthrough.md](media/demo-walkthrough.md). Trim to under 5 minutes
by cutting the approval clicks (5:31 to 6:12) and part of the verdict block. The same video can serve as
the brief's fallback screen recording.

To record live instead, follow the shot list below.

## Loom shot list (target 3:30)

1. **0:00 to 0:20, the problem.** A finished AutoHDR photo with two rooms fused, beside its brackets. One
   sentence: editing pipelines make mistakes a photographer has to catch by hand.
2. **0:20 to 1:30, QC pass.** Open the "Demo shoot" (`shootagent demo showcase`) and press Run QC.
   Narrate the trace as it streams: the plan, run_qc_checks finding candidates, the agent dismissing
   legitimate edits with reasons, the regroup splitting the fused photo into two views, the ghosting
   restore, and any failed check followed by a retry (amber row). Drag the before/after slider on the
   stop sign. The clean photo and the customer photos stay untouched.
3. **1:30 to 2:30, chat across the shoot.** Type "remove the cars and trash bins": the agent edits the
   driveway and bedroom photos and reports the rest as not needed, with reasons. Then shift-click the TV
   in the bedroom and send "put the reference image on this screen". Show the per-photo
   results: applied, or not needed with the reason. Zoom into the removal and point out the outside-mask
   delta of 0 in the check row.
4. **2:30 to 3:10, reel.** Approve the photos, press Build reel, and play the 9:16 result. The agent
   ordered the rooms and chose the camera moves; the trace shows its reasoning.
5. **3:10 to 3:30, numbers.** The README Results table: 21/21 grouping fixed, 31/59 overall, 0 clean
   photos touched, $0.07 per photo. Close on the repo URL.

Record at 1920x1080 with the browser at 100% zoom. Start the QC run before recording if the room is slow,
and cut to it when the trace is streaming; the Loom should show real runs, not the demo-mode replay.

## Checklist from the brief

- [x] Public repo with quick start, stack, architecture diagram, `.env.example`, datasets and provenance,
      limitations and next steps
- [ ] Loom, 2 to 5 minutes (silent walkthrough ready to narrate: docs/media/demo-walkthrough.mp4)
- [ ] Deployed URL for AutoHDR Track 3: https://shootagent.sillion.app (served from the demo MacBook through a
      named Cloudflare tunnel; passcode in the git-ignored .env.local on that machine). Their form:
      https://docs.google.com/forms/d/e/1FAIpQLSdDi6tLfJ_eglXgwOMU3p2QoSTvKf0CxKr4t82ZeDjoWZdR9A/viewform
- Tracks: AutoHDR Track 3 (conversational editing) and Antler (Agents). No separate Track 2 entry.
- [x] Team roster: Sam Gu, Bryan Wang
- [x] Write-up, 150 to 300 words (above)
