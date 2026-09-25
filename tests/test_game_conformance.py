"""`fg_env.rl.conformance` over every example game, and the problems it finds in broken ones.

A longer run over more playouts: FG_ENV_SLOW=1 pytest tests/test_game_conformance.py
"""
import os

import pytest
from game_contracts import GAMES, load_game

import fg_env
from fg_env.game import game

EXAMPLES = sorted(GAMES.glob("*.json"))
SIMS = 12 if os.environ.get("FG_ENV_SLOW") else 2
SIMULTANEOUS = {"biased_pennies", "chicken", "first_price_auction", "goofspiel", "matching_pennies",
                "prisoners_dilemma", "rock_paper_scissors", "stag_hunt"}


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_every_example_game_is_conformant(path):
    report = fg_env.rl.conformance(path, sims=SIMS, seed=3, max_steps=400)
    assert report.ok, report.summary()
    assert report.decisions > 0 and report.checks["replay"] == SIMS
    if path.stem in SIMULTANEOUS:
        sealed = fg_env.rl.conformance(path, sims=SIMS, seed=4, simultaneous="turn_based", resume=False, max_steps=400)
        assert sealed.ok, sealed.summary()


@pytest.mark.parametrize("stem, tool, move, returns", [
    ("rock_paper_scissors", "throw", {"move": "rock"}, {"a": -1, "b": 1}),      # showing nothing loses
    ("biased_pennies", "show", {"side": "heads"}, {"a": -1, "b": 1}),         # A's worst result
    ("prisoners_dilemma", "choose", {"move": "defect"}, {"a": 0, "b": 5}),    # a missed choice cooperates
    ("chicken", "drive", {"move": "straight"}, {"a": -1, "b": 1}),           # a missed choice swerves
    ("stag_hunt", "hunt", {"move": "hare"}, {"a": 0, "b": 3}),                # a missed choice hunts stag
])
def test_a_matrix_game_scores_a_missed_move_by_its_stated_rule(stem, tool, move, returns):
    env = fg_env.load(GAMES / f"{stem}.json", seed=1)

    def b_moves(wake):
        wake.call(tool, move)

    result = env.run({"a": "idle", "b": b_moves})
    assert result.status == "completed", result.error
    assert result.returns == returns
    assert fg_env.load(GAMES / f"{stem}.json", seed=1).run("idle").status == "completed"


def test_a_hidden_card_only_its_owner_reads_is_no_leak_and_one_shown_to_all_is_refused():
    # A private property of an entity that is not an agent is hidden from every agent but the owner its type names.
    sealed = load_game("kuhn_poker")
    sealed["types"]["envelope"] = {"owner": "holder",
                                   "props": {"card": {"type": "int", "default": 0, "private": True},
                                               "holder": {"type": "text", "default": "p1"}}}
    sealed["entities"]["envelope"] = {"type": "envelope"}
    sealed["events"][0]["do"][1]["do"].append("$entity('envelope').card = $first")  # P0's card, which P1 holds
    sealed["views"]["envelope"] = {"of": "envelope", "where": "$it.holder == $actor.id", "show": "P0 has: {card}."}
    report = fg_env.rl.conformance(sealed, sims=4, resume=False, leak_branches=4)
    assert report.ok, report.summary()  # P1 owns what the envelope holds: telling its states apart is no leak
    sealed["views"]["envelope"] = {"of": "envelope", "show": "P0 has: {card}."}
    assert any(issue.path == "views.envelope.show" and "private card" in issue.message
               for issue in fg_env.check(sealed) if issue.severity == "error")


def test_a_view_that_reads_the_other_players_private_hand_is_refused_by_the_engine():
    peeking = load_game("goofspiel")
    peeking["views"]["table"]["show"] = "Prize: {$world.prize}. Their cards: {$join($other($actor).hand, ' ')}."
    report = fg_env.rl.conformance(peeking, sims=4, resume=False, leak_branches=4)
    assert any("B's hand is private" in issue.message for issue in report.issues), report.summary()


def test_an_offered_move_whose_rule_fails_as_it_applies_is_caught_without_dry_runs():
    contract = load_game("tic_tac_toe")
    # a taken cell breaks a rule (a `fail` there would be a played, wasted move, not a failure)
    contract["actions"]["mark"]["do"][0]["then"] = ["$world.turn = 1 / 0"]
    subject = game(contract, dry_run=False)  # lists every call that validates, taken cells included
    report = fg_env.rl.conformance(subject, sims=1, resume=False, leak_branches=0)
    assert any(issue.check == "legal" and "applying it fails" in issue.message for issue in report.issues)


def test_returns_outside_the_declared_bounds_are_reported():
    loose = fg_env.expand(load_game("tic_tac_toe"))
    loose["types"]["player"]["score"].update({"min": 0, "max": 0})
    report = fg_env.rl.conformance(loose, sims=2, resume=False, leak_branches=0)
    assert any(issue.check == "returns" and "types.player.score.m" in issue.message for issue in report.issues)
    assert fg_env.check(loose) == [] or all(i.severity != "error" for i in fg_env.check(loose))


def test_an_inconsistent_bound_declaration_is_a_check_error():
    wrong = fg_env.expand(load_game("nim"))
    wrong["types"]["player"]["score"].update({"min": 1, "max": -1})
    assert any(issue.path == "types.player.score.min" and issue.severity == "error" for issue in fg_env.check(wrong))


def test_the_report_names_the_seed_and_steps_that_reproduce_an_issue():
    broken = load_game("nim")
    broken["types"]["player"]["score"]["value"] = "1 if $world.winner != '' else 0"  # both seats win: not zero-sum
    report = fg_env.rl.conformance(broken, sims=1, resume=False, leak_branches=0)
    issue = next(issue for issue in report.issues if issue.check == "returns")
    assert "zero_sum" in issue.message and issue.history and "seed 0" in str(issue)
    assert issue.to_dict()["steps"] == list(issue.steps)
    assert issue.reproduce(game(broken)).is_terminal()


def test_a_game_whose_agents_pick_entities_created_as_it_plays_is_checked():
    """Papers are submitted during the run and praised by id: the praise action is parametric, and trying a submit
    while listing legal calls leaves no trace in the state key."""
    c = {"name": "Papers", "clock": {"rounds": 3},
         "types": {"author": {"agent": True, "props": {"q": {"type": "int", "default": 0, "private": True}}},
                   "paper": {"props": {"by": "", "score": 0}}},
         "entities": {"a1": {"type": "author"}, "a2": {"type": "author"}},
         "actions": {"submit": {"by": "author", "description": "s",
                                "do": [{"create": "paper", "props": {"by": "$actor.id"}}]},
                     "praise": {"by": "author", "description": "p", "params": {"p": {"type": "entity", "of": "paper"}},
                                "do": ["$params.p.score += 1"]}},
         "stages": [{"name": "s"}], "outputs": {"papers": "$count(paper)"}}
    report = fg_env.rl.conformance(c, sims=5)
    assert report.ok, report.summary()
    assert "praise" in fg_env.rl.game(c).space.parametric


def test_a_move_refused_by_a_hidden_value_stays_legal_and_is_spent_when_played():
    """A dry run that refuses a call by a value hidden from the seat keeps the call legal, as the live engine does
    (audit 11 H3): otherwise the legal set would name the opponent's hidden code."""
    contract = {"name": "Code guess", "clock": {"rounds": 2},
                "types": {"p": {"agent": True, "props": {"code": {"default": "$randint(0,3)", "private": True},
                                                          "won": 0}, "score": {"value": "$it.won"}}},
                "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
                "actions": {"guess": {"by": "p", "params": {"g": {"type": "int", "min": 0, "max": 3, "step": 1}},
                                      "when": [{"expr": "$params.g == $first($filter(p, $it.id != $actor.id)).code",
                                                "why": "Wrong."}],
                                      "do": ["$actor.won = 1"], "announce": False}},
                "views": {"me": {"show": "my code {code}"}},
                "stages": [{"name": "s"}], "outputs": {"w": "$dict(p, $it.id, $it.won)"}}
    for seed in range(4):
        state = fg_env.rl.game(contract, seed=seed).new_initial_state()
        seat = state.current_player()
        legal = [state.action_to_string(seat, a) for a in state.legal_actions()]
        assert legal == ["end_turn", *(f"guess(g={g})" for g in range(4))]
        code = state.entity("b")["props"]["code"]
        state.apply_action(next(a for a in state.legal_actions()
                                if state.action_to_string(seat, a) not in ("end_turn", f"guess(g={code})")))
        assert state.current_player() != seat  # the wrong guess was played: it spent the seat's one action
    assert fg_env.rl.conformance(contract, sims=4).issues == []


PEEK = {"name": "Peek", "clock": {"rounds": 2},
        "types": {"player": {"agent": True, "props": {"card": {"type": "int", "default": 0, "private": True},
                                                       "coins": 10}, "score": {"value": "$it.coins"}}},
        "entities": {"ann": {"type": "player"}, "bob": {"type": "player"}},
        "events": [{"on": "round.start", "when": "$round == 1",
                    "do": {"chance": [{"p": 0.5, "do": "$entity(bob).card = 1"},
                                      {"p": 0.5, "do": "$entity(bob).card = 2"}]}}],
        "actions": {"noop": {"by": "player", "description": "Do nothing.", "do": "$actor.coins += 0"},
                    "peek": {"by": "player", "description": "See bob's card.", "announce": False,
                             "do": "$seen = $entity(bob).card", "outcome": "The card is {$seen}."}}}


def test_what_a_seat_s_own_call_told_it_stays_in_its_information_state():
    """audit 13 H1: an outcome ("The card is 2.") is part of what the seat knows; two playouts it told apart are two
    information states, and the conformance branch check holds every game to that (perfect recall)."""
    states = set()
    for card in (0, 1):
        state = fg_env.rl.game(PEEK, seed=1).new_initial_state()
        state.apply_action(card)  # the chance node: bob's card
        seat = state.current_player()
        state.apply_action(next(a for a in state.legal_actions() if state.action_to_string(seat, a) == "peek"))
        while state.current_player() != seat:
            player = state.current_player()
            state.apply_action(next(a for a in state.legal_actions() if state.action_to_string(player, a) == "noop"))
        assert f"you were told: The card is {card + 1}." in state.information_state_string(seat)
        states.add(state.information_state(seat))
    assert len(states) == 2
    assert fg_env.rl.conformance(PEEK, sims=6, seed=1).ok
