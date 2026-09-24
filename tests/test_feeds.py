"""External data feeds: host answers written into world props and records, recorded for replay."""
import copy
import json

import pytest

import fg_env
from fg_env import host
from fg_env.expr import Untrusted
from fg_env.host.adapters import historical
from fg_env.host.stubs import StubFeed

MARKET = {
    "name": "Oil desk",
    "clock": {"rounds": 4, "unit": "day", "start": "2026-01-01"},
    "world": {"oil": {"type": "number", "default": 80, "min": 0}, "draws": 0},
    "types": {"trader": {"agent": True, "props": {"cash": 100}}},
    "entities": {"t1": {"type": "trader"}},
    "records": {"news": {"fields": {"headline": "text", "impact": "number"}}},
    "feeds": {
        "oil": {"host": "prices", "into": "world.oil", "query": {"symbol": "BRENT", "date": "{$clock.date}"},
                "fallback": "$world.oil * $uniform(0.9, 1.1)"},
        "wire": {"host": "news", "into": "records.news", "every": 2, "query": "$round",
                 "fallback": [{"headline": "Quiet day", "impact": 0}]},
    },
    "actions": {"wait": {"by": "trader", "do": []}},
    "events": [{"do": ["$world.draws += $random()"]}],
    "metrics": {"oil": "$world.oil"},
    "outputs": {"oil": "$world.oil", "news": "$count($records(news))", "draws": "$world.draws"},
}


def _hosts():
    return {"prices": StubFeed(lambda request: 90 + request["round"]),
            "news": StubFeed(lambda request: [{"headline": f"Day {request['query']} report", "impact": 1}])}


def test_a_host_answers_into_world_props_and_records_and_its_text_is_untrusted():
    hosts = _hosts()
    env = host.load(MARKET, hosts=hosts, seed=1)
    result = env.run()
    assert result.status == "completed", result.error
    assert result.series["oil"] == [91, 92, 93, 94]
    assert [call["round"] for call in hosts["news"].calls] == [1, 3]
    first = hosts["prices"].calls[0]
    assert first["query"] == {"symbol": "BRENT", "date": "2026-01-01"}
    assert first["expects"] == {"type": "number", "min": 0} and first["into"] == "world.oil"
    headlines = [entry["headline"] for entry in env.world.records("news")]
    assert headlines == ["Day 1 report", "Day 3 report"] and all(isinstance(h, Untrusted) for h in headlines)
    assert all(entry["author"] is None for entry in env.world.records("news"))


def test_replays_and_restores_never_ask_the_host_again():
    straight_env = host.load(MARKET, hosts=_hosts(), seed=1)
    straight = straight_env.run().to_dict()
    replay = host.load(MARKET, hosts=host.Hosts.replaying(host.tape_of(straight_env)), seed=1)
    assert replay.run().to_dict() == straight
    env = host.load(MARKET, hosts=_hosts(), seed=1)
    env.run(rounds=2)
    later = _hosts()
    restored = host.restore(MARKET, json.loads(json.dumps(env.snapshot())), hosts=later)
    assert restored.run().to_dict() == straight
    assert [call["round"] for call in later["prices"].calls] == [3, 4]


def test_without_a_host_the_fallback_answers_deterministically_and_draws_nothing_from_the_run():
    first = fg_env.load(MARKET, seed=2)
    result = first.run()
    assert result.to_dict() == fg_env.load(MARKET, seed=2).run().to_dict()
    assert result.series["oil"] != [80, 80, 80, 80]
    assert [entry["headline"] for entry in first.world.records("news")] == ["Quiet day", "Quiet day"]
    assert not isinstance(first.world.records("news")[0]["headline"], Untrusted)  # the contract's own text
    without = {key: value for key, value in MARKET.items() if key != "feeds"}
    assert fg_env.load(without, seed=2).run().outputs["draws"] == result.outputs["draws"]
    replay = host.load(MARKET, hosts=host.Hosts.replaying(host.tape_of(first)), seed=2)
    assert replay.run().to_dict() == result.to_dict()


def test_a_feed_without_a_host_or_fallback_stops_the_run_naming_the_host():
    contract = copy.deepcopy(MARKET)
    del contract["feeds"]["oil"]["fallback"]
    result = fg_env.load(contract, seed=1).run()
    assert result.status == "failed"
    assert "feeds.oil" in result.error and "needs the host 'prices'" in result.error


def test_when_decides_which_rounds_fetch():
    contract = copy.deepcopy(MARKET)
    contract["feeds"]["oil"]["when"] = "$round > 2"
    hosts = _hosts()
    result = host.load(contract, hosts=hosts, seed=1).run()
    assert [call["round"] for call in hosts["prices"].calls] == [3, 4]
    assert result.series["oil"] == [80, 80, 93, 94]


@pytest.mark.parametrize("prices, news, message", [
    (StubFeed(["high"]), StubFeed([[]]), "world.oil: must be a finite number"),
    (StubFeed([95]), StubFeed([[{"bogus": 1}]]), "record 'news' has no fields ['bogus']"),
])
def test_an_answer_outside_the_target_shape_fails_clearly(prices, news, message):
    result = host.load(MARKET, hosts={"prices": prices, "news": news}, seed=1).run()
    assert result.status == "failed"
    assert "answered outside its protocol" in result.error and message in result.error


def test_the_historical_adapter_replays_a_price_history_by_date():
    rows = [{"date": "2026-01-04", "close": 72}, {"date": "2025-12-31", "close": 70},
            {"date": "2026-01-02", "close": 75}]
    news = StubFeed([[]])
    result = host.load(MARKET, hosts={"prices": historical(rows, at="date", value="close"), "news": news}, seed=1).run()
    assert result.series["oil"] == [70, 75, 75, 72]
    late = historical([{"date": "2026-02-01", "close": 1}], value="close")
    failed = host.load(MARKET, hosts={"prices": late, "news": news}, seed=1).run()
    assert failed.status == "failed" and "the history starts after date 2026-01-01" in failed.error
    with pytest.raises(ValueError):
        historical([{"day": 1}], at="date")


def test_previews_never_change_the_run():
    straight = host.load(MARKET, hosts=_hosts(), seed=4).run().to_dict()
    env = host.load(MARKET, hosts=_hosts(), seed=4)
    env.run(rounds=1)
    tape = host.tape_of(env)
    assert env.preview("t1")["tools"]  # plays round 2's feeds on a copy of the run
    assert host.tape_of(env) == tape
    assert env.run().to_dict() == straight


def test_the_checker_validates_feed_targets_queries_and_fallbacks():
    contract = copy.deepcopy(MARKET)
    contract["feeds"].update({
        "a": {"host": "x", "into": "world.nope"},
        "b": {"host": "x", "into": "records.nope"},
        "c": {"host": "x", "into": "entities.t1"},
        "d": {"host": " ", "into": "world.oil", "fallback": "high", "when": "$nope > 1", "query": "{$world.nah}"},
        "e": {"host": "x", "into": "world.host_tape"},
    })
    found = [(i.path, i.message) for i in fg_env.check(contract) if i.severity == "error"]
    for issue in [
        ("feeds.a.into", "world has no property 'nope'"),
        ("feeds.b.into", "'nope' is not a declared record"),
        ("feeds.c.into", "a feed writes into 'world.<prop>' or 'records.<record>'"),
        ("feeds.d.host", "names no host"),
        ("feeds.d.fallback", "world.oil must be a number, got 'high'"),
        ("feeds.e.into", "world has no property 'host_tape'"),
    ]:
        assert issue in found, (issue, found)
    assert any(path == "feeds.d.when" for path, _ in found) and any(path == "feeds.d.query" for path, _ in found)
    assert [i for i in fg_env.check(MARKET) if i.severity == "error"] == []
    assert "host_tape" in fg_env.load(MARKET, seed=1).props
