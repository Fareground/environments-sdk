"""`keep` for a record whose entries not every agent sees: each reader keeps its own latest entries.

A record's `keep` shows each reader its latest ``keep`` entries. Where every agent sees every entry, those are the
record's latest, and the store keeps just them. Where some entry is hidden from some agent (a directed entry, a
`visible` rule), counting every entry would let a reader's own entries fall out of its view as others whisper among
themselves — how many it could see would tell it how many it could not. So each reader counts only the entries it
sees: its window is its latest ``keep`` visible entries, and game logic's is the record's latest ``keep``. The store
keeps an entry while some window holds it, so what each reader sees never depends on what is hidden from it.

:class:`KeptWindows` tracks each living agent's window as entries are posted (an entry enters the window of every
agent that sees it when it is posted), so a post costs one visibility test per agent, never a scan of the record.
A window is rebuilt from the kept entries after a restore or a copy. What a reader reads (and hears as news) is its
latest ``keep`` visible entries among those kept, worked out as it reads (world/evaluation.py).
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from .parts import Entry

if TYPE_CHECKING:
    from ..expr.objects import Entity
    from .store import World

__all__ = ["KeptWindows"]

#: What one post changed, for its undo: each agent whose window the entry entered, and the entry that left it then.
Moves = list[tuple[str, int | None]]


class KeptWindows:
    """Each agent's window over record ``name`` (its latest ``keep`` visible entries), and how many windows hold each
    kept entry (see the module docstring)."""

    def __init__(self, world: World, name: str, keep: int):
        self.world, self.name, self.keep = world, name, keep
        self.windows: dict[str, deque[int]] = {}
        #: Kept entry → how many agents' windows hold it.
        self.holders: dict[int, int] = {}
        self.rebuild()

    def rebuild(self) -> None:
        """Every agent's window from the entries the store keeps now (after a restore or a copy)."""
        self.windows, self.holders = {}, {}
        rows = self.world.records_store[self.name]
        for agent in self._agents():
            window = self._window_of(agent, rows)
            self.windows[agent.id] = window
            for seq in window:
                self.holders[seq] = self.holders.get(seq, 0) + 1

    def posted(self, entry: Entry) -> tuple[Moves, list[Entry]]:
        """Enter ``entry`` (just appended to the record) into the window of every agent that sees it; the entries no
        window holds any more, nor the record's latest ``keep``, are dropped from the store. What moved, and what was
        dropped."""
        world, keep, seq = self.world, self.keep, entry["seq"]
        rows = world.records_store[self.name]
        moves: Moves = []
        left: list[int] = []
        for agent in self._agents():
            window = self.windows.get(agent.id)
            if window is None:  # an agent that joined since: its window from what is kept, before this entry
                window = self.windows[agent.id] = self._window_of(agent, rows[:-1])
                for held in window:
                    self.holders[held] = self.holders.get(held, 0) + 1
            if not world.evaluation.entry_visible(self.name, entry, agent):
                continue
            window.append(seq)
            self.holders[seq] = self.holders.get(seq, 0) + 1
            out = window.popleft() if len(window) > keep else None
            if out is not None:
                self._release(out)
                left.append(out)
            moves.append((agent.id, out))
        if len(rows) > keep:
            left.append(rows[-keep - 1]["seq"])  # it left the record's latest: game logic's window
        latest = rows[-keep]["seq"] if len(rows) >= keep else 0
        gone = {out for out in left if out < latest and not self.holders.get(out)}
        dropped = [row for row in rows if row["seq"] in gone] if gone else []
        if dropped:
            rows[:] = [row for row in rows if row["seq"] not in gone]
        return moves, dropped

    def undo(self, seq: int, moves: Moves) -> None:
        """Take back the post of entry ``seq``, which made ``moves``."""
        for agent_id, out in reversed(moves):
            window = self.windows[agent_id]
            window.pop()
            self._release(seq)
            if out is not None:
                window.appendleft(out)
                self.holders[out] = self.holders.get(out, 0) + 1

    def _release(self, seq: int) -> None:
        count = self.holders.get(seq, 0) - 1
        if count > 0:
            self.holders[seq] = count
        else:
            self.holders.pop(seq, None)

    def _window_of(self, agent: Entity, rows: list[Entry]) -> deque[int]:
        visible = self.world.evaluation.entry_visible
        seen: list[int] = []
        for row in reversed(rows):
            if len(seen) == self.keep:
                break
            if visible(self.name, row, agent):
                seen.append(row["seq"])
        return deque(reversed(seen))

    def _agents(self) -> list[Entity]:
        world = self.world
        roots = {world.contract.lineage(kind)[0] for kind in world.hidden.agents}
        return [agent for kind in sorted(roots) for agent in world.alive_of(kind)]
