# Listing walkthrough video

`listing-walkthrough.mp4` is a 3:07 screen recording (1920x1080, H.264 with AAC, 15 MB) of the listing-launch
flow on the 7-photo demo house (shoot `c46bbca496`), recorded on 2026-10-29 against a copy of the final demo
data. It runs in real time with no cuts. It is silent except from 1:15 to 1:37, where the film plays with its
own narration and music, English and then Spanish, so the voice-over can pause there.

What is on screen is recorded data: the QC run `20261028-202226-9e831c` ($0.45) that split each photo into its
two camera positions, and the film-finish run `20261029-002833-d8fc3d` ($0.27) whose 10 generated clips all
failed the check, so every shot is a depth move. The buyer question was answered live by the Q&A model during
the recording. The tour booking is made in the recording and shows up as the realtor's newest lead. The
listing details are the sample listing ("Sample Home, High Plains"), marked "Sample listing" on every page.
The QR dialog warns that the address only opens on this computer, because the recording ran on a local server.

Recorded by `web/scripts/record-listing-walkthrough.mjs` (`record`, then `finish`, which lays the film's audio
under the seconds the film plays). The pointer is drawn by the recorder because headless capture has none.

## Timestamps and narration

One line per segment, written to be read over the video.

| Time | On screen | Narration |
|---|---|---|
| 0:00 | Workspace, primary bedroom | This is the demo house: seven finished photos from AutoHDR, each with its camera brackets. |
| 0:07 | Trace of the QC run | QC found the same problem in every photo: each one blended brackets from two camera positions. |
| 0:29 | Primary bathroom, the delivered photo | Here is the delivered bathroom photo, two viewpoints fused into one. |
| 0:37 | Its split part with the freestanding tub | ShootAgent split it into one photo per position. This one shows the tub and both vanities. |
| 0:47 | Launch listing | Launch listing turns the approved photos into a narrated vertical film. |
| 0:52 | Storyboard, rejected clips | Every AI-generated clip is checked against its photo. This one added an outlet that isn't there. |
| 1:00 | Rejected room-to-room walk | A walk from one room into another can't be checked against a single photo, so it never passes. |
| 1:06 | Rejected clip with an invented door | This one put a door where the photo shows a wall. Each rejected shot falls back to a depth move. |
| 1:15 | English film plays (with sound) | (pause for the film's own narration) |
| 1:31 | Switch to Spanish | The same film in Spanish, picked up on the same shot. |
| 1:39 | QR code | Publish gives a QR code for the buyer page. |
| 1:46 | Buyer page on a phone | The buyer opens it on a phone: the film first, then the rooms and the details. |
| 1:57 | Buyer asks about a bathtub | Buyers can ask about the home. Answers come only from the photos and the listing. |
| 2:14 | The answer | It found the tub in the bathroom photo. When the photos can't answer, it says the agent will follow up. |
| 2:22 | Book a tour | Booking a tour takes a name, a contact and a time. |
| 2:31 | Request sent | With an ntfy topic set, the realtor gets a push notification right away. |
| 2:36 | Realtor's lead view | On the realtor's phone: the new lead, the requested time, and what the buyer asked. |
| 2:48 | Disclosure page | California's AB 723 requires showing what changed. Every listed photo sits next to its original. |
| 3:01 | Film section of the disclosure | No shot uses AI motion, and the ten rejected clips are on record. |
