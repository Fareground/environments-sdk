"""Authors see a run's end state without repurposing outputs, and check reaches every policy rule."""
import fg_env

SHOP = {
    "name": "Shop",
    "clock": {"rounds": 3},
    "world": {"footfall": 0},
    "types": {
        "shop": {"agent": True, "props": {"stock": 5, "margin": {"default": 0.25, "private": True}}},
        "shopper": {"props": {"cash": 10}},
    },
    "entities": {"corner": {"type": "shop"}},
    "population": [{"type": "shopper", "count": 7}],
    "actions": {"sell": {"by": "shop", "do": ["$actor.stock -= 1", "$world.footfall += 1"]}},
    "metrics": {"stock": "$sum(shop, $it.stock)"},
    "outputs": {"sold": "$world.footfall"},
}


def test_summary_shows_the_end_state_and_metric_tail():
    result = fg_env.run(SHOP, seed=1)
    lines = result.summary().splitlines()
    assert "state at the end:" in lines
    assert f"  world: footfall={result.outputs['sold']}" in lines
    shop_rows = [line for line in lines if line.startswith("    corner:")]
    assert (shop_rows and "stock=" in shop_rows[0]
            and "margin=0.25" in shop_rows[0])  # private props too: the author's view
    assert "  shopper (7 alive, first 3):" in lines
    assert sum(line.startswith("    shopper_") for line in lines) == 3
    assert any(line.startswith("  stock: ") and "→" in line for line in lines)


def test_a_saved_result_keeps_its_end_state(tmp_path):
    result = fg_env.run(SHOP, seed=1)
    result.save(tmp_path / "run.json")
    assert fg_env.RunResult.load(tmp_path / "run.json").summary() == result.summary()


TRIAGE = {
    "name": "Triage",
    "clock": {"rounds": 3},
    "world": {"treated": 0},
    "types": {
        "doctor": {"agent": True, "props": {"busy": False}},
        "nurse": {"agent": True, "props": {"triaged": 0}},
    },
    "entities": {"doc": {"type": "doctor"}, "nina": {"type": "nurse"}},
    "actions": {"treat": {"by": ["doctor", "nurse"], "do": "$world.treated += 1"}},
    "policies": {"priority": {"rules": [
        {"do": "treat"},
        {"when": "$actor.busy == false", "do": "treat"},
    ]}},
    "outputs": {"treated": "$world.treated"},
}


def test_check_reports_a_broken_rule_an_earlier_rule_always_shadows():
    rules = [{"do": "treat"}, {"when": "$world.treated / $world.zero > 0", "do": "treat"}]
    shadowed = {**TRIAGE, "world": {"treated": 0, "zero": 0},
                "types": {"doctor": {**TRIAGE["types"]["doctor"], "policies": {"priority": {"rules": rules}}},
                          "nurse": TRIAGE["types"]["nurse"]}}
    del shadowed["policies"]
    issues = [issue for issue in fg_env.check(shadowed) if issue.severity == "error"]
    assert [issue.path for issue in issues] == ["types.doctor.policies.priority.rules[1]"]
    assert "division by zero" in issues[0].message


def test_a_shared_policy_is_checked_for_each_type_that_plays_it():
    """Each type that plays a shared policy gets its own copy, checked against its own properties."""
    issues = [issue for issue in fg_env.check(TRIAGE) if issue.severity == "error"]
    assert "types.nurse.policies.priority.rules[1].when" in [issue.path for issue in issues]


def test_a_guarded_shadowed_rule_is_not_reported_and_runs_are_unchanged():
    rules = [{"do": "treat"}, {"when": "$actor.busy == false", "do": "treat"}]
    guarded = {**TRIAGE, "types": {"doctor": {**TRIAGE["types"]["doctor"], "policies": {"priority": {"rules": rules}}},
                                   "nurse": {**TRIAGE["types"]["nurse"],
                                             "policies": {"priority": {"rules": [{"do": "treat"}]}}}}}
    del guarded["policies"]
    assert not [issue for issue in fg_env.check(guarded) if issue.severity == "error"]
    result = fg_env.run(guarded, participants={"doctor": "policy:priority", "nurse": "policy:priority"}, seed=1)
    assert result.ok and result.outputs["treated"] == 6
