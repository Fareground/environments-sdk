"""Self-description: metadata derived from contracts (with the evidence), claims, the ODD document and the CLI."""
import copy
import json
from pathlib import Path

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.describe import check_claims, describe

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"

#: Two players take 1–3 stones in turn; whoever takes the last stone wins. Everything is on the table.
NIM = {
    "name": "Nim",
    "clock": {"rounds": 30},
    "world": {"stones": {"type": "int", "default": 10, "min": 0}},
    "types": {"player": {"agent": True, "inspect": True, "props": {"taken": 0}}},
    "entities": {"ann": {"type": "player"}, "bob": {"type": "player"}},
    "actions": {"take": {"by": "player", "params": {"n": {"type": "int", "min": 1, "max": 3}},
                         "when": "$world.stones > 0", "terminal": True,
                         "do": ["$world.stones -= $params.n", "$actor.taken += $params.n",
                                {"if": "$world.stones <= 0", "then": [{"end": "emptied", "winner": "$actor"}]}]}},
    "stages": [{"name": "move", "turns": "sequential"}],
    "views": {"heap": {"for": "player", "show": "Stones left: {$world.stones}"}},
    "outputs": {"either": {"expr": "$world.stones == 0 || $world.stones > 9", "type": "bool"}},
}


def _variant(**changes):
    contract = copy.deepcopy(NIM)
    for path, value in changes.items():
        node = contract
        *head, last = path.split("__")
        for key in head:
            node = node[key]
        node[last] = value
    return contract


def test_the_example_contract_is_valid():
    assert [i for i in fg_env.check(NIM) if i.severity == "error"] == []


def test_a_sequential_open_deterministic_game_is_described_exactly():
    m = describe(NIM).metadata
    assert m["dynamics"] == "sequential" and m["chance_mode"] == "deterministic" and m["chance_during"] == []
    assert m["information"] == "perfect"
    assert (m["num_players"], m["min_players"], m["max_players"]) == (2, 2, 2)
    assert m["max_game_length"] == {"rounds": 30, "decisions": 60}  # 30 rounds × 1 pass × 1 action × 2
    assert m["action_space"] == {"kind": "finite", "size": 3, "per_action": {"take": 3}}
    assert m["utility"] == "unknown" and m["evidence"]["utility"]
    assert m["observations"]["views"] == {"player": ["heap"]} and m["observations"]["inspect"]["player"] == "everyone"


def test_state_the_rules_read_but_nobody_is_shown_makes_information_unknown():
    m = describe(_variant(views__heap__show="It is your turn.")).metadata
    assert m["information"] == "unknown"
    assert any("stones" in line for line in m["evidence"]["information"])


@pytest.mark.parametrize("changes, clue", [
    ({"types__player__props__taken": {"default": 0, "private": True}}, "private props: types.player.props.taken"),
    ({"stages": [{"name": "move", "turns": "simultaneous"}]}, "without seeing each other's choices"),
    ({"entities__ann__brief": "You secretly know the heap started at 10."}, "entities.ann.brief is private"),
])
def test_declared_hiding_makes_information_imperfect(changes, clue):
    m = describe(_variant(**changes)).metadata
    assert m["information"] == "imperfect"
    assert any(clue in line for line in m["evidence"]["information"]), m["evidence"]["information"]


def test_chance_is_found_in_setup_and_in_play_with_its_paths():
    setup = describe(_variant(world__stones={"type": "int", "default": "$randint(8, 12)", "min": 0})).metadata
    assert setup["chance_mode"] == "sampled" and setup["chance_during"] == ["setup"]
    assert any("world.stones.default calls $randint" in line for line in setup["evidence"]["chance_mode"])
    play = describe(_variant(actions__take__do=[{"if": "$chance(0.9)", "then": []}])).metadata
    assert play["chance_during"] == ["play"]
    assert "actions.take.do[0].if calls $chance" in play["evidence"]["chance_mode"]


def test_player_counts_follow_bounded_inputs_and_become_unknown_when_agents_can_be_created():
    crowd = _variant(inputs={"players": {"type": "int", "default": 3, "min": 2, "max": 6}},
                     population=[{"type": "player", "count": "$inputs.players"}], entities={})
    m = describe(crowd).metadata
    assert (m["num_players"], m["min_players"], m["max_players"]) == (3, 2, 6)
    assert m["max_game_length"]["decisions"] is None and "not fixed" in " ".join(m["evidence"]["max_game_length"])
    assert describe(crowd, inputs={"players": 5}).metadata["num_players"] == 5
    spawning = _variant(events=[{"at": 2, "do": [{"create": "player"}]}])
    assert describe(spawning).metadata["max_players"] is None


def test_counts_that_need_the_built_world_are_unknown_when_it_cannot_be_built():
    needs_input = _variant(inputs={"players": {"type": "int", "required": True}},
                           population=[{"type": "player", "count": "$inputs.players"}], entities={})
    m = describe(needs_input).metadata
    assert m["num_players"] is None and m["min_players"] is None
    assert "could not be built" in m["evidence"]["players"][0]


def test_parametric_and_entity_choices_in_the_action_space():
    targeted = _variant(actions__take__params={"n": {"type": "number", "min": 1, "max": 3}})
    assert describe(targeted).metadata["action_space"]["kind"] == "parametric"
    rival = _variant(actions__take__params={"rival": {"type": "entity", "of": "player"}, "loud": "bool"},
                     actions__take__do=["$params.rival.taken += 1"])
    assert describe(rival).metadata["action_space"] == {"kind": "finite", "size": 4, "per_action": {"take": 4}}  # 2 × 2


def test_example_contracts_are_classified_from_their_expanded_parts():
    holdem = describe(EXAMPLES / "holdem_lite.json").metadata
    assert holdem["information"] == "imperfect" and holdem["chance_mode"] == "sampled"
    werewolf = describe(EXAMPLES / "werewolf.json").metadata
    assert any("records.den" in line for line in werewolf["evidence"]["information"])
    assert werewolf["dynamics"] == "mixed"
    chess = describe(EXAMPLES / "chess.json").metadata
    assert chess["dynamics"] == "sequential" and "board" in chess["concepts"]
    assert chess["information"] in ("perfect", "unknown")  # never imperfect: chess hides nothing


def test_feeds_noise_hooks_lossy_messages_atomic_turns_and_spectators_are_described():
    outbreak = describe(EXAMPLES / "outbreak_network.json").metadata
    assert outbreak["external_data"] == [{"feed": "weather", "host": "weather", "into": "world.temperature",
                                          "every": 1}]
    chance = outbreak["evidence"]["chance_mode"]
    assert "mechanisms.weather.fallback calls $normal" in chance
    assert "mechanisms.physics.per.resident.vars.viral_load.noise is a random term" in chance
    assert "actions.advise.do[0] may lose the message (drop)" in chance
    assert outbreak["information"] == "imperfect"
    assert ({"entity_dynamics", "lifecycle_hooks", "external_data", "delayed_or_lossy_messages"}
            <= set(outbreak["concepts"]))
    hop = describe(EXAMPLES / "hopscotch_race.json")
    assert hop.metadata["evidence"]["dynamics"] == [
        "stage hop: sequential turns, atomic (a turn's actions stand or fall together)"]
    assert hop.metadata["observations"]["spectator"] == ["race"]
    assert hop.metadata["max_game_length"]["decisions"] is None  # a turn that breaks `valid` is played again
    assert "### Turn rules of stage `hop`" in hop.markdown and "never shown to an agent: `race`" in hop.markdown


def test_a_spectator_view_does_not_show_state_to_agents():
    omniscient = describe(_variant(views={"heap": {"for": "spectator", "show": "Stones left: "
                                                                               "{$world.stones}"}})).metadata
    assert omniscient["information"] == "unknown"
    assert omniscient["observations"]["spectator"] == ["heap"] and omniscient["observations"]["views"] == {"player": []}


def test_every_example_contract_is_described():
    for path in sorted(EXAMPLES.glob("*.json")):
        description = describe(path)
        for heading in ("## 1. Purpose", "## 2. Entities", "## 3. Process", "## 4. Design concepts",
                        "## 5. Initialisation", "## 6. Input data", "## 7. Submodels", "## Game-theoretic summary"):
            assert heading in description.markdown, (path.name, heading)
        json.dumps(description.to_dict())


def test_odd_markdown_escapes_table_cells_and_names_every_part():
    text = describe(NIM).markdown
    assert "$world.stones == 0 \\|\\| $world.stones > 9" in text
    assert "### Action `take` (by player)" in text and "| n | int | 1…3 |" in text
    assert "whoever" not in text  # nothing is invented beyond the contract


def test_claims_that_the_contract_contradicts_are_errors_and_unverifiable_ones_warnings():
    issues = check_claims(NIM, {"dynamics": "sequential", "information": "imperfect", "utility": "zero_sum",
                                "num_distinct_actions": 3, "colour": "blue"})
    by_path = {i.path: i for i in issues}
    assert set(by_path) == {"game.information", "game.utility", "game.colour"}
    assert (by_path["game.information"].severity == "error"
            and "makes it 'perfect'" in by_path["game.information"].message)
    assert by_path["game.utility"].severity == "warning" and "cannot be verified" in by_path["game.utility"].message
    assert by_path["game.colour"].severity == "warning"


def test_cli_describe_and_its_metadata(tmp_path, capsys):
    path = tmp_path / "nim.json"
    path.write_text(json.dumps(NIM))
    assert main(["describe", str(path)]) == 0
    assert capsys.readouterr().out.startswith("# Nim — ODD description")
    assert main(["describe", str(path), "--metadata"]) == 0
    out = capsys.readouterr().out
    assert "information: perfect" in out and "action space: finite, 3 distinct actions" in out
    assert main(["describe", str(path), "--metadata", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["dynamics"] == "sequential"
    assert main(["describe", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["metadata"]["dynamics"] == "sequential"
    assert main(["describe", str(tmp_path / "missing.json")]) == 1
