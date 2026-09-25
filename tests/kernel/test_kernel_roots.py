"""What the checker lets an event read is what the run binds for it.

An event's `when`, `do` and `say` read the roots every expression reads and the names its anchor binds
(``contract.anchor_roots``: ``$actor $acted $timed_out`` after a turn, ``$it`` on a creation or removal). The checker
allows exactly those, and the run must bind exactly those for all three fields: a field that checks clean and then
fails at run time for a missing root is the class this test closes (audit 13 H4: `say` was checked with the turn's
roots and rendered with none, so a timeout crashed the run).

Every contract of the corpus gets, on every anchor it has (each round and stage point, the creation and removal of
each type, a change), an event whose `when`, `do` and `say` each read every name the anchor binds. The contract must
still check clean (the checker allows them), and a run must play without error, render each probe's `say` wherever
its event fired, and have fired the probes of every anchor a run always reaches.
"""
import time

import pytest
from _corpus import EXAMPLES, FAST_EXAMPLES, FUZZ_FAST, FUZZ_SLOW, SEEDS_FAST, SEEDS_SLOW, clean_fuzz, source
from _leaks import SMALL

import fg_env
from fg_env.contract import anchor_roots
from fg_env.participants import RandomAgent

#: What every probe says, with its anchor, so the log shows which probes fired.
MARK = "kernel-roots"


def _reads(roots):
    """A template, a condition and an effect that each read every one of ``roots``."""
    template = " ".join(f"{{${root}}}" for root in roots)
    condition = " and ".join(f"(${root} == ${root})" for root in roots) or "true"
    return template, condition


def _anchors(contract):
    stages = [stage.name for stage in contract.stage_list()]
    return ["round.start", "round.end",
            *(f"stage.{name}.{point}" for name in stages for point in ("start", "end", "turn")),
            *(f"{kind}.{name}" for name in contract.types for kind in ("create", "remove")), "change"]


def _options(subject):
    """How ``subject`` loads: an example small and with its folder's files, a generated contract as it is."""
    return {} if isinstance(subject, int) else {"inputs": SMALL.get(subject.stem), "data_dir": subject.parent}


def _probed(subject):
    """``subject``'s contract in its current form, with a probe on every anchor; and the probes' anchors."""
    raw = source(subject)
    data, _ = fg_env.migrate(raw)
    if data.get("imports"):  # read from the example's folder, as its file is
        data = {**data, "imports": [str(subject.parent / name) for name in data["imports"]]}
    loaded = fg_env.load(raw, **_options(subject)).contract
    anchors = _anchors(loaded)
    probes = []
    for anchor in anchors:
        template, condition = _reads(anchor_roots(anchor))
        condition = "$round >= 1" if anchor == "change" else condition  # a change event fires as its `when` rises
        say = f"{MARK}[{anchor}] {template}"
        probes.append({"on": anchor, "when": condition, "say": say,
                       "do": [{"emit": "news", "say": say, "to": []}]})
    data = {**data, "events": [*data.get("events", []), *probes]}
    return data, anchors


def _roots_bound(subject, seed):
    data, anchors = _probed(subject)
    options = _options(subject)
    issues = [issue for issue in fg_env.check(data, rounds=0, **options)
              if issue.severity == "error" and MARK in str(issue)]
    assert not issues, issues  # the checker allows every probe
    env = fg_env.load(data, seed=seed, **options)
    result = env.run(RandomAgent(seed=seed), rounds=2)
    assert result.error is None, result.error
    said = {event["text"].split("]")[0].removeprefix(f"{MARK}[") for event in result.events
            if event.get("text", "").startswith(MARK)}
    reached = {"round.start"} if result.rounds else set()  # a run may end before its round does
    assert reached <= said, sorted(reached - said)
    assert said <= set(anchors)


@pytest.mark.parametrize("seed", SEEDS_FAST)
@pytest.mark.parametrize("path", FAST_EXAMPLES, ids=lambda path: path.stem)
def test_an_example_s_events_read_what_the_checker_allows(path, seed):
    _roots_bound(path, seed)


@pytest.mark.parametrize("fuzz", FUZZ_FAST)
def test_a_generated_contract_s_events_read_what_the_checker_allows(fuzz):
    if clean_fuzz(fuzz) is None:
        pytest.skip("the generated contract does not check clean")
    _roots_bound(fuzz, fuzz)


@pytest.mark.slow
@pytest.mark.parametrize("seed", SEEDS_SLOW[:3])
@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.stem)
def test_every_example_s_events_read_what_the_checker_allows(path, seed):
    _roots_bound(path, seed)


@pytest.mark.slow
@pytest.mark.parametrize("fuzz", FUZZ_SLOW)
def test_many_generated_contracts_events_read_what_the_checker_allows(fuzz):
    if clean_fuzz(fuzz) is None:
        pytest.skip("the generated contract does not check clean")
    _roots_bound(fuzz, fuzz)


def test_a_turn_event_s_say_reads_the_turn_it_follows_when_the_turn_ran_out_of_time():
    """audit 13 H4: the documented forfeit, told as news, on a turn that ran out of time."""
    contract = {"name": "Timeout say", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"c": 0}}},
                "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
                "actions": {"inc": {"by": "p", "description": "Inc.", "do": "$actor.c += 1"}},
                "events": [{"on": "stage.play.turn", "when": "$timed_out", "do": "$actor.c -= 5",
                            "say": "{$actor.name} ran out of time."},
                           {"on": "stage.play.turn", "when": "not $acted", "say": "{$actor.name} passed."}]}
    assert [issue for issue in fg_env.check(contract) if issue.severity == "error"] == []

    def slow(wake):
        time.sleep(0.2)
        wake.end()

    result = fg_env.load(contract, seed=1).run({"a": slow, "b": lambda wake: wake.end()}, time_limit=0.05)
    assert result.error is None, result.error
    texts = [event.get("text") for event in result.events]
    assert "a ran out of time." in texts and "b passed." in texts
