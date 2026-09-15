"""What the example environments and mechanisms tell a model: limits in words, floor protocol, hands, cash, replies."""
import copy
import json
from pathlib import Path

import fg_env
from fg_env.sdk.mechanisms.card_scoring import poker_hand
from fg_env.sdk.mechanisms.deliberation import KIND, DeliberationConfig, _house
from fg_env.sdk.mechanisms._social import config_of

EXAMPLES = Path(__file__).parents[2] / "examples" / "contracts"


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
    seen = _first_tools(_example("werewolf.json"), "player", rounds=1)
    whisper = seen["tools"].get("whisper") or seen["tools"]["say"]
    assert "Twice per turn." in whisper.description or "Once per turn." in whisper.description
    assert whisper.input_schema["properties"]["text"]["description"].endswith("Up to 300 characters (about 40 words).")
    from fg_env.sdk.tool_text import text_limit, usage_limits

    assert text_limit(400) == "Up to 400 characters (about 60 words)." and text_limit(600).endswith("(about 90 words).")
    assert usage_limits(1, None) == "Once per turn." and usage_limits(None, 3) == "At most 3 times per round."


def test_holdem_says_whether_a_hand_uses_the_hole_cards_or_is_on_the_board():
    assert poker_hand(["AS", "KD"], ["9H", "9C", "2D"]) == "pair of nines on the board — shared by everyone"
    assert poker_hand(["KS", "7D"], ["KH", "7C", "2D"]) == "two pair, kings and sevens, using both hole cards"
    assert poker_hand(["QS", "2D"], ["QH", "9C", "5D", "8S"]) == "pair of queens, using one hole card"
    assert poker_hand(["9S", "9D"], []) == "pair of nines"
    seen = _first_tools(_example("texas_holdem.json"), "player", inputs={"players": 4, "hands": 1})
    assert "Your hand: " in seen["update"]


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

    def resident(wake):
        if wake.stage != "hall" or texts:
            return wake.end() if not wake.done else None
        texts.append(wake.call("hall_speak", {"text": "Parks are good."}).text)
        wake.call("hall_raise_hand", {})
        texts.append(wake.call("hall_speak", {"text": "Parks are good."}).text)
        texts.append(wake.call("hall_raise_hand", {}).text)
        wake.end()

    fg_env.run(_example("town_hall.json"), {"r1": resident}, seed=1, inputs={"residents": 3}, rounds=1)
    assert texts[0] == "You cannot hall speak now: You do not hold the floor: raise your hand and wait to be recognized."
    assert texts[1] == "You cannot hall speak now: Your hand is raised: wait to be recognized."
    assert texts[2] == "You cannot hall raise hand now: Your hand is raised: wait to be recognized."


def test_the_house_view_shows_the_ballot_count_only_while_voting_is_open():
    env = fg_env.load(_example("town_hall.json"), seed=1, inputs={"residents": 3})
    world = env.world
    config = config_of(world, "hall", KIND, DeliberationConfig)
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


def test_one_shared_tool_keeps_each_actions_constraints_and_description():
    contract = _farm(2.5)
    contract["mechanisms"]["market"]["tools"] = "one"
    tool = _first_tools(contract, "ana", inputs={"rounds": 1, "shoppers": 1}, others={"shopper": "idle"})["tools"]["market"]
    assert "Only these actions are available now:\n- set_price: Change a listing's asking price." in tool.description
    rounds = tool.input_schema["properties"]["rounds"]
    assert "anyOf" not in rounds and rounds["type"] == "integer" and rounds["maximum"] == 20
    assert rounds["description"] == ("Only for promote, sponsor. For promote: Rounds the discount runs. "
                                     "For sponsor: Rounds the listing stays first. From 1 to 2.")
    social = _example("social_network.json")
    social["mechanisms"]["net"]["tools"] = "one"
    net = _first_tools(social, "u1", inputs={"accounts": 30}, rounds=3)["tools"]["net"]
    who = net.input_schema["properties"]["who"]
    assert "anyOf" not in who and len(who["enum"]) == 30 and "For unfollow: An account you follow. One of:" in who["description"]


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
