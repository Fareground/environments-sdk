"""What arrives after a turn is over: a host answer stays off the tape; reported usage still counts; nothing else does."""
import json
import threading
import time
from pathlib import Path

from test_time_limits import GAME

import fg_env
from fg_env import host
from fg_env.host.stubs import StubTools

COUNCIL = json.loads((Path(__file__).parents[1] / "examples" / "contracts" / "host" / "research_council.json")
                     .read_text())
#: Seconds a turn may take, and how long the slow answer and the late report take: well past the limit.
LIMIT, LATE = 0.2, 0.6


def test_a_host_answer_that_lands_after_its_turn_timed_out_stays_off_the_tape_and_the_run_replays():
    landed = threading.Event()
    lock = threading.Lock()
    slow = []

    def answer(name, args):
        if args["query"] == "slow":
            time.sleep(LATE)
        return f"[1] A report on {args['query']}."

    def participant(wake):
        with lock:
            first = not slow
            slow.append(wake.entity_id)
        refused = wake.call("search", {"query": "slow" if first else "fast"})
        if first:
            slow[0] = refused
            landed.set()

    env = host.load(COUNCIL, hosts={"web_search": StubTools(answer)}, seed=7, exposures=True)
    env.run(participant, rounds=1, time_limit=LIMIT)
    assert landed.wait(5)
    assert slow[0].data["error"] == "timeout"
    answers = [entry["response"] for entry in host.tape_of(env).values()]
    assert answers and not any("slow" in text for text in answers)
    recording = env.result()
    assert recording.budget == {} and recording.stats["timeouts"] == 1
    replayed = fg_env.analysis.trace(recording).replay(COUNCIL)
    assert replayed.ok, replayed.message


#: Ann's turn times out; she reports her model usage after it closed, while Bo's turn waits for the report: the run's
#: turn limit, and when the report lands (after Ann's deadline, well before Bo's).
TURN, REPORT = 0.5, 0.75


def _late_reporter(late):
    reported = threading.Event()

    def participant(wake):
        if wake.entity_id == "ann":
            time.sleep(REPORT)  # the turn closed at its deadline
            wake.record_usage(llm_calls=1, input_tokens=500, output_tokens=20)
            late.append(wake.call("score", {"points": 3}))
            reported.set()
        else:
            reported.wait(5)
            wake.call("score", {"points": 1})

    return participant


def test_usage_reported_after_the_deadline_counts_toward_stats_and_the_budget_but_the_agent_cannot_act():
    late = []
    env = fg_env.load(GAME, seed=1, exposures=True)
    result = env.run(_late_reporter(late), budget={"tokens": 100}, time_limit=TURN)
    assert (result.ended_by, result.rounds) == ("budget", 1)
    assert result.stats["input_tokens"] == 500 and result.agent_stats["ann"]["output_tokens"] == 20
    assert not late[0].ok and env.entity("ann")["props"]["score"] == 0
    ann = next(wake for wake in result.exposures["wakes"] if wake["entity"] == "ann")
    assert ann["late_usage"] == ann["usage"] == {"llm_calls": 1, "input_tokens": 500, "output_tokens": 20}


def test_a_replay_adds_usage_reported_after_the_deadline_as_that_turn_ends():
    result = fg_env.load(GAME, seed=1, exposures=True).run(_late_reporter([]), rounds=1, time_limit=TURN)
    replayed = fg_env.analysis.trace(result).replay(GAME)
    assert replayed.ok, replayed.message
    assert replayed.result.stats["input_tokens"] == result.stats["input_tokens"] == 500


def test_what_a_participant_does_with_its_finished_turns_counts_nothing():
    """A turn's numbers join the run's when the engine is done with it: reading its brief, calling and its deadline
    passing afterwards, from a later turn, change neither the run's numbers nor its agent's (they still add up)."""
    c = {"name": "Pokes", "clock": {"rounds": 3}, "types": {"p": {"agent": True}},
         "entities": {"ann": {"type": "p"}, "bo": {"type": "p"}}, "actions": {"poke": {"by": "p", "do": []}},
         "stages": [{"name": "act", "actions": ["poke"]}], "outputs": {"n": "$count(p)"}}
    kept = []

    def participant(wake):
        if (wake.round, wake.entity_id) == (3, "ann"):
            time.sleep(LIMIT)  # this turn runs out of time; so has every earlier turn's clock by now
            return
        for old in kept:
            assert old.brief and not old.call("poke", {}).ok
        assert wake.call("poke", {}).ok
        kept.append(wake)

    result = fg_env.load(c, seed=1).run(participant, time_limit=LIMIT / 2)
    assert result.status == "completed", result.error
    stats = result.stats
    assert (stats["wakes"], stats["calls"], stats["actions"], stats["timeouts"]) == (6, 5, 5, 1)
    assert (stats["brief_reads"], stats["invalid_calls"]) == (0, 0)
    for field in ("wakes", "calls", "brief_reads", "timeouts"):
        assert sum(agent[field] for agent in result.agent_stats.values()) == stats[field]
