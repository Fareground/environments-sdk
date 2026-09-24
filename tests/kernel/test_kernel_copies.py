"""Copy equivalence: every way the engine copies a run continues exactly as the run itself would.

At safe points (a run stopped by ``run(stop=...)``, between rounds or part-way through one) a clone (a restored
snapshot between rounds, a replay part-way) and a JSON snapshot round trip each play on to the result of one straight
run, and so does the stopped run itself. At decisions (a turn waiting for a call) a direct copy of a stepped run
and a replayed copy of a piloted one each continue, under the same calls, exactly like the runs they copy: the same
state, the same brief, update and tools, the same call results. This extends ``tests/test_game_stepping.py`` from the
example games to every example and generated contract.

A direct copy refuses runs it does not carry (physics, a space, uncommitted atomic changes, mechanism stores it does
not know) and the caller replays instead; those are compared through the replayed copy alone.
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
from fg_env.copying.branch import Branch, copy_pilot, fresh_copy
from fg_env.copying.direct import NotCopyable
from fg_env.copying.replay import Tape
from fg_env.copying.stepping import SteppedEnv, Stepper
from fg_env.game.runs import ThreadedRun
from fg_env.participants.builtin import sample_args

ROUNDS = 3


# -- safe points -------------------------------------------------------------------------------------------------


def _nth(point):
    seen = [0]

    def stop(_env):
        seen[0] += 1
        return seen[0] == point

    return stop


def _rounds_left(env, rounds):
    return None if rounds is None else rounds - env.round + (1 if env._in_round else 0)


def _copies_at_safe_points_continue_exactly(subject, seed, points, rounds=ROUNDS):
    straight = load(subject, seed).run("random", rounds=rounds).to_dict()
    for point in points:
        env = load(subject, seed)
        env.run("random", rounds=rounds, stop=_nth(point))
        if env.status != "stopped":
            break  # the run ended before this point
        left = _rounds_left(env, rounds)
        copies = {"clone": env.clone(),
                  "snapshot": fg_env.Env.restore(source(subject), json.loads(json.dumps(env.snapshot())))}
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
    """A run stepped on this thread, pausing for every agent (its copies are direct)."""
    stepper = Stepper(fresh_copy(root, None, None, SteppedEnv), agents, explicit=False)
    stepper.start()
    return stepper


def _piloted(root, agents):
    """A run piloted on a thread of its own, pausing for every agent (its copies replay)."""
    pilot = copy_pilot(root, Tape(), 0, None, controlled=set(agents), explicit=False)
    pilot.start()
    return ThreadedRun(Branch(pilot), None)


def _seen(run):
    """The run's state now, and what the waiting agent is shown and offered."""
    pause = run.pause

    def read(env):
        out = {"state": undoable_state(env), "turns": env._turn_count, "firings": dict(env.world.firings),
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
    """Take the same ``steps`` random calls in every one of ``runs``, which must stay alike throughout."""
    for _ in range(steps):
        seen = [_seen(run) for run in runs]
        assert all(other == seen[0] for other in seen[1:]), "copies differ"
        if runs[0].pause is None:
            break
        name, args = _decide(seen[0], rng)
        results = [run.call(name, args) for run in runs]
        assert len({(r.ok, r.text, r.ended) for r in results}) == 1, f"{name} answered differently"
    results = [run.result().to_dict() for run in runs]
    assert all(other == results[0] for other in results[1:]), "results differ"


def _copies_at_decisions_continue_exactly(subject, seed, steps, clone_every, ahead):
    root = load(subject, seed)
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
                twins = [piloted.clone()]
                try:
                    twins.append(stepped.clone())
                except NotCopyable:
                    pass  # copied by replaying instead: the piloted twin covers it
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
