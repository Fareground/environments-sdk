"""Repeated business workflows share resources without sharing cohort completion."""
import copy
import json
from pathlib import Path

import pytest

import fg_env

PATH = Path(__file__).parents[1] / "examples/contracts/business_composition/regional_rollouts.json"


def contract():
    return fg_env.expand(json.loads(PATH.read_text()))


def run(c=PATH, **kwargs):
    env = fg_env.load(c, seed=7, **kwargs)
    result = env.run()
    assert result.status == "completed", result.error
    assert result.outputs["money_conserved"] and result.outputs["stock_conserved"]
    return env, result.outputs


def test_two_rollouts_wait_for_shared_funding_and_finish_independently():
    assert not [i for i in fg_env.check(PATH, rounds=0) if i.severity == "error"]
    env, out = run()
    assert (out["launch_north"], out["launch_south"]) == (1, 4)
    assert (out["sales_north"], out["sales_south"]) == (3, 3)
    assert out["cash"] == 60
    assert env.entity("vendor")["props"]["cash"] == 140
    for region in ("north", "south"):
        assert env.entity(f"store_{region}")["props"]["stock"] == {"standard": 2, "premium": 2}
        assert not any(env.entity(f"transit_{region}")["props"]["stock"].values())


def test_an_unfinished_approval_cohort_does_not_block_the_other_workflow():
    c = contract()
    c["entities"]["manager_north_2"]["props"]["available_round"] = 20
    env, out = run(c)
    assert (out["launch_north"], out["launch_south"]) == (0, 1)
    assert (out["sales_north"], out["sales_south"]) == (0, 3)
    assert out["phase_north"] == "review" and out["phase_south"] == "live"
    assert env.entity("vendor")["props"]["cash"] == 70


def test_unfunded_rollout_waits_without_duplicate_payment_or_stock_allocation():
    env, out = run(inputs={"funding": 0})
    assert (out["launch_north"], out["launch_south"]) == (1, 0)
    assert out["cash"] == 30
    assert env.entity("warehouse")["props"]["stock"] == {"standard": 15, "premium": 6}
    assert env.entity("vendor")["props"]["cash"] == 70


@pytest.mark.parametrize("rounds, region", [(2, "north"), (5, "south")])
def test_pending_deliveries_replay_without_duplicate_settlement(rounds, region):
    env = fg_env.load(PATH, seed=7)
    env.run(rounds=rounds)
    assert env.entity(f"transit_{region}")["props"]["stock"] == {"standard": 5, "premium": 2}
    restored = fg_env.Env.restore(PATH, json.loads(json.dumps(env.snapshot())))
    assert env.run().to_dict() == restored.run().to_dict()
    for entity_id in env.world.entities:
        assert env.entity(entity_id) == restored.entity(entity_id)


def test_independent_rollout_results_do_not_depend_on_declaration_order_when_funded():
    c = contract()
    c["entities"]["hq"]["props"]["cash"] = 200
    reversed_c = copy.deepcopy(c)
    reversed_c["mechanisms"] = dict(reversed(list(c["mechanisms"].items())))
    a, outputs = run(c)
    b, reversed_outputs = run(reversed_c)
    assert outputs == reversed_outputs
    for entity_id in a.world.entities:
        assert a.entity(entity_id) == b.entity(entity_id)


def test_specialized_manager_and_customer_types_keep_workflow_access():
    c = contract()
    c["types"]["senior_manager"] = {"extends": "manager"}
    c["types"]["loyal_customer"] = {"extends": "customer"}
    c["entities"]["manager_north_2"]["type"] = "senior_manager"
    c["entities"]["customer_south_3"]["type"] = "loyal_customer"
    _, out = run(c)
    assert (out["launch_north"], out["launch_south"]) == (1, 4)
    assert (out["sales_north"], out["sales_south"]) == (3, 3)


@pytest.mark.parametrize("funding_round", [1, 4, 7])
@pytest.mark.parametrize("funding", [0, 40, 100])
def test_funding_size_and_timing_preserve_resource_and_delivery_constraints(funding_round, funding):
    _, out = run(inputs={"funding_round": funding_round, "funding": funding})
    second_launch = funding_round if funding >= 40 else 0
    assert out["launch_north"] == 1
    assert out["launch_south"] == second_launch
    assert out["sales_north"] == 3
    assert out["sales_south"] == (3 if second_launch and second_launch + 3 <= 8 else 0)
    assert out["cash"] == 100 + funding - 70 * (1 + bool(second_launch))


def test_nested_workflows_need_no_explicit_global_stage_names():
    c = contract()
    for mechanism in c["mechanisms"].values():
        if mechanism.get("kind") == "flow" and mechanism.get("mode") == "procedure":
            for phase in mechanism["phases"].values():
                for stage in phase.get("stages", []):
                    stage.pop("name", None)
    _, expected = run()
    _, actual = run(c)
    assert actual == expected
