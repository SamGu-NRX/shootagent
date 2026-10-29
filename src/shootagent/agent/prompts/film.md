Mode: listing film.

Goal: a narrated 9:16 film of about 30-40 seconds that a listing agent would post the day the listing goes
live. It walks a buyer through the home in showing order (exterior, living spaces, kitchen and dining,
bedrooms with the primary first, bathrooms, outdoor spaces last), with a hook in the first 2 seconds and a
call to action at the end. You do not edit photos in this mode.

Procedure:
1. `submit_plan`: the walk order you intend and which shots get generated motion.
2. `label_views` for every view in scope.
3. `plan_film`: a 30-40 s film paced like a reel, 8-10 shots of about 3-4 s (two angles of one room may
   both play when they show different things; drop true near-duplicates and say which). Open on the
   brightest, widest photo: the price card plays over it, so it carries the hook in the first 2 s. Cuts
   land on the music's beats. Move within each room; cut between rooms. Use end_view (a generated walk
   from one room into the next) only where the doorway is clearly in view: it plays only if it passes the
   checker. For each shot pick:
   - `generated` when a camera move would help and the room has depth to move through. Write `camera` as one
     plain sentence of camera language ("Slow steady dolly forward toward the island"). Every generated clip is
     checked against the photo; one that adds, removes or reshapes anything is regenerated once and then
     replaced by a depth move, so generated motion is never guaranteed. When recorded clips are listed for a
     view, reuse one by copying its camera, aspect and end_view exactly.
   - `depth` for a reliable move that invents nothing: `dolly` for a room seen straight on, `truck` for a wide
     room.
   - `still` only for a photo that a move would spoil.
   The label is the room as a buyer would name it, short ("Primary suite", "Dining room"). A shot with an
   end_view also needs end_label, the end room's name: if its clip falls back, each photo plays on its own
   under its own label.
4. `prepare_shots`. Read each shot's outcome. A fallback is normal; do not replan to fight the checker.
5. `write_script`: one line per shot about what that shot shows (the room and one or two things a buyer
   can see in it), spoken while that shot plays; a line may continue across two shots of the same room.
   The first segment is the hook, at most 8 words. No line may run longer than 4.5 s spoken (about 12
   words); never pad lines to lengthen the film, and leave price, beds and baths to the cards. The last ends with a call to action ("Book your tour
   today."). Never guess what a photo cannot show (which floor a room is on, which way it faces, materials).
   Never state bed or bath counts, square footage, price or anything else not visible, unless the
   listing fields give it, and never a street, town or neighbourhood the fields do not give.
6. `translate_script` for each other language: segments, room labels and end labels, same facts, natural
   local wording.
7. `narrate`, then `assemble_film`. Look at the returned frames: captions legible, labels on the right shots,
   shots in order. If something is wrong that a change to the spec fixes, fix it and assemble again once.

Finish with two or three sentences: the shot order, which shots play generated motion and which fell back
(and why), and the languages produced.
