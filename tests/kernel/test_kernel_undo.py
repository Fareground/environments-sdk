"""Atomic undo: a block of logic that fails anywhere leaves the run exactly as it was before the block — its store,
indexes, journal version and undoable bookkeeping — while the luck it drew stays spent (``firings`` never goes back).

Blocks are drawn from the corpus itself: an example's own action bodies (with valid arguments) and the effect grammar
of ``_fuzz.py``, each with a failure injected at a random place — a `fail` (an ``Abort``), an expression that cannot be
worked out (a ``RunError``), a write out of bounds and, where the contract has one, a broken invariant. They run the
way an agent's action does (inside :func:`~fg_env.actions.faults.guarded`) and as bare world logic, and a generated
contract's agents also call such a block as an action of their own, through the whole turn pipeline.
"""
import copy
import random

import _fuzz
import pytest
from _corpus import (
    EXAMPLES,
    FAST_EXAMPLES,
    FUZZ_FAST,
    FUZZ_SLOW,
    SEEDS_FAST,
    SEEDS_SLOW,
    clean_fuzz,
    clean_seed,
    load,
    restored_state,
    undoable_state,
)

import fg_env
from fg_env.actions.faults import guarded
from fg_env.contract.base import TAPE
from fg_env.errors import RunError
from fg_env.participants import RandomAgent
from fg_env.participants.builtin import sample_args

#: Failures every contract can meet: a `fail`, and a division by zero (an expression error).
FAIL = {"fail": "Injected refusal."}
BROKEN = "$kernel_fault = 1 / 0"
#: Failures only the generated contracts can meet: a write past `cash`'s maximum (100), and a pot below the invariant
#: `$world.pot >= 0` — broken as the block commits, so only where it is guarded (world logic that breaks an
#: invariant fails the run, undone or not).
BOUNDS = "$actor.cash = 1000"
INVARIANT = "$world.pot = -1000"


def _spent(env):
    """What is spent for good once drawn or asked for: every site's firings, and the host answers recorded."""
    return dict(env.world.firings), set(env.world.props.get(TAPE) or ())


def _still_spent(env, spent, version):
    """Luck drawn and host answers recorded stayed spent; and the journal is back at its ``version`` unless an answer
    was recorded (a change kept outside the journal, which no earlier version may stand for)."""
    firings, answers = spent
    assert all(env.world.firings.get(site, 0) >= count for site, count in firings.items()), "luck came back"
    recorded = set(env.world.props.get(TAPE) or ())
    assert answers <= recorded, "a recorded host answer was dropped"
    if recorded == answers:
        assert env.world.journal.version == version, "the undone world has another version"


def _stopped(subject, seed):
    """A run of ``subject`` stopped at a safe point part-way through its first rounds."""
    env = load(subject, seed)
    seen = [0]

    def stop(_env):
        seen[0] += 1
        return seen[0] == 4

    env.run("random", rounds=2, stop=stop)
    return env


def _action_block(env, rng):
    """An action's own body, for a living actor with arguments that validate, or None when no action has one."""
    world = env.world
    names = list(env.contract.actions)
    rng.shuffle(names)
    for name in names:
        spec = env.contract.actions[name]
        actors = [e for e in world.entities.values() if e.alive and world.is_a(e.entity_type, spec.by)] \
            if isinstance(spec.by, str) and spec.by in env.contract.types else []
        if not actors or not spec.do:
            continue
        actor = rng.choice(actors)
        schema = env.actions.tool(actor, name).input_schema
        params, problem = env.actions.validate(actor, name, sample_args(schema, rng))
        if problem:
            continue
        return actor, list(spec.do), {"actor": actor, "params": params}, f"actions.{name}.do", ()
    return None


def _grammar_block(env, rng):
    """A block from the fuzz effect grammar (no parameters), for a living agent of a generated contract, and the
    failures only such a contract can meet."""
    agents = [e for e in env.world.entities.values() if e.alive and env.contract.is_agent(e.entity_type)]
    if not agents:
        return None
    actor = rng.choice(agents)
    return actor, _fuzz._effects(rng, {}), {"actor": actor}, "kernel.block", (BOUNDS, INVARIANT)


def _undo_restores(subject, seed, trials, pick):
    env = _stopped(subject, seed)
    if env.status != "stopped":
        pytest.skip("the run ended before its first safe points")
    rng = random.Random(seed)
    tried = 0
    for trial in range(trials):
        drawn = pick(env, rng)
        if drawn is None:
            pytest.skip("no action body to run")
        actor, block, vars, path, own = drawn
        is_guarded = trial % 2 == 0
        failures = [FAIL, BROKEN, *(failure for failure in own if is_guarded or failure != INVARIANT)]
        block.insert(rng.randint(0, len(block)), rng.choice(failures))
        before, version, spent = restored_state(env), env.world.journal.version, _spent(env)

        def work(block=block, vars=vars, path=path, actor=actor):
            env._atomic(block, vars, path, owner=actor)

        if is_guarded:  # as inside an agent's action: refused and undone, the run goes on
            _, fault = guarded(env, work)
            assert fault is not None, block
        else:  # as world logic: the run would fail, with the block undone
            with pytest.raises(RunError):
                work()
        assert restored_state(env) == before, block
        _still_spent(env, spent, version)
        tried += 1
    assert tried == trials


@pytest.mark.parametrize("seed", SEEDS_FAST)
@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_an_example_action_body_that_fails_anywhere_is_undone_whole(path, seed):
    _undo_restores(path, seed, trials=12, pick=_action_block)


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_a_generated_block_that_fails_anywhere_is_undone_whole(fuzz):
    _undo_restores(clean_seed(fuzz), fuzz, trials=12, pick=_grammar_block)


@pytest.mark.slow
@pytest.mark.parametrize("seed", SEEDS_SLOW)
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_every_example_action_body_that_fails_anywhere_is_undone_whole(path, seed):
    _undo_restores(path, seed, trials=30, pick=_action_block)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_many_generated_blocks_that_fail_anywhere_are_undone_whole(fuzz):
    _undo_restores(clean_seed(fuzz), fuzz, trials=30, pick=_grammar_block)


# -- through the turn pipeline ---------------------------------------------------------------------------------------


class _Prober:
    """Calls the contract's ``kernel_probe`` action (a block with a failure in it) first thing every turn, and holds
    the run to it: refused, and the run as it was — but for the use a spent refusal counts."""

    concurrent = False

    def __init__(self, env, seed):
        self.env, self.inner, self.probed = env, RandomAgent(seed=seed), 0

    def __call__(self, wake):
        stage = next(stage for stage in self.env.contract.stage_list() if stage.name == wake.stage)
        if stage.turns != "simultaneous" and any(tool.name == "kernel_probe" for tool in wake.tools):
            env = self.env
            before, version, spent = restored_state(env), env.world.journal.version, _spent(env)
            result = wake.call("kernel_probe", {})
            if result.ok:  # an atomic turn holds its actions until it settles: the failure undoes the turn then
                assert stage.valid, result.text
                result = wake.call("end_turn", {})
                assert (result.data or {}).get("error") == "undone", result.text
            assert not result.ok, result.text
            if (result.data or {}).get("spent"):
                used = before["used_round"].setdefault(wake.entity_id, {})
                used["kernel_probe"] = used.get("kernel_probe", 0) + 1
            assert restored_state(env) == before, result.text
            _still_spent(env, spent, version)
            self.probed += 1
        if not wake.done:
            self.inner(wake)


def _probed_contract(seed):
    contract = copy.deepcopy(clean_fuzz(seed))
    rng = random.Random(seed)
    block = _fuzz._effects(rng, {})
    block.insert(rng.randint(0, len(block)), rng.choice([FAIL, BROKEN, BOUNDS, INVARIANT]))
    by = sorted({action["by"] for action in contract["actions"].values()})[0]
    contract["actions"]["kernel_probe"] = {"by": by, "description": "A move that cannot work.", "do": block}
    return contract


def _probed_turns_change_nothing(seed):
    if all(stage["turns"] == "simultaneous" for stage in clean_fuzz(clean_seed(seed))["stages"]):
        pytest.skip("only sealed stages: a sealed choice is refused when it commits, not when it is called")
    env = fg_env.load(_probed_contract(seed), seed=seed)
    prober = _Prober(env, seed)
    result = env.run(prober)
    assert result.status in ("completed", "ended"), result.error
    assert prober.probed


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_an_action_that_fails_anywhere_leaves_the_run_as_it_was_before_the_call(fuzz):
    _probed_turns_change_nothing(fuzz)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_many_actions_that_fail_anywhere_leave_the_run_as_it_was_before_the_call(fuzz):
    _probed_turns_change_nothing(fuzz)


# -- violations the rebuild must fix ------------------------------------------------------------------------------

LATCH = {
    "name": "Latch",
    "clock": {"rounds": 3},
    "world": {"x": 0, "rang": 0, "once": 0},
    "types": {"p": {"agent": True}},
    "entities": {"a": {"type": "p"}},
    "actions": {"wait": {"by": "p", "do": []}},
    "events": [{"on": "change", "when": "$world.x > 0", "do": ["$world.rang += 1"]},
               {"on": "change", "when": "$world.x > 0", "once": True, "do": ["$world.once += 1"]}],
}


@pytest.mark.xfail(strict=True, reason=(
    "guarded(env, work, mark) undoes the world back to `mark` but restores env.state.armed and fired_once only to "
    "their values when `work` began (actions/faults.py): a `change` event armed, or a `once` event fired, by a commit "
    "between `mark` and `work` stays armed or fired while that commit's changes are gone, so the event never fires "
    "again when its `when` next becomes true. Latent today: no caller commits between the mark it passes and the work "
    "(atomic turns defer every commit to settle). Kernel step 2 journals armed/fired_once and deletes the restore."))
def test_undoing_back_to_a_mark_restores_armed_and_fired_events_as_they_were_there():
    env = fg_env.load(LATCH, seed=1)
    world = env.world
    before = undoable_state(env)
    mark = world.journal.mark()
    with world.journal.held():  # a part of an atomic turn: a change committed in it, then a failure undoes it all
        env._atomic(["$world.x = 1"], {}, "kernel.earlier")
        assert world.props["rang"] == 1 and world.props["once"] == 1
        _, fault = guarded(env, lambda: env._atomic([FAIL], {}, "kernel.later"), mark)
    assert fault is not None and world.props == {"x": 0, "rang": 0, "once": 0}
    assert undoable_state(env) == before  # armed[0], armed[1] and fired_once {1} survive the undo
    env._atomic(["$world.x = 1"], {}, "kernel.again")
    assert world.props["rang"] == 1 and world.props["once"] == 1  # the events should fire as the first time


def test_undoing_a_scheduled_effect_restores_the_schedule_count():
    env = fg_env.load(LATCH, seed=1)
    before = undoable_state(env)
    _, fault = guarded(env, lambda: env._atomic([{"after": 1, "do": ["$world.x = 1"]}, FAIL], {}, "kernel.block"))
    assert fault is not None and not env.world.scheduled
    assert undoable_state(env) == before
