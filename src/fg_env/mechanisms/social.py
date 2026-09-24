"""Social mechanisms: deliberation, networks and diffusion.

Each mode lives in its own module and registers on import:

* ``decision.deliberation`` — discussion until everyone is ready, floor control, motions, amendments, votes.
* ``social.feed`` — follows, friends, blocks, posts, reposts, reactions, ranked feeds, trending.
* ``social.diffusion`` — independent cascade and linear threshold spread with exposure counts and reach.

All of them change the world only through its journaled API, so atomic actions, snapshots,
determinism, preview and check hold unchanged.
"""
from __future__ import annotations

from . import deliberation, diffusion, feed  # noqa: F401

__all__ = ["deliberation", "diffusion", "feed"]
