"""The operations queue mode: a service system that matches known queueing results, routes by skill and priority,
offers callbacks, brings back retries, and keeps its state through snapshots, clones and forks."""
import copy
import json
import math

import pytest

import fg_env

HALF_HOURS = {"unit": "minute", "step": 30}


def centre(rounds=8, channels=None, servers=None, clock=None, **extra):
    queue = {"kind": "operations", "mode": "queue",
             "channels": channels or {"calls": {"arrivals": 100, "service": {"mean": 180}, "threshold": 20}},
             "servers": servers or {"agents": {"staff": 12}}, **extra}
    return {"name": "Centre", "clock": clock or {"rounds": rounds, **HALF_HOURS}, "types": {"clerk": {}},
            "inputs": {"staffing": {"type": "list", "default": [12] * rounds}},
            "mechanisms": {"q": queue}}


def run(contract, seed=1, **options):
    result = fg_env.run(contract, seed=seed, **options)
    assert result.status == "completed", result.error
    return result


def erlang_c(servers, load):
    """Probability an arriving customer waits in an M/M/c queue with offered load ``load`` Erlangs."""
    below = sum(load ** k / math.factorial(k) for k in range(servers))
    top = load ** servers / math.factorial(servers) * servers / (servers - load)
    return top / (below + top)


def erlang_a(rate, service, patience, servers, cap=400):
    """(abandonment probability, utilisation) of M/M/c+M from its birth-death chain."""
    weights, weight = [1.0], 1.0
    for n in range(1, cap):
        weight *= rate / (min(n, servers) / service + max(0, n - servers) / patience)
        weights.append(weight)
    total = sum(weights)
    probs = [w / total for w in weights]
    in_line = sum(max(0, n - servers) * p for n, p in enumerate(probs))
    busy = sum(min(n, servers) * p for n, p in enumerate(probs))
    return in_line / patience / rate, busy / servers


def test_a_stationary_mmc_queue_matches_erlang_c_service_level_and_speed_of_answer():
    result = run(centre(rounds=400))
    rate, service, servers = 100 / 1800, 180.0, 12
    waits = erlang_c(servers, rate * service)
    drain = servers / service - rate
    assert result.outputs["q_service_level"] == pytest.approx(1 - waits * math.exp(-drain * 20), abs=0.02)
    assert result.outputs["q_asa"] == pytest.approx(waits / drain, rel=0.1)
    assert result.outputs["q_utilisation"] == pytest.approx(rate * service / servers, abs=0.01)
    assert result.outputs["q_abandon_rate"] == 0


def test_customers_with_patience_abandon_at_the_erlang_a_rate():
    contract = centre(rounds=300, channels={"calls": {"arrivals": 100, "service": {"mean": 180},
                                                      "patience": {"mean": 120}, "threshold": 20}},
                      servers={"agents": {"staff": 10}})
    result = run(contract)
    abandon, utilisation = erlang_a(100 / 1800, 180.0, 120.0, 10)
    assert result.outputs["q_abandon_rate"] == pytest.approx(abandon, rel=0.1)
    assert result.outputs["q_utilisation"] == pytest.approx(utilisation, abs=0.015)


def test_staffing_is_an_input_vector_and_changing_it_never_changes_who_arrives():
    contract = centre(servers={"agents": {"staff": "$inputs.staffing[$interval]", "cost": 20}})
    lean = run(contract, inputs={"staffing": [8] * 8})
    rich = run(contract, inputs={"staffing": [9, 10, 11, 12, 13, 14, 15, 16]})
    assert lean.outputs["q_offered_by_interval"] == rich.outputs["q_offered_by_interval"]
    assert rich.outputs["q_staff_by_interval"] == [9, 10, 11, 12, 13, 14, 15, 16]
    assert rich.outputs["q_service_level"] > lean.outputs["q_service_level"]
    assert rich.outputs["q_cost"] == pytest.approx(sum(range(9, 17)) * 0.5 * 20)


def test_paid_hours_gross_staff_up_for_shrinkage():
    result = run(centre(rounds=2, servers={"agents": {"staff": 10, "cost": 30, "shrinkage": 0.25}}))
    assert result.outputs["q_paid_hours"] == pytest.approx(10 * 0.5 * 2 / 0.75)
    assert result.outputs["q_cost"] == pytest.approx(result.outputs["q_paid_hours"] * 30)


def test_a_higher_priority_channel_is_answered_before_customers_who_came_earlier():
    channels = {"regular": {"arrivals": 90, "service": {"mean": 180}},
                "vip": {"arrivals": 20, "service": {"mean": 180}, "priority": 1}}
    result = run(centre(rounds=40, channels=channels, servers={"agents": {"staff": 11}}))
    totals = fg_env.load(centre(rounds=40, channels=channels, servers={"agents": {"staff": 11}}), seed=1)
    totals.run()
    by_channel = totals.props["q_totals"]["channels"]
    assert by_channel["vip"]["asa"] < by_channel["regular"]["asa"] / 4
    assert result.outputs["q_offered"] == by_channel["vip"]["offered"] + by_channel["regular"]["offered"]


def test_skills_route_each_channel_only_to_pools_that_serve_it():
    channels = {"calls": {"arrivals": 50, "service": {"mean": 180}, "patience": {"mean": 60}},
                "chats": {"arrivals": 50, "service": {"mean": 180}}}
    servers = {"phones": {"staff": 0, "skills": ["calls"]}, "desk": {"staff": 20, "skills": ["chats"]}}
    env = fg_env.load(centre(rounds=4, channels=channels, servers=servers), seed=2)
    env.run()
    by_channel = env.props["q_totals"]["channels"]
    assert by_channel["calls"]["answered"] == 0 and by_channel["calls"]["abandoned"] > 0
    assert by_channel["chats"]["answered"] == by_channel["chats"]["offered"]


def test_a_customer_counts_for_the_interval_they_arrived_in_even_when_answered_later():
    contract = centre(rounds=2, channels={"calls": {"arrivals": 30, "service": {"mean": 60}}},
                      servers={"agents": {"staff": "0 if $interval == 0 else 5"}})
    records = fg_env.load(contract, seed=3)
    records.run()
    first, second = records.props["q_intervals"]
    assert first["offered"] > 0 and first["answered"] == first["offered"]
    assert first["within"] < first["answered"] and first["asa"] > 0
    assert first["utilisation"] is None and second["staff"] == 5


def test_callbacks_are_taken_when_the_wait_is_long_and_served_once_nobody_is_waiting():
    channels = {"calls": {"arrivals": 120, "service": {"mean": 180}, "patience": {"mean": 200},
                          "callback": {"when": 60, "accept": 0.5}}}
    servers = {"agents": {"staff": "10 if $interval < 3 else 20"}}
    env = fg_env.load(centre(rounds=6, channels=channels, servers=servers), seed=4)
    result = env.run()
    totals = env.props["q_totals"]
    assert result.outputs["q_callbacks"] == totals["callbacks"] > 0
    assert totals["callbacks_served"] > 0 and result.outputs["q_callbacks_unserved"] == totals["callbacks_waiting"]
    assert totals["callbacks_served"] + totals["callbacks_waiting"] == totals["callbacks"]
    without = copy.deepcopy(centre(rounds=6, channels=channels, servers=servers))
    del without["mechanisms"]["q"]["channels"]["calls"]["callback"]
    assert run(without, seed=4).outputs["q_abandoned"] > result.outputs["q_abandoned"]


def test_a_callback_reserve_keeps_servers_for_live_callers_so_callbacks_stop_taking_their_service():
    def centre_with(reserve):
        channels = {"calls": {"arrivals": 115, "service": {"mean": 180}, "patience": {"mean": 120},
                              "callback": {"when": 30, "accept": 0.7, "reserve": reserve}}}
        return centre(rounds=12, channels=channels, servers={"agents": {"staff": 12}})

    eager, reserved = run(centre_with(0), seed=6).outputs, run(centre_with(3), seed=6).outputs
    assert eager["q_callbacks"] > 0 and reserved["q_callbacks"] > 0
    assert reserved["q_service_level"] > eager["q_service_level"]
    assert reserved["q_callbacks_unserved"] >= eager["q_callbacks_unserved"]


def test_customers_who_gave_up_come_back_as_retries():
    channels = {"calls": {"arrivals": 100, "service": {"mean": 180}, "patience": {"mean": 30},
                          "retry": {"chance": 1, "delay": {"mean": 300}, "max": 2}}}
    result = run(centre(rounds=6, channels=channels, servers={"agents": {"staff": 8}}))
    assert result.outputs["q_retrials"] > 0
    assert result.outputs["q_offered"] > sum(run(centre(rounds=6, channels={"calls": {**channels["calls"], "retry": None}},
                                                        servers={"agents": {"staff": 8}})).outputs["q_offered_by_interval"])


def test_a_continuous_clock_plays_the_same_intervals_as_a_round_clock():
    rounds = run(centre(rounds=4, channels={"calls": {"arrivals": 80, "service": {"mean": 200}, "patience": {"mean": 100}}},
                        servers={"agents": {"staff": "$inputs.staffing[$interval]"}}), inputs={"staffing": [6, 8, 10, 12]})
    continuous = centre(rounds=4, channels={"calls": {"arrivals": 80, "service": {"mean": 200}, "patience": {"mean": 100}}},
                        servers={"agents": {"staff": "$inputs.staffing[$interval]"}},
                        clock={"mode": "continuous", "unit": "minute", "horizon": 120}, interval=1800)
    timed = run(continuous, inputs={"staffing": [6, 8, 10, 12]})
    for name in ("q_offered_by_interval", "q_service_level_by_interval", "q_staff_by_interval", "q_abandoned"):
        assert timed.outputs[name] == rounds.outputs[name], name
    assert timed.time == 120


def test_a_run_split_by_a_snapshot_a_clone_or_a_fork_continues_exactly():
    contract = centre(rounds=6, channels={"calls": {"arrivals": 110, "service": {"mean": 180}, "patience": {"mean": 90},
                                                    "callback": {"when": 30, "accept": 0.5},
                                                    "retry": {"chance": 0.4, "delay": {"mean": 400}}}},
                      servers={"agents": {"staff": 9}})
    straight = fg_env.load(contract, seed=5).run().to_dict()
    env = fg_env.load(contract, seed=5)
    env.run(rounds=3)
    assert env.props["q_state"]["waiting"] or env.props["q_state"]["busy"]
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight
    twin = fg_env.load(contract, seed=5)
    twin.run(rounds=3)
    assert twin.clone().run().to_dict() == straight
    assert twin.fork().run().to_dict()["outputs"] == straight["outputs"]


def test_config_mistakes_name_what_to_fix():
    unknown_skill = centre(servers={"agents": {"staff": 3, "skills": ["cals"]}})
    issues = [i for i in fg_env.check(unknown_skill, rounds=0) if i.severity == "error"]
    assert any("'cals' is not a channel" in i.message and "calls" in (i.fix or "") for i in issues)
    unserved = centre(channels={"calls": {"arrivals": 1, "service": {"mean": 1}}, "chats": {"arrivals": 1, "service": {"mean": 1}}},
                      servers={"agents": {"staff": 3, "skills": ["calls"]}})
    assert any("no server pool serves chats" in i.message for i in fg_env.check(unserved, rounds=0))
    no_length = centre(clock={"rounds": 3})
    assert any("needs `interval`" in i.message for i in fg_env.check(no_length, rounds=0))
    fractional = centre(servers={"agents": {"staff": 2.5}})
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(fractional, seed=1)
    result = failed.value.result
    assert result.status == "failed" and "whole number of servers" in result.error


def test_the_guide_documents_the_mode():
    page = fg_env.guide("operations.queue")
    assert "patience" in page and "callback" in page and "$interval" in page
    assert "operations" in fg_env.guide()
