"""Studio: turns a ReelConcept into a finished 9:16 reel (creative/render.py is the entry point).

It builds on the listing reel renderer (reel/render.py): photos move by sub-pixel affine warps, type is set with
PIL into premultiplied layers, and frames are drawn on a thread pool and piped into one ffmpeg process. On top
of that it adds what creator reels are made of: kinetic type, transitions, a generated score cut to the beat with
sound effects, a macOS `say` voiceover with word-synced captions, and Reel Studio's animation cards (vendored
under reelstudio/, MIT).

    plan.py      concept -> timed shots, voice lines, sound effects, timeline entries
    engine.py    the whole render: voice, plan, cards, photos, score, frames, cover
"""
