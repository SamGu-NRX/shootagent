# Submission kit

Text to paste into the hackathon forms, and the Loom shot list. Numbers come from the README's Results
section; update both together if a rerun changes them.

## Write-up (294 words)

**Problem.** AutoHDR edits about one in ten US real-estate listings with AI. When an edit goes wrong, a
person finds it: two camera positions fused into one photo, a lamp erased along with the tripod, a blue wall
turned grey. Then the listing still needs marketing: a video, a listing page, answers for buyers, and in
California a disclosure for every altered photo.

**Who it helps.** Real-estate photographers, who deliver dozens of photos per listing, and the agents
they shoot for.

**Solution.** ShootAgent is a Claude agent that works on a whole shoot through typed tools. Its QC pass
fuses each view's exposure brackets into a reference, compares the finished photo against it, and fixes
real defects by restoring from the brackets, splitting wrongly merged views or recolouring. Customers make
chat edits across the shoot, and pixels outside each edit stay byte-identical. Then it launches the
listing: a narrated 9:16 film in English and Spanish, where every Grok-generated clip is checked against
the real photos and replaced with motion made from the photo's own pixels if it invents anything; a buyer
page that answers questions from the photos and books tours to the agent's phone; and a disclosure page
pairing each edited photo with its original. Every plan, tool call, check and retry streams to a live trace.

**Impact.** On AutoHDR's 63 defect cases it fixed all 21 grouping and ghosting errors and 31 of 59 defects
overall, without editing a clean photo, for about $0.07 per photo. On the demo house it found that all
seven finished photos merged two camera positions, and recovered four more deliverable photos. Grok
invented something in 10 of the 14 clips we generated, including a person in a shower window; none reached
the final film.

## Loom shot list (target 3:30)

1. **0:00 to 0:20, the problem.** The demo house's finished bathroom photo beside its brackets: AutoHDR
   merged two camera positions, and the freestanding tub seen from one of them doesn't appear. One sentence:
   editing pipelines make mistakes a photographer has to catch by hand.
2. **0:20 to 1:00, QC pass.** Open the demo house and play its QC trace: the plan, the grouping check
   finding two camera positions in every photo, the split, and the bathroom part with the tub. The 21/21
   grouping result in one line.
3. **1:00 to 1:30, chat across the shoot.** On the showcase shoot, send "remove the cars and trash bins":
   per-photo results, applied or not needed with the reason, and the outside-mask delta of 0 in the check row.
4. **1:30 to 2:30, launch the listing.** Open Launch listing. Walk the storyboard: Grok's rejected clips
   with the checker's reasons (an outlet, a door, a vanity, and the person in the shower window) and the
   real-photo moves that replaced them. Play the English film, then switch to Spanish.
5. **2:30 to 3:10, the buyer side.** Publish, scan the QR code with a phone, ask "Is there a bathtub in the
   primary bath?", then book a tour; the agent's phone gets the push notification. Open the disclosure page.
6. **3:10 to 3:30, numbers.** 31/59 defects fixed, 21/21 grouping, Grok invented something in 10 of 14
   clips and none shipped. Close on the repo URL.

Record at 1920x1080 with the browser at 100% zoom. Show real runs and their traces, not the demo-mode
replay. The older silent walkthrough of the QC and chat passes, docs/media/demo-walkthrough.mp4, can fill
in for steps 2 and 3.

## Checklist from the brief

- [x] Public repo with quick start, stack, architecture diagram, `.env.example`, datasets and provenance,
      limitations and next steps
- [ ] Loom, 2 to 5 minutes (shot list above)
- [ ] Deployed URL for AutoHDR Track 3: https://shootagent.sillion.app (served from the demo MacBook through a
      named Cloudflare tunnel; passcode in the git-ignored .env.local on that machine). Their form:
      https://docs.google.com/forms/d/e/1FAIpQLSdDi6tLfJ_eglXgwOMU3p2QoSTvKf0CxKr4t82ZeDjoWZdR9A/viewform
- Tracks: AutoHDR Track 3 (conversational editing) and Antler (Agents). No separate Track 2 entry.
- [x] Team roster: Sam Gu, Bryan Wang
- [x] Write-up, 150 to 300 words (above)
