"""What the kernel property tests run over, and the canonical form of a run's state they compare.

The corpus is every example contract that loads (made small by ``_leaks.SMALL``) and the random valid contracts of
``_fuzz.py`` that check clean. The default run takes a few of each; ``@pytest.mark.slow`` variants take all of them
over more seeds. Nothing here is cached across tests: every test builds the runs it needs and drops them.

:func:`undoable_state` is the one definition of "the world is as it was" the tests hold the engine to: every piece
of run state an undo must bring back, and nothing that is spent for good (luck: the main stream and ``firings``) or
only a cache or a diagnostic count: the run's canonical state (``RunState.encode``) as far as an undo brings it back.
"""
import hashlib
import json
from pathlib import Path
from typing import Any

import _fuzz
import pytest
from _leaks import EXAMPLES, SMALL

import fg_env
from fg_env.contract.base import TAPE
from fg_env.participants import RandomAgent
from fg_env.runtime.state import UNDONE

#: Seeds the default run plays each property with, and the slow variants.
SEEDS_FAST = (1,)
SEEDS_SLOW = tuple(range(1, 21))
#: Fuzz seeds: the first few for the default run (skipped when a seed does not check clean), many more when slow.
FUZZ_FAST = range(5)
FUZZ_SLOW = range(5, 105)
#: A spread of examples for the default run: sealed and sequential stages, atomic stages, hidden information,
#: explicit turn order, crowds, spaces and physics.
FAST_EXAMPLES = [path for path in EXAMPLES if path.stem in {
    "auction_house", "blackjack", "boltzmann_wealth", "climate_club", "coffee_market", "hopscotch_race", "kuhn_poker",
    "lemonade_stand", "prediction_market", "werewolf"}]


def clean_fuzz(seed: int) -> dict[str, Any] | None:
    """The fuzz contract of ``seed``, or None when it does not check clean."""
    contract = _fuzz.contract(seed)
    if any(issue.severity == "error" for issue in fg_env.check(contract, rounds=0)):
        return None
    return contract


def clean_seed(seed: int) -> int:
    """``seed``, when its fuzz contract checks clean; otherwise the test is skipped."""
    if clean_fuzz(seed) is None:
        pytest.skip("the generated contract does not check clean")
    return seed


def source(subject: Any) -> Any:
    """A contract to load: an example path as it is, a fuzz seed as its generated contract."""
    return subject if isinstance(subject, Path) else _fuzz.contract(subject)


def load(subject: Any, seed: int, **options: Any) -> fg_env.Env:
    """``subject`` (an example path or a fuzz seed) loaded small under ``seed``."""
    inputs = SMALL.get(subject.stem) if isinstance(subject, Path) else None
    return fg_env.load(source(subject), seed=seed, inputs=inputs, **options)


def agent_ids(env: fg_env.Env) -> list[str]:
    return [e.id for e in env.world.entities.values() if env.contract.is_agent(e.entity_type)]


def events_sha256(result: fg_env.RunResult) -> str:
    return hashlib.sha256(json.dumps(result.events, sort_keys=True, default=str).encode()).hexdigest()


def undoable_state(env: fg_env.Env) -> dict[str, Any]:
    """Everything an undo must restore, as plain data: the parts of the run's canonical state an undo brings back
    (``RunState.encode`` restricted to ``runtime.state.UNDONE``), and the world's indexes, which must agree with the
    store. The journal's version is left out: versions are unique across runs, so only the undo tests compare it (with
    the same run's)."""
    w, encoded = env.world, env.state.encode()
    state = {key: encoded[key] for key in sorted(UNDONE)}
    # A relation's links compare as a set: an undone unlink puts its edge back last.
    state["links"] = {kind: sorted(rows) for kind, rows in state["links"].items()}
    state["counters"] = {kind: count for kind, count in state["counters"].items() if count}  # a count of 0 is no count
    state["used_round"] = {actor: {name: n for name, n in used.items() if n}  # a use undone to 0 is no use
                           for actor, used in sorted(state["used_round"].items()) if any(used.values())}
    state.update({
        "alive": {kind: [e.id for e in w.alive_of(kind)] for kind in env.contract.types},
        "adjacent": {kind: {a: dict(linked) for a, linked in pairs.items() if linked}  # no edges is no entry
                     for kind, pairs in w.adjacent.items()},
        "entry_seqs": sorted(w.entry_by_seq),
        "record_authors": {name: {repr(key): list(rows) for key, rows in owners.items()}
                           for name, owners in w.record_authors.by_record.items()},
        "record_events": {str(record): {repr(key): [event.seq for event in events] for key, events in owners.items()}
                          for record, owners in w.record_events.groups.items()},
    })
    return state


def restored_state(env: fg_env.Env, *, luck: bool = False) -> dict[str, Any]:
    """:func:`undoable_state` as an undo must bring it back: without the host answers the run recorded
    (``host_tape``), which like luck are kept once asked for (see host/tape.py). With ``luck`` — for an attempt that
    drew nothing — the luck too (every site's firings and the main stream): an attempt that drew nothing and was
    undone leaves the world byte for byte as it found it."""
    state = undoable_state(env)
    state["props"] = {key: value for key, value in state["props"].items() if key != TAPE}
    if luck:
        encoded = env.state.encode()
        state.update(firings=encoded["firings"], rng=encoded["rng"])
    return state


def same_bytes(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two states are the same byte for byte in their canonical JSON."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


class ConcurrentRandom(RandomAgent):
    """A random agent the engine may play on worker threads: sealed turns of a simultaneous stage run concurrently
    (up to the run's ``parallel``), which must play exactly as inline."""

    concurrent = True
