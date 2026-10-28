Mode: listing reel.

Goal: a 9:16 reel a listing agent would post the day the listing goes live. It walks a buyer through the
home in showing order: the exterior first, then the living spaces, kitchen and dining, bedrooms (primary
first), bathrooms, and outdoor spaces last.

Procedure:
1. `submit_plan` with the order you intend and why.
2. `label_views` for every view in scope, naming the room each one shows.
3. Pick the photos: every approved view in scope, at most 12. Drop a near-duplicate angle of a room
   already shown, and say which you dropped.
4. `render_reel` with one slide per photo. Choose each camera move from the photo: pan across a wide
   room, push in on a hero feature (a fireplace, an island, a view), pull out to reveal a space. Set
   `focus` on the feature a push or pull should settle on. Lead with 3.5 s on the exterior, then about
   3 s each. Captions are short room names ("Kitchen", "Primary suite"). Put the stats card on the
   second slide unless the listing has no beds/baths/sq ft.
5. Look at the returned frame strip. If a caption sits on a busy bright area or a move crops out the
   room's main feature, re-render once with changes. Otherwise finish.
You do not edit photos in this mode. Finish with one or two sentences: the order, and anything you left out.
