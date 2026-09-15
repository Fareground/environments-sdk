"""`fg_env.conformance` over every example game, and the problems it finds in broken ones.

A longer run over more playouts: FG_ENV_SLOW=1 pytest tests/sdk/test_game_conformance.py
"""
import copy
import os

import pytest

import fg_env
from fg_env.sdk.game import game
from game_contracts import GAMES, load_game

EXAMPLES = sorted(GAMES.glob("*.json"))
SIMS = 12 if os.environ.get("FG_ENV_SLOW") else 2
SIMULTANEOUS = {"biased_pennies", "chicken", "first_price_auction", "goofspiel", "matching_pennies",
                "prisoners_dilemma", "rock_paper_scissors", "stag_hunt"}


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_every_example_game_is_conformant(path):
    report = fg_env.conformance(path, sims=SIMS, seed=3, max_steps=400)
    assert report.ok, report.summary()
    assert report.decisions > 0 and report.checks["replay"] == SIMS
    if path.stem in SIMULTANEOUS:
        sealed = fg_env.conformance(path, sims=SIMS, seed=4, simultaneous="turn_based", resume=False, max_steps=400)
        assert sealed.ok, sealed.summary()


def test_a_view_that_shows_another_players_private_card_is_reported_with_both_playouts():
    leaky = load_game("kuhn_poker")
    leaky["views"]["table"]["show"] = "Your card: {$actor.card}. Theirs: {$filter(player, $it.id != $actor.id)[0].card}."
    report = fg_env.conformance(leaky, sims=2, resume=False)
    leaks = [issue for issue in report.issues if issue.check == "leak"]
    assert leaks and "can tell apart" in leaks[0].message and leaks[0].other_steps is not None
    state = leaks[0].reproduce(game(leaky))
    assert not state.is_terminal()


def test_a_view_that_shows_the_other_players_hand_in_a_simultaneous_game_is_reported():
    peeking = load_game("goofspiel")
    peeking["views"]["table"]["show"] = "Prize: {$world.prize}. Their cards: {$join($other($actor).hand, ' ')}."
    report = fg_env.conformance(peeking, sims=4, resume=False, leak_branches=4)
    assert any(issue.check == "leak" and "Their cards" in issue.message for issue in report.issues), report.summary()


def test_an_offered_move_that_the_effects_refuse_is_caught_without_dry_runs():
    contract = load_game("tic_tac_toe")
    subject = game(contract, dry_run=False)  # lists every call that validates, taken cells included
    report = fg_env.conformance(subject, sims=1, resume=False, leak_branches=0)
    assert any(issue.check == "legal" and "applying it fails" in issue.message for issue in report.issues)


def test_returns_outside_the_declared_bounds_are_reported():
    loose = load_game("tic_tac_toe")
    loose["game"].update({"min_return": 0, "max_return": 0})
    report = fg_env.conformance(loose, sims=2, resume=False, leak_branches=0)
    assert any(issue.check == "returns" and "game.m" in issue.message for issue in report.issues)
    assert fg_env.check(loose) == [] or all(i.severity != "error" for i in fg_env.check(loose))


def test_an_inconsistent_bound_declaration_is_a_check_error():
    wrong = copy.deepcopy(load_game("nim"))
    wrong["game"].update({"min_return": 1, "max_return": -1})
    assert any(issue.path == "game.min_return" and issue.severity == "error" for issue in fg_env.check(wrong))


def test_the_report_names_the_seed_and_steps_that_reproduce_an_issue():
    broken = load_game("nim")
    broken["game"]["returns"] = "1 if $world.winner != '' else 0"  # both seats win: not zero-sum
    report = fg_env.conformance(broken, sims=1, resume=False, leak_branches=0)
    issue = next(issue for issue in report.issues if issue.check == "returns")
    assert "zero_sum" in issue.message and issue.history and "seed 0" in str(issue)
    assert issue.to_dict()["steps"] == list(issue.steps)
    assert issue.reproduce(game(broken)).is_terminal()
