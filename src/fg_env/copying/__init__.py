"""Copies of a run — each a copy of its state (``Env.copy``): branches to look ahead on (:mod:`.branch`), forks under
changes (:mod:`.forks`), snapshots (:mod:`.snapshot`, and the previous format's reader :mod:`.legacy_snapshot`),
previews (:mod:`.previews`), and copies stepped or piloted by a caller (:mod:`.stepping`, :mod:`.pilot`); and playing
back what participants did (:mod:`.replay`)."""
