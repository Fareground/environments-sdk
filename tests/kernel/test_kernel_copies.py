"""Copy equivalence: a copy of a run (``Env.copy``, the one way a run is copied) continues exactly as the run itself
would, and exactly as the oracle does — the run rebuilt from its build with every recorded participant step played back
(``copying/replay.py``).

At safe points (a run stopped by ``run(stop=...)``, between rounds or part-way through one) a clone, a JSON snapshot
round trip, the replayed oracle and the stopped run itself are in the same state and play on to the result of one
straight run. At decisions (a turn waiting for a call) copies of a stepped run and of a piloted one, and the replayed
oracle, stay alike under the same calls — the same state, the same brief, update and tools, the same call results — to
the same result. This covers every example and generated contract, physics, spaces and atomic turns part-way included.

A copy shares nothing it changes with its original, and the parts of a run hold no state of their own: rebuilt around a
copy of its state, they are what they were (only caches differ).
"""
import json
import random
from contextlib import ExitStack

import pytest
from _corpus import (
    EXAMPLES,
    FAST_EXAMPLES,
    FUZZ_FAST,
    FUZZ_SLOW,
    SEEDS_FAST,
    SEEDS_SLOW,
    agent_ids,
    clean_seed,
    load,
    source,
    undoable_state,
)

import fg_env
from fg_env.copying.branch import Branch, driven_copy
from fg_env.copying.pilot import Pilot, PilotedEnv
from fg_env.copying.replay import Playback, Tape
from fg_env.copying.stepping import SteppedEnv, Stepper
from fg_env.game.runs import ThreadedRun
from fg_env.participants.builtin import sample_args
from fg_env.runtime.turn_tools import wrap
from fg_env.world.copies import _SHARED

ROUNDS = 3


# -- the oracle --------------------------------------------------------------------------------------------------


def _tape(env):
    """Every participant step ``env`` has taken, by turn: the finished turns' from its exposure log, the turns in play
    from themselves."""
    tape = Tape()
    for record in env.world.exposures.wakes:
        tape.turns[record["turn"]] = (record["entity"], [tuple(step) for step in record["steps"]])
    state = env.state
    for turn in [state.cursor.turn, *state.staged] if state.in_round else []:
        if turn is not None and not turn.tallied:
            tape.turns[turn.number] = (turn.actor.id, list(turn.steps))
            if not turn.done:
                tape.open.add(turn.number)
    return tape


def _replayed_to_point(subject, seed, point, env):
    """The oracle of ``env`` stopped at its safe point ``point``: the run rebuilt from its build, its recorded steps
    played back to the same point."""
    oracle = load(subject, seed, exposures=True)
    playback = Playback(_tape(env), env.state.turn_count)
    oracle.run(wrap(oracle, lambda wake: playback.play(wake)), rounds=ROUNDS, stop=_nth(point))
    return oracle


def _replayed_to_decision(root, run, agents):
    """The oracle of ``run`` waiting for a decision: a piloted run rebuilt from the build, its recorded steps played
    back, paused where ``run`` waits."""
    tape, count = run.read(lambda env: (_tape(env), env.state.turn_count))
    env = PilotedEnv(root.contract, root.inputs, root.seed, root.arm, parallel=1, exposures=True,
                     assets=root.world.assets.catalog())
    pilot = Pilot(env, playback=Playback(tape, count), controlled=set(agents))
    pilot.start()
    return _Oracle(Branch(pilot), None)


class _Oracle(ThreadedRun):
    """A replayed run, the oracle copies are held to."""


# -- safe points -------------------------------------------------------------------------------------------------


def _nth(point):
    seen = [0]

    def stop(_env):
        seen[0] += 1
        return seen[0] == point

    return stop


def _rounds_left(env, rounds):
    return None if rounds is None else rounds - env.round + (1 if env.state.in_round else 0)


def _canonical(env):
    return json.loads(json.dumps(env.state.encode(), sort_keys=True, default=str))


def _copies_at_safe_points_continue_exactly(subject, seed, points, rounds=ROUNDS):
    straight = load(subject, seed, exposures=True).run("random", rounds=rounds).to_dict()
    for point in points:
        env = load(subject, seed, exposures=True)
        env.run("random", rounds=rounds, stop=_nth(point))
        if env.status != "stopped":
            break  # the run ended before this point
        left = _rounds_left(env, rounds)
        copies = {"clone": env.clone(),
                  "snapshot": fg_env.Env.restore(source(subject), json.loads(json.dumps(env.snapshot()))),
                  "replay": _replayed_to_point(subject, seed, point, env)}
        for name, copy in copies.items():
            assert _canonical(copy) == _canonical(env), f"the {name} taken at safe point {point} is not the run"
        continued = {name: copy.run("random", rounds=left).to_dict() for name, copy in copies.items()}
        continued["original"] = env.run("random", rounds=left).to_dict()
        for name, result in continued.items():
            assert result == straight, f"the {name} taken at safe point {point} went another way"


@pytest.mark.parametrize("seed", SEEDS_FAST)
@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_copies_of_an_example_at_safe_points_continue_exactly(path, seed):
    _copies_at_safe_points_continue_exactly(path, seed, points=(2, 5))


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_copies_of_a_generated_contract_at_safe_points_continue_exactly(fuzz):
    _copies_at_safe_points_continue_exactly(clean_seed(fuzz), fuzz, points=(2, 5))


@pytest.mark.slow
@pytest.mark.parametrize("seed", SEEDS_SLOW)
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_copies_of_every_example_at_every_early_safe_point_continue_exactly(path, seed):
    _copies_at_safe_points_continue_exactly(path, seed, points=range(1, 16))


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_copies_of_many_generated_contracts_at_every_early_safe_point_continue_exactly(fuzz):
    _copies_at_safe_points_continue_exactly(clean_seed(fuzz), fuzz, points=range(1, 16))


# -- decisions ---------------------------------------------------------------------------------------------------


def _stepped(root, agents):
    """A run stepped on this thread, pausing for every agent."""
    stepper = Stepper(driven_copy(root, SteppedEnv), agents, explicit=False)
    stepper.start()
    return stepper


def _piloted(root, agents):
    """A run piloted on a thread of its own, pausing for every agent."""
    pilot = Pilot(driven_copy(root, PilotedEnv), controlled=set(agents))
    pilot.start()
    return ThreadedRun(Branch(pilot), None)


def _seen(run):
    """The run's state now, and what the waiting agent is shown and offered."""
    pause = run.pause

    def read(env):
        out = {"state": undoable_state(env), "turns": env.state.turn_count, "firings": dict(env.world.luck.firings),
               "status": env.status}
        if pause is not None:
            wake = pause.wake
            out["waiting"] = [pause.kind, wake.entity_id, wake.round, wake.stage, wake.brief, wake.update]
            out["tools"] = [(tool.name, tool.kind, tool.description, tool.input_schema) for tool in wake.tools]
        return out

    return run.read(read)


def _decide(seen, rng):
    acts = [tool for tool in seen["tools"] if tool[1] == "act"]
    if not acts or rng.random() < 0.2:
        return "end_turn", {}
    name, _, _, schema = rng.choice(acts)
    return name, sample_args(schema, rng)


def _in_step(runs, rng, steps):
    """Take the same ``steps`` random calls in every one of ``runs``, which must stay alike throughout. (An oracle's
    exposure log holds the tool sets its playback read, not every one the test read of the others: results are compared
    without it.)"""
    for _ in range(steps):
        seen = [_seen(run) for run in runs]
        assert all(other == seen[0] for other in seen[1:]), "copies differ"
        if runs[0].pause is None:
            break
        name, args = _decide(seen[0], rng)
        results = [run.call(name, args) for run in runs]
        assert len({(r.ok, r.text, r.ended) for r in results}) == 1, f"{name} answered differently"
    results = [_without(run.result().to_dict(), "exposures" if isinstance(run, _Oracle) else None) for run in runs]
    first = _without(results[0], "exposures")
    for run, result in zip(runs[1:], results[1:]):
        assert result == (first if isinstance(run, _Oracle) else results[0]), "results differ"


def _without(result, key):
    return {name: value for name, value in result.items() if name != key}


def _copies_at_decisions_continue_exactly(subject, seed, steps, clone_every, ahead):
    root = load(subject, seed, exposures=True)
    agents = agent_ids(root)
    if not agents:
        pytest.skip("no agent decides anything (its safe points are compared)")
    with ExitStack() as held:
        stepped, piloted = _stepped(root, agents), _piloted(root, agents)
        held.callback(piloted.close)
        rng = random.Random(seed)
        for step in range(steps):
            if stepped.pause is None:
                return
            if step % clone_every == clone_every - 1:
                twins = [stepped.clone(), piloted.clone(), _replayed_to_decision(root, stepped, agents)]
                try:
                    _in_step([stepped, piloted, *twins], random.Random(rng.random()), ahead)
                finally:
                    for twin in twins:
                        twin.close()
                if stepped.pause is None:
                    return
            _in_step([stepped, piloted], rng, 1)


@pytest.mark.parametrize("seed", SEEDS_FAST)
@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_copies_of_an_example_at_decisions_continue_exactly(path, seed):
    _copies_at_decisions_continue_exactly(path, seed, steps=24, clone_every=8, ahead=6)


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_copies_of_a_generated_contract_at_decisions_continue_exactly(fuzz):
    _copies_at_decisions_continue_exactly(clean_seed(fuzz), fuzz, steps=24, clone_every=8, ahead=6)


@pytest.mark.slow
@pytest.mark.parametrize("seed", SEEDS_SLOW)
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_copies_of_every_example_at_many_decisions_continue_exactly(path, seed):
    _copies_at_decisions_continue_exactly(path, seed, steps=80, clone_every=3, ahead=10)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_copies_of_many_generated_contracts_at_many_decisions_continue_exactly(fuzz):
    _copies_at_decisions_continue_exactly(clean_seed(fuzz), fuzz, steps=80, clone_every=3, ahead=10)


# -- what a copy shares ------------------------------------------------------------------------------------------

#: Values a copy may share with its original: nothing changes them in place.
_IMMUTABLE = (str, int, float, bool, type(None), tuple, frozenset, bytes)


def _mutable_shared(original, copy, path, seen, found):
    """Every mutable object reachable from ``copy`` through plain containers that is also ``original``'s (walking the
    two side by side)."""
    if id(copy) in seen or isinstance(copy, _IMMUTABLE):
        return
    seen.add(id(copy))
    if copy is original and isinstance(copy, (list, dict, set)):
        found.append(path)
        return
    if isinstance(copy, dict) and isinstance(original, dict):
        for key, value in copy.items():
            if key in original:
                _mutable_shared(original[key], value, f"{path}[{key!r}]", seen, found)
    elif isinstance(copy, list) and isinstance(original, list):
        for index, (was, value) in enumerate(zip(original, copy)):
            _mutable_shared(was, value, f"{path}[{index}]", seen, found)


#: What a copy's state shares on purpose: its world (walked on its own), and the log's events converted to rows (which
#: results share too: nothing changes them).
_SHARED_STATE = {"world", "rows", "rows_last"}


@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_a_copy_shares_nothing_its_run_changes(path):
    env = load(path, 1, exposures=True)
    env.run("random", rounds=2, stop=_nth(3))
    copy = env.copy()
    found: list[str] = []
    seen: set[int] = set()
    pairs = [("world", env.world, copy.world), ("state", env.state, copy.state)]
    pairs += [(f"entities.{key}", entity, copy.world.entities[key]) for key, entity in env.world.entities.items()]
    for name, original, copied in pairs:
        assert copied is not original
        for key, value in vars(copied).items():
            if key not in _SHARED and key not in _SHARED_STATE and key in vars(original):
                _mutable_shared(vars(original)[key], value, f"{name}.{key}", seen, found)
    assert not found, f"the copy shares what its run changes: {found}"
    copy.run("random", rounds=1)
    assert _canonical(env) != _canonical(copy) or env.finished


#: What a part of a run may keep that is not configuration: caches of what it worked out, and the round in progress
#: (a generator over the cursor, which a copy resumes from the cursor).
_CACHES = {"effects": {"_hooks"}, "actions.redaction": {"_kept_secrets"},
           "information.perception": {"_selections", "_news"}, "driver": {"_resolved", "_turn_tools"},
           "schedule": {"_round"}}
_PARTS = ("effects", "actions", "actions.redaction", "actions.validation", "information", "information.perception",
          "information.schemas", "facts", "rules", "rules.events", "driver", "previews", "schedule")


def _part(env, path):
    part = env
    for name in path.split("."):
        part = getattr(part, name)
    return part


def _wired(value, env):
    """Whether ``value`` is one of the things a part is wired to: another part, the world, the state, the run, its gate
    — each the run's own."""
    parts = [env, env.world, env.state, env.gate, *(_part(env, path) for path in _PARTS)]
    if getattr(value, "__self__", None) is not None:  # a bound method of one of them
        value = value.__self__
    return any(value is part for part in parts)


@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_the_parts_of_a_run_hold_only_configuration_and_caches(path):
    """A part keeping state of its own would lose it in every copy (built afresh around the copied state): its
    attributes after a run are its wiring, its caches, and configuration equal to a fresh part's."""
    env = load(path, 1)
    env.run("random", rounds=2)
    copy = env.copy()
    for name in _PARTS:
        played, fresh = _part(env, name), _part(copy, name)
        assert type(played) is type(fresh)
        for key in set(vars(played)) | set(vars(fresh)):
            if key in _CACHES.get(name, ()):
                continue
            assert key in vars(played) and key in vars(fresh), f"{name}.{key} is kept by one part and not the other"
            was, now = vars(played)[key], vars(fresh)[key]
            if _wired(was, env):
                assert _wired(now, copy), f"{name}.{key} is wired to the original run"
            else:
                assert _equal(was, now), f"{name}.{key} changed as the run played: it is state, not config"


def _equal(was, now):
    """Equal values, or objects of one kind whose attributes are equal (configuration worked out again)."""
    if was is now or was == now:
        return True
    return (type(was) is type(now) and hasattr(was, "__dict__")
            and vars(was).keys() == vars(now).keys() and all(_equal(vars(was)[k], vars(now)[k]) for k in vars(was)))

