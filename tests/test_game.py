"""`fg_env.rl.game`: contracts as OpenSpiel-style games, proven with search and a solver."""
import copy

import pytest
from game_contracts import KUHN, MATCHING_PENNIES, NIM, TIC_TAC_TOE

import fg_env
from fg_env import ContractError
from fg_env.game import CHANCE, SIMULTANEOUS, TERMINAL
from fg_env.mechanisms import merge_sections

DUEL = {
    "name": "Duel",
    "clock": {"rounds": 1},
    "types": {"gunner": {"agent": True, "props": {"seat": 0, "hit": False}}},
    "entities": {"a": {"type": "gunner", "props": {"seat": 0}}, "b": {"type": "gunner", "props": {"seat": 1}}},
    "actions": {"shoot": {"by": "gunner", "terminal": True, "private": True, "do": [
        {"chance": [{"p": 0.25, "label": "hit", "do": ["$actor.hit = true"]}, {"p": 0.75, "label": "miss"}]}]}},
    "stages": [{"name": "fire", "turns": "simultaneous", "must_act": True, "order": "seat"}],  # a's roll comes first
    "game": {"players": "gunner", "seat": "$it.seat", "returns": "1 if $actor.hit else 0"},
    "outputs": {"hits": {"expr": "$count(gunner, $it.hit)", "type": "int"}},
}

AUCTIONEER = {
    "name": "Open outcry",
    "clock": {"rounds": 1},
    "types": {"bidder": {"agent": True, "props": {"bid": 0, "said": {"type": "text", "default": ""}}}},
    "entities": {"x": {"type": "bidder"}},
    "actions": {"bid": {"by": "bidder", "terminal": True,
                        "params": {"amount": {"type": "number", "min": 0, "max": 10, "step": 2.5}},
                        "do": ["$actor.bid = $params.amount"]},
                "say": {"by": "bidder", "params": {"text": "text"}, "do": ["$actor.said = $params.text"]}},
    "stages": [{"name": "outcry", "max_actions": 2}],
    "game": {"returns": "$actor.bid"},
    "outputs": {"bid": {"expr": "$entity('x').bid", "type": "number"}},
}


def _minimax(state):
    if state.is_terminal():
        return state.returns()[0]
    values = [_minimax(state.child(action)) for action in state.legal_actions()]
    return max(values) if state.current_player() == 0 else min(values)


def test_minimax_over_game_states_solves_nim():
    for stones, value in ((5, 1.0), (4, -1.0)):
        root = fg_env.rl.game(NIM, inputs={"stones": stones}).new_initial_state()
        assert _minimax(root) == value
    root = fg_env.rl.game(NIM).new_initial_state()
    best = max(root.legal_tool_calls(), key=lambda action: _minimax(root.child(action.id)))
    assert best.text == "take(count=1)"


def test_tic_tac_toe_is_a_draw_under_perfect_play():
    game = fg_env.rl.game(TIC_TAC_TOE)
    state = game.new_initial_state()
    state.apply_action({"tool": "mark", "args": {"cell": 4}})
    state.apply_action({"tool": "mark", "args": {"cell": 0}})
    table = {}

    def solve(node):
        key = node.state_key()
        if key not in table:
            if node.is_terminal():
                table[key] = node.returns()[0]
            else:
                values = []
                for action in node.legal_actions():
                    child = node.child(action)
                    values.append(solve(child))
                    child.close()
                table[key] = max(values) if node.current_player() == 0 else min(values)
        return table[key]

    assert solve(state) == 0.0


def _tree(state):
    if state.is_terminal():
        return ("end", state.returns()[0])
    if state.is_chance_node():
        return ("chance", [(p, _tree(state.child(outcome))) for outcome, p in state.chance_outcomes()])
    player = state.current_player()
    return ("decide", player, state.information_state(player),
            [(action, _tree(state.child(action))) for action in state.legal_actions()])


def test_cfr_on_the_extracted_kuhn_tree_reaches_the_known_game_value():
    tree = _tree(fg_env.rl.game(KUHN).new_initial_state())
    regrets, totals = {}, {}

    def strategy(key, actions, table):
        weights = {a: max(0.0, table.get(key, {}).get(a, 0.0)) for a in actions}
        total = sum(weights.values())
        return {a: (weights[a] / total if total > 0 else 1 / len(actions)) for a in actions}

    def cfr(node, reach, iteration):
        if node[0] == "end":
            return node[1]
        if node[0] == "chance":
            return sum(p * cfr(child, [r * p for r in reach], iteration) for p, child in node[1])
        _, player, key, children = node
        actions = [a for a, _ in children]
        sigma = strategy(key, actions, regrets)
        values = {}
        for action, child in children:
            inner = list(reach)
            inner[player] *= sigma[action]
            values[action] = cfr(child, inner, iteration)
        value = sum(sigma[a] * values[a] for a in actions)
        sign = 1 if player == 0 else -1
        for action in actions:
            table = regrets.setdefault(key, {})
            table[action] = max(0.0, table.get(action, 0.0) + sign * reach[1 - player] * (values[action] - value))
            sums = totals.setdefault(key, {})
            sums[action] = sums.get(action, 0.0) + iteration * reach[player] * sigma[action]
        return value

    def expected(node):
        if node[0] == "end":
            return node[1]
        if node[0] == "chance":
            return sum(p * expected(child) for p, child in node[1])
        _, _, key, children = node
        sigma = strategy(key, [a for a, _ in children], totals)
        return sum(sigma[a] * expected(child) for a, child in children)

    for iteration in range(1, 1501):
        cfr(tree, [1.0, 1.0], iteration)
    assert expected(tree) == pytest.approx(-1 / 18, abs=2e-3)


def test_chance_nodes_list_their_outcomes_and_each_seat_knows_only_its_own_card():
    game = fg_env.rl.game(KUHN)
    root = game.new_initial_state()
    assert root.current_player() == CHANCE and sum(p for _, p in root.chance_outcomes()) == pytest.approx(1)

    def dealt(first, second):
        state = game.new_initial_state()
        state.apply_action(first)
        state.apply_action(second)
        return state

    low, high = dealt(0, 0), dealt(2, 1)  # p0 holds 1 or 3; p1 holds 2 either way
    assert low.information_state(1) == high.information_state(1)
    assert low.information_state(0) != high.information_state(0)
    assert "Your card: 2" in low.observation_string(1) and "Your card: 1" in low.observation_string(0)
    assert low.history() == [{"chance": 0, "outcome": "1"}, {"chance": 0, "outcome": "2"}]


def test_action_ids_are_stable_and_masks_match_legal_actions():
    game = fg_env.rl.game(TIC_TAC_TOE)
    assert game.num_distinct_actions() == 10  # end_turn and mark(cell=0..8)
    state = game.new_initial_state()
    assert state.legal_actions() == list(range(1, 10))
    assert game.space.decode(5) == ("mark", {"cell": 4})
    assert game.space.encode("mark", {"cell": 4}) == 5
    state.apply_action(5)
    assert 5 not in state.legal_actions()
    mask = state.legal_actions_mask()
    assert len(mask) == 10 and mask[5] == 0 and sum(mask) == 8
    assert state.legal_tool_calls()[0].text == "mark(cell=0)"


def test_free_text_is_unlisted_and_a_step_makes_numbers_enumerable():
    game = fg_env.rl.game(AUCTIONEER)
    assert game.space.parametric == {"say": "text: free text"}
    state = game.new_initial_state()
    assert [a.args["amount"] for a in state.legal_tool_calls() if a.tool == "bid"] == [0.0, 2.5, 5.0, 7.5, 10.0]
    assert "say" in state.unlisted_actions()
    state.apply_action({"tool": "say", "args": {"text": "Going once"}})
    with pytest.raises(ValueError, match="not legal"):
        state.apply_action({"tool": "bid", "args": {"amount": 3}})
    state.apply_action({"tool": "bid", "args": {"amount": 7.5}})
    assert state.is_terminal() and state.returns() == [7.5]
    replies = []
    fg_env.run(AUCTIONEER, lambda wake: replies.append(wake.call("bid", {"amount": 3}).text) or wake.end(), seed=1)
    assert "must go in steps of 2.5 from 0" in replies[0]
    schema = fg_env.load(AUCTIONEER, seed=1).preview("x")["tools"][0]["input_schema"]["properties"]["amount"]
    assert schema["multipleOf"] == 2.5


def test_illegal_moves_raise_and_change_nothing():
    state = fg_env.rl.game(TIC_TAC_TOE).new_initial_state()
    state.apply_action(5)
    key = state.state_key()
    with pytest.raises(ValueError, match="not legal for seat 1"):
        state.apply_action({"tool": "mark", "args": {"cell": 4}})
    assert state.state_key() == key and state.current_player() == 1


def test_clones_and_children_are_independent_and_states_serialize():
    game = fg_env.rl.game(NIM, inputs={"stones": 7})
    state = game.new_initial_state()
    child = state.child(3)
    assert state.legal_actions() == [1, 2, 3] and state.move_number() == 0
    assert child.move_number() == 1 and child.current_player() == 1
    copy_of_child = child.clone()
    copy_of_child.apply_action(1)
    assert child.move_number() == 1
    restored = game.deserialize_state(copy_of_child.serialize())
    assert restored.state_key() == copy_of_child.state_key()
    assert restored.history() == copy_of_child.history()
    with pytest.raises(ValueError, match="belongs to game"):
        fg_env.rl.game(NIM, inputs={"stones": 9}).deserialize_state(copy_of_child.serialize())


def test_simultaneous_stages_are_joint_nodes_or_turn_based_sequences():
    game = fg_env.rl.game(MATCHING_PENNIES)
    state = game.new_initial_state()
    assert state.current_player() == SIMULTANEOUS and state.acting_players() == [0, 1]
    assert state.legal_actions(1) == [1, 2]
    with pytest.raises(ValueError, match="apply_actions"):
        state.apply_action(1)
    state.apply_actions({0: {"tool": "show", "args": {"side": "tails"}},
                         1: {"tool": "show", "args": {"side": "tails"}}})
    assert state.current_player() == TERMINAL and state.returns() == [1.0, -1.0]
    turns = game.as_turn_based()
    heads, tails = turns.new_initial_state(), turns.new_initial_state()
    assert heads.current_player() == 0
    heads.apply_action({"tool": "show", "args": {"side": "heads"}})
    tails.apply_action({"tool": "show", "args": {"side": "tails"}})
    assert heads.current_player() == 1
    assert heads.information_state(1) == tails.information_state(1)


def test_sealed_choices_meet_chance_only_when_they_commit():
    state = fg_env.rl.game(DUEL).new_initial_state()
    assert state.is_simultaneous_node() and state.legal_actions(0) == [1]
    state.apply_actions([{"tool": "shoot"}, "shoot"])
    assert state.is_chance_node() and state.chance_outcomes() == [(0, 0.25), (1, 0.75)]
    state.apply_action("hit")
    state.apply_action(1)
    assert state.is_terminal() and state.returns() == [1.0, 0.0]


def test_rewards_add_up_to_returns_and_every_run_reports_returns_per_seat():
    state = fg_env.rl.game(NIM, inputs={"stones": 6}).new_initial_state()
    earned = [0.0, 0.0]
    while not state.is_terminal():
        state.apply_action(state.legal_actions()[-1])
        earned = [total + reward for total, reward in zip(earned, state.rewards())]
    assert earned == state.returns()
    result = fg_env.run(NIM, seed=2)
    assert set(result.returns) == {"a", "b"} and sum(result.returns.values()) == 0
    unfair = copy.deepcopy(NIM)
    unfair["game"]["returns"] = "1 if $world.winner != '' else 0"  # every seat wins: not zero-sum
    broken = fg_env.run(unfair, seed=2)
    assert not broken.ok and [issue["path"] for issue in broken.output_issues] == ["game.utility"]


def test_observations_are_what_the_seat_reads_and_what_it_may_see():
    state = fg_env.rl.game(TIC_TAC_TOE).new_initial_state()
    text = state.observation_string(0)
    assert "0 1 2\n3 4 5\n6 7 8" in text and "Now: It is your turn." in text
    acting, waiting = state.observation(0, "struct"), state.observation(1, "struct")
    assert len(acting["actions"]) == 9 and waiting["actions"] is None
    assert acting["views"]["board"].startswith("You play x. Board") and acting["me"]["id"] == "x"
    assert acting["entities"] == []  # nobody else is inspectable by default
    open_kuhn = copy.deepcopy(KUHN)
    open_kuhn["types"]["player"]["inspect"] = True
    kuhn = fg_env.rl.game(open_kuhn).new_initial_state()
    kuhn.apply_action(0)
    kuhn.apply_action(0)
    other = kuhn.observation(0, "struct")["entities"]
    assert [entity["props"] for entity in other if entity["id"] == "p1"] == [{"seat": 1}]  # the private card is hidden


def test_the_game_section_is_checked_and_mechanisms_can_fill_it_in():
    def errors(contract):
        return {(issue.path, issue.message) for issue in fg_env.check(contract) if issue.severity == "error"}

    odd = copy.deepcopy(NIM)
    odd["game"].update({"utility": "zero sum", "players": "stone"})
    del odd["game"]["returns"]
    found = errors(odd)
    assert any(path == "game.utility" for path, _ in found)
    assert any(path == "game.returns" for path, _ in found)
    assert any(path == "game.players" for path, _ in found)
    constant = copy.deepcopy(NIM)
    constant["game"]["utility"] = "constant_sum"
    assert any("needs `total`" in message for _, message in errors(constant))
    stepped = copy.deepcopy(AUCTIONEER)
    stepped["actions"]["say"]["params"]["text"] = {"type": "text", "step": 1}
    assert any(path.endswith("params.text.step") for path, _ in errors(stepped))
    data = {"game": {"returns": "$actor.score"}}
    merge_sections(data, {"game": {"returns": "$actor.chips", "utility": "zero_sum"}})
    assert data["game"] == {"returns": "$actor.score", "utility": "zero_sum"}


def test_tournaments_score_seats_by_their_returns_and_describe_reads_the_utility_class():
    from fg_env.describe import describe
    from fg_env.tournament.scoring import SeatScorer

    result = fg_env.run(NIM, seed=2)
    scorer = SeatScorer(fg_env.parse(NIM), None, ["a", "b"], {"a": "Ann", "b": "Bob"})
    assert scorer(result) == (result.returns, "")
    metadata = describe(NIM).metadata
    assert metadata["utility"] == "zero_sum" and "checked" in metadata["evidence"]["utility"][0]
    constant = copy.deepcopy(NIM)
    constant["game"].update({"returns": "1", "utility": "general_sum"})
    assert describe(constant).metadata["utility"] == "general_sum"


def test_claims_in_the_game_section_are_verified_by_check():
    claiming = copy.deepcopy(NIM)
    claiming["game"].update({"dynamics": "simultaneous", "returns": "1", "utility": "zero_sum"})
    found = {issue.path: issue for issue in fg_env.check(claiming)}
    assert found["game.dynamics"].severity == "error" and "'sequential'" in found["game.dynamics"].message
    assert found["game.utility"].severity == "error" and "'identical'" in found["game.utility"].message
    honest = copy.deepcopy(NIM)
    honest["game"]["dynamics"] = "sequential"
    assert [i for i in fg_env.check(honest) if i.severity == "error"] == []


def test_a_game_without_returns_says_what_to_declare():
    plain = copy.deepcopy(NIM)
    del plain["game"]
    state = fg_env.rl.game(plain).new_initial_state()
    with pytest.raises(ContractError, match="game.returns"):
        state.returns()
