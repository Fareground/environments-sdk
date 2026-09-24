"""One turn's accounting: what it may still do, what it has used, and — in an atomic turn — the part an undo returns to.

The one decision of what an attempt costs is :func:`attempt_cost`: an attempt refused after it drew luck or read a value
hidden from its agent is spent — it counts as a use of the action, for good — and any other refusal is free. A free
retry of a spent one would let an agent reroll its luck, or probe the hidden value again and again.

An atomic turn (a stage with `valid` rules) plays in parts: a part begins at a world mark and the ledger's checkpoint,
and undoing it rolls the world back to the mark and the ledger back to the checkpoint. The world's journal undoes the
part's uses of actions this round with it; a spent attempt stays spent, so the checkpoint takes it in.
"""
from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..assets.delivery import Attachment
    from ..world.live import SdkWorld
    from ..world.randomness import Observation

__all__ = ["AttemptLedger", "Pending", "attempt_cost"]

#: Every pending list's versions come from one count, so no two different lists anywhere share a version.
_VERSIONS = itertools.count(1)


def attempt_cost(observed: Observation) -> Literal["free", "spent"]:
    """What a refused attempt costs, ``observed`` from its start: spent when working it out drew luck or read a value
    hidden from the actor, else free."""
    return "spent" if observed.drew or observed.read_hidden else "free"


class Pending:
    """What a turn already did (sequential) or submitted (simultaneous), as the turn's rules read it (``$pending``:
    :attr:`items`), with a version for caches of what was worked out from it. Appending moves the version to one never
    used before; cutting the list back brings back the version it had at that length, since it holds the same items."""

    __slots__ = ("items", "_versions")

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        #: The version at each length the list has had, up to its current one.
        self._versions = [next(_VERSIONS)]

    @property
    def version(self) -> int:
        return self._versions[-1]

    def append(self, item: dict[str, Any]) -> None:
        self.items.append(item)
        self._versions.append(next(_VERSIONS))

    def truncate(self, length: int) -> None:
        """Keep the first ``length`` items."""
        del self.items[length:]
        del self._versions[length + 1:]

    def copy(self) -> Pending:
        copied = Pending.__new__(Pending)
        copied.items, copied._versions = list(self.items), list(self._versions)
        return copied

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


class AttemptLedger:
    """The accounting of one turn by ``actor_id`` in ``world``: ``max_actions`` actions and ``max_calls`` tool calls,
    and as many free reads; ``atomic`` turns play in parts."""

    def __init__(self, world: SdkWorld, actor_id: str, max_actions: int, max_calls: int, atomic: bool):
        self.world = world
        self.actor_id = actor_id
        self.max_actions = max_actions
        self.max_calls = max_calls
        self.atomic = atomic
        self.calls_left = max_calls
        #: Looks and inspects that do not spend a call (see :mod:`fg_env.information.reads`); below zero, the refused
        #: ones.
        self.reads_left = max_calls
        self.actions_left = max_actions
        #: This turn's uses of each action (this round's are the world's: ``world.used_round``).
        self.used: dict[str, int] = {}
        #: Sealed choices submitted, to commit when everyone has chosen.
        self.intents: list[tuple[str, dict[str, Any]]] = []
        self.pending = Pending()
        #: Actions applied this turn (a part's undone ones are taken back).
        self.applied = 0
        #: Atomic turns: the world mark the open part is undone to (None: no part is open), the ledger there
        #: (actions left, uses, pending length, applied), and the uses counted in the part.
        self.mark: int | None = None
        self._checkpoint: tuple[int, dict[str, int], int, int] = (max_actions, {}, 0, 0)
        self._counted: list[str] = []
        #: Atomic turns: the outcome texts (and files) of the part's actions, shown once the part commits — an undone
        #: part must not leave its agent knowing what it showed — and those committed, shown with the next result.
        self._held: list[tuple[str, list[Attachment]]] = []
        self._committed: list[tuple[str, list[Attachment]]] = []
        if atomic:
            self.begin_part()

    def copy(self, world: SdkWorld) -> AttemptLedger:
        """This ledger, over the copy ``world`` of its world (no part may be open)."""
        assert self.mark is None, "an open part is not copied"
        copied = AttemptLedger.__new__(AttemptLedger)
        copied.__dict__.update(self.__dict__)
        copied.world = world
        copied.used, copied.intents, copied.pending = dict(self.used), list(self.intents), self.pending.copy()
        copied._counted, copied._held, copied._committed = list(self._counted), list(self._held), list(self._committed)
        return copied

    def spend_call(self) -> bool:
        """Spend one tool call; False when none is left."""
        if self.calls_left <= 0:
            return False
        self.calls_left -= 1
        return True

    @property
    def acted(self) -> bool:
        """Whether the turn has taken an action or submitted a choice."""
        return self.actions_left < self.max_actions or bool(self.intents)

    # -- uses ------------------------------------------------------------------------------------------------------

    def refused(self, name: str, observed: Observation) -> bool:
        """An attempt at ``name`` was refused: spend it when :func:`attempt_cost` says so. Whether it was spent."""
        if attempt_cost(observed) == "free":
            return False
        self._count(name, spent=True)
        return True

    def submitted(self, name: str, args: dict[str, Any], entry: dict[str, Any]) -> None:
        """A sealed choice of ``name`` with ``args`` was submitted; ``entry`` is what ``$pending`` shows of it."""
        self.intents.append((name, args))
        self.pending.append(entry)
        self._count(name)

    def took(self, name: str) -> None:
        """An action of ``name`` applied (its ``$pending`` entry already added)."""
        self._count(name)
        self.applied += 1

    def _count(self, name: str, spent: bool = False) -> None:
        """Count one use of ``name``: this turn's and, in the world, this round's. An atomic turn's uses are undone with
        the part they were made in, but for a ``spent`` one, which the part's checkpoint takes in, so undoing the part
        keeps it. Elsewhere a use stands once counted (its change has committed, or it is a sealed choice)."""
        self.used[name] = self.used.get(name, 0) + 1
        self.world.count_use(self.actor_id, name, undoable=self.atomic and not spent)
        self.actions_left -= 1
        if spent and self.mark is not None:
            actions_left, used, pending, applied = self._checkpoint
            self._checkpoint = (actions_left - 1, {**used, name: used.get(name, 0) + 1}, pending, applied)
        if self.atomic:
            self._counted.append(name)

    # -- parts (atomic turns) --------------------------------------------------------------------------------------

    @property
    def part_open(self) -> bool:
        return self.mark is not None

    @property
    def counted_in_part(self) -> bool:
        """Whether the open part used an action (then the stage's `valid` rules judge it)."""
        return bool(self._counted)

    def begin_part(self) -> None:
        """Start the part of the turn that the next settle checks and an undo returns to."""
        self.mark = self.world.journal.mark()
        self._checkpoint = (self.actions_left, dict(self.used), len(self.pending), self.applied)
        self._counted.clear()

    def hold(self, text: str, files: list[Attachment]) -> None:
        """Keep an action's outcome from its agent until its part commits."""
        self._held.append((text, files))

    def commit_part(self) -> None:
        """The open part stands: what its actions showed is released with the next result."""
        self.mark = None
        self._committed += self._held
        self._held.clear()

    def undo_part(self) -> int:
        """Undo the open part — the world to its mark, with the part's uses of actions this round, and the ledger to its
        checkpoint; what the part drew stays spent. How many applied actions were undone."""
        assert self.mark is not None
        actions_left, used, pending, applied = self._checkpoint
        self.world.journal.rollback(self.mark)
        self._counted.clear()
        self._held.clear()
        self.used = dict(used)
        self.pending.truncate(pending)
        self.actions_left = actions_left
        undone, self.applied = self.applied - applied, applied
        return undone

    def released(self) -> list[tuple[str, list[Attachment]]]:
        """The outcomes of committed parts not yet shown, taken out of the ledger."""
        released, self._committed = self._committed, []
        return released
