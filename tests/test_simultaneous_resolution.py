"""Simultaneous stages are fair: sealed choices commit in a seeded random order (not seat order), a choice is tried at
submit after the agent's own earlier choices in the stage, and choices that must be resolved together (auctions,
pro-rata fills) are recorded by the action and resolved in the stage's `on_exit`."""
import time
from collections import Counter


import fg_env


def market(**stage):
    """Three buyers, one item in stock; each buyer submits `buy` at once."""
    return {
        "name": "Last item", "clock": {"rounds": 1},
        "world": {"stock": 1},
        "types": {"buyer": {"agent": True, "props": {"priority": 0, "got": 0, "coins": 5}}},
        "entities": {"a": {"type": "buyer", "props": {"priority": 3}}, "b": {"type": "buyer", "props": {"priority": 1}},
                     "c": {"type": "buyer", "props": {"priority": 2}}},
        "actions": {"buy": {"by": "buyer", "private": True,
                            "do": [{"if": "$world.stock < 1", "then": [{"fail": "sold out"}]},
                                   "$world.stock = $world.stock - 1", "$actor.got = 1"]}},
        "stages": [{"name": "shop", "turns": "simultaneous", **stage}],
        "outputs": {"winner": "$pick(buyer, $it.got == 1).id"},
    }


def everyone_buys(wake):
    assert wake.call("buy", {}).ok
    wake.end()


def winner(c, seed):
    result = fg_env.load(c, seed=seed).run(everyone_buys)
    assert result.status == "completed", result.error
    return result.outputs["winner"]


def test_a_contested_item_goes_to_each_buyer_about_equally_often():
    wins = Counter(winner(market(), seed) for seed in range(150))
    assert set(wins) == {"a", "b", "c"}
    assert all(30 <= n <= 70 for n in wins.values()), wins


def test_the_commit_order_is_reproducible_from_the_seed():
    assert [winner(market(), seed) for seed in range(10)] == [winner(market(), seed) for seed in range(10)]


def test_an_order_setting_still_decides_the_commit_order():
    assert {winner(market(order="seat"), seed) for seed in range(10)} == {"a"}
    assert {winner(market(order="$it.priority"), seed) for seed in range(10)} == {"b"}


def test_a_choice_is_tried_after_the_agents_own_earlier_choices():
    c = market(max_actions=2)
    c["world"]["stock"] = 5
    c["actions"]["buy"]["do"] = [{"if": "$actor.coins < 5", "then": [{"fail": "you cannot afford it"}]},
                                        "$actor.coins = $actor.coins - 5"]
    replies = {}

    def buy_twice(wake):  # sealed turns run in parallel threads: keep each agent's replies apart
        replies[wake.entity_id] = [wake.call("buy", {}) for _ in range(2)]
        wake.end()

    fg_env.load(c, seed=1).run(buy_twice)
    first, second = next(iter(replies.values()))
    assert first.ok and "Submitted" in first.text
    assert not second.ok and "you cannot afford it" in second.text, second.text


def test_a_game_lists_only_the_sealed_choices_the_engine_would_take():
    c = market(max_actions=2)
    c["actions"]["buy"]["do"] = [{"if": "$actor.coins < 5", "then": [{"fail": "you cannot afford it"}]},
                                 "$actor.coins = $actor.coins - 5"]
    c["game"] = {"players": "buyer", "returns": "$actor.coins"}
    state = fg_env.rl.game(c).as_turn_based().new_initial_state()
    seat = state.current_player()
    state.apply_action({"tool": "buy"})
    assert state.current_player() == seat
    assert [call.text for call in state.legal_tool_calls(seat)] == ["end_turn"]


# Choices resolved together: the action records the choice; the stage's `on_exit` resolves them all at once.

def auction(bids):
    return {
        "name": "Sealed bids", "clock": {"rounds": 1},
        "types": {"bidder": {"agent": True, "props": {"bid": {"type": "int", "default": 0, "private": True},
                                                      "won": 0}}},
        "entities": {name: {"type": "bidder"} for name in bids},
        "actions": {"bid": {"by": "bidder", "private": True, "terminal": True,
                            "params": {"amount": {"type": "int", "min": 0, "max": 100}},
                            "do": ["$actor.bid = $params.amount"]}},
        "stages": [{"name": "bid", "turns": "simultaneous", "on_exit": [
            "$top = $max(bidder, $it.bid)",
            "$winner = $choice($filter(bidder, $it.bid == $top))",
            "$winner.won = 1",
        ]}],
        "outputs": {"winner": "$pick(bidder, $it.won == 1).id"},
    }


def bidding(bids):
    def participant(wake):
        assert wake.call("bid", {"amount": bids[wake.entity_id]}).ok
        wake.end()
    return participant


def test_a_sealed_bid_auction_goes_to_the_highest_bid_whatever_the_order():
    bids = {"a": 10, "b": 40, "c": 25}
    for seed in range(5):
        result = fg_env.load(auction(bids), seed=seed).run(bidding(bids))
        assert result.outputs["winner"] == "b", result.error


def test_a_tie_is_broken_at_random():
    bids = {"a": 30, "b": 30, "c": 5}
    winners = {fg_env.load(auction(bids), seed=seed).run(bidding(bids)).outputs["winner"] for seed in range(30)}
    assert winners == {"a", "b"}


def test_orders_larger_than_the_stock_are_filled_pro_rata():
    c = {
        "name": "Pro-rata", "clock": {"rounds": 1},
        "world": {"stock": 60},
        "types": {"buyer": {"agent": True, "props": {"want": 0, "got": 0.0}}},
        "entities": {"a": {"type": "buyer"}, "b": {"type": "buyer"}},
        "actions": {"order": {"by": "buyer", "private": True, "terminal": True,
                              "params": {"qty": {"type": "int", "min": 1, "max": 100}},
                              "do": ["$actor.want = $params.qty"]}},
        "stages": [{"name": "order", "turns": "simultaneous", "on_exit": [
            "$demand = $sum(buyer, $it.want)",
            "$fill = $min(1, $world.stock / $max($demand, 1))",
            {"each": "buyer", "do": ["$it.got = $it.want * $fill"]},
            "$world.stock = $world.stock - $sum(buyer, $it.got)",
        ]}],
        "outputs": {"a": "$entity(a).got", "b": "$entity(b).got", "left": "$world.stock"},
    }
    wants = {"a": 30, "b": 90}

    def participant(wake):
        assert wake.call("order", {"qty": wants[wake.entity_id]}).ok
        wake.end()

    result = fg_env.load(c, seed=1).run(participant)
    assert result.status == "completed", result.error
    assert (result.outputs["a"], result.outputs["b"], result.outputs["left"]) == (15, 45, 0)


def test_the_same_choices_give_the_same_result_however_long_each_agent_takes():
    c = {"name": "Stuck", "clock": {"rounds": 3},
         "types": {"a": {"agent": True, "props": {"x": 0}}, "b": {"agent": True, "props": {"x": 0}}},
         "entities": {"a1": {"type": "a"}, "b1": {"type": "b"}},
         "actions": {"go": {"by": "a", "when": "$actor.x > 0", "do": []}, "run": {"by": "b", "when": "$actor.x > 0", "do": []}},
         "stages": [{"name": "s", "turns": "simultaneous"}]}

    def slow(who):
        def play(wake):
            if wake.entity_id == who:
                time.sleep(0.05)  # the other agent reads its tools first
            wake.tools
            wake.end()
        return play

    assert fg_env.run(c, slow("a1"), seed=1).to_dict() == fg_env.run(c, slow("b1"), seed=1).to_dict()


def test_how_many_sealed_turns_run_at_once_does_not_change_the_outcome():
    c = {"name": "Conc", "clock": {"rounds": 5}, "world": {"pot": 0, "order": {"type": "list", "default": []}},
         "types": {"p": {"agent": True, "props": {"luck": 0, "cash": 20}}},
         "entities": {f"p{i}": {"type": "p"} for i in range(12)},
         "actions": {"roll": {"by": "p", "chance": 0.5, "otherwise": ["$actor.cash -= 1"],
                              "do": ["$actor.luck += $randint(1, 100)", "$world.pot += 1", "$world.order += $actor.id"]}},
         "stages": [{"name": "s", "turns": "simultaneous"}], "outputs": {"pot": "$world.pot"}}
    runs = [fg_env.load(c, seed=9, parallel=n).run({"*": lambda w: w.call("roll", {})}).to_dict() for n in (1, 3, 8)]
    assert runs[0] == runs[1] == runs[2]
