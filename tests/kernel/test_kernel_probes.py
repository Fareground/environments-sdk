"""No free probe: a refused call that is not spent tells the agent nothing hidden and costs nothing — so it must change
nothing either. Repeating it leaves the world's state, every site's luck (``firings``) and the turn's own draw and
hidden-read counts exactly as they were. (A refusal that drew luck or read a hidden value is spent instead: the
attempt counts as a use, so it cannot be repeated for free.)

Every agent of every run probes on each of its turns before it plays: calls with arguments its tools' schemas rule out,
calls with random arguments, and calls to actions it is not offered. Extends ``tests/test_fair_randomness.py`` and
``tests/test_hidden_probing.py`` to the whole corpus.
"""
import copy
import random

import pytest
from _adversaries import _outside
from _corpus import (
    EXAMPLES,
    FAST_EXAMPLES,
    FUZZ_FAST,
    FUZZ_SLOW,
    SEEDS_FAST,
    SEEDS_SLOW,
    clean_seed,
    load,
    restored_state,
)

import fg_env
from fg_env.participants import RandomAgent
from fg_env.participants.builtin import sample_args

#: How often a free refusal is repeated.
REPEATS = 3
#: Probes per turn, at most.
PROBES = 6


def _observed(env):
    """What a free refusal may not move — the run's undoable state and the luck of every site — and, observed inside
    the turn from now on, whether it draws luck or reads a value hidden from its agent."""
    return restored_state(env), dict(env.world.luck.firings), env.world.luck.observe()


def _unmoved(env, before):
    """Whether the run is as ``before``, having drawn nothing and read nothing hidden since."""
    state, firings, observed = before
    return (restored_state(env), dict(env.world.luck.firings)) == (state, firings) and \
        not observed.drew and not observed.read_hidden


class _Prober:
    """Probes every turn with refused calls, holds each free one to changing nothing however often it is repeated,
    then plays randomly."""

    concurrent = False  # inline: the turn is observed on its own thread

    def __init__(self, env, seed):
        self.env, self.inner, self.seed = env, RandomAgent(seed=seed), seed
        self.free = 0

    def _probes(self, wake, rng):
        acts = [tool for tool in wake.tools if tool.kind == "act"]
        offered = {tool.name for tool in acts}
        probes = []
        for tool in acts:
            props = tool.input_schema.get("properties", {})
            probes.append((tool.name, {name: _outside(prop) for name, prop in props.items()} or {"no_such": 1}))
            probes.append((tool.name, sample_args(tool.input_schema, rng)))
        probes += [(name, {}) for name in self.env.contract.actions if name not in offered]
        rng.shuffle(probes)
        return probes[:PROBES]

    def __call__(self, wake):
        env, rng = self.env, random.Random(f"{self.seed}/{wake.round}/{wake.stage}/{wake.entity_id}")
        for name, args in self._probes(wake, rng):
            if wake.done or wake.calls_left < REPEATS + 3:
                break
            before = _observed(env)
            result = wake.call(name, args)
            if result.ok or result.ended:
                break  # it applied (or was submitted), or the turn is over: the world may move from here
            if (result.data or {}).get("spent"):
                continue  # counted as a use: repeating it is not free
            assert _unmoved(env, before), f"refused {name}{args} changed the run: {result.text}"
            before = _observed(env)
            for attempt in range(REPEATS):
                again = wake.call(name, args)
                assert not again.ok and not (again.data or {}).get("spent"), again.text
                assert _unmoved(env, before), f"refused {name}{args} changed the run when repeated " \
                                                           f"({attempt + 1}): {again.text}"
            self.free += 1
        if not wake.done:
            self.inner(wake)


def _free_refusals_change_nothing(subject, seed):
    env = load(subject, seed)
    prober = _Prober(env, seed)
    result = env.run(prober, rounds=3)
    assert result.status != "failed", result.error


@pytest.mark.parametrize("seed", SEEDS_FAST)
@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_a_free_refusal_in_an_example_changes_nothing_however_often_it_is_repeated(path, seed):
    _free_refusals_change_nothing(path, seed)


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_a_free_refusal_in_a_generated_contract_changes_nothing_however_often_it_is_repeated(fuzz):
    _free_refusals_change_nothing(clean_seed(fuzz), fuzz)


@pytest.mark.slow
@pytest.mark.parametrize("seed", SEEDS_SLOW)
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_free_refusals_in_every_example_change_nothing(path, seed):
    _free_refusals_change_nothing(path, seed)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_free_refusals_in_many_generated_contracts_change_nothing(fuzz):
    _free_refusals_change_nothing(clean_seed(fuzz), fuzz)


# -- violations the rebuild fixed --------------------------------------------------------------------------------------

VAULT = {
    "name": "Vault",
    "clock": {"rounds": 1},
    "world": {"allowed": False},
    "types": {"p": {"agent": True}, "vault": {"props": {"code": {"type": "int", "default": 0, "private": True}}}},
    "entities": {"a": {"type": "p"}, "v": {"type": "vault", "props": {"code": 6}}},
    "actions": {
        "guess": {"by": "p", "params": {"x": {"type": "int", "min": 0, "max": 9}},
                  "do": [{"if": "$params.x != $entity(v).code", "then": [{"fail": "Wrong code."}]}]},
        "wait": {"by": "p", "do": []},
    },
    "stages": [{"name": "play", "max_actions": 2, "max_calls": 40,
                "valid": [{"expr": "$world.allowed", "why": "not yet"}]}],
}


def test_a_refusal_spent_on_a_hidden_value_stays_spent_when_its_atomic_turn_is_undone():
    guesses = []

    def play(wake):
        for x in range(10):
            if wake.done:
                return
            result = wake.call("guess", {"x": x})
            guesses.append((x, result.ok, result.data or {}))
            if result.ok or wake.done:
                return
            wake.call("wait", {})  # the turn's last action: it settles, is not allowed, and is undone to play again

    fg_env.run(copy.deepcopy(VAULT), play, seed=1)
    # each wrong guess read the hidden code: spent, and it settles the atomic turn at once (here: undone and over)
    assert all(data.get("spent") or data.get("error") == "undone" for _, ok, data in guesses if not ok)
    assert len(guesses) <= VAULT["stages"][0]["max_actions"], guesses  # was 0..6: the code found in one turn


def test_checking_a_refused_tool_for_a_working_choice_reads_nothing_hidden_for_the_turn():
    """The run's diagnostics ask whether a refused tool had any choice that works, trying each (0..6 read the hidden
    code): that is the run's question, not the agent's, so the turn reads nothing hidden."""
    read = []

    def play(wake):
        observed = wake._turn.env.world.luck.observe()
        result = wake.call("guess", {"x": 99})  # past the maximum: refused for its arguments, free
        assert not result.ok and not (result.data or {}).get("spent")
        read.append(observed.read_hidden)
        wake.end()

    fg_env.run({**copy.deepcopy(VAULT), "stages": [{"name": "play"}]}, play, seed=1)
    assert read == [False]
