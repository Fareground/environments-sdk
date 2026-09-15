"""Host tools inside a turn: the research council searches in sequential and simultaneous stages, keeps
private untrusted evidence, recalls it, stays within its limits, and replays exactly."""
import copy
import hashlib
import json
import time
from pathlib import Path

import fg_env
from fg_env.sdk import host
from fg_env.sdk.expr import Untrusted
from fg_env.sdk.host.stubs import StubTools

COUNCIL = json.loads((Path(__file__).parents[2] / "examples" / "contracts" / "host" / "research_council.json").read_text())


def researcher(log):
    """Searches once in every turn (even past the run's limit), recalls in round 2, posts, forecasts."""
    def participant(wake):
        log.append((wake.entity_id, wake.round, wake.stage, wake.call("search", {"query": f"metro delays {wake.stage}"})))
        if wake.round == 2 and wake.stage == "discuss":
            log.append((wake.entity_id, wake.round, "recall", wake.call("recall", {"query": "metro delays"})))
        if wake.stage == "discuss":
            wake.call("post", {"text": f"{wake.name}: delays are common."})
        else:
            wake.call("forecast", {"p": 0.2 + 0.1 * int(wake.entity_id[-1])})
        wake.end()
    return participant


def slow_tools():
    """Answers after a delay that depends on the arguments, so concurrent turns finish in shuffled order."""
    def answer(name, args):
        digest = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).digest()
        time.sleep(digest[0] / 25_000)
        return f"[1] Metro report on {args['query']}: the opening slipped a year."
    return StubTools(answer)


def _state(env):
    return {key: value for key, value in env.snapshot().items() if key != "stats"}


def test_panelists_search_inside_turns_and_keep_private_untrusted_evidence():
    log = []
    tools = StubTools()
    env = host.load(COUNCIL, hosts={"web_search": tools}, seed=1)
    result = host.run(env, researcher(log))
    assert result.status == "completed", result.error
    searches = [r for _, _, stage, r in log if stage in ("discuss", "ballot")]
    assert len(searches) == 18
    ok = [r for r in searches if r.ok]
    refused = [r for r in searches if not r.ok]
    assert len(ok) == 15 == result.outputs["searches"] == len(tools.calls)
    assert len(refused) == 3 and all("You have used all 5 search calls of this run" in r.text for r in refused)
    assert all(r.text.startswith("«[1] Stub source") for r in ok)
    evidence = env.entity("panelist_1")["props"]["search_evidence"]
    assert len(evidence) == 5 and all(isinstance(e["text"], Untrusted) for e in evidence)
    assert result.outputs["panel_forecast"] == 0.4
    assert result.stats["actions"] == 18  # three posts and three forecasts per round; searches used none


def test_evidence_is_visible_only_to_its_owner_and_recall_finds_it():
    log = []
    peek = {}

    def participant(wake):
        if wake.entity_id == "panelist_2" and wake.round == 2 and wake.stage == "discuss":
            peek["inspect"] = wake.call("inspect", {"id": "panelist_1"}).text
            peek["look"] = wake.call("look", {"view": "search_evidence"}).text
        researcher(log)(wake)

    env = host.load(COUNCIL, hosts={"web_search": StubTools()}, seed=1)
    host.run(env, participant)
    assert "search_evidence" not in peek["inspect"] and "Stub source" not in peek["inspect"]
    assert peek["look"].startswith("Your search evidence:") and "metro delays discuss" in peek["look"]
    recalled = [r for _, _, kind, r in log if kind == "recall"]
    assert recalled and all("You looked up «metro delays discuss» with search" in r.text for r in recalled)


def test_limits_per_turn():
    results = []

    def greedy(wake):
        if wake.stage == "discuss":
            results.extend(wake.call("search", {"query": f"q{i}"}) for i in range(3))
            wake.call("post", {"text": "hi"})
        else:
            wake.call("forecast", {"p": 0.5})
        wake.end()

    env = host.load(COUNCIL, hosts={"web_search": StubTools()}, seed=1)
    result = host.run(env, greedy, rounds=1)
    assert result.status == "running" and result.error is None
    assert [r.ok for r in results[:3]] == [True, True, False]
    assert "2 time(s) per turn" in results[2].text


def test_concurrent_searches_are_deterministic_and_replay_without_the_host():
    runs = []
    for _ in range(2):
        env = host.load(COUNCIL, hosts={"web_search": slow_tools()}, seed=7)
        result = host.run(env, researcher([]))
        assert result.status == "completed", result.error
        runs.append((env, result))
    (first, first_result), (second, _) = runs
    assert _state(first) == _state(second)
    replay = host.load(COUNCIL, hosts=host.Hosts.replaying(host.tape_of(first)), seed=7)
    replayed = host.run(replay, researcher([]))
    assert replayed.outputs == first_result.outputs and _state(replay) == _state(first)


def test_a_council_without_its_search_host_stops_clearly():
    result = host.run(host.load(COUNCIL, seed=1), researcher([]))
    assert result.status == "failed" and "needs the host 'web_search'" in result.error


def test_shared_evidence_is_published_in_seat_order_at_the_end_of_the_round():
    shared = copy.deepcopy(COUNCIL)
    shared["mechanisms"]["search"]["share"] = "all"
    env = host.load(shared, hosts={"web_search": StubTools()}, seed=1)
    host.run(env, researcher([]), rounds=1)
    entries = env.world.records("search")
    assert [e["author"] for e in entries] == ["panelist_1", "panelist_1", "panelist_2", "panelist_2", "panelist_3", "panelist_3"]
    assert all(e["round"] == 1 and isinstance(e["text"], Untrusted) for e in entries)
    assert all(item["shared"] for item in env.entity("panelist_3")["props"]["search_evidence"])


def test_the_plain_engine_offers_search_as_an_action():
    calls = []

    def participant(wake):
        if "search" in {t.name for t in wake.tools}:
            calls.append(wake.call("search", {"query": f"metro {wake.stage}"}))
        wake.end()

    env = fg_env.load(COUNCIL, seed=1)
    host.bind(env, {"web_search": StubTools()})
    result = env.run(participant, rounds=1)
    assert result.status == "running" and result.error is None
    assert len(calls) == 6 and all(c.ok for c in calls)
    assert len(env.entity("panelist_1")["props"]["search_evidence"]) == 2
