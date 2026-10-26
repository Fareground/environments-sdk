"""Declaring a stage a mechanism generates refines it: the author's fields win, the rest of the generated stage stays,
and the generated stages keep their order."""
import fg_env

HALL = {
    "name": "Hall",
    "clock": {"rounds": 1},
    "types": {"member": {"agent": True, "props": {"waved": 0}}},
    "entities": {name: {"type": "member"} for name in ("ana", "ben", "cy")},
    "mechanisms": {"hall": {"kind": "decision", "mode": "deliberation", "who": "member", "second": False,
                            "min_debate": 0}},
    "actions": {"wave": {"by": "member", "do": ["$actor.waved += 1"], "terminal": False}},
}


def _stage(contract, name):
    return next(s for s in contract.stage_list() if s.name == name)


def test_a_declared_generated_stage_keeps_its_generated_fields_and_place():
    generated = fg_env.load(HALL, seed=1).contract
    actions = [*_stage(generated, "hall_vote").actions, "wave"]
    refined = fg_env.load({**HALL, "stages": [{"name": "hall_vote", "actions": actions}]}, seed=1).contract
    assert [s.name for s in refined.stage_list()] == [s.name for s in generated.stage_list()] == ["hall", "hall_vote"]
    vote, original = _stage(refined, "hall_vote"), _stage(generated, "hall_vote")
    assert "wave" in vote.actions and (vote.who, vote.until, vote.turns) == (original.who, original.until,
                                                                            original.turns)
    assert ([event.do for event in refined.events if event.on == "stage.hall_vote.start"]
            == [event.do for event in generated.events if event.on == "stage.hall_vote.start"])


def test_stages_the_author_adds_keep_their_place_before_the_generated_ones():
    contract = {**HALL, "stages": [{"name": "warmup", "actions": ["wave"]}]}
    assert [s.name for s in fg_env.load(contract, seed=1).contract.stage_list()] == ["warmup", "hall", "hall_vote"]
