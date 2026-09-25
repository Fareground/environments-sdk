"""What the example environments and mechanisms tell a model: limits in words, floor protocol, hands, cash, replies."""
import copy
import json
from pathlib import Path

import fg_env
from fg_env.mechanisms.card_scoring import poker_hand
from fg_env.mechanisms.deliberation import KIND, DeliberationConfig, _house
from fg_env.registry import mechanism_config

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"


def _example(name):
    return json.loads((EXAMPLES / name).read_text())


def _first_tools(contract, seat, inputs=None, rounds=1, others=None):
    seen = {}

    def agent(wake):
        if "tools" not in seen:
            seen["tools"] = {tool.name: tool for tool in wake.tools}
            seen["update"] = wake.update
        if any(tool.kind == "end" for tool in wake.tools):
            wake.end()

    fg_env.run(contract, {**(others or {}), seat: agent}, seed=1, inputs=inputs or {}, rounds=rounds)
    return seen


def test_text_limits_are_stated_in_words_and_usage_caps_before_the_first_call():
    contract = {"name": "Square", "clock": {"rounds": 1}, "types": {"person": {"agent": True}},
                "entities": {"ann": {"type": "person"}}, "stages": [{"name": "talk"}],
                "actions": {"say": {"by": "person", "per_turn": 1, "per_round": 3,
                                    "params": {"text": {"type": "text", "max_len": 400,
                                                        "description": "What you say."}}}}}
    say = _first_tools(contract, "ann")["tools"]["say"]
    assert say.description == "Say. Once per turn and at most 3 times per round."
    assert (say.input_schema["properties"]["text"]["description"]
            == "What you say. Up to 400 characters (about 50 words).")
    from fg_env.information.tool_text import text_limit

    assert (text_limit(600) == "Up to 600 characters (about 75 words)." and text_limit(4)
            == "Up to 4 characters (about 1 word).")


def test_holdem_says_whether_a_hand_uses_the_hole_cards_or_is_on_the_board():
    assert poker_hand(["AS", "KD"], ["9H", "9C", "2D"]) == "pair of nines on the board — shared by everyone"
    assert poker_hand(["KS", "7D"], ["KH", "7C", "2D"]) == "two pair, kings and sevens, using both hole cards"
    assert poker_hand(["QS", "2D"], ["QH", "9C", "5D", "8S"]) == "pair of queens, using one hole card"
    assert poker_hand(["9S", "9D"], []) == "pair of nines"
    seen = _first_tools(_example("texas_holdem.json"), "player", inputs={"players": 4, "hands": 1})
    assert "Best hand: " in seen["update"] and seen["update"].count("Your hand") == 1  # one heading per thing


def test_a_later_pass_says_again_only_to_an_agent_that_already_had_a_turn_in_the_stage():
    contract = {"name": "Passes", "clock": {"rounds": 2}, "world": {"open": 0},
                "types": {"player": {"agent": True, "props": {"joins": 0}}},
                "entities": {"ann": {"type": "player", "props": {"joins": 0}},
                             "ben": {"type": "player", "props": {"joins": 1}}},
                "actions": {"move": {"by": "player", "do": ["$world.open = 1"], "terminal": True}},
                "stages": [{"name": "play", "passes": 2, "who": "$it.joins <= $world.open", "must_act": True}]}
    turns = []

    def mover(wake):
        turns.append((wake.round, wake.entity_id, wake.reason, "So far:" in wake.update, wake.brief))
        wake.call("move", {})

    fg_env.run(contract, mover, seed=1)
    assert [turn[:3] for turn in turns] == [
        (1, "ann", "It is your turn."), (1, "ann", "Your turn again."), (1, "ben", "It is your turn."),
        (2, "ann", "It is your turn."), (2, "ben", "It is your turn."), (2, "ann", "Your turn again."),
        (2, "ben", "Your turn again.")]
    assert [turn[3] for turn in turns] == [False, False, True] + [False] * 4  # Ben's first turn: "So far:"
    assert turns[0][4].endswith("Your turn ends when you take a final action.")  # a must-act stage offers no pass


def test_the_first_turn_reports_what_happened_so_far_not_since_a_last_turn():
    seen = _first_tools(_example("texas_holdem.json"), "player_4", inputs={"players": 4, "hands": 1})
    assert "So far:" in seen["update"] and "Since your last turn" not in seen["update"]


def test_a_silent_poker_player_is_reported_and_folds_visibly():
    result = fg_env.run(_example("texas_holdem.json"), {"*": "random", "player_1": lambda wake: None}, seed=3,
                        inputs={"players": 4, "hands": 1})
    texts = [event["text"] for event in result.events]
    name = next(text.split(" did not act.")[0] for text in texts if text.endswith(" did not act."))
    after = texts[texts.index(f"{name} did not act.") + 1]
    assert after in (f"{name} folds.", f"{name} checks.")
    assert result.agent_stats["player_1"]["invalid_calls"] == 0


def test_the_floor_refusal_says_what_to_do_before_and_after_raising_a_hand():
    texts = []
    tools = {}

    def resident(wake):
        if wake.stage != "hall" or texts:
            return wake.end() if not wake.done else None
        tools.update({tool.name: tool for tool in wake.tools})
        texts.append(wake.call("hall_speak", {"text": "Parks are good."}).text)
        texts.append(wake.call("hall_raise_hand", {}).text)
        texts.append(wake.call("hall_speak", {"text": "Parks are good."}).text)
        texts.append(wake.call("hall_raise_hand", {}).text)
        wake.end()

    fg_env.run(_example("town_hall.json"), {"r1": resident}, seed=1, inputs={"residents": 3}, rounds=1)
    assert (texts[0]
            == "You cannot hall speak now: You do not hold the floor: raise your hand and wait to be recognized.")
    assert texts[1] == ("Your hand is raised. The chair gives the floor between turns: end your turn now; you will be "
                        "woken when you hold the floor.")
    assert texts[2] == "You cannot hall speak now: Your hand is raised: wait to be recognized."
    assert texts[3] == "You cannot hall raise hand now: Your hand is raised: wait to be recognized."
    assert tools["hall_raise_hand"].description.startswith(
        "Ask the chair for the floor, then end your turn: you are woken when you hold the floor.")


def test_a_refused_tool_name_points_to_end_turn_when_nothing_is_open():
    closed = {"name": "Closed", "clock": {"rounds": 1}, "types": {"p": {"agent": True, "props": {"score": 0}}},
              "entities": {"ann": {"type": "p"}}, "stages": [{"name": "play"}],
              "actions": {"move": {"by": "p", "when": ["$actor.score > 5"], "do": ["$actor.score += 1"]}}}
    refused = []
    fg_env.run(closed, {"ann": lambda wake: refused.append(wake.call("vote", {}).text)}, seed=1)
    assert refused == ["'vote' is not a tool. No actions are available now — call end_turn."]


def test_the_house_view_shows_the_ballot_count_only_while_voting_is_open():
    env = fg_env.load(_example("town_hall.json"), seed=1, inputs={"residents": 3})
    world = env.world
    config = mechanism_config(world, "hall", KIND, DeliberationConfig)
    world.props["hall"] = {**world.props["hall"], "phase": "voting"}
    world.stage = "hall"
    assert "voting opens in the next stage" in _house(world, "hall", config, None)
    assert "Voting now" not in _house(world, "hall", config, None)
    world.stage = "hall_vote"
    assert "Voting now: 0 of 3 ballots cast." in _house(world, "hall", config, None)


def test_a_template_that_quotes_participant_text_again_gets_a_warning():
    contract = _example("werewolf.json")
    assert not [issue for issue in fg_env.check(contract) if "«»" in str(issue)]
    contract["views"]["chat_history"]["show"] = "Day {$it.round} · {$it.author}: «{$it.text}»"
    warned = [issue for issue in fg_env.check(contract) if "«»" in str(issue)]
    assert warned and warned[0].severity == "warning" and "views.chat_history.show" in str(warned[0])


def test_werewolf_chat_history_quotes_each_message_once():
    seen = []

    def player(wake):
        if wake.stage == "day_discussion" and any(t.name == "say" for t in wake.tools):
            wake.call("say", {"text": "I trust Ada."})
        if any(t.name == "look" for t in wake.tools) and wake.stage == "day_vote":
            seen.append(wake.call("look", {"view": "chat_history"}).text)
        if not wake.done and any(t.kind == "end" for t in wake.tools):
            wake.end()

    fg_env.run(_example("werewolf.json"), {"*": "random", "p1": player, "p2": player}, seed=2, rounds=1)
    assert seen and "«I trust Ada.»" in seen[0] and "««" not in seen[0]


def test_werewolf_dawn_is_told_once_per_day_and_recalled_at_the_vote():
    updates = {}

    def player(wake):
        updates.setdefault(wake.stage, wake.update)
        fg_env.participants.RandomAgent(1)(wake)

    env = fg_env.load(_example("werewolf.json"), seed=3)
    env.run({"*": "random", "p1": player}, rounds=1)
    dawn = env.props["last_death"]
    assert updates["day_discussion"].count(dawn) == 1
    assert f"Last night: {dawn}" in updates["day_vote"]


def _farm(cash):
    contract = _example("farmers_market.json")
    contract["entities"]["ana"]["props"] = {"cash": cash}
    return contract


def test_sellers_see_their_cash_and_sponsoring_is_bounded_by_what_they_can_pay():
    broke = _first_tools(_farm(0), "ana", inputs={"rounds": 1, "shoppers": 1}, others={"shopper": "idle"})
    assert "Your listings (your cash: $0.00):" in broke["update"]
    assert "market_sponsor" not in broke["tools"] and "market_promote" in broke["tools"]
    funded = _first_tools(_farm(2.5), "ana", inputs={"rounds": 1, "shoppers": 1}, others={"shopper": "idle"})
    assert funded["tools"]["market_sponsor"].input_schema["properties"]["rounds"]["maximum"] == 2


def test_an_account_may_reply_to_a_trending_post_it_does_not_follow():
    contract = {
        "name": "Square",
        "clock": {"rounds": 2},
        "types": {"account": {"agent": True}},
        "entities": {"a": {"type": "account", "name": "Ann"}, "b": {"type": "account", "name": "Bo"},
                     "c": {"type": "account", "name": "Cy"}},
        "mechanisms": {"net": {"kind": "social", "mode": "feed", "who": "account", "turns": "sequential"}},
        "events": [{"at": 1, "phase": "start", "do": [
            {"social": "net", "action": "follow", "who": "$entity('b')", "account": "$entity('c')"},
            {"social": "net", "action": "post", "who": "$entity('c')", "text": "The river is high."}]}],
    }
    outcome = {}

    def bo(wake):
        if wake.round == 1:
            wake.call("net_like", {"post": "net_post_1"})
        wake.end()

    def ann(wake):
        if wake.round == 2:
            outcome["reply"] = wake.call("net_reply", {"post": "net_post_1", "text": "Stay safe."})
        wake.end()

    fg_env.run(copy.deepcopy(contract), {"a": ann, "b": bo, "c": "idle"}, seed=1)
    assert outcome["reply"].ok, outcome["reply"].text


def test_the_participants_guide_says_how_to_avoid_truncated_replies():
    from fg_env.guides.text import RUNNING

    assert "reasoning_effort=\"low\"" in RUNNING and "frequent decisions" in RUNNING


def _talk(overflow=None):
    text = {"type": "text", "max_len": 60, "description": "What you say."}
    if overflow:
        text["overflow"] = overflow
    return {"name": "Square", "clock": {"rounds": 1}, "types": {"person": {"agent": True}},
            "entities": {"ann": {"type": "person"}}, "stages": [{"name": "talk"}],
            "records": {"chat": {"fields": {"text": "text"}, "show": "{author}: {text}"}},
            "actions": {"say": {"by": "person", "params": {"text": text},
                                "do": [{"post": "chat", "text": "$params.text"}],
                                "outcome": "Said.", "terminal": True}}}


LONG = "The dam is fine, I checked it myself. Please stop sharing the rumor now. It is false."


def test_long_text_is_refused_by_default_and_cut_after_the_last_sentence_that_fits_when_asked():
    seen = {}

    def speaker(wake):
        say = next(t for t in wake.tools if t.name == "say")
        seen["description"] = say.input_schema["properties"]["text"]["description"]
        seen["result"] = wake.call("say", {"text": LONG})
        if not wake.done:
            wake.end()

    fg_env.run(_talk(), {"ann": speaker}, seed=1)
    assert not seen["result"].ok and "the limit is 60" in seen["result"].text
    result = fg_env.run(_talk("truncate"), {"ann": speaker}, seed=1)
    assert seen["result"].ok
    assert seen["result"].text == "Said. (Your text was cut to 37 of 85 characters; the rest was not said.)"
    assert seen["description"] == ("What you say. Up to 60 characters (about 7 words); longer text is cut after the "
                                   "last full sentence that fits.")
    assert [e["data"]["fields"]["text"] for e in result.events if e["kind"] == "record"] == [LONG[:37]]


def test_overflow_truncate_needs_a_text_parameter_with_a_limit():
    contract = _talk("truncate")
    del contract["actions"]["say"]["params"]["text"]["max_len"]
    issues = [issue for issue in fg_env.check(contract, rounds=0) if "overflow" in str(issue)]
    assert issues and issues[0].severity == "error"


def test_werewolf_speech_that_runs_long_is_cut_not_lost():
    seen = []
    first_try = ("Hugo here. Nothing strong yet, and I won't pretend otherwise. Two observations: the near-unanimous "
                 "\"Ada was too eager\" chorus is real, but it's also the safest line to echo — Greta's right not to "
                 "let it harden into today's exile. And \"let's hear from the quiet seats\" spreads suspicion thin, "
                 "as Finn noted. My weak read: wolves are more likely among those shaping the frame early and "
                 "steering consensus than in silence. I'll decide my vote late and watch who pushes a fast bandwagon.")

    def player(wake):
        if wake.stage == "day_discussion" and not seen and any(t.name == "say" for t in wake.tools):
            seen.append(wake.call("say", {"text": first_try}))
        if not wake.done and any(t.kind == "end" for t in wake.tools):
            wake.end()

    fg_env.run(_example("werewolf.json"), {"*": "random", "p8": player}, seed=2, rounds=1)
    assert seen and seen[0].ok and "was cut to" in seen[0].text


def test_the_social_follow_target_lists_the_accounts_and_the_brief_says_the_fact_desk_is_not_one():
    seen = _first_tools(_example("social_network.json"), "u1", inputs={"accounts": 150})
    who = seen["tools"]["net_follow"].input_schema["properties"]["who"]
    assert who["description"] == "The account (not yourself). One of: u1–u150."
    assert "inspect" not in seen["tools"]  # only the agent itself would be inspectable
    briefs = []
    fg_env.run(_example("social_network.json"), {"u1": lambda wake: briefs.append(wake.brief)}, seed=1,
               inputs={"accounts": 150}, rounds=1)
    assert "fact desk" in briefs[0] and "cannot be followed" in briefs[0]


def test_an_enum_schema_names_the_type_its_values_share():
    """Some providers refuse an enum without a `type`: goofspiel's cards are integers."""
    card = _first_tools(_example("games/goofspiel.json"), "a")["tools"]["bid"].input_schema["properties"]["card"]
    assert card["type"] == "integer" and all(isinstance(v, int) for v in card["enum"])
    mixed = {"name": "Mixed", "clock": {"rounds": 1}, "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
             "actions": {"pick": {"by": "p",
                                  "params": {"v": {"type": "enum", "values": [1, 2.5]},
                                             "w": {"type": "enum", "values": [1, "x"]}}}}}
    schema = _first_tools(mixed, "a")["tools"]["pick"].input_schema["properties"]
    assert schema["v"]["type"] == "number" and "type" not in schema["w"]


def test_an_agents_update_opens_with_what_its_last_action_returned_even_when_that_action_ended_its_turn():
    """The built-in LLM participants stop when a turn ends, so a turn-ending action's result is never sent to the
    model: the next update says it."""
    contract = {"name": "Well", "clock": {"rounds": 3}, "types": {"person": {"agent": True, "props": {"coins": 0}}},
                "entities": {"ann": {"type": "person"}}, "stages": [{"name": "day"}],
                "actions": {"dig": {"by": "person", "terminal": True, "do": ["$actor.coins += 2"],
                                    "outcome": "You dig up 2 coins; you have {$actor.coins}."}}}
    updates = []

    def ann(wake):
        updates.append(wake.update)
        if wake.round < 3:
            assert wake.call("dig", {}).ended

    fg_env.run(contract, ann, seed=1)
    assert "Your last turn" not in updates[0]
    assert updates[1].splitlines()[1] == "Your last turn: You dig up 2 coins; you have 2."
    assert updates[2].splitlines()[1] == "Your last turn: You dig up 2 coins; you have 4."


def test_a_moderators_label_tool_offers_exactly_the_posts_its_review_queue_shows():
    """Before, the tool offered every post while the queue listed only engaged ones: the model labelled blind."""
    import re

    seen = []

    def checker(wake):
        label = next((tool for tool in wake.tools if tool.name == "net_label"), None)
        offered = set(label.input_schema["properties"]["post"]["enum"]) if label else set()
        queue = wake.update.split("Most engaged recent posts", 1)[-1] if "Most engaged" in wake.update else ""
        seen.append((offered, set(re.findall(r"\[(net_post_\d+)\]", queue))))
        wake.end()

    fg_env.run(_example("social_network.json"), {"fact_checker": checker}, seed=1, rounds=3)
    assert seen and all(offered == listed for offered, listed in seen), seen
    assert any(offered for offered, _ in seen)
