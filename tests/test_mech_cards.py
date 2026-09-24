"""Cards, pots, hidden roles and worker placement: native scoring, deck ops, betting, visibility and leakage."""
import json
import random
import re
from pathlib import Path

import pytest

import fg_env
from fg_env.mechanisms.card_scoring import blackjack, follow_suit, poker_rank, runs, sets, trick_winner
from fg_env.mechanisms.cards import card_visible
from fg_env.mechanisms.pot import side_pots
from fg_env.mechanisms.roles import known_role
from fg_env.participants.builtin import sample_args

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"
NAMES = ["Ana", "Ben", "Cleo", "Dev", "Eli", "Fay"]


def _rank(cards: str) -> dict:
    return poker_rank(cards.split())


def _seats(count: int) -> dict:
    return {f"p{i}": {"type": "player", "name": NAMES[i - 1], "props": {"seat": i - 1}} for i in range(1, count + 1)}


def _script(moves: dict, log: list = None):
    """Plays queued (tool, args) moves per player until one ends the turn; logs every call."""
    queues = {pid: list(steps) for pid, steps in moves.items()}

    def participant(wake):
        offered = sorted(t.name for t in wake.tools if t.kind == "act")
        while queues.get(wake.entity_id) and not wake.done:
            tool, args = queues[wake.entity_id].pop(0)
            result = wake.call(tool, args)
            if log is not None:
                log.append((wake.entity_id, offered, tool, result.ok, result.text))
            if result.ended:
                return
        if not wake.done:
            wake.end()

    return participant


def _errors(contract) -> list:
    return [i for i in fg_env.check(contract) if i.severity == "error"]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_poker_rank_categories_names_and_best_five_of_seven():
    royal = _rank("AS KS QS JS 10S 2D 3C")
    assert (royal["category"], royal["name"], royal["level"]) == ("straight flush", "royal flush", 8)
    assert sorted(royal["best"]) == sorted(["AS", "KS", "QS", "JS", "10S"])
    wheel = _rank("AS 2D 3C 4H 5S KD QC")
    assert (wheel["category"], wheel["name"]) == ("straight", "straight, 5 high")
    assert _rank("2S 3D 4C 5H 6S KD QC")["score"] > wheel["score"]  # six-high beats the wheel
    assert wheel["score"] > _rank("7S 7D 7C 2H 9S KD QC")["score"]  # a straight beats trips
    assert _rank("2H 7H 9H JH KH AS AD")["category"] == "flush"
    assert _rank("KS KD KC 5H 5S 2D 3C")["name"] == "full house, kings over fives"
    assert _rank("2S 2D 2C KH KD AS AD")["name"] == "full house, twos over aces"  # the higher pair plays
    assert _rank("9S 9D 9C 9H 2S")["name"] == "four of a kind, nines"
    assert _rank("TS TD 4C 4H 2S")["name"] == "two pair, tens and fours"


def test_poker_rank_kickers_and_exact_ties():
    board = "AH 9C 7D 4S 2C"
    assert _rank(f"AS KD {board}")["score"] > _rank(f"AD QS {board}")["score"]  # king kicker
    assert _rank("KS KD QH QC 9S")["score"] > _rank("KH KC QS QD 8S")["score"]  # two pair, kicker decides
    playing_the_board = "AS KS QS JS 10S"
    assert _rank(f"2D 3C {playing_the_board}")["score"] == _rank(f"4D 5C {playing_the_board}")["score"]
    assert poker_rank([])["score"] == 0
    assert _rank("AS AD")["name"] == "pair of aces"  # fewer than five cards are ranked as they are
    with pytest.raises(ValueError, match="cannot read"):
        poker_rank(["XX"])


def test_blackjack_totals_soft_hands_and_busts():
    assert blackjack(["AS", "KD"]) == {"total": 21, "soft": True, "bust": False, "blackjack": True}
    assert blackjack(["AS", "AD", "9C"])["total"] == 21 and blackjack(["AS", "AD", "9C"])["soft"]
    hard = blackjack(["AS", "9C", "5D"])
    assert (hard["total"], hard["soft"]) == (15, False)
    assert blackjack(["KS", "QD", "5C"])["bust"]
    assert blackjack([{"rank": 6, "suit": "hearts"}, {"rank": 11, "suit": "clubs"}])["total"] == 16


def test_sets_and_runs_for_rummy():
    assert sets("7S 7H 7D 9C 9S".split()) == [["7S", "7H", "7D"]]
    assert sets("7S 7H 9C 9S".split(), 2) == [["9C", "9S"], ["7S", "7H"]]
    assert runs("5H 6H 7H 8H 2C AH 2H 3H".split()) == [["5H", "6H", "7H", "8H"], ["AH", "2H", "3H"]]  # ace low
    assert runs("QD KD AD".split()) == [["QD", "KD", "AD"]]  # or high
    assert runs("5H 6S 7H".split()) == []  # one suit only


def test_trick_winner_and_follow_suit():
    trick = ["10H", "KH", "AS", "2H"]
    assert trick_winner(trick) == "KH"  # the suit led wins without trumps
    assert trick_winner(trick, trump="spades") == "AS"
    assert trick_winner(trick, lead_suit="spades") == "AS"
    assert trick_winner([]) is None
    assert follow_suit(["2C", "KH", "9H"], "hearts") == ["KH", "9H"]
    assert follow_suit(["2C", "KH"], "diamonds") == ["2C", "KH"]  # void in the suit: anything goes
    assert follow_suit(["2C", "KH"], None) == ["2C", "KH"]


# ---------------------------------------------------------------------------
# Decks, zones and visibility
# ---------------------------------------------------------------------------


def _card_game(players: int = 3, **cards) -> dict:
    return {"name": "Cards", "clock": {"rounds": 2},
            "types": {"player": {"agent": True, "props": {"seat": 0}}},
            "entities": _seats(players),
            "mechanisms": {"cards": {"kind": "game", "mode": "cards", "who": "player", **cards}},
            "stages": [{"name": "play", "turns": "sequential", "max_actions": 4, "max_calls": 12}],
            "outputs": {"hands": {"expr": "$dict(player, $it.id, $len($hand($it)))", "type": "map"}}}


def test_standard_deck_is_shuffled_and_dealt_round_robin_into_hands():
    contract = _card_game(hand_size=5, play=True, draw=True)
    assert _errors(contract) == []
    env = fg_env.load(contract, seed=4)
    declared = [e["id"] for e in env.entities("card")]
    env.run("idle", rounds=1)
    cards = env.entities("card")
    assert len(cards) == 52 and {"AS", "10H", "2C"} <= set(declared)
    assert env.entity("AS")["name"] == "A♠" and env.entity("QH")["props"]["rank"] == 12
    for pid in ("p1", "p2", "p3"):
        assert sum(1 for c in cards if c["props"]["zone"] == "hand" and c["props"]["owner"] == pid) == 5
    pile = [c["id"] for c in sorted((c for c in cards if c["props"]["zone"] == "deck"), key=lambda c: c["props"]["order"])]
    assert len(pile) == 37 and pile != [cid for cid in declared if cid in pile]  # shuffled
    again = fg_env.load(contract, seed=4)
    again.run("idle", rounds=1)
    assert again.entities("card") == cards  # the same seed deals the same hands


def test_tools_offer_only_legal_cards_and_hidden_cards_never_reach_a_player():
    contract = _card_game(deck=[{"suits": ["red", "blue"], "ranks": [1, 2, 3, 4, 5, 6]}], hand_size=3,
                          play={"where": "$it.rank >= 4"}, draw=True,
                          after_deal=[{"game": "cards", "action": "deal", "qty": 1, "zone": "discard"}])
    assert _errors(contract) == []
    env = fg_env.load(contract, seed=2)
    checked = []

    def watch(wake):
        world, me = env.world, wake.entity_id
        cards = list(world.entities_of("card"))
        mine = {c.id for c in cards if c.properties["zone"] == "hand" and c.properties["owner"] == me}
        hidden = [c.id for c in cards if not card_visible(world, c, me)]
        tools = {t.name: t for t in wake.tools}
        legal = sorted(c.id for c in cards if c.id in mine and c.properties["rank"] >= 4)
        if legal:
            assert sorted(tools["cards_play"].input_schema["properties"]["card"]["enum"]) == legal
        else:
            assert "cards_play" not in tools
        text = wake.update + json.dumps([t.to_dict() for t in wake.tools])
        assert not [cid for cid in hidden if re.search(rf"(?<![\w]){re.escape(cid)}(?![\w])", text)]
        other = next(cid for cid in hidden if world.entities[cid].properties["zone"] == "hand")
        refused = wake.call("inspect", {"id": other}).text
        assert refused.startswith("No entity with that id is available to inspect.") and other not in refused
        assert "rank" in wake.call("inspect", {"id": sorted(mine)[0]}).text
        checked.append(me)
        wake.end()

    env.run(watch, rounds=2)
    assert len(checked) == 6


@pytest.mark.parametrize("keep_top", [True, False])
def test_an_empty_draw_pile_is_refilled_by_shuffling_the_discards(keep_top):
    contract = _card_game(deck=[{"suit": "x", "ranks": [1, 2, 3, 4, 5, 6]}], hand_size=2, keep_top=keep_top,
                          discard={"terminal": False}, draw=True)
    env = fg_env.load(contract, seed=3)
    seen = {}

    def turn(wake):
        if wake.entity_id == "p1" and wake.round == 1:
            first, second = sorted(next(t for t in wake.tools if t.name == "cards_discard").input_schema["properties"]["card"]["enum"])
            wake.call("cards_discard", {"card": first})
            wake.call("cards_discard", {"card": second})
            seen.update(first=first, second=second, drew=wake.call("cards_draw", {}).text)
        if not wake.done:
            wake.end()

    result = env.run(turn, rounds=1)
    cards = {c["id"]: c["props"] for c in env.entities("card")}
    assert any("shuffled into the draw pile" in e.get("text", "") for e in result.events)
    if keep_top:
        assert cards[seen["second"]]["zone"] == "discard"  # the top card stays
        assert cards[seen["first"]]["zone"] == "hand" and cards[seen["first"]]["owner"] == "p1"
        assert seen["drew"] == f"You drew {env.entity(seen['first'])['name']}."
    else:
        assert sorted(cards[c]["zone"] for c in (seen["first"], seen["second"])) == ["deck", "hand"]
    assert result.outputs["hands"]["p1"] == 1


def test_personal_decks_give_each_player_their_own_cards_pile_and_reshuffle():
    contract = _card_game(players=2, personal=True, zones={"market": "public"}, hand_size=2,
                          deck=[{"name": "Copper", "per_player": True, "copies": 3, "props": {"value": 1}},
                                {"name": "Gold", "zone": "market", "copies": 2, "props": {"value": 3}}],
                          discard={"terminal": False}, draw={"qty": 2})
    assert _errors(contract) == []
    env = fg_env.load(contract, seed=8)
    drew = []

    def turn(wake):
        if wake.entity_id == "p1" and wake.round == 1:
            for card in sorted(next(t for t in wake.tools if t.name == "cards_discard").input_schema["properties"]["card"]["enum"]):
                wake.call("cards_discard", {"card": card})
            drew.append(wake.call("cards_draw", {}).text)
        if not wake.done:
            wake.end()

    result = env.run(turn, rounds=1)
    cards = {c["id"]: c["props"] for c in env.entities("card")}
    assert sorted(cards) == ["copper_p1", "copper_p1_2", "copper_p1_3", "copper_p2", "copper_p2_2", "copper_p2_3", "gold", "gold_2"]
    assert all(props["owner"] == "p1" for cid, props in cards.items() if cid.startswith("copper_p1"))
    mine = [props["zone"] for cid, props in cards.items() if cid.startswith("copper_p1")]
    assert sorted(mine) == ["deck", "hand", "hand"]  # 1 left in the pile, 2 discards reshuffled, 2 drawn
    assert sorted(props["zone"] for cid, props in cards.items() if cid.startswith("copper_p2")) == ["deck", "hand", "hand"]
    assert [props["zone"] for cid, props in cards.items() if cid.startswith("gold")] == ["market", "market"]
    assert cards["gold"]["value"] == 3 and drew == ["You drew Copper, Copper."]
    assert any(e.get("text") == "Ana's discard pile was shuffled into the draw pile." for e in result.events)


def test_pass_peek_reveal_change_who_sees_a_card_and_failed_actions_roll_back():
    contract = _card_game(hand_size=2, give={"terminal": False})
    contract["actions"] = {
        "spy": {"by": "player", "do": [{"game": "cards", "action": "peek", "cards": "$top_cards(deck, 1)"}]},
        "flip": {"by": "player", "do": [{"game": "cards", "action": "reveal", "cards": "$hand($actor)"}]},
        "doomed": {"by": "player", "do": [{"game": "cards", "action": "draw", "qty": 2}, {"fail": "Not today."}]},
    }
    assert _errors(contract) == []
    env = fg_env.load(contract, seed=5)
    state = {}

    def turn(wake):
        world = env.world
        if wake.entity_id != "p1" or wake.round != 1:
            return wake.end()
        before = {c.id: dict(c.properties) for c in world.entities_of("card")}
        state["doomed"] = wake.call("doomed", {})
        state["unchanged"] = before == {c.id: dict(c.properties) for c in world.entities_of("card")}
        state["top"] = max((c for c in world.entities_of("card") if c.properties["zone"] == "deck"), key=lambda c: c.properties["order"]).id
        wake.call("spy", {})
        state["given"] = sorted(c.id for c in world.entities_of("card") if c.properties["owner"] == "p1")[0]
        assert wake.call("cards_give", {"card": state["given"], "to": "p2"}).ok
        wake.call("flip", {})
        wake.end()

    result = env.run(turn, rounds=1)
    world = env.world
    assert not state["doomed"].ok and state["doomed"].text == "Not today." and state["unchanged"]
    top, given = world.entities[state["top"]], world.entities[state["given"]]
    assert [card_visible(world, top, p) for p in ("p1", "p2", "p3")] == [True, False, False]
    assert given.properties["owner"] == "p2"
    assert [card_visible(world, given, p) for p in ("p1", "p2", "p3")] == [True, True, False]
    kept = next(c for c in world.entities_of("card") if c.properties["owner"] == "p1" and c.properties["zone"] == "hand")
    assert card_visible(world, kept, "p3")  # revealed to everyone
    passed_news = [e for e in result.events if "passed you" in e.get("text", "")]
    assert len(passed_news) == 1 and passed_news[0]["to"] == ["p2"]


# ---------------------------------------------------------------------------
# Betting and pots
# ---------------------------------------------------------------------------


def _table(stacks: list, score: str = "$it.seat", streets: dict = None, **pot) -> dict:
    return {"name": "Table", "clock": {"rounds": 1, "unit": "hand"},
            "types": {"player": {"agent": True, "props": {"seat": 0}}},
            "entities": {f"p{i + 1}": {"type": "player", "name": NAMES[i], "props": {"seat": i, "stack": s}}
                         for i, s in enumerate(stacks)},
            "mechanisms": {"table": {"kind": "game", "mode": "pot", "who": "player", "seat": "$it.seat", "stack": 0, "score": score,
                                     "streets": streets or {"preflop": [], "flop": []}, **pot}},
            "outputs": {"stacks": {"expr": "$map($sort(player, $it.seat), $it.stack)", "type": "list"}}}


def test_side_pots_count_folded_chips_and_return_uncalled_bets():
    committed = {"a": 50, "b": 120, "c": 300, "d": 300, "folded": 80}
    pots = side_pots(committed, ["a", "b", "c", "d"])
    assert pots == [(250, ["a", "b", "c", "d"]), (240, ["b", "c", "d"]), (360, ["c", "d"])]
    assert sum(amount for amount, _ in pots) == sum(committed.values())
    assert side_pots({"a": 100, "b": 40}, ["a"]) == [(140, ["a"])]  # everyone else folded


def test_min_raise_and_a_short_all_in_that_does_not_reopen_raising():
    log: list = []
    moves = {"p1": [("table_raise", {"to": 30}), ("table_raise", {"to": 60}), ("table_call", {})],
             "p2": [("table_raise", {"to": 45}), ("table_call", {}), ("table_call", {})],
             "p3": [("table_all_in", {})]}
    contract = _table([1000, 1000, 45], blinds=[5, 10])
    assert _errors(contract) == []
    result = fg_env.load(contract, seed=1).run(_script(moves, log), rounds=1)
    assert result.status == "completed", result.error
    assert [(pid, tool, ok) for pid, _, tool, ok, _ in log] == [
        ("p1", "table_raise", True), ("p2", "table_raise", False), ("p2", "table_call", True), ("p3", "table_all_in", True),
        ("p1", "table_raise", False), ("p1", "table_call", True), ("p2", "table_call", True)]
    assert "at least 50" in log[1][4]  # a raise must be at least the last raise size (20) more
    assert log[4][1] == ["table_call", "table_fold"]  # Cleo's all-in of 15 more is not a full raise: Ana may not re-raise
    assert result.outputs["stacks"] == [955, 955, 135]
    assert any(e.get("text") == "Cleo goes all-in (bet 45)." for e in result.events)


def test_three_all_ins_of_different_sizes_build_side_pots_paid_by_rank():
    contract = _table([100, 250, 400, 1000], score="[3, 2, 1, 0][$it.seat]", streets={"betting": []})
    moves = {"p2": [("table_all_in", {})], "p3": [("table_all_in", {})], "p4": [("table_call", {})], "p1": [("table_call", {})]}
    env = fg_env.load(contract, seed=1)
    result = env.run(_script(moves), rounds=1)
    assert result.status == "completed", result.error
    assert result.outputs["stacks"] == [400, 450, 300, 600]
    pots = [(p["amount"], p["eligible"], p["winners"]) for p in env.props["table_result"]["pots"]]
    assert pots == [(400, ["p2", "p3", "p4", "p1"], ["p1"]), (450, ["p2", "p3", "p4"], ["p2"]), (300, ["p3", "p4"], ["p3"])]


def test_a_short_stack_may_always_go_all_in_and_it_acts_as_a_call():
    log: list = []
    moves = {"p2": [("table_check", {}), ("table_all_in", {})], "p1": [("table_bet", {"amount": 300})]}
    result = fg_env.load(_table([1000, 100], streets={"betting": []}), seed=1).run(_script(moves, log), rounds=1)
    assert result.status == "completed", result.error
    assert [(pid, tool, ok) for pid, _, tool, ok, _ in log] == [
        ("p2", "table_check", True), ("p1", "table_bet", True), ("p2", "table_all_in", True)]
    assert "table_all_in" in log[2][1]
    assert result.outputs["stacks"] == [900, 200]  # Ben's 100 called 100 of the 300; the other 200 went back


def test_two_pots_on_one_player_type_are_refused_because_they_would_share_chips():
    contract = _table([100, 100])
    contract["mechanisms"]["side"] = {**contract["mechanisms"]["table"], "stack": 500}
    found = _errors(contract)
    assert [i.path for i in found] == ["mechanisms.side.who"] and "'table' already bets with player" in found[0].message


def test_a_pot_game_is_conformant_when_stacks_grow_past_the_start():
    contract = {**_table([30, 30, 30], blinds=[1, 2]), "clock": {"rounds": 4, "unit": "hand"}}
    report = fg_env.rl.conformance(contract, sims=3, seed=2, max_steps=300)
    assert report.ok, report.summary()


def test_split_pots_give_the_odd_chip_left_of_the_button_and_antes_count():
    contract = _table([51, 51, 51, 100], score="$it.seat * 0", streets={"betting": []}, ante=1)
    moves = {"p2": [("table_all_in", {})], "p3": [("table_call", {})], "p4": [("table_fold", {})], "p1": [("table_call", {})]}
    result = fg_env.load(contract, seed=1).run(_script(moves), rounds=1)
    assert result.outputs["stacks"] == [51, 52, 51, 99]


def test_holdem_showdown_ranks_real_hands_and_returns_uncalled_chips():
    move = {"game": "cards", "action": "move"}
    rig = [{"game": "cards", "action": "collect"}, {**move, "cards": ["AS", "AD"], "to": "hand", "owner": "p1"},
           {**move, "cards": ["KS", "KD"], "to": "hand", "owner": "p2"}, {**move, "cards": ["2C", "7D"], "to": "hand", "owner": "p3"},
           {**move, "cards": ["AH", "KH", "3C", "9S", "4D"], "to": "board"}]
    contract = _table([100, 200, 300], score="$poker_rank($hand($it) + $zone(board)).score", streets={"preflop": []},
                      setup=rig, label="$poker_rank($hand($it) + $zone(board)).name",
                      before_showdown=[{"game": "cards", "action": "reveal", "cards": "$flatten($map($pot_live(table), $hand($it)))"}])
    contract["mechanisms"] = {"cards": {"kind": "game", "mode": "cards", "who": "player", "deal": "never", "zones": {"board": "public"}},
                              **contract["mechanisms"]}
    assert _errors(contract) == []
    moves = {"p2": [("table_all_in", {})], "p3": [("table_all_in", {})], "p1": [("table_call", {})]}
    env = fg_env.load(contract, seed=9)
    result = env.run(_script(moves), rounds=1)
    assert result.outputs["stacks"] == [300, 200, 100]
    showdown = next(e["text"] for e in result.events if e.get("text", "").startswith("Showdown"))
    assert showdown == ("Showdown: Ana (three of a kind, aces) wins the main pot (300); "
                        "Ben (three of a kind, kings) wins side pot 1 (200); Cleo takes back 100 uncalled chips.")
    shown = env.props["table_result"]
    assert [p["amount"] for p in shown["pots"]] == [300, 200] and shown["returned"] == {"p3": 100}  # uncalled is no pot
    assert all(card_visible(env.world, env.world.entities[c], None) for c in ("AS", "KD", "7D"))


@pytest.mark.parametrize("seed", range(1, 7))
def test_texas_holdem_example_conserves_chips_and_finishes(seed):
    players = 4 + seed % 3
    participants = "random" if seed % 2 else {"player_1": "policy:shover", "player": "policy:tight"}
    env = fg_env.load(EXAMPLES / "texas_holdem.json", inputs={"players": players, "hands": 15}, seed=seed)
    result = env.run(participants)
    assert result.status in ("completed", "ended"), result.error  # the generated invariant guards every action
    stacks = [p["props"]["stack"] for p in env.entities("player")]
    assert sum(stacks) == players * 500 and all(p["props"]["committed"] == 0 for p in env.entities("player"))
    assert result.outputs["hands_played"] >= 1


# ---------------------------------------------------------------------------
# Hidden roles and worker placement
# ---------------------------------------------------------------------------

VILLAGE = {
    "name": "Village", "clock": {"rounds": 2},
    "types": {"player": {"agent": True, "inspect": True, "props": {"seat": 0}}},
    "entities": _seats(6),
    "mechanisms": {"roles": {"kind": "groups", "mode": "roles", "who": "player", "deck": {"wolf": 2, "seer": 1, "villager": "rest"},
                             "teams": {"pack": ["wolf"], "town": ["seer", "villager"]}, "know": ["pack"],
                             "actions": {"bite": {"roles": ["wolf"], "do": [], "terminal": True}}}},
    "events": [{"at": 2, "do": [{"groups": "roles", "action": "eliminate", "who": "$pick(player, $it.role == wolf)"}]}],
    "stages": [{"name": "night", "turns": "sequential"}],
    "outputs": {"roles": {"expr": "$dict(player, $it.id, $it.role)", "type": "map"}},
}


def test_roles_are_dealt_teammates_know_each_other_and_elimination_reveals():
    assert _errors(VILLAGE) == []
    env = fg_env.load(VILLAGE, seed=6)
    wakes = []

    def watch(wake):
        others = [p for p in _seats(6) if p != wake.entity_id]
        wakes.append((wake.round, wake.entity_id, {t.name for t in wake.tools}, wake.update,
                      {p: wake.call("inspect", {"id": p}).text for p in others}))
        wake.end()

    result = env.run(watch)
    roles = result.outputs["roles"]
    assert sorted(roles.values()) == ["seer", "villager", "villager", "villager", "wolf", "wolf"]
    wolves = sorted(p for p, r in roles.items() if r == "wolf")
    world = env.world
    assert known_role(world, world.entities[wolves[0]], world.entities[wolves[1]]) == "wolf"
    eliminated = wolves[0]  # the round-2 event takes out the first wolf
    for round_, me, tools, update, inspected in wakes:
        assert ("bite" in tools) == (roles[me] == "wolf" and not (round_ == 2 and me == eliminated))  # role-gated, living only
        mate = next((w for w in wolves if w != me), None)
        assert (f"Your team:\n- [{mate}]" in update) == (roles[me] == "wolf")  # only the pack sees its members
        for other, text in inspected.items():
            assert "\nrole:" not in text and "\nteam:" not in text
            revealed = round_ == 2 and other == wolves[0]
            assert (f"revealed_role: {roles[other]}" in text) == revealed
    assert any("They were a wolf." in e.get("text", "") for e in result.events)


def test_worker_placement_offers_open_spaces_and_resets_each_round():
    farm = {"name": "Farm", "clock": {"rounds": 2},
            "types": {"farmer": {"agent": True, "props": {"wood": 0, "coins": 0}}},
            "entities": {"f1": {"type": "farmer", "name": "Ana"}, "f2": {"type": "farmer", "name": "Ben"},
                         "f3": {"type": "farmer", "name": "Cleo"}},
            "mechanisms": {"board": {"kind": "game", "mode": "slots", "who": "farmer", "spaces": {
                "forest": {"capacity": 1, "description": "+2 wood", "do": ["$actor.wood += 2"]},
                "market": {"capacity": 2, "do": ["$actor.coins += 1"]}}}},
            "outputs": {"goods": {"expr": "$dict(farmer, $it.id, [$it.wood, $it.coins])", "type": "map"}}}
    assert _errors(farm) == []
    offered, boards = [], []

    def place(wake):
        spaces = next(t for t in wake.tools if t.name == "board_place").input_schema["properties"]["space"]["enum"]
        offered.append((wake.round, wake.entity_id, spaces))
        if wake.entity_id == "f2":
            assert not wake.call("board_place", {"space": "forest"}).ok  # full: not a valid choice
        if wake.entity_id == "f3":
            boards.append(wake.update)
        wake.call("board_place", {"space": spaces[0]})
        wake.end()

    env = fg_env.load(farm, seed=1)
    result = env.run(place)
    assert offered == [(r, f, s) for r in (1, 2) for f, s in
                       (("f1", ["forest", "market"]), ("f2", ["market"]), ("f3", ["market"]))]
    assert result.outputs["goods"] == {"f1": [4, 0], "f2": [0, 2], "f3": [0, 2]}
    assert len(boards) == 2 and all("forest (1/1): Ana — +2 wood" in board and "market (1/2): Ben" in board for board in boards)


# ---------------------------------------------------------------------------
# Authoring errors and documentation
# ---------------------------------------------------------------------------


def test_config_mistakes_are_reported_with_what_to_fix():
    wrong_players = _card_game()
    wrong_players["mechanisms"]["cards"]["who"] = "gambler"
    assert any("who 'gambler' is not a declared type" in i.message for i in fg_env.check(wrong_players))
    leaky = _card_game()
    leaky["types"]["card"] = {"props": {"cost": 0}}
    assert any("without the card visibility rule" in i.message for i in fg_env.check(leaky))
    zone = _card_game()
    zone["events"] = [{"do": [{"game": "cards", "action": "deal", "qty": 1, "zone": "table"}]}]
    assert any("'table' is not a zone of cards" in i.message for i in fg_env.check(zone))
    pot = _table([10, 10])
    pot["events"] = [{"do": [{"game": "bank", "action": "call"}]}, {"do": [{"game": "table", "action": "shove"}]}]
    messages = [i.message for i in fg_env.check(pot)]
    assert "`game` names a declared game mechanism, got 'bank'" in messages
    assert "'shove' is not an action of table (game pot)" in messages
    ghost = json.loads(json.dumps(VILLAGE))
    ghost["mechanisms"]["roles"]["teams"]["town"].append("ghost")
    assert any("lists 'ghost', which is not in the deck" in i.message for i in fg_env.check(ghost))
    crowded = json.loads(json.dumps(VILLAGE))
    crowded["mechanisms"]["roles"]["deck"] = {"wolf": 5, "seer": 2, "villager": 0}
    assert "role deck holds 7 roles for 6 players" in (fg_env.load(crowded, seed=1).run("idle").error or "")
    old = json.loads(json.dumps(VILLAGE))
    old["mechanisms"]["roles"] = {**{k: v for k, v in old["mechanisms"]["roles"].items() if k not in ("mode", "who")},
                                  "kind": "roles", "players": "player"}
    assert any(i.message == "'roles' is a mode of kind 'groups'" for i in _errors(old))
    unnamed = {**VILLAGE, "events": [{"do": [{"groups": "roles", "action": "eliminate", "say": "Gone."}]}]}
    assert any(i.message == "`groups.eliminate` needs `who`" for i in _errors(unnamed))
    seated = {**VILLAGE, "types": {**VILLAGE["types"], "ghost": {"agent": True}}, "entities": {**VILLAGE["entities"],
              "g1": {"type": "ghost"}}, "events": [{"at": 1, "do": [{"groups": "roles", "action": "reveal", "who": "$entity(g1)"}]}]}
    assert "g1 is a ghost, not a player holding a role of roles" in (fg_env.load(seated, seed=1).run("idle").error or "")


def test_a_pot_fills_the_game_section_with_the_chips_each_player_won_or_lost():
    contract = _table([1000, 1000, 45], blinds=[5, 10])
    game = fg_env.parse(contract).game
    assert (game.players, game.seat, game.utility) == ("player", "$it.seat", "zero_sum")
    moves = {"p1": [("table_raise", {"to": 30}), ("table_call", {})], "p2": [("table_call", {}), ("table_call", {})],
             "p3": [("table_all_in", {})]}
    result = fg_env.load(contract, seed=1).run(_script(moves), rounds=1)
    assert result.status == "completed", result.error
    assert result.returns == {f"p{i + 1}": float(stack - start) for i, (stack, start) in
                              enumerate(zip(result.outputs["stacks"], [1000, 1000, 45]))}
    assert sum(result.returns.values()) == 0
    assert fg_env.parse(_table([10, 10], conserve=False)).game.utility == "general_sum"
    assert fg_env.parse(_card_game()).game is None  # a deck alone does not know what a player scores


def test_old_card_pot_and_slots_kinds_name_their_game_mode():
    for kind, config in (("cards", {"players": "player"}), ("pot", {"players": "player", "score": "0"}),
                         ("slots", {"workers": "player", "spaces": {"a": {}}})):
        contract = {**_card_game(), "mechanisms": {"old": {"kind": kind, **config}}}
        issue = next(i for i in _errors(contract) if i.path == "mechanisms.old.kind")
        assert issue.message == f"'{kind}' is a mode of kind 'game'"


def test_a_renamed_card_field_names_the_new_one():
    issue = _errors(_card_game(play_card=True))[0]
    assert issue.message == "`play_card` is not a field of `game` mode `cards`" and issue.fix.startswith("did you mean 'play'?")
    issue = _errors(_table([10, 10], players="player"))[0]
    assert issue.message == "`players` is not a field of `game` mode `pot`"


def test_card_and_pot_actions_check_their_own_keys():
    def op(contract, *effects):
        return [(i.path, i.message, i.fix) for i in _errors({**contract, "events": [{"do": list(effects)}]})]

    cards = _card_game()
    assert any(m == "`game.move` needs `cards`" for _, m, _ in op(cards, {"game": "cards", "action": "move", "to": "hand"}))
    assert any(m == "'count' is not part of `game.deal`" for _, m, _ in op(cards, {"game": "cards", "action": "deal", "count": 2}))
    path, _, fix = op(cards, {"game": "cards", "action": "shufle"})[0]
    assert path.endswith(".action") and fix == "did you mean 'shuffle'?"
    _, _, fix = op(cards, {"deal": "cards", "count": 2})[0]
    assert fix.startswith('`deal` is an action of the `game` op: {"game": "<mechanism>", "action": "deal"')
    table = _table([10, 10])
    assert any(m == "`game.raise` needs `to`" for _, m, _ in op(table, {"game": "table", "action": "raise"}))
    assert any(m == "'amount' is not part of `game.fold`" for _, m, _ in op(table, {"game": "table", "action": "fold", "amount": 1}))
    _, _, fix = op(table, {"call": "table"})[0]
    assert fix.startswith('`call` is an action of the `game` or `host` op: {"game": "<mechanism>", "action": "call"')


def test_a_moved_card_must_belong_to_the_deck_the_action_names():
    contract = _card_game(players=2, hand_size=1)
    contract["mechanisms"]["chips"] = {"kind": "game", "mode": "cards", "who": "player", "type": "chip", "deal": "never",
                                       "deck": [{"name": "Chip", "copies": 2}]}
    contract["actions"] = {"swap": {"by": "player", "do": [{"game": "chips", "action": "discard", "cards": "$hand($actor, cards)"}]}}
    env = fg_env.load(contract, seed=1)
    seen = []

    def turn(wake):
        seen.append(wake.call("swap", {}).text)
        wake.end()

    result = env.run(turn, rounds=1)
    assert "was not done" in seen[0] and result.error is None  # refused and undone: the run goes on
    assert any("is not a card of chips" in d["message"] for d in result.diagnostics)


def test_tools_one_offers_every_betting_move_as_one_tool():
    offered = []

    def fold(wake):
        offered.append(sorted(t.name for t in wake.tools if t.kind == "act"))
        assert wake.call("table", {"action": "fold"}).ok

    result = fg_env.load(_table([100, 100], blinds=[5, 10], tools="one"), seed=1).run(fold, rounds=1)
    assert result.status == "completed", result.error
    assert offered == [["table"]] and sorted(result.outputs["stacks"]) == [95, 105]


def test_guide_documents_the_card_mechanisms_ops_and_functions():
    mechanisms = fg_env.guide("mechanisms")
    assert "| `game` | board, cards, pot, slots |" in mechanisms and "| `groups` | roles, relationships, factions, matching |" in mechanisms
    game = "\n".join(fg_env.guide(key) for key in ("game.cards", "game.pot", "game.slots"))
    for key in ("game.cards", "game.pot", "game.slots"):
        assert f"### `{key}`" in game
    for action in ("deal", "draw", "reveal", "peek", "give", "fold", "raise", "place"):
        assert f"- `{action}`" in game
    assert "- `setup`" not in fg_env.guide("game.cards") and "- `timeout`" not in game
    roles = fg_env.guide("groups.roles")
    assert "- `eliminate`" in roles and "- `reveal`" in roles and "- `deal`" not in roles
    functions = fg_env.guide("functions.game")
    for name in ("poker_rank", "blackjack_value", "trick_winner", "follow_suit", "hand", "zone", "top_card", "pot_options"):
        assert f"${name}(" in functions and f"${name}" in fg_env.guide("functions")


# ---------------------------------------------------------------------------
# Leakage: an LLM-like participant reads everything it is given and probes inspect
# ---------------------------------------------------------------------------


class Spy:
    """Reads the brief, the update, every tool schema and three inspect results per wake, then acts at random."""

    def __init__(self, env, seed, hidden):
        self.env, self.rng, self.hidden = env, random.Random(seed), hidden
        self.leaks: list = []
        self.reads = 0

    def __call__(self, wake):
        world = self.env.world
        texts = [wake.brief, wake.update, json.dumps([t.to_dict() for t in wake.tools], ensure_ascii=False)]
        ids = sorted(world.entities)
        texts += [wake.call("inspect", {"id": target}).text for target in self.rng.sample(ids, 3)]
        self.reads += len(texts)
        for text in texts:
            self.leaks += [(wake.entity_id, what, text[:160]) for what in self.hidden(world, wake.entity_id, text)]
        acts = [t for t in wake.tools if t.kind == "act"]
        if acts and not wake.done:
            tool = self.rng.choice(acts)
            wake.call(tool.name, sample_args(tool.input_schema, self.rng))
        if not wake.done:
            wake.end()


def _hidden_cards(world, me, text):
    """Hidden cards named or referenced in text. Runs are one hand long, so a card hidden now was never shown to `me`."""
    return [card.id for card in world.entities_of("card") if not card_visible(world, card, me)
            and (card.name in text or re.search(rf"(?<![\w]){re.escape(card.id)}(?![\w])", text))]


def _hidden_roles(world, me, text):
    """Text segments naming another player together with a role `me` does not know (the seer's own readings
    excepted). Segments split on newlines, ';' and '"', so a tool schema's rules description and its list of
    player options are not read as one sentence."""
    viewer = world.entities[me]
    found = []
    for other in world.entities_of("player"):
        role = other.properties["role"]
        if other.id == me or not role or known_role(world, viewer, other) or other.id in viewer.properties["inspected"]:
            continue
        for line in re.split(r'[\n;"]', text):
            if other.name in line and re.search(rf"\b{role}\b", line.lower()):
                found.append(f"{other.name} is {role}")
    return found


@pytest.mark.parametrize("seed", range(1, 13))
def test_no_player_ever_sees_another_players_hidden_cards(seed):
    env = fg_env.load(EXAMPLES / "texas_holdem.json", inputs={"players": 4 + seed % 3, "hands": 1, "starting_stack": 60}, seed=seed)
    spy = Spy(env, seed, _hidden_cards)
    result = env.run(spy)
    assert result.status in ("completed", "ended"), result.error
    assert spy.reads > 20 and spy.leaks == []


@pytest.mark.parametrize("seed", range(1, 5))
def test_no_player_learns_a_hidden_role_before_it_is_revealed(seed):
    env = fg_env.load(EXAMPLES / "werewolf.json", seed=seed)
    spy = Spy(env, seed, _hidden_roles)
    result = env.run(spy)
    assert result.status in ("completed", "ended"), result.error
    assert spy.reads > 50 and spy.leaks == []
