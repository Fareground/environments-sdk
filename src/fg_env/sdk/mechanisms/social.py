"""Social mechanisms: communication, deliberation, networks, diffusion, beliefs, relations.

Each mode lives in its own module and registers on import:

* ``social.channels`` — rooms, private groups, direct messages, broadcasts, unread tracking, mentions.
* ``decision.deliberation`` — discussion until everyone is ready, floor control, motions, amendments, votes.
* ``social.feed`` — follows, friends, blocks, posts, reposts, reactions, ranked feeds, trending.
* ``social.diffusion`` — independent cascade and linear threshold spread with exposure counts and reach.
* ``mind.beliefs`` — per-agent facts with confidence, source and decay; ``learn`` and ``tell``.
* ``relationships`` and ``factions`` — decaying trust with threshold events; alliances and ``$allies``.

All of them change the world only through its journaled API, so atomic actions, snapshots,
determinism, preview and check hold unchanged.
"""
from __future__ import annotations

from . import beliefs, channels, deliberation, diffusion, feed, relationships  # noqa: F401

__all__ = ["beliefs", "channels", "deliberation", "diffusion", "feed", "relationships"]
