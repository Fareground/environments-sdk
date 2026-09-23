"""Randomness cannot be probed: no refusal, dry run, undo or legal-action mask tells an agent how its luck will fall.

A call refused before it draws gives nothing away and costs nothing; a call refused after it drew has been played:
its luck is spent and the attempt counts. Sealed choices are checked without their luck, which is drawn when they
commit; an atomic turn is settled by the action that draws, so what the turn does next cannot undo it.
"""
import pytest

import fg_env
from fg_env.checks import parse_contract
from fg_env.runtime import Env

ROUNDS = 300


def _guess(**action):
    return {
        "name": "Guess",
        "clock": {"rounds": ROUNDS},
        "types": {"player": {"agent": True, "props": {"score": 0}}},
        "entities": {"a": {"type": "player"}},
        "actions": {"guess": {"by": "player", "params": {"n": {"type": "int", "min": 1, "max": 99}},
                              "do": [{"if": "$params.n > 10", "then": [{"fail": "Pick 1 to 10."}]},
                                     "$secret = $randint(1, 10)",
                                     {"if": "$params.n != $secret", "then": [{"fail": "Wrong."}]},
                                     "$actor.score += 1"], **action}},
        "stages": [{"name": "play", "max_actions": 10, "max_calls": 30}],
        "outputs": {"score": "$entity(a).score"},
    }


def _count_up(wake):
    for n in range(1, 11):
        if wake.call("guess", {"n": n}).ok or wake.done:
            return


def test_counting_up_through_refusals_wins_no_more_often_than_chance():
    once = fg_env.run(_guess(per_turn=1), _count_up, seed=5).outputs["score"]
    assert once < 0.16 * ROUNDS  # one fair guess a turn: 10% (the probe won ~80% while refusals were free retries)
    tries = fg_env.run(_guess(), _count_up, seed=5).outputs["score"]
    assert tries < 0.75 * ROUNDS  # ten fresh 10% guesses: 1 - 0.9**10 ≈ 65%; a free probe of one draw won every turn


def test_a_refusal_after_a_draw_spends_the_attempt():
    replies = []

    def twice(wake):
        replies.append((wake.call("guess", {"n": 1}), wake.call("guess", {"n": 2})))

    fg_env.run(_guess(per_turn=1) | {"clock": {"rounds": 40}}, twice, seed=1)
    lost = [second for first, second in replies if not first.ok]
    assert lost and all(not r.ok and "1 time(s) per turn" in r.text for r in lost)


def test_a_refusal_before_any_draw_is_free_and_leaves_the_luck_as_it_was():
    def plain(wake):
        wake.call("guess", {"n": 3})

    def mistaken(wake):
        assert not wake.call("guess", {"n": 50}).ok  # refused before the draw: nothing played
        wake.call("guess", {"n": 3})

    contract = _guess(per_turn=1) | {"clock": {"rounds": 60}}
    assert fg_env.run(contract, mistaken, seed=2).outputs == fg_env.run(contract, plain, seed=2).outputs


COIN = {
    "name": "Coin",
    "clock": {"rounds": ROUNDS},
    "types": {"player": {"agent": True, "props": {"cash": 0}}},
    "entities": {"a": {"type": "player"}, "b": {"type": "player"}},
    "actions": {"gamble": {"by": "player", "chance": 0.5, "do": ["$actor.cash += 5"],
                           "otherwise": [{"fail": "You lose."}]},
                "safe": {"by": "player", "do": ["$actor.cash += 1"]}},
    "stages": [{"name": "choose", "turns": "simultaneous"}],
    "outputs": {"cash": "$entity(a).cash"},
}


def test_submitting_a_sealed_choice_does_not_reveal_its_luck():
    submitted = []

    def peek(wake):
        reply = wake.call("gamble", {})
        submitted.append(reply.ok)
        if not reply.ok:
            wake.call("safe", {})

    cash = fg_env.run(COIN, {"a": peek, "b": "idle"}, seed=2).outputs["cash"]
    assert all(submitted)  # the submission checks only what does not depend on luck
    assert cash < 0.6 * 5 * ROUNDS  # half the gambles win; the peek switched every loser to `safe`


SCUM = {
    "name": "Scum",
    "clock": {"rounds": ROUNDS},
    "types": {"player": {"agent": True, "props": {"cash": 100000}}},
    "entities": {"a": {"type": "player"}},
    "actions": {"gamble": {"by": "player", "do": ["$actor.cash += 5 if $chance(0.5) else -5"]},
                "splurge": {"by": "player", "do": ["$actor.cash -= 1000000"]}},
    "stages": [{"name": "play", "max_actions": 3, "max_calls": 10,
                "valid": [{"expr": "$actor.cash >= 0", "why": "no debt"}]}],
    "outputs": {"cash": "$entity(a).cash"},
}


def test_breaking_an_atomic_turn_on_purpose_does_not_undo_its_luck():
    changes = []

    def scum(wake):
        before = wake.me["cash"]
        wake.call("gamble", {})
        changes.append(wake.me["cash"] - before)
        if wake.me["cash"] < before:
            undone = wake.call("splurge", {})
            undone = wake.call("end_turn", {}) if undone.ok else undone
            assert not undone.ok and "undone" in undone.text
        elif not wake.done:
            wake.call("end_turn", {})

    cash = fg_env.run(SCUM, scum, seed=3).outputs["cash"]
    assert cash == 100000 + sum(changes)  # every loss stood: only the splurge after it was undone
    assert 0.4 * ROUNDS < changes.count(5) < 0.6 * ROUNDS


def test_a_chance_that_breaks_an_atomic_turn_undoes_it_and_ends_it():
    broke = dict(SCUM, types={"player": {"agent": True, "props": {"cash": 0}}}, clock={"rounds": 40})
    replies = []

    def gamble_twice(wake):
        replies.append((wake.call("gamble", {}), wake.call("gamble", {})))

    fg_env.run(broke, gamble_twice, seed=4)
    lost = [(first, second) for first, second in replies if not first.ok]
    assert lost and all("undone" in first.text and first.ended and "already over" in second.text for first, second in lost)


def test_legal_action_masks_do_not_depend_on_luck():
    contract = {
        "name": "Mask",
        "clock": {"rounds": 1},
        "types": {"player": {"agent": True, "props": {"wins": 0}}},
        "entities": {"a": {"type": "player"}},
        "actions": {"gamble": {"by": "player", "do": [{"if": "$chance(0.5)", "then": [{"fail": "You lose."}]},
                                                      "$actor.wins += 1"]},
                    "pass": {"by": "player"}},
        "game": {"returns": "$actor.wins"},
        "outputs": {"wins": "$entity(a).wins"},
    }
    for seed in range(30):
        state = fg_env.rl.game(contract, seed=seed).new_initial_state()
        assert "gamble" in {action.tool for action in state.legal_tool_calls()}, seed


CROWD = {
    "name": "Crowd",
    "clock": {"rounds": 4},
    "types": {"p": {"agent": True, "props": {"n": 0, "m": 0}}},
    "entities": {k: {"type": "p"} for k in "abcdef"},
    "actions": {"wait": {"by": "p"}},
    "events": [{"phase": "end", "each": "p", "where": "$chance(0.7)", "do": ["$it.n += $randint(1, 1000)"]},
               {"phase": "end", "each": "p", "sync": True, "do": ["$it.m = $it.m + $randint(1, 1000)"]}],
    "stages": [{"name": "act", "who": "$chance(0.5)"}],
    "outputs": {"alive": "$count(p)"},
}


def _crowd(contract):
    woken = set()

    def wait(wake):
        woken.add((wake.round, wake.entity_id))
        wake.call("wait", {})

    env = fg_env.load(contract, seed=11)
    assert env.run({"*": wait}).status == "completed"  # a sync event may draw (its luck was once journaled, refused)
    return {e["id"]: e["props"] for e in env.entities(alive=False) if e["id"] != "b"}, \
        {w for w in woken if w[1] != "b"}


def test_an_entity_leaving_does_not_shift_the_luck_of_the_others():
    purge = dict(CROWD, events=CROWD["events"] + [{"phase": "start", "at": 2, "do": [{"remove": "$entity(b)"}]}])
    assert _crowd(purge) == _crowd(CROWD)  # `each` (its `where` too, and sync) and `who` draw per entity


def test_a_move_refused_by_its_luck_is_a_move_in_a_game():
    contract = {
        "name": "Coin game",
        "clock": {"rounds": 1},
        "types": {"player": {"agent": True, "props": {"wins": 0}}},
        "entities": {"a": {"type": "player"}},
        "actions": {"gamble": {"by": "player", "do": [{"if": "$chance(0.5)", "then": [{"fail": "You lose."}]},
                                                      "$actor.wins += 1"]}},
        "game": {"returns": "$actor.wins"},
        "outputs": {"wins": "$entity(a).wins"},
    }
    returns = []
    for seed in range(20):
        state = fg_env.rl.game(contract, seed=seed).new_initial_state()
        state.apply_action({"tool": "gamble", "args": {}})
        assert state.is_terminal()
        returns.append(state.returns()[0])
    assert 0 < sum(returns) < 20  # a loss is an outcome of the move, as a win is


def test_who_a_stage_wakes_by_chance_does_not_depend_on_what_the_agents_do():
    c = {"name": "Who", "clock": {"rounds": 5},
         "types": {"p": {"agent": True, "props": {"luck": 0}}, "thing": {}},
         "entities": {f"p{i}": {"type": "p"} for i in range(8)},
         "actions": {"roll": {"by": "p", "do": ["$actor.luck += $randint(1, 9)"]},
                     "make": {"by": "p", "do": [{"create": "thing"}]}, "wait": {"by": "p"}},
         "stages": [{"name": "s", "who": "$chance(0.5)"}, {"name": "t", "who": "$chance(0.5)", "order": "random"}],
         "outputs": {"count": "$count(p)"}}

    def woken(action):
        seen = []
        fg_env.run(c, {"*": lambda w: (seen.append((w.round, w.stage, w.entity_id)), w.call(action, {}))}, seed=2)
        return seen

    assert woken("wait") == woken("roll") == woken("make")


def _lucky(**action):
    return {"name": "Lucky", "clock": {"rounds": 20},
            "types": {"p": {"agent": True, "props": {"cash": 0}}}, "entities": {"a": {"type": "p"}},
            "actions": {"lucky": {"by": "p", "do": ["$actor.cash += 1"], **action}},
            "stages": [{"name": "s", "max_calls": 8}]}


def _retry(wake):
    for _ in range(8):
        reply = wake.call("lucky", {"x": 1})
        if reply.ok or reply.ended:
            break
    wake.end()


@pytest.mark.parametrize("path, action", [
    ("when[0]", {"when": "$random() < 0.5"}),
    ("when[0]", {"params": {"x": {"type": "int", "min": 0, "max": 1}}, "when": "$chance(0.5) && $params.x >= 0"}),
    ("params.x.max", {"params": {"x": {"type": "int", "min": 0, "max": "$floor($random() * 2)"}}}),
    ("params.y.default", {"params": {"x": {"type": "int"}, "y": {"type": "int", "default": "$randint(0, 1)"}}}),
    ("params.x.where", {"params": {"x": {"type": "entity", "of": "p", "where": "$chance(0.5)"}}}),
])
def test_luck_cannot_decide_whether_a_call_is_allowed(path, action):
    # A refused call costs nothing, so luck drawn while deciding it would be a free reroll: calling again until it
    # let the call through won ~398 of 400 "50%" rounds.
    contract = _lucky(**action)
    issues = [i for i in fg_env.check(contract) if i.path == f"actions.lucky.{path}" and i.severity == "error"]
    assert issues and "`do`" in issues[0].fix, fg_env.check(contract)
    unchecked = Env(parse_contract(contract), {}, 1)  # the engine refuses it even when nothing checked it
    result = unchecked.run(_retry)
    assert unchecked.entity("a")["props"]["cash"] == 0
    finding = next(d for d in result.diagnostics if d["code"] == "action_rule_failed")
    assert finding["path"] == f"actions.lucky.{path}" and "luck cannot decide" in finding["message"]


def test_a_rule_that_fails_after_drawing_spends_the_attempt():
    contract = {"name": "Fault", "clock": {"rounds": ROUNDS}, "world": {"zero": 0},
                "types": {"p": {"agent": True, "props": {"cash": 10.0, "wins": 0}}}, "entities": {"a": {"type": "p"}},
                "actions": {"gamble": {"by": "p", "chance": 0.5, "do": ["$actor.wins += 1"],
                                       "otherwise": ["$actor.cash = $actor.cash / $world.zero"]}},
                "stages": [{"name": "s", "max_actions": 1, "max_calls": 8}],
                "outputs": {"wins": "$entity(a).wins"}}
    replies = []

    def retry(wake):
        for _ in range(8):
            replies.append(wake.call("gamble", {}))
            if replies[-1].ok or replies[-1].ended:
                break

    wins = fg_env.run(contract, retry, seed=1).outputs["wins"]
    assert 0.4 * ROUNDS < wins < 0.6 * ROUNDS  # retrying the faulting branch won every round
    faulted = [r for r in replies if not r.ok]
    assert faulted and all(r.ended and "divide by zero" in r.text for r in faulted)


NOISY = {"name": "Noisy", "clock": {"rounds": 3}, "world": {"truth": 37.0},
         "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
         "actions": {"wait": {"by": "p"}},
         "views": {"signal": {"for": "p", "look": True, "show": "Signal: {$round($world.truth + $normal(0, 20), 1)}"},
                   "hint": {"for": "p", "show": "Hint: {$round($world.truth + $normal(0, 20), 1)}"}},
         "stages": [{"name": "s", "max_calls": 40}]}


def test_looking_again_shows_the_same_noisy_signal_within_a_turn():
    looks = []

    def stare(wake):
        looks.append({wake.call("look", {"view": "signal"}).text for _ in range(10)})

    fg_env.run(NOISY, stare, seed=1)
    firsts = [texts - {"Unchanged since you read it earlier this turn."} for texts in looks]
    assert all(len(texts) == 1 for texts in firsts), looks  # 30 free looks cut the noise ~5x
    assert len(set().union(*firsts)) == 3  # a fresh signal every turn


def test_a_preview_shows_the_noisy_view_the_turn_will_show():
    env = fg_env.load(NOISY, seed=1)
    previewed = env.preview("a")["update"]
    updates = []
    env.run(lambda wake: updates.append(wake.update), rounds=1)
    assert previewed.splitlines()[-1] == updates[0].splitlines()[-1]
