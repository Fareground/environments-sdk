"""Properties every contract must have, checked on random valid contracts (_fuzz.py): a clean check means it plays
without crashing, a seed replays exactly, a snapshot resumes exactly, removals never cost another agent its turn,
private values stay private and a call refused for its arguments changes nothing. ``FG_ENV_SLOW=1`` checks thousands.
"""
import json
import os
import random

import pytest

import fg_env
from fg_env.participants import RandomAgent

import _fuzz
import _leaks
from _adversaries import played, probing

SEEDS = range(3000 if os.environ.get("FG_ENV_SLOW") else 100)


def _clean(seed):
    contract = _fuzz.contract(seed)
    if any(issue.severity == "error" for issue in fg_env.check(contract, rounds=0)):
        pytest.skip("the generated contract does not check clean")
    return contract


def test_the_grammar_generates_contracts_that_check_clean():
    checked = [fg_env.check(_fuzz.contract(seed), rounds=0) for seed in range(40)]
    assert sum(not any(i.severity == "error" for i in issues) for issues in checked) >= 36


@pytest.mark.parametrize("seed", SEEDS)
def test_a_clean_contract_plays_with_random_and_idle_agents(seed):
    contract = _clean(seed)
    for who in ("random", "idle"):
        result = fg_env.load(contract, seed=seed).run(who)
        assert result.status in ("completed", "ended"), (who, result.error)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_same_seed_plays_the_same_run(seed):
    contract = _clean(seed)
    first, second = (fg_env.load(contract, seed=seed).run("random") for _ in range(2))
    assert first.to_dict() == second.to_dict()


@pytest.mark.parametrize("seed", SEEDS)
def test_a_snapshot_taken_anywhere_resumes_exactly(seed):
    contract = _clean(seed)
    straight = fg_env.load(contract, seed=seed).run("random")
    point, seen = random.Random(seed).randint(1, 12), [0]

    def stop(_env):
        seen[0] += 1
        return seen[0] == point

    env = fg_env.load(contract, seed=seed)
    env.run("random", stop=stop)
    resumed = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot()))).run("random")
    assert resumed.to_dict() == straight.to_dict()


class _TurnBook:
    """Plays random moves and books who took a turn where, and who was removed before theirs came."""

    concurrent = False  # sealed turns one at a time, so the book sees each turn's removals alone

    def __init__(self, env):
        self.env, self.agent = env, RandomAgent(seed=env.seed)
        self.took, self.lost = {}, {}

    def __call__(self, wake):
        where = (wake.round, wake.stage)
        self.took.setdefault(where, []).append(wake.entity_id)
        before = {e["id"] for e in self.env.entities()}
        self.agent(wake)
        for gone in before - {e["id"] for e in self.env.entities()}:
            if gone not in self.took[where]:
                self.lost[gone] = self.lost.get(gone, 0) + 1


@pytest.mark.parametrize("seed", SEEDS)
def test_removing_an_agent_never_costs_another_agent_its_turn(seed):
    contract = _clean(seed)
    timed = any("duration" in action for action in contract["actions"].values())
    if timed or any("passes" in stage or "valid" in stage for stage in contract["stages"]):
        pytest.skip("timed actions, repeated passes and replayed turns change how many turns a seat gets")
    acting = {action["by"] for action in contract["actions"].values()}
    env = fg_env.load(contract, seed=seed)
    book = _TurnBook(env)
    result = env.run(book)
    assert result.status in ("completed", "ended"), result.error  # degraded is fine: random play proves little
    turns = [who for took in book.took.values() for who in took]
    assert all(took.count(who) == 1 for took in book.took.values() for who in took), "two turns in one stage"
    for agent in env.entities(alive=False):
        if agent["type"] in acting:
            expected = agent["props"]["seated"] - book.lost.get(agent["id"], 0)
            assert turns.count(agent["id"]) == expected, agent["id"]


@pytest.mark.parametrize("seed", SEEDS)
def test_no_agent_is_shown_another_agents_private_values(seed):
    scanner = _leaks.scan(_clean(seed), seed=seed)
    assert scanner.result.status in ("completed", "ended"), scanner.result.error
    assert scanner.leaks == []


@pytest.mark.parametrize("seed", SEEDS)
def test_a_call_refused_for_its_arguments_changes_nothing(seed):
    contract = _clean(seed)
    plain = fg_env.load(contract, seed=seed).run(RandomAgent(seed=seed))
    probed = fg_env.load(contract, seed=seed).run(probing(RandomAgent(seed=seed)))
    assert played(probed) == played(plain)
