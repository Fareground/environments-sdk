"""The social mechanism family (feed, diffusion) and deliberation."""
import json
from pathlib import Path

import pytest

import fg_env
from fg_env.expr import Untrusted, compile_expr

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def ev(env, source, **vars):
    return compile_expr(source)(env.world.evaluation.scope(**vars))


def do(env, actor, effects):
    """Apply effects atomically (as `actor` when given); False when they were refused (a `fail`) and rolled back."""
    try:
        env.rules.run_block(effects, {"actor": env.world.entities[actor]} if actor else {}, "test")
    except fg_env.RunError as exc:
        if "World logic cannot be refused" not in str(exc):
            raise
        return False
    return True


class Script:
    """Participants that work through a queue of tool calls per agent.

    A step is (tool, args) or (tool, args, predicate(env)); it runs as soon as the tool is offered
    (and the predicate holds). ``probes`` are calls made on an agent's first wake whether or not
    the tool is offered (to see what a refusal says). Every update, tool list and result is kept.
    """

    def __init__(self, env, queues, probes=None):
        self.env = env
        self.queues = {k: list(v) for k, v in queues.items()}
        self.probes = {k: list(v) for k, v in (probes or {}).items()}
        self.seen = {}
        self.results = []

    def __call__(self, wake):
        me = wake.entity_id
        log = self.seen.setdefault(me, [])
        log.append(wake.update)
        log.append(json.dumps([t.to_dict() for t in wake.tools]))
        for tool, args in self.probes.pop(me, []):
            log.append(wake.call(tool, args).text)
        queue = self.queues.get(me, [])
        while queue and not wake.done:
            tool, args, *rest = queue[0]
            if tool not in {t.name for t in wake.tools} or (rest and not rest[0](self.env)):
                break
            queue.pop(0)
            result = wake.call(tool, args)
            self.results.append((wake.round, me, tool, result.ok, result.text))
            log.append(result.text)
        wake.end()

    def text(self, agent):
        return "\n".join(self.seen.get(agent, []))


def split_run(contract, participants, seed=3, first=1):
    """(one straight run, the same run split by a JSON snapshot after `first` rounds)."""
    straight = fg_env.load(contract, seed=seed).run(participants).to_dict()
    env = fg_env.load(contract, seed=seed)
    env.run(participants, rounds=first)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    return straight, restored.run(participants).to_dict()


def errors(contract):
    return [str(i) for i in fg_env.check(contract) if i.severity == "error"]


# ---------------------------------------------------------------------------
# deliberation
# ---------------------------------------------------------------------------

HALL = {
    "name": "Hall",
    "clock": {"rounds": 6},
    "types": {"resident": {"agent": True}, "moderator": {"agent": True}},
    "population": [{"type": "resident", "count": 4, "id": "r{$i}", "name": "R{$i}"}],
    "entities": {"mod": {"type": "moderator", "name": "Mod"}},
    "mechanisms": {"hall": {"kind": "decision", "mode": "deliberation", "who": "resident", "chair": "moderator",
                            "floor": True, "speaker_limit": 2, "passes": 8, "question": "Build a skate park?",
                            "end": "decision"}},
    "outputs": {"decided": {"expr": "$len($decisions())", "type": "int"}},
}


def _top(kind):
    def holds(env):
        stack = env.props["hall"]["stack"]
        return bool(stack) and stack[-1]["kind"] == kind and stack[-1]["status"] == "open"
    return holds


def _hall(**config):
    return {**HALL, "mechanisms": {"hall": {**HALL["mechanisms"]["hall"], **config}}}


def test_motion_amendment_and_vote_with_floor_control():
    env = fg_env.load(HALL, seed=1)
    script = Script(env, {
        "mod": [("hall_recognize", {"who": "r1"}), ("hall_recognize", {"who": "r3"}, _top("motion")),
                ("hall_call_question", {}, _top("amendment")), ("hall_call_question", {}, _top("motion"))],
        "r1": [("hall_raise_hand", {}), ("hall_propose", {"text": "Build it by June"}),
               ("hall_vote", {"choice": "yes"}), ("hall_vote", {"choice": "yes"})],
        "r2": [("hall_second", {}), ("hall_second", {}), ("hall_vote", {"choice": "yes"}),
               ("hall_vote", {"choice": "yes"})],
        "r3": [("hall_raise_hand", {}), ("hall_amend", {"text": "Build it by July"}), ("hall_vote", {"choice": "yes"}),
               ("hall_vote", {"choice": "no"})],
        "r4": [("hall_vote", {"choice": "no"}), ("hall_vote", {"choice": "yes"})],
    })
    result = env.run(script)
    assert result.status == "ended" and result.ended_by == "hall", (result.error, script.results)
    assert all(ok for *_, ok, _ in script.results), script.results
    assert not any(script.queues.values()), script.queues  # every scripted step happened
    decision = env.props["hall"]["decisions"][0]
    assert decision["text"] == "Build it by July" and decision["passed"] and decision["counts"]["yes"] == 3
    news = [e["text"] for e in result.events if e["kind"] == "hall"]
    assert any("amendment 2 to motion 1" in t and "passes" in t for t in news)
    assert "R1 raised a hand" in script.text("mod")
    assert result.outputs["decided"] == 1


def test_speaking_needs_the_floor_and_the_speaker_limit_returns_it():
    env = fg_env.load(HALL, seed=1)
    script = Script(env, {
        "r1": [("hall_raise_hand", {}), ("hall_speak", {"text": "one"}), ("hall_speak", {"text": "two"}),
               ("hall_speak", {"text": "three"})],
        "mod": [("hall_recognize", {"who": "r1"})],
    })
    env.run(script, rounds=1)
    assert [r[2] for r in script.results if r[1] == "r1"] == ["hall_raise_hand", "hall_speak", "hall_speak"]
    assert any("R1's time is up" in e.text for e in env.world.log if e.kind == "hall")
    assert env.props["hall"]["floor"] is None


def _stages(env, participant):
    stages = []

    def watch(wake):
        stages.append(wake.stage)
        participant(wake)

    env.run(watch, rounds=1)
    return stages


def test_silence_ends_the_discussion_in_one_pass_and_a_late_speech_is_heard():
    env = fg_env.load(HALL, seed=1)
    assert _stages(env, lambda wake: wake.end()).count("hall") == 5
    assert all(e.properties["hall_ready"] for e in env.world.entities_of("resident"))
    late = fg_env.load(_hall(floor=False), seed=1)
    script = Script(late, {"r4": [("hall_speak", {"text": "one more thing"})]})
    stages = _stages(late, script)
    assert stages.count("hall") == 5 + 4  # everyone else hears r4 in a second pass; r4 has nothing new
    assert not any("Time is up" in e.text for e in late.world.log)


def test_an_endless_debate_hits_the_backstop_and_forces_readiness():
    env = fg_env.load(_hall(floor=False, passes=3), seed=1)
    script = Script(env, {"r1": [("hall_speak", {"text": f"r1 point {n}"}) for n in range(20)],
                          "r2": [("hall_speak", {"text": f"r2 point {n}"}) for n in range(20)]})
    env.run(script, rounds=1)
    assert len(script.results) == 3 * 2 * 2  # three passes, two speakers, two speeches a turn
    assert any("Time is up" in e.text for e in env.world.log)
    assert all(e.properties["hall_ready"] for e in env.world.entities_of("resident"))


def test_member_calls_need_debate_and_sealed_ballots_stay_private():
    contract = {**HALL, "entities": {}, "types": {"resident": {"agent": True}},
                "mechanisms": {"hall": {"kind": "decision", "mode": "deliberation", "who": "resident", "second": False,
                                        "min_debate": 1, "private": True, "end": "never"}}}
    env = fg_env.load(contract, seed=1)
    script = Script(env, {
        "r1": [("hall_propose", {"text": "Adopt"}), ("hall_vote", {"choice": "yes"})],
        "r2": [("hall_speak", {"text": "I support it"}), ("hall_call_question", {}), ("hall_vote", {"choice": "yes"})],
        "r3": [("hall_vote", {"choice": "no"})],
        "r4": [("hall_vote", {"choice": "abstain"})],
    })
    offered_before_debate = []

    def watch(wake):
        stack = env.props["hall"]["stack"]
        if wake.entity_id == "r2" and stack and stack[-1]["speeches"] == 0:
            offered_before_debate.append("hall_call_question" in {t.name for t in wake.tools})
        script(wake)

    env.run(watch, rounds=2)
    assert offered_before_debate == [False]
    assert [(r[1], r[3]) for r in script.results if r[2] == "hall_call_question"] == [("r2", True)]
    votes = [e for e in env.world.log if e.kind == "action" and e.data.get("action") == "hall_vote"]
    assert votes and all(e.to is not None for e in votes)
    decision = env.props["hall"]["decisions"][0]
    assert decision["passed"] and decision["counts"] == {"yes": 2, "no": 1, "abstain": 1}
    assert not env.finished


def test_deliberation_config_errors_and_resume():
    bad = _hall(chair=None, floor=True)
    assert any("floor control needs a chair" in e for e in errors(bad))
    assert errors(HALL) == []
    straight, resumed = split_run(HALL, "random", seed=4)
    assert straight == resumed


def test_deliberation_actions_check_their_own_keys():
    def op(**effect):
        return errors({**HALL, "stages": [{"name": "s", "actions": [], "on_enter": [{"decision": "hall", **effect}]}]})

    assert any("`decision.speak` needs `text`" in e for e in op(action="speak"))
    assert any("'text' is not part of `decision.vote`" in e for e in op(action="vote", choice="yes", text="hi"))
    assert any("did you mean 'raise_hand'" in e for e in op(action="raise_hnd"))


def _top_status(status):
    return lambda env: bool(env.props["hall"]["stack"]) and env.props["hall"]["stack"][-1]["status"] == status


# ---------------------------------------------------------------------------
# social graph
# ---------------------------------------------------------------------------

NET = {
    "name": "Net",
    "clock": {"rounds": 3},
    "types": {"account": {"agent": True, "props": {"side": "a"}}, "moderator": {"agent": True}},
    "entities": {**{k: {"type": "account", "name": k.upper()} for k in ("a", "b", "c", "d")},
                 "m": {"type": "moderator", "name": "Mod"}},
    "links": [{"relation": "net_follows", "from": "a", "to": "b"}, {"relation": "net_follows", "from": "a", "to": "c"},
              {"relation": "net_follows", "from": "b", "to": "c"}, {"relation": "net_follows", "from": "d", "to": "c"}],
    "mechanisms": {"net": {"kind": "social", "mode": "feed", "who": "account", "moderators": "moderator", "mute": True,
                           "friends": True, "downrank": {"labels": ["misleading"], "factor": 0.01}}},
    "metrics": {"insularity": "$insularity()"},
}


def _feed(env, who):
    return [p.properties["author"] for p in ev(env, "$feed($it)", it=env.world.entities[who])]


def test_feeds_rank_followed_posts_and_moderation_downranks_labels():
    env = fg_env.load(NET, seed=1)
    for author in ("b", "c", "d"):
        assert do(env, author, [{"social": "net", "action": "post", "text": f"hello from {author}"}])
    posts = {p.properties["author"]: p.id for p in env.world.entities_of("net_post")}
    assert _feed(env, "a") == ["c", "b"]  # followed accounts only; equal scores → newer first
    assert do(env, "d", [{"social": "net", "action": "react", "target": posts["b"], "reaction": "like"}])
    assert _feed(env, "a") == ["b", "c"]  # engagement lifts b
    assert not do(env, "d", [{"social": "net", "action": "react", "target": posts["b"], "reaction": "like"}])  # once
    assert do(env, "m", [{"social": "net", "action": "label", "target": posts["b"], "label": "misleading"}])
    assert _feed(env, "a") == ["c", "b"]  # downranked below
    assert env.world.entities["b"].properties["net_reputation"] == pytest.approx(0.5 + 0.005 - 0.05)
    assert do(env, "a", [{"social": "net", "action": "repost", "target": posts["c"]}])
    assert not do(env, "a", [{"social": "net", "action": "repost", "target": posts["c"]}])
    assert env.world.entities["c"].properties["net_reposts_received"] == 1
    assert ev(env, "$influence(c)") == 3 + 1  # followers a, b, d + one repost
    repost = [p for p in env.world.entities_of("net_post") if p.properties["kind"] == "repost"][0]
    assert repost.properties["origin"] == "c" and repost.properties["text"] == "hello from c"
    assert not do(env, "m", [{"social": "net", "action": "post", "text": "moderators have no account"}])


def test_blocks_mutes_friends_and_notifications():
    env = fg_env.load(NET, seed=1)
    for author in ("a", "b", "c"):
        assert do(env, author, [{"social": "net", "action": "post", "text": f"post {author}"}])
    posts = {p.properties["author"]: p.id for p in env.world.entities_of("net_post")}
    assert do(env, "a", [{"social": "net", "action": "block", "account": "b"}])
    assert _feed(env, "a") == ["c"] and ev(env, "$linked(a, b, net_follows)") is False
    assert not do(env, "b", [{"social": "net", "action": "reply", "target": posts["a"], "text": "hey"}])
    assert not do(env, "b", [{"social": "net", "action": "follow", "account": "a"}])
    assert do(env, "a", [{"social": "net", "action": "mute", "account": "c"}])
    assert _feed(env, "a") == []
    assert do(env, "c", [{"social": "net", "action": "befriend", "account": "d"}])
    assert do(env, "d", [{"social": "net", "action": "befriend", "account": "c"}])
    assert ev(env, "$linked(c, d, net_friends)") and ev(env, "$relation(c, d, net_requests)") is None
    assert do(env, "d", [{"social": "net", "action": "reply", "target": posts["c"], "text": "nice"}])
    last = [e for e in env.world.log if e.kind == "social"][-1]
    assert (last.to, last.text) == (("c",), f"D replied to your post [{posts['c']}] with [net_post_4].")
    assert ev(env, "$following(a)") == ["c"]


def test_insularity_homophily_and_untrusted_posts():
    tight = {**NET,
             "links": [{"relation": "net_follows", "among": "account", "graph": "complete", "where": "$it.id != d"},
                       {"relation": "net_follows", "from": "d", "to": "a"}]}
    env = fg_env.load(tight, seed=1)
    for x, y in (("b", "a"), ("c", "a"), ("c", "b")):  # complete links each pair one way; add the other
        assert do(env, None, [{"link": "net_follows", "from": x, "to": y}])
    assert ev(env, "$insularity(a)") == 1.0 and ev(env, "$insularity(d)") == 0.0
    assert ev(env, "$insularity()") == 1.0 and ev(env, "$homophily(side)") == 1.0
    script = Script(env, {"a": [("net_post", {"text": "$world.secret {$round}"})]})
    env.run(script, rounds=1)
    post = env.world.entities_of("net_post")[0]
    assert post.properties["text"] == "$world.secret {$round}" and isinstance(post.properties["text"], Untrusted)
    straight, resumed = split_run(NET, "random")
    assert straight == resumed


# ---------------------------------------------------------------------------
# diffusion
# ---------------------------------------------------------------------------

LINE = {
    "name": "Rumor line",
    "clock": {"rounds": 6},
    "types": {"person": {"agent": True, "props": {"heard": 0}}},
    "population": [{"type": "person", "count": 5, "id": "p{$i}", "name": "P{$i}"}],
    "relations": {"knows": {"symmetric": True}},
    "links": [{"relation": "knows", "from": f"p{i}", "to": f"p{i + 1}"} for i in range(1, 5)],
    "mechanisms": {"rumor": {"kind": "social", "mode": "diffusion", "who": "person", "over": "knows",
                             "model": "cascade", "p": 1, "seeds": {"moon": ["p1"]}, "on_adopt": ["$it.heard += 1"]}},
    "metrics": {"reach": "$reach(moon)"},
}


def _with(contract, **config):
    return {**contract, "mechanisms": {"rumor": {**contract["mechanisms"]["rumor"], **config}}}


def test_independent_cascade_spreads_one_hop_per_step():
    env = fg_env.load(LINE, seed=1)
    env.run("idle", rounds=1)
    assert ev(env, "$adopter_count(moon)") == 2 and ev(env, "$spread_state(p3, moon)") == "unaware"
    env.run("idle", rounds=3)
    assert ev(env, "$adopter_count(moon)") == 5 and ev(env, "$reach(moon)") == 5
    assert [e.properties["heard"] for e in env.world.entities_of("person")] == [0, 1, 1, 1, 1]
    assert ev(env, "$exposures(p2, moon)") == 1
    assert ev(env, "$heard(p2)") == [{"item": "moon", "state": "adopted", "exposures": 1}]
    assert do(env, None, [{"social": "rumor", "action": "reject", "item": "moon", "who": "p2"}])
    assert ev(env, "$spread_state(p2, moon)") == "rejected" and ev(env, "$adopter_count(moon)") == 4
    assert ev(env, "$reach(moon)") == 5


def test_cascade_with_no_chance_only_exposes_and_flow_follows_direction():
    env = fg_env.load(_with(LINE, p=0), seed=1)
    env.run("idle", rounds=3)
    assert ev(env, "$adopter_count(moon)") == 1 and ev(env, "$reach(moon)") == 2
    assert ev(env, "$spread_state(p2, moon)") == "exposed"
    against = fg_env.load({**_with(LINE, flow="against"), "relations": {"knows": {}}}, seed=1)
    against.run("idle", rounds=3)
    assert ev(against, "$reach(moon)") == 1  # p1 only tells those who link to it; nobody does
    along = fg_env.load({**_with(LINE, flow="along"), "relations": {"knows": {}}}, seed=1)
    along.run("idle", rounds=3)
    assert ev(along, "$adopter_count(moon)") == 4


def test_linear_threshold_adopts_when_enough_neighbours_have():
    star = {**LINE, "links": [{"relation": "knows", "from": "p1", "to": f"p{i}"} for i in range(2, 6)],
            "mechanisms": {"rumor": {"kind": "social", "mode": "diffusion", "who": "person", "over": "knows",
                                     "model": "threshold", "threshold": 0.5, "seeds": {"moon": ["p2", "p3"]}}}}
    env = fg_env.load(star, seed=1)
    env.run("idle", rounds=1)
    assert ev(env, "$spread_state(p1, moon)") == "adopted" and ev(env, "$exposures(p1, moon)") == 2
    assert ev(env, "$spread_state(p4, moon)") == "unaware"
    env.run("idle", rounds=1)
    assert ev(env, "$adopter_count(moon)") == 5


def test_random_diffusion_is_seeded_resumes_and_checks_its_expressions():
    noisy = {**_with(LINE, p=0.5), "links": [{"relation": "knows", "among": "person", "graph": "complete"}]}
    first = fg_env.load(noisy, seed=9).run("idle").series["reach"]
    assert first == fg_env.load(noisy, seed=9).run("idle").series["reach"]
    straight, resumed = split_run(noisy, "idle", seed=9, first=2)
    assert straight == resumed
    assert any("$it is not available here" in e for e in errors(_with(LINE, p="$it.heard")))
    assert errors(LINE) == []


def _event(contract, *effects):
    return errors({**contract, "events": [{"do": list(effects)}]})


def test_an_old_social_kind_or_a_field_typo_says_what_to_write():
    graph = {**NET, "mechanisms": {"net": {"kind": "feed", "accounts": "account"}}}
    assert any("'feed' is a mode of kind 'social'" in e for e in errors(graph))
    typo = {**NET, "mechanisms": {"net": {**NET["mechanisms"]["net"], "feed_sise": 3}}}
    assert any("`feed_sise` is not a field of `social` mode `feed` → did you mean 'feed_size'?" in e
               for e in errors(typo))
    op = {**NET, "events": [{"do": [{"social": "nett", "action": "follow", "who": "a", "account": "b"}]}]}
    assert any("`social` names a declared social mechanism, got 'nett' → did you mean 'net'?" in e for e in errors(op))


def test_social_actions_check_their_own_keys():
    assert any("`social.follow` needs `account`" in e
               for e in _event(NET, {"social": "net", "action": "follow", "who": "a"}))
    assert any("did you mean 'repost'" in e
               for e in _event(NET, {"social": "net", "action": "repost_it", "target": "x"}))
    assert any('`follow` is an action of the `social` op: {"social": "<mechanism>", "action": "follow"' in e
               for e in _event(NET, {"follow": "net", "who": "a", "account": "b"}))
    assert any("`social.adopt` needs `who`" in e
               for e in _event(LINE, {"social": "rumor", "action": "adopt", "item": "moon"}))
    assert _event(LINE, {"social": "rumor", "action": "step"}) == []
    page = fg_env.guide("social.feed")
    assert page.startswith("### `social.feed`") and "- `follow`" in page and "`max_chars`" in page


# ---------------------------------------------------------------------------
# acceptance examples
# ---------------------------------------------------------------------------

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"


def test_town_hall_debates_amends_and_ends_on_the_vote():
    path = EXAMPLES / "town_hall.json"
    assert errors(path) == []
    for seed in (1, 2, 5, 7):  # residents' stances are drawn at build: on these seeds the hall reaches a decision
        result = fg_env.load(path, seed=seed).run()
        assert result.status == "ended" and result.ended_by == "hall", (seed, result.error)
        assert result.outputs["decided"] and result.outputs["yes"] + result.outputs["no"] > 0
    amended = fg_env.load(path, seed=7).run()
    assert amended.outputs["amended"] and "September" in amended.outputs["motion"]
    env = fg_env.load(path, seed=7)
    env.run(rounds=1)
    assert not env.finished
    restored = fg_env.Env.restore(path, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == amended.to_dict()


@pytest.mark.slow
def test_social_network_reports_reach_and_insularity_and_downranking_cuts_reach():
    path = EXAMPLES / "social_network.json"
    assert errors(path) == []
    env = fg_env.load(path, seed=2)  # the accounts' traits are drawn at build: on this seed moderators label posts
    assert len(env.entities("account")) >= 100
    result = env.run()
    assert result.status == "completed", result.error
    assert result.outputs["reach"] >= 3 and 0 <= result.outputs["insularity"] <= 1
    assert result.outputs["rumor_posts"] > 0 and result.outputs["labelled_posts"] > 0
    experiment = fg_env.experiment(str(path), runs=4, arms=["control", "downrank"], seed=1)
    assert experiment.deltas("control")["downrank"]["reach"]["mean"] < 0


def _star_cascade(persistent, rounds):
    contract = {"name": "Star", "clock": {"rounds": rounds}, "types": {"person": {}},
                "population": [{"type": "person", "count": 21}], "relations": {"knows": {"symmetric": True}},
                "links": [{"relation": "knows", "among": "person", "graph": "star"}],
                "mechanisms": {"word": {"kind": "social", "mode": "diffusion", "who": "person", "over": "knows",
                                        "model": "cascade", "p": 0.3, "persistent": persistent,
                                        "seeds": {"idea": ["person_1"]}}},
                "outputs": {"reach": "$adopter_count('idea')"}}
    return fg_env.run(contract, None, seed=1).outputs["reach"]


def test_a_cascade_adopter_gets_one_chance_unless_the_cascade_is_persistent():
    # the hub tells each leaf once, right after adopting; leaves only know the hub, so a one-shot star stops at step 1
    assert _star_cascade(False, rounds=5) == _star_cascade(False, rounds=1) < 21
    assert _star_cascade(True, rounds=5) > _star_cascade(True, rounds=1)  # a persistent hub keeps trying every step


def test_a_ballots_tools_end_a_question_once_and_leave_a_template_question_to_the_rendered_texts():
    ballot = {"name": "Vote", "clock": {"rounds": 1}, "world": {"bill": "B7"}, "types": {"m": {"agent": True}},
              "entities": {"m": {"type": "m", "count": 3}},
              "mechanisms": {"b": {"kind": "decision", "mode": "ballot", "who": "m", "options": ["yes", "no"],
                                   "method": "approval", "question": "Which projects?"},
                             "c": {"kind": "decision", "mode": "ballot", "who": "m", "options": ["yes", "no"],
                                   "question": "Pass {$world.bill}?"}}}
    env = fg_env.load(ballot)
    asked = [t["description"] for t in env.preview("m_1", stage="b").tools]
    templated = [t["description"] for t in env.preview("m_1", stage="c").tools]
    assert asked[0].startswith("Approve options on: Which projects? (") and "?." not in " ".join(asked)
    assert not any("{" in text for text in templated)


def _spread(seed_id):
    return {"name": "Spread", "clock": {"rounds": 5}, "types": {"person": {}, "rock": {}},
            "entities": {"person": {"type": "person", "count": 4}, "boulder": {"type": "rock"}},
            "inputs": {"e": {"type": "list", "default": [{"from": "person_1", "to": "person_2"},
                                                         {"from": "person_2", "to": "person_3"}]}},
            "relations": {"link": {"symmetric": True, "links": [{"rows": "$inputs.e"}]}},
            "mechanisms": {"d": {"kind": "social", "mode": "diffusion", "who": "person", "over": "link", "p": 1.0,
                                 "seeds": {"x": [seed_id]}}},
            "outputs": {"reach": "$adopter_count('x')"}}


@pytest.mark.parametrize("seed_id, said", [("person1", "did you mean 'person_1'"),
                                           ("person", "names a group of entities"), ("boulder", "is a rock")])
def test_a_seed_that_is_no_entity_of_its_group_is_a_contract_error(seed_id, said):
    """Every entity id a mechanism's config names is checked against the entities the contract makes, with the one
    helper the auction house uses (audit 11 mechanisms HIGH-2): a typo seeded nobody, silently."""
    issues = [i for i in fg_env.check(_spread(seed_id), rounds=0) if i.path == "mechanisms.d.seeds.x[0]"]
    assert [i.severity for i in issues] == ["error"] and said in str(issues[0]), [str(i) for i in issues]
    assert fg_env.run(_spread("person_1"), seed=1).outputs["reach"] == 3
