"""Engine hardening: atomicity, conservation, loop locals, scheduling, stop/resume, snapshots, preview."""
import json

import pytest

import fg_env
from fg_env.errors import SnapshotError
from fg_env.expr import Untrusted
from fg_env.participants import SAMPLE_TEXT

TALK = {
    "name": "Talk",
    "clock": {"rounds": 3},
    "types": {"member": {"agent": True, "props": {"said": 0, "cash": {"default": 10, "min": 5},
                                                  "purse": {"default": 0, "max": 12}}}},
    "population": [{"type": "member", "count": 3}],
    "records": {"chat": {"fields": {"text": "text"}}},
    "actions": {
        "say": {"by": "member", "params": {"text": {"type": "text"}},
                "do": [{"post": "chat", "text": "$params.text"}, "$actor.said += 1"]},
        "nudge_then_fail": {"by": "member", "do": [{"wake": "member_3", "why": "Nudged."}, {"fail": "No."}]},
        "give": {"by": "member", "params": {"amount": {"type": "number", "min": 0, "max": 20}},
                 "do": [{"transfer": "cash", "from": "$actor", "to": "$entity(member_3)", "amount": "$params.amount",
                         "into": "purse"}]},
    },
    "stages": [{"name": "talk", "turns": "sequential"}],
}


def _load(contract=TALK, **kw):
    return fg_env.load(contract, seed=kw.pop("seed", 1), **kw)


def _act(env, entity_id, name, args=None):
    """Play one call as `entity_id` inside a real turn and return the tool result."""
    results = []

    def participant(wake):
        if wake.entity_id == entity_id and not results:
            results.append(wake.call(name, args or {}))
        wake.end()

    env.run(participant, rounds=1)
    return results[0]


def test_wake_is_rolled_back_with_a_failed_action():
    env = _load()
    result = _act(env, "member_1", "nudge_then_fail")
    assert not result.ok
    assert env.world.wake_requests == {}


def test_transfer_never_creates_or_destroys_value():
    env = _load()
    over_max = _act(env, "member_1", "give", {"amount": 5})  # member_3's purse holds at most 12 → fits
    assert over_max.ok
    too_much = _act(env, "member_2", "give", {"amount": 5})  # cash may not go below 5 → 5 is fine
    assert too_much.ok
    purse = _act(env, "member_1", "give", {"amount": 0.5})  # would leave 4.5 cash, below min 5
    assert not purse.ok and "cannot go below" in purse.text
    env2 = _load()
    env2.world.entities["member_3"].properties["purse"] = 10
    refused = _act(env2, "member_1", "give", {"amount": 5})
    assert not refused.ok and "can hold at most 12" in refused.text
    total = sum(e.properties["cash"] + e.properties["purse"] for e in env2.world.entities.values())
    assert total == 10 * 3 + 10


def test_locals_assigned_inside_each_stay_assigned():
    contract = {
        "name": "Totals", "clock": {"rounds": 1},
        "world": {"total": 0, "best": {"type": "text", "default": ""}},
        "types": {"shop": {"props": {"sales": 0}}, "player": {"agent": True}},
        "entities": {"a": {"type": "shop", "props": {"sales": 3}}, "b": {"type": "shop", "props": {"sales": 7}},
                     "p": {"type": "player"}},
        "stages": [{"name": "tally", "turns": "sequential", "on_enter": [
            "$sum = 0", "$top = 0", "$name = ''",
            {"each": "shop", "do": ["$sum += $it.sales",
                                    {"if": "$it.sales > $top", "then": ["$top = $it.sales", "$name = $it.id"]}]},
            "$world.total = $sum", "$world.best = $name"]}],
    }
    env = fg_env.load(contract, seed=1)
    env.run()
    assert env.props == {"total": 10, "best": "b"}


def test_effects_scheduled_in_the_same_round_run_in_order():
    contract = {
        "name": "Schedule", "clock": {"rounds": 3},
        "world": {"log": {"type": "list", "default": []}},
        "types": {"player": {"agent": True}},
        "entities": {"p": {"type": "player"}},
        "events": [{"at": 1, "do": [{"after": 1, "do": ["$world.log += first"]},
                                    {"after": 1, "do": ["$world.log += second"]},
                                    {"after": 1, "do": ["$world.log += third"]}]}],
        "stages": [{"name": "wait", "turns": "sequential"}],
    }
    env = fg_env.load(contract, seed=1)
    env.run("idle")
    assert env.props["log"] == ["first", "second", "third"]


def test_list_defaults_are_not_shared_with_the_contract():
    contract = {"name": "Alias", "clock": {"rounds": 1}, "world": {"xs": {"type": "list", "default": [1, 2]}},
                "types": {"player": {"agent": True}}, "entities": {"p": {"type": "player"}},
                "stages": [{"name": "s", "turns": "sequential"}]}
    env = fg_env.load(contract, seed=1)
    env.world.props["xs"].append(3)
    assert contract["world"]["xs"]["default"] == [1, 2]
    assert env.contract.world["xs"].default == [1, 2]


def _talker(wake):
    wake.call("say", {"text": f"hello from {wake.entity_id} in round {wake.round}"})
    wake.end()


def test_stopping_mid_round_resumes_exactly():
    straight = _load(seed=4).run(_talker)
    calls = {"n": 0}

    def stop_on_fourth_point(env):
        calls["n"] += 1
        return calls["n"] == 4

    env = _load(seed=4)
    first = env.run(_talker, stop=stop_on_fourth_point)
    assert first.status == "stopped" and env._in_round
    saved = json.loads(json.dumps(env.snapshot()))
    resumed = env.run(_talker)
    assert resumed.to_dict() == straight.to_dict()
    assert fg_env.Env.restore(TALK, saved).run(_talker).to_dict() == straight.to_dict()


def test_a_stopped_round_counts_as_one_of_rounds():
    env = _load(seed=2)
    env.run(_talker, stop=lambda e: e.round == 2 and e.world.stage is not None)
    assert env.status == "stopped" and env.round == 2
    env.run(_talker, rounds=1)
    assert env.round == 2 and not env._in_round and env.status == "running"


def test_snapshot_round_trip_keeps_provenance_and_continues_identically():
    straight = _load(seed=9).run(_talker)
    env = _load(seed=9)
    env.run(_talker, rounds=1)
    snap = json.loads(json.dumps(env.snapshot()))
    restored = fg_env.Env.restore(env.contract, snap)
    said = [e for e in restored.world.log if e.kind == "action"]
    assert said and all(isinstance(e.data["params"]["text"], Untrusted) for e in said)
    assert restored.run(_talker).to_dict() == straight.to_dict()


def test_snapshot_encodes_maps_with_untrusted_or_non_text_keys():
    from fg_env.snapshot import decode, encode

    value = {Untrusted("injected"): 1, 3: [Untrusted("x")], "$untrusted": "literal", "plain": {"a": 1}}
    back = decode(json.loads(json.dumps(encode(value))))
    assert back == value
    assert any(isinstance(k, Untrusted) for k in back)
    assert isinstance(back[3][0], Untrusted)


def test_restore_accepts_the_base_contract_for_an_arm():
    contract = {**TALK, "inputs": {"bonus": {"type": "number", "default": 0}},
                "arms": {"patched": {"patch": {"clock": {"rounds": 2}}}}}
    env = fg_env.load(contract, seed=3, arm="patched")
    env.run(_talker, rounds=1)
    restored = fg_env.Env.restore(contract, env.snapshot())
    assert restored.run(_talker).rounds == 2


def test_corrupted_snapshots_raise_snapshot_errors():
    env = _load()
    snap = env.snapshot()
    broken = {k: v for k, v in snap.items() if k != "memory"}
    with pytest.raises(SnapshotError, match="incomplete or corrupted"):
        fg_env.Env.restore(env.contract, broken)
    with pytest.raises(SnapshotError, match="version"):
        fg_env.Env.restore(env.contract, {**snap, "fg_env_snapshot": 1})
    with pytest.raises(SnapshotError):
        fg_env.Env.restore(env.contract, "not a snapshot")  # type: ignore[arg-type]


def test_preview_changes_nothing_and_shows_earlier_seats():
    env = _load(seed=5)
    env.run(_talker, rounds=1)
    before = json.dumps(env.snapshot(), sort_keys=True)
    preview = env.preview("member_3")  # earlier seats are played on a copy with built-in participants
    assert preview["update"].startswith("Round 2 of 3")
    assert f"Member 1: «{SAMPLE_TEXT}»" in preview["update"]  # member_1's round-2 post, made on the copy before member_3's turn
    assert json.dumps(env.snapshot(), sort_keys=True) == before
    env.run(_talker, stop=lambda e: e.world.stage is not None)
    count = env._turn_count
    env.preview("member_2")
    assert env._turn_count == count  # mid-round previews take no turn number


def test_preview_prefers_a_stage_that_runs():
    contract = {**TALK, "world": {"open": False},
                "stages": [{"name": "closed", "turns": "sequential", "when": "$world.open"},
                           {"name": "talk", "turns": "sequential"}]}
    env = fg_env.load(contract, seed=1)
    env.run("idle", stop=lambda e: e.world.stage == "talk")
    assert "· talk" in env.preview("member_1")["update"]


def test_run_rejects_bad_arguments_and_reentry():
    env = _load()
    with pytest.raises(ValueError, match="rounds"):
        env.run(rounds=-1)
    with pytest.raises(ValueError, match="unknown participant"):
        env.run({"member": "policy:nope"})
    assert env.status == "ready"

    def reenter(wake):
        env.run(rounds=1)

    with pytest.raises(RuntimeError, match="already running"):
        env.run(reenter, raise_errors=True)


def test_garbage_tool_calls_never_crash():
    env = _load()
    texts = []

    def junk(wake):
        for name, args in [(None, {}), (42, None), ("say", "text"), ("say", [1, 2]), ("look", {"view": ["x"]}),
                           ("inspect", {"id": {"a": 1}}), ("say", {None: None})]:
            texts.append(wake.call(name, args).text)
        wake.end()

    result = env.run(junk, rounds=1)
    assert result.status == "running", result.error
    assert len(texts) == 7 * 3


def test_a_callback_error_marks_the_run_failed_and_is_raised():
    env = _load()

    def boom(event):
        raise KeyError("sink down")

    with pytest.raises(KeyError):
        env.run(_talker, on_event=boom)
    assert env.status == "failed" and "sink down" in (env.error or "")


CACHED = {
    "name": "Def cache", "clock": {"rounds": 3},
    "world": {"pot": 0, "seen": {"type": "list", "default": []}, "draws": {"type": "list", "default": []}},
    "types": {"player": {"agent": True, "props": {"score": 0}}},
    "entities": {"a": {"type": "player"}, "b": {"type": "player"}},
    "defs": {"lead": {"args": ["p"], "expr": "$p.score - $sum(player, $it.score) / 2"},
             "rolls": {"args": [], "expr": "$randint(1, 1000000)"},
             "last_total": {"args": [], "expr": "$len($series.total)"}},
    "metrics": {"total": "$sum(player, $it.score)"},
    "actions": {
        "score": {"by": "player", "do": ["$world.seen += $lead($actor)", "$actor.score += 3",
                                         "$world.seen += $lead($actor)"]},
        "score_then_fail": {"by": "player", "do": ["$actor.score += 100", "$world.seen += $lead($actor)",
                                                   {"fail": "no"}]},
        "roll": {"by": "player", "do": ["$world.draws += $rolls", "$world.draws += $rolls"]},
    },
    "stages": [{"name": "play", "turns": "sequential", "on_exit": ["$world.pot = $last_total"]}],
}


def test_def_results_refresh_after_changes_rollbacks_and_metrics():
    two = [{**CACHED["stages"][0], "max_actions": 2}]  # the refused action is spent, so the next needs a second action
    env = fg_env.load({**CACHED, "stages": two}, seed=1)

    def play(wake):
        if wake.entity_id == "a" and wake.round == 1:
            wake.call("score_then_fail")
            wake.call("score")
        if wake.entity_id == "b" and wake.round == 2:  # one action per turn by default
            wake.call("roll")
        wake.end()

    env.run(play, rounds=2)
    assert env.props["seen"] == [0, 1.5]  # before and after the change in one action; the failed one left nothing
    first, second = env.props["draws"]
    assert first != second  # random defs are never cached
    assert env.props["pot"] == 1  # $series grew by the round-1 sample before round 2's on_exit


def test_experiments_keep_every_run_and_report_paired_deltas():
    contract = {**TALK, "inputs": {"lucky": {"type": "number", "default": 0}},
                "world": {"luck": "$inputs.lucky"},
                "outputs": {"luck": {"expr": "$world.luck + $sum(member, $it.said) * 0", "type": "number"},
                            "talkers": {"expr": "$count(member, $it.said > 0)", "type": "number"}},
                "arms": {"control": {"inputs": {"lucky": 0}}, "lucky": {"inputs": {"lucky": 3}}}}
    result = fg_env.experiment(contract, runs=4, participants="random")
    deltas = result.deltas("control")["lucky"]
    assert deltas["luck"]["mean"] == 3 and deltas["luck"]["clear"]
    assert deltas["talkers"]["mean"] == 0 and not deltas["talkers"]["clear"]  # common random numbers: identical play
    assert "lucky − control · luck: +3" in result.table()

    calls = {"n": 0}

    def sometimes_broken(index, arm):
        calls["n"] += 1

        def participant(wake):
            if index == 1:
                raise RuntimeError("model down")
            wake.end()

        return participant

    mixed = fg_env.experiment(contract, runs=3, participants_for=sometimes_broken)
    assert [r.status for r in mixed.arms["control"].runs].count("failed") == 1
    assert "model down" in mixed.arms["control"].failed[0].error
    assert "failed runs: 2" in mixed.table()

    with pytest.raises(fg_env.ContractError, match="not declared"):
        fg_env.experiment(contract, runs=1, arms=["nope"])
    with pytest.raises(ValueError, match="unknown participant"):
        fg_env.experiment(contract, runs=1, participants="policy:nope")
    with pytest.raises(ValueError, match="runs"):
        fg_env.experiment(contract, runs=0)


def test_views_never_reveal_events_addressed_to_someone_else():
    contract = {**TALK, "views": {"log": {"for": "member", "title": "Log", "show": "{$len($events(action))} actions"}},
                "actions": {**TALK["actions"], "whisper": {"by": "member", "private": True, "do": ["$actor.said += 1"]}}}
    env = fg_env.load(contract, seed=1)
    seen = {}

    def play(wake):
        if wake.entity_id == "member_1":
            wake.call("whisper")
        else:
            seen[wake.entity_id] = wake.update
        wake.end()

    env.run(play, rounds=1)
    private = [e for e in env.world.log if e.kind == "action" and e.to is not None]
    assert private, "the whisper is logged privately"
    assert "0 actions" in seen["member_2"]


def test_effect_results_past_the_size_limits_are_errors():
    from fg_env.expr import MAX_INT_BITS

    contract = {**TALK, "world": {"n": 3, "xs": {"type": "list", "default": [1]}},
                "actions": {"square": {"by": "member", "do": ["$world.n *= $world.n"]},
                            "grow": {"by": "member", "do": ["$world.xs += $world.xs"]},
                            "spawn": {"by": "member", "do": [{"create": "member", "count": "$world.n"}]}}}

    def failure(action, **props):
        env = fg_env.load(contract, seed=1)
        env.world.props.update(props)

        def play(wake):
            wake.call(action)
            wake.end()

        result = env.run(play, rounds=1)
        assert result.error is None and result.stats["faulted_actions"], action  # refused; the run goes on
        return " ".join(d["message"] for d in result.diagnostics if d["code"] == "action_rule_failed")

    assert "bits" in failure("square", n=2 ** (MAX_INT_BITS - 10))
    assert "limit" in failure("grow", xs=list(range(600_000)))
    assert "limit" in failure("spawn", n=10 ** 9)
    with pytest.raises(ValueError, match="at most"):
        fg_env.load(TALK, seed=1).run(rounds=10 ** 9)


NESTED = {
    "name": "Nested", "clock": {"rounds": 1},
    "world": {"grid": {"type": "list", "default": [[0, 0, 0], [0, 0, 0]]},
              "book": {"type": "map", "default": {"bids": [{"px": 10, "qty": 5}], "tally": {}}}},
    "types": {"hero": {"agent": True, "props": {"stats": {"type": "map", "default": {"hp": 10, "tags": []}}}}},
    "entities": {"h": {"type": "hero"}},
    "actions": {
        "paint": {"by": "hero", "params": {"r": "int", "c": "int"}, "do": ["$world.grid[$params.r][$params.c] = 7"]},
        "hurt": {"by": "hero", "do": ["$actor.stats.hp -= 3", "$actor.stats.tags += poisoned"]},
        "fill": {"by": "hero", "do": ["$world.book.bids[0].qty -= 2", "$world.book.tally.yes += 1",
                                     "$world.book.tally.yes += 1"]},
        "paint_then_fail": {"by": "hero", "do": ["$world.grid[0][0] = 9", {"fail": "no"}]},
    },
    "stages": [{"name": "s", "turns": "sequential", "max_actions": 5}],
}


def test_nested_writes_update_copies_and_roll_back():
    env = fg_env.load(NESTED, seed=1)
    before = env.contract.world["grid"].default

    def play(wake):
        assert wake.call("paint", {"r": 1, "c": 2}).ok
        assert wake.call("hurt").ok
        assert wake.call("fill").ok
        assert not wake.call("paint_then_fail").ok
        wake.end()

    result = env.run(play)
    assert result.status == "completed", result.error
    assert env.props["grid"] == [[0, 0, 0], [0, 0, 7]]
    assert before == [[0, 0, 0], [0, 0, 0]]
    assert env.entity("h")["props"]["stats"] == {"hp": 7, "tags": ["poisoned"]}
    assert env.props["book"] == {"bids": [{"px": 10, "qty": 3}], "tally": {"yes": 2}}


def test_nested_write_errors_are_precise():
    env = fg_env.load(NESTED, seed=1)
    texts = []

    def play(wake):
        texts.append(wake.call("paint", {"r": 5, "c": 0}))
        wake.end()

    result = env.run(play)
    assert not texts[0].ok and result.error is None
    assert any("index 5 is out of range for a list of 2" in d["message"] for d in result.diagnostics)
    bad = {**NESTED, "actions": {"x": {"by": "hero", "do": ["$world.grid[0]..y = 1"]}}}
    assert any("property name" in i.message for i in fg_env.check(bad))


def test_arithmetic_overflow_in_a_rule_is_the_rule_s_error_not_the_participant_s():
    contract = {"name": "Overflow", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"cash": 10.5, "count": {"type": "int", "default": 3}}}},
                "entities": {"p": {"type": "p"}},
                "actions": {"grow": {"by": "p", "do": ["$actor.cash *= (10 ** 1000)"], "terminal": True},
                            "grow_int": {"by": "p", "do": ["$actor.count *= (10 ** 1000)"], "terminal": True},
                            "set_huge": {"by": "p", "do": ["$actor.cash = (10 ** 1000)"], "terminal": True}},
                "stages": [{"name": "s", "turns": "sequential"}]}
    for action, expected in (("grow", "too large to represent"), ("grow_int", "fits in a float"),
                             ("set_huge", "fits in a float")):
        env = fg_env.load(contract, seed=1)

        def play(wake, action=action):
            wake.call(action)
            wake.end()

        result = env.run(play)
        assert result.error is None, action  # the action is refused and undone: the rule's error, reported to its author
        message = next(d["message"] for d in result.diagnostics if d["code"] == "action_rule_failed")
        assert expected in message and "participant" not in message, (action, message)
        assert "digits" not in message and len(message) < 400, message


def test_a_snapshot_is_refused_by_a_contract_changed_since_it_was_taken():
    c = {"name": "Save", "clock": {"rounds": 3}, "types": {"p": {"agent": True, "props": {"coins": 1}}},
         "entities": {"a": {"type": "p"}}, "actions": {"earn": {"by": "p", "do": "$actor.coins += 1"}}}
    env = fg_env.load(c, seed=1)
    env.run(lambda wake: wake.call("earn", {}), rounds=1)
    changed = {**c, "actions": {"earn": {"by": "p", "do": "$actor.coins += 100"}}}
    with pytest.raises(SnapshotError, match="different contract"):
        fg_env.Env.restore(changed, env.snapshot())
