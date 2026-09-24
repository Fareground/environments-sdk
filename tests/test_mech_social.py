"""The social mechanism family: channels, deliberation, social graph, diffusion, beliefs, relationships, factions."""
import json
from pathlib import Path

import pytest

import fg_env
from fg_env.expr import Untrusted, compile_expr


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def ev(env, source, **vars):
    return compile_expr(source)(env.world.scope(**vars))


def do(env, actor, effects):
    """Apply effects atomically (as `actor` when given); False when they were refused and rolled back."""
    return env._atomic(effects, {"actor": env.world.entities[actor]} if actor else {}, "test")


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
# channels
# ---------------------------------------------------------------------------

CHAT = {
    "name": "Town chat",
    "clock": {"rounds": 3},
    "types": {"citizen": {"agent": True, "props": {"mood": 1}}, "mayor": {"agent": True}},
    "entities": {"ana": {"type": "citizen", "name": "Ana"}, "ben": {"type": "citizen", "name": "Ben"},
                 "cy": {"type": "citizen", "name": "Cy"}, "dee": {"type": "citizen", "name": "Dee"},
                 "mo": {"type": "mayor", "name": "Mo"}},
    "mechanisms": {"chat": {"kind": "social", "mode": "channels", "who": "citizen", "rooms": ["plaza"], "broadcast": "mayor",
                            "groups": {"cabal": {"members": ["ana", "ben"], "title": "Night committee"}},
                            "create_groups": True, "per_turn": 2}},
    "outputs": {"messages": {"expr": "$len($records(chat))", "type": "int"}},
}

SECRETS = ("SECRET-GROUP", "SECRET-DM", "SECRET-REPLY", "cabal", "Night committee")


def test_channels_contract_checks_clean_runs_and_resumes():
    assert errors(CHAT) == []
    result = fg_env.load(CHAT, seed=2).run()
    assert result.status == "completed", result.error
    straight, resumed = split_run(CHAT, "random")
    assert straight == resumed


def test_private_group_and_dm_never_leak_to_non_members():
    env = fg_env.load(CHAT, seed=1)
    probe = [("inspect", {"id": "ana"}), ("inspect", {"id": "ben"}), ("chat_read", {"channel": "@ben"}),
             ("chat_reply", {"message": 1, "text": "let me in"}), ("chat_say", {"channel": "group_1", "text": "hi"})]
    script = Script(env, {
        "ana": [("chat_say", {"channel": "cabal", "text": "SECRET-GROUP tonight @ben @cy"}),
                ("chat_dm", {"to": "ben", "text": "SECRET-DM"})],
        "ben": [("chat_reply", {"message": 2, "text": "SECRET-REPLY"})],
        "mo": [("chat_broadcast", {"text": "Curfew at ten."})],
    }, probes={"cy": probe, "dee": probe, "mo": probe[:2]})
    # cy and dee probe on their first wake, after ana and ben spoke in seat order
    result = env.run(script)
    assert result.status == "completed", result.error
    world = env.world
    # positive control: members read everything addressed to them
    assert "SECRET-GROUP" in script.text("ben") and "SECRET-DM" in script.text("ben")
    assert "SECRET-REPLY" in script.text("ana")
    assert "Ana mentioned you" in script.text("ben")
    for outsider in ("cy", "dee", "mo"):
        viewer = world.entities[outsider]
        surfaces = [script.text(outsider)]
        surfaces += env.perception.news(viewer, 0)[0]
        surfaces += [env.perception.render_view(n, v, viewer) or "" for n, v in env.contract.views.items()]
        surfaces.append(json.dumps(env.preview(outsider), default=str))
        surfaces.append(json.dumps(ev(env, "$records(chat)", viewer=viewer), default=str))
        surfaces.append(json.dumps([e.to_dict() for e in ev(env, "$events()", viewer=viewer)], default=str))
        if viewer.entity_type == "citizen":
            surfaces.append(json.dumps(ev(env, "[$inbox($it), $recent_messages($it), $inbox_channels($it), $groups($it)]",
                                          it=viewer), default=str))
        blob = "\n".join(surfaces)
        for secret in SECRETS:
            assert secret not in blob, (outsider, secret)
        assert "mentioned you" not in blob
    private = [e for e in result.events if e["kind"] == "record" and e["data"]["fields"]["kind"] in ("group", "dm")]
    assert len(private) == 3 and all(e.get("to") for e in private)  # the operator's log keeps each audience
    assert "Curfew at ten." in script.text("cy")  # broadcasts reach everyone


def test_unread_counts_read_marks_and_replies_stay_in_their_channel():
    env = fg_env.load(CHAT, seed=1)
    ana, ben = env.world.entities["ana"], env.world.entities["ben"]
    assert do(env, "ana", [{"social": "chat", "action": "say", "channel": "cabal", "text": "one"},
                           {"social": "chat", "action": "say", "channel": "cabal", "text": "two"},
                           {"social": "chat", "action": "dm", "to": "ben", "text": "three"}])
    assert ev(env, "$unread($it)", it=ben) == 3 and ev(env, "$unread($it, cabal)", it=ben) == 2
    assert ev(env, "$unread($it, '@ana')", it=ben) == 1 and ev(env, "$unread($it)", it=ana) == 0
    assert do(env, "ben", [{"social": "chat", "action": "read", "channel": "cabal"}])
    assert ev(env, "$unread($it, cabal)", it=ben) == 0 and ev(env, "$unread($it)", it=ben) == 1
    assert "[2] Ana: two" in ev(env, "$channel_log($it, cabal)", it=ben)
    assert do(env, "ben", [{"social": "chat", "action": "reply", "message": 3, "text": "four"}])
    reply = env.world.records("chat")[-1]
    assert reply["kind"] == "dm" and reply["to"] == ["ana"] and reply["reply_to"] == 3
    assert not do(env, "cy", [{"social": "chat", "action": "reply", "message": 1, "text": "sneaky"}])  # cannot see it
    assert not do(env, "cy", [{"social": "chat", "action": "say", "channel": "cabal", "text": "sneaky"}])


def test_participant_text_stays_untrusted_and_mentions_wake_only_the_audience():
    env = fg_env.load(CHAT, seed=1)
    script = Script(env, {"ana": [("chat_say", {"channel": "cabal", "text": "@ben @Cy @nobody $world.chat_groups"}),
                                  ("chat_say", {"channel": "plaza", "text": "hi @dee"})]})
    env.run(script, rounds=1)
    first, second = env.world.records("chat")[:2]
    assert first["text"] == "@ben @Cy @nobody $world.chat_groups" and isinstance(first["text"], Untrusted)
    assert first["mentions"] == ["ben"] and second["mentions"] == ["dee"]
    assert "Ana mentioned you in group cabal [1]." in script.text("ben")
    assert "mentioned" not in script.text("cy")


def test_rate_and_length_limits_are_tool_rules():
    env = fg_env.load(CHAT, seed=1)
    tries = []

    def chatty(wake):
        if wake.entity_id == "ana":
            for n in range(3):
                tries.append(wake.call("chat_say", {"channel": "plaza", "text": f"msg {n}"}).ok)
            tries.append(wake.call("chat_dm", {"to": "ben", "text": "x" * 501}).ok)
        wake.end()

    env.run(chatty, rounds=1)
    assert tries == [True, True, False, False]


def test_groups_can_be_created_joined_and_left_and_only_members_read_them():
    env = fg_env.load(CHAT, seed=1)
    assert do(env, "cy", [{"social": "chat", "action": "create_group", "title": "Book club", "invite": ["dee"]}])
    cy, dee = env.world.entities["cy"], env.world.entities["dee"]
    assert ev(env, "$groups($it)", it=cy) == ["group_2"] and ev(env, "$invites($it)", it=dee) == ["group_2"]
    assert do(env, "cy", [{"social": "chat", "action": "say", "channel": "group_2", "text": "before you joined"}])
    assert do(env, "dee", [{"social": "chat", "action": "join", "group": "group_2"}])
    assert do(env, "cy", [{"social": "chat", "action": "say", "channel": "group_2", "text": "after you joined"}])
    assert [e["text"] for e in env.world.visible_records("chat", dee)] == ["after you joined"]
    assert do(env, "dee", [{"social": "chat", "action": "leave", "group": "group_2"}])
    assert not do(env, "dee", [{"social": "chat", "action": "say", "channel": "group_2", "text": "gone"}])
    assert not do(env, "ana", [{"social": "chat", "action": "join", "group": "group_2"}])  # no invitation


def test_channel_config_errors_say_what_to_fix():
    bad = json.loads(json.dumps(CHAT))
    bad["mechanisms"]["chat"]["rooms"] = ["plaza", "cabal"]
    assert any("declared twice" in e for e in errors(bad))
    bad["mechanisms"]["chat"].update(rooms=["plaza"], who="resident")
    assert any("who 'resident' is not a declared type" in e for e in errors(bad))
    op = {**CHAT, "events": [{"do": [{"social": "chatter", "action": "say", "channel": "plaza", "text": "hi"}]}]}
    assert any("`social` names a declared social mechanism, got 'chatter' → did you mean 'chat'?" in e for e in errors(op))


def _event(contract, *effects):
    return errors({**contract, "events": [{"do": list(effects)}]})


def test_an_old_social_kind_or_a_field_typo_says_what_to_write():
    old = {**CHAT, "mechanisms": {"chat": {"kind": "channels", "members": "citizen"}}}
    assert any("mechanisms.chat.kind: 'channels' is a mode of kind 'social'" in e for e in errors(old))
    graph = {**NET, "mechanisms": {"net": {"kind": "feed", "accounts": "account"}}}
    assert any("'feed' is a mode of kind 'social'" in e for e in errors(graph))
    typo = {**CHAT, "mechanisms": {"chat": {**CHAT["mechanisms"]["chat"], "room": ["plaza"]}}}
    assert any("`room` is not a field of `social` mode `channels` → did you mean 'rooms'?" in e for e in errors(typo))
    foreign = {**CHAT, "mechanisms": {"chat": {**CHAT["mechanisms"]["chat"], "feed_size": 3}}}
    assert any("`feed_size` is not a field of `social` mode `channels`" in e for e in errors(foreign))


def test_social_actions_check_their_own_keys():
    assert any("`social.say` needs `channel`" in e for e in _event(CHAT, {"social": "chat", "action": "say", "text": "hi"}))
    assert any("'in' is not part of `social.say`" in e
               for e in _event(CHAT, {"social": "chat", "action": "say", "in": "plaza", "channel": "plaza", "text": "hi"}))
    assert any("'shout' is not an action of chat (social channels)" in e
               for e in _event(CHAT, {"social": "chat", "action": "shout"}))
    assert any('`say` is an action of the `social` op: {"social": "<mechanism>", "action": "say"' in e
               for e in _event(CHAT, {"say": "chat", "channel": "plaza", "text": "hi"}))
    assert any("`social.follow` needs `account`" in e for e in _event(NET, {"social": "net", "action": "follow", "who": "a"}))
    assert any("did you mean 'repost'" in e for e in _event(NET, {"social": "net", "action": "repost_it", "target": "x"}))
    assert any("`social.adopt` needs `who`" in e for e in _event(LINE, {"social": "rumor", "action": "adopt", "item": "moon"}))
    assert _event(LINE, {"social": "rumor", "action": "step"}) == []


def test_tools_one_offers_the_channels_as_one_tool():
    env = fg_env.load({**CHAT, "mechanisms": {"chat": {**CHAT["mechanisms"]["chat"], "tools": "one"}}}, seed=1)
    script = Script(env, {"ana": [("chat", {"action": "say", "channel": "cabal", "text": "hello @ben"}),
                                  ("chat", {"action": "dm", "to": "ben", "text": "psst"})]})
    env.run(script, rounds=1)
    assert [(r[2], r[3]) for r in script.results] == [("chat", True), ("chat", True)], script.results
    assert [e["text"] for e in env.world.records("chat")] == ["hello @ben", "psst"]
    tools = json.loads(script.seen["ana"][1])
    assert [t["name"] for t in tools if t["kind"] == "act"] == ["chat"]
    page = fg_env.guide("social.feed")
    assert page.startswith("### `social.feed`") and "- `follow`" in page and "`max_chars`" in page


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
        "r1": [("hall_raise_hand", {}), ("hall_propose", {"text": "Build it by June"}), ("hall_vote", {"choice": "yes"}),
               ("hall_vote", {"choice": "yes"})],
        "r2": [("hall_second", {}), ("hall_second", {}), ("hall_vote", {"choice": "yes"}), ("hall_vote", {"choice": "yes"})],
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


def test_tools_one_offers_the_whole_body_as_one_tool():
    env = fg_env.load(_hall(floor=False, tools="one"), seed=1)
    script = Script(env, {"r1": [("hall", {"action": "propose", "text": "Adopt the plan"})],
                          "r2": [("hall", {"action": "second"}, _top_status("proposed"))]})
    env.run(script, rounds=1)
    assert [(r[2], r[3]) for r in script.results] == [("hall", True), ("hall", True)], script.results
    assert [d["text"] for d in env.props["hall"]["decisions"]] == ["Adopt the plan"]  # seconded, debated, put and counted
    tools = json.loads(script.seen["r1"][1])
    assert [t["name"] for t in tools if t["kind"] == "act"] == ["hall"]


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
    tight = {**NET, "links": [{"relation": "net_follows", "among": "account", "graph": "complete", "where": "$it.id != d"},
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
    "mechanisms": {"rumor": {"kind": "social", "mode": "diffusion", "who": "person", "over": "knows", "model": "cascade", "p": 1,
                             "seeds": {"moon": ["p1"]}, "on_adopt": ["$it.heard += 1"]}},
    "metrics": {"reach": "$reach(moon)"},
}


def _with(contract, **config):
    return {**contract, "mechanisms": {"rumor": {**contract["mechanisms"]["rumor"], **config}}}


def test_independent_cascade_spreads_one_hop_per_step():
    env = fg_env.load(LINE, seed=1)
    env.run("idle", rounds=1)
    assert ev(env, "$adopters(moon)") == 2 and ev(env, "$spread_state(p3, moon)") == "unaware"
    env.run("idle", rounds=3)
    assert ev(env, "$adopters(moon)") == 5 and ev(env, "$reach(moon)") == 5
    assert [e.properties["heard"] for e in env.world.entities_of("person")] == [0, 1, 1, 1, 1]
    assert ev(env, "$exposures(p2, moon)") == 1
    assert ev(env, "$heard(p2)") == [{"item": "moon", "state": "adopted", "exposures": 1}]
    assert do(env, None, [{"social": "rumor", "action": "reject", "item": "moon", "who": "p2"}])
    assert ev(env, "$spread_state(p2, moon)") == "rejected" and ev(env, "$adopters(moon)") == 4
    assert ev(env, "$reach(moon)") == 5


def test_cascade_with_no_chance_only_exposes_and_flow_follows_direction():
    env = fg_env.load(_with(LINE, p=0), seed=1)
    env.run("idle", rounds=3)
    assert ev(env, "$adopters(moon)") == 1 and ev(env, "$reach(moon)") == 2
    assert ev(env, "$spread_state(p2, moon)") == "exposed"
    against = fg_env.load({**_with(LINE, flow="against"), "relations": {"knows": {}}}, seed=1)
    against.run("idle", rounds=3)
    assert ev(against, "$reach(moon)") == 1  # p1 only tells those who link to it; nobody does
    along = fg_env.load({**_with(LINE, flow="along"), "relations": {"knows": {}}}, seed=1)
    along.run("idle", rounds=3)
    assert ev(along, "$adopters(moon)") == 4


def test_linear_threshold_adopts_when_enough_neighbours_have():
    star = {**LINE, "links": [{"relation": "knows", "from": "p1", "to": f"p{i}"} for i in range(2, 6)],
            "mechanisms": {"rumor": {"kind": "social", "mode": "diffusion", "who": "person", "over": "knows", "model": "threshold",
                                     "threshold": 0.5, "seeds": {"moon": ["p2", "p3"]}}}}
    env = fg_env.load(star, seed=1)
    env.run("idle", rounds=1)
    assert ev(env, "$spread_state(p1, moon)") == "adopted" and ev(env, "$exposures(p1, moon)") == 2
    assert ev(env, "$spread_state(p4, moon)") == "unaware"
    env.run("idle", rounds=1)
    assert ev(env, "$adopters(moon)") == 5


def test_random_diffusion_is_seeded_resumes_and_checks_its_expressions():
    noisy = {**_with(LINE, p=0.5), "links": [{"relation": "knows", "among": "person", "graph": "complete"}]}
    first = fg_env.load(noisy, seed=9).run("idle").series["reach"]
    assert first == fg_env.load(noisy, seed=9).run("idle").series["reach"]
    straight, resumed = split_run(noisy, "idle", seed=9, first=2)
    assert straight == resumed
    assert any("$it is not available here" in e for e in errors(_with(LINE, p="$it.heard")))
    assert errors(LINE) == []


# ---------------------------------------------------------------------------
# beliefs
# ---------------------------------------------------------------------------

VILLAGE = {
    "name": "Village",
    "clock": {"rounds": 3},
    "types": {"villager": {"agent": True}},
    "entities": {"ana": {"type": "villager", "name": "Ana"}, "ben": {"type": "villager", "name": "Ben"},
                 "cy": {"type": "villager", "name": "Cy"}},
    "relations": {"trusts": {}},
    "links": [{"relation": "trusts", "from": "cy", "to": "ben", "value": 0.5}],
    "mechanisms": {"memory": {"kind": "mind", "mode": "beliefs", "who": "villager", "decay": 0.1, "secondhand": 0.5,
                              "trust": "trusts", "share": True}},
}


def test_learn_tell_secondhand_trust_and_decay():
    env = fg_env.load(VILLAGE, seed=1)
    assert do(env, "ana", [{"mind": "memory", "action": "learn", "key": "wolf", "value": "ben", "confidence": 0.8}])
    assert ev(env, "[$believes(ana, wolf), $believes(ana, wolf, ben), $believes(ana, wolf, cy)]") == [True, True, False]
    assert do(env, "ana", [{"mind": "memory", "action": "tell", "key": "wolf", "to": "ben"}])
    assert ev(env, "$belief(ben, wolf)") == {"value": "ben", "confidence": 0.4, "source": "told", "told_by": "ana", "round": 0}
    assert do(env, "ben", [{"mind": "memory", "action": "tell", "key": "wolf", "to": "cy"}])
    assert ev(env, "$confidence(cy, wolf)") == pytest.approx(0.4 * 0.5 * 0.5)  # secondhand × trust
    assert do(env, "ben", [{"mind": "memory", "action": "learn", "key": "wolf", "value": "cy", "confidence": 0.3}])  # weaker and contradicting: ignored
    assert ev(env, "$belief(ben, wolf).value") == "ben"
    assert not do(env, "cy", [{"mind": "memory", "action": "tell", "key": "stash", "to": "ben"}])  # nothing to pass on
    assert not do(env, "ana", [{"mind": "memory", "action": "tell", "key": "wolf", "to": "ana"}])  # no telling yourself
    assert [e.to for e in env.world.log if e.kind == "told"] == [("ben",), ("cy",)]
    env.run("idle", rounds=1)
    assert ev(env, "$confidence(ana, wolf)") == pytest.approx(0.72)
    assert ev(env, "$confidence(cy, wolf)") == pytest.approx(0.09)
    for _ in range(6):
        assert do(env, None, [{"mind": "memory", "action": "decay"}])
    assert ev(env, "$believes(cy, wolf)") is False  # 0.09 × 0.9⁶ < forget_below
    assert do(env, "ana", [{"mind": "memory", "action": "forget", "key": "wolf"}]) and ev(env, "$beliefs_of(ana)") == []


def test_an_agent_sees_only_its_own_beliefs():
    env = fg_env.load(VILLAGE, seed=1)
    assert do(env, "ana", [{"mind": "memory", "action": "learn", "key": "stash", "value": "the old barn", "confidence": 0.9}])
    script = Script(env, {}, probes={"ben": [("inspect", {"id": "ana"})]})
    env.run(script, rounds=1)
    assert "the old barn" not in script.text("ben") and "the old barn" not in json.dumps(env.preview("cy"))
    assert "stash: the old barn (81% sure, seen yourself)" in env.preview("ana")["update"]  # one round of decay
    tell = next(t for t in env.preview("ana")["tools"] if t["name"] == "memory_tell")
    assert tell["input_schema"]["properties"]["about"]["enum"] == ["stash"]
    ben_tools = [t["name"] for t in json.loads(script.seen["ben"][1])]
    assert "memory_tell" not in ben_tools  # ben held nothing to tell during the round
    straight, resumed = split_run(VILLAGE, "random")
    assert straight == resumed


def test_beliefs_config_and_actions_say_what_to_fix():
    def with_config(**config):
        return {**VILLAGE, "mechanisms": {"memory": {**VILLAGE["mechanisms"]["memory"], **config}}}

    assert errors(with_config(decay_curve="linear")) == []
    assert any("`holders` is not a field of `mind` mode `beliefs`" in e for e in errors(with_config(holders="villager")))
    assert any("'beliefs' is a mode of kind 'mind'" in e
               for e in errors({**VILLAGE, "mechanisms": {"memory": {"kind": "beliefs", "holders": "villager"}}}))

    def op(*effects):
        return errors({**VILLAGE, "events": [{"do": list(effects)}]})

    assert any("`mind.tell` needs `to`" in e for e in op({"mind": "memory", "action": "tell", "key": "wolf"}))
    assert any("'from' is not part of `mind.tell`" in e
               for e in op({"mind": "memory", "action": "tell", "key": "wolf", "to": "ben", "from": "ana"}))
    assert any('`learn` is an action of the `mind` op: {"mind": "<mechanism>", "action": "learn"' in e for e in op({"learn": "wolf"}))


# ---------------------------------------------------------------------------
# relationships and factions
# ---------------------------------------------------------------------------

BONDS = {
    "name": "Bonds",
    "clock": {"rounds": 4},
    "world": {"fired": 0},
    "types": {"nation": {"agent": True}},
    "entities": {"fr": {"type": "nation", "name": "France"}, "uk": {"type": "nation", "name": "Britain"},
                 "de": {"type": "nation", "name": "Germany"}},
    "mechanisms": {
        "bonds": {"kind": "groups", "mode": "relationships", "relations": {"trust": {"baseline": 0, "decay": 0.5, "thresholds": [
            {"at": 0.6, "say": "{$from.name} now trusts {$to.name}.", "to": "both", "do": ["$world.fired += 1"]}]}}},
        "blocs": {"kind": "groups", "mode": "factions", "who": "nation",
                  "factions": {"entente": {"members": ["fr"]}, "central": {"members": ["de"], "open": True}}},
    },
}


RELATE = {"groups": "bonds", "action": "relate", "relation": "trust", "from": "fr", "to": "uk"}


def test_relations_decay_toward_baseline_and_thresholds_rearm_after_crossing_back():
    env = fg_env.load(BONDS, seed=1)
    assert do(env, None, [{**RELATE, "add": 0.8}])
    assert env.props["fired"] == 1 and ev(env, "$relation(fr, uk, trust)") == pytest.approx(0.8)
    assert do(env, None, [{**RELATE, "add": 0.1}])
    assert env.props["fired"] == 1  # still above: no second event
    notice = [e for e in env.world.log if e.kind == "bonds"]
    assert [(e.text, e.to) for e in notice] == [("France now trusts Britain.", ("fr", "uk"))]
    env.run("idle", rounds=1)  # decays to 0.45: crossed back, armed again
    assert ev(env, "$relation(fr, uk, trust)") == pytest.approx(0.45)
    assert do(env, None, [{**RELATE, "set": 0.7}])
    assert env.props["fired"] == 2
    once = json.loads(json.dumps(BONDS))
    once["mechanisms"]["bonds"]["relations"]["trust"]["thresholds"][0]["once"] = True
    env = fg_env.load(once, seed=1)
    for step in ({"set": 0.9}, {"set": 0.1}, {"set": 0.9}):
        assert do(env, None, [{**RELATE, **step}])
    assert env.props["fired"] == 1


def test_relate_add_saturates_at_the_relations_bound_while_an_explicit_set_past_it_is_refused():
    env = fg_env.load(BONDS, seed=1)
    assert do(env, None, [{**RELATE, "add": 0.8}, {**RELATE, "add": 0.5}])
    assert ev(env, "$relation(fr, uk, trust)") == pytest.approx(1.0)
    with pytest.raises(fg_env.RunError, match="France's trust link to Britain cannot go above 1: it would be 1.5"):
        do(env, None, [{**RELATE, "set": 1.5}])


def test_factions_invitations_alliances_and_allies():
    env = fg_env.load(BONDS, seed=1)
    assert ev(env, "$allies(fr, de)") is False and ev(env, "$joinable(uk)") == ["central"]
    assert not do(env, "uk", [{"groups": "blocs", "action": "join", "in": "entente"}])  # needs an invitation
    assert do(env, "fr", [{"groups": "blocs", "action": "invite", "in": "entente", "guest": "uk"}])
    assert do(env, "uk", [{"groups": "blocs", "action": "join", "in": "entente"}])
    assert ev(env, "$allies(fr, uk)") is True and ev(env, "$faction_of(uk)") == ["entente"]
    assert not do(env, "uk", [{"groups": "blocs", "action": "join", "in": "central"}])  # one faction at a time
    assert do(env, "fr", [{"groups": "blocs", "action": "ally", "in": "entente", "other": "central"}])
    assert ev(env, "$allies(uk, de)") is False  # proposed, not yet accepted
    assert do(env, "de", [{"groups": "blocs", "action": "ally", "in": "central", "other": "entente"}])
    assert ev(env, "$allies(uk, de)") is True
    assert do(env, "de", [{"groups": "blocs", "action": "break_alliance", "in": "central", "other": "entente"}])
    assert ev(env, "$allies(uk, de)") is False
    assert not do(env, "fr", [{"groups": "blocs", "action": "found", "title": "Mine"}])  # founding is off
    assert do(env, None, [{"groups": "blocs", "action": "add", "in": "central", "who": "uk"}])  # no consent needed
    assert ev(env, "$faction_of(uk)") == ["central"]
    assert errors(BONDS) == []
    straight, resumed = split_run(BONDS, "random")
    assert straight == resumed


def test_relationship_and_faction_actions_check_their_own_keys():
    def op(*effects):
        return errors({**BONDS, "events": [{"do": list(effects)}]})

    assert any("`groups.relate` takes exactly one of `add` or `set`" in e for e in op(RELATE))
    assert any("'by' is not part of `groups.relate`" in e for e in op({**RELATE, "by": 0.2}))
    assert any("'rivalry' is not a relation of bonds" in e for e in op({**RELATE, "relation": "rivalry", "add": 1}))
    assert any("`groups.invite` needs `guest`" in e for e in op({"groups": "blocs", "action": "invite", "in": "entente"}))
    assert any("did you mean 'break_alliance'" in e
               for e in op({"groups": "blocs", "action": "break_aliance", "in": "entente", "other": "central"}))
    assert any("`join` is an action of the `groups` or `social` op" in e for e in op({"join": "blocs", "in": "central"}))
    assert any("`relate` is an action of the `groups` op" in e for e in op({"relate": "trust", "from": "fr", "to": "uk", "by": 1}))


def test_relationships_and_factions_written_as_kinds_name_their_groups_family():
    old = {**BONDS, "mechanisms": {"bonds": {**BONDS["mechanisms"]["bonds"], "kind": "relationships"},
                                   "blocs": {"kind": "factions", "members": "nation"}}}
    found = errors(old)
    assert any("'relationships' is a mode of kind 'groups'" in e for e in found)
    assert any("'factions' is a mode of kind 'groups'" in e for e in found)
    renamed = errors({**BONDS, "mechanisms": {"blocs": {"kind": "groups", "mode": "factions", "members": "nation"}}})
    assert any("`members` is not a field of `groups` mode `factions`" in e for e in renamed)


def test_tools_one_offers_every_faction_tool_as_one():
    contract = {**BONDS, "mechanisms": {"blocs": {**BONDS["mechanisms"]["blocs"], "tools": "one"}}}
    env = fg_env.load(contract, seed=1)
    script = Script(env, {"uk": [("blocs", {"action": "join", "faction": "central"})]})
    env.run(script, rounds=1)
    assert [(r[2], r[3]) for r in script.results] == [("blocs", True)], script.results
    assert ev(env, "$faction_of(uk)") == ["central"]
    assert [t["name"] for t in json.loads(script.seen["uk"][1]) if t["kind"] == "act"] == ["blocs"]


# ---------------------------------------------------------------------------
# acceptance examples
# ---------------------------------------------------------------------------

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"


def test_town_hall_debates_amends_and_ends_on_the_vote():
    path = EXAMPLES / "town_hall.json"
    assert errors(path) == []
    for seed in (1, 2, 3, 7):
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
    env = fg_env.load(path, seed=1)
    assert len(env.entities("account")) >= 100
    result = env.run()
    assert result.status == "completed", result.error
    assert result.outputs["reach"] >= 3 and 0 <= result.outputs["insularity"] <= 1
    assert result.outputs["rumor_posts"] > 0 and result.outputs["labelled_posts"] > 0
    experiment = fg_env.experiment(str(path), runs=4, arms=["control", "downrank"])
    assert experiment.deltas("control")["downrank"]["reach"]["mean"] < 0


def _star_cascade(persistent, rounds):
    contract = {"name": "Star", "clock": {"rounds": rounds}, "types": {"person": {}},
                "population": [{"type": "person", "count": 21}], "relations": {"knows": {"symmetric": True}},
                "links": [{"relation": "knows", "among": "person", "graph": "star"}],
                "mechanisms": {"word": {"kind": "social", "mode": "diffusion", "who": "person", "over": "knows",
                                        "model": "cascade", "p": 0.3, "persistent": persistent,
                                        "seeds": {"idea": ["person_1"]}}},
                "outputs": {"reach": "$adopters('idea')"}}
    return fg_env.run(contract, None, seed=1).outputs["reach"]


def test_a_cascade_adopter_gets_one_chance_unless_the_cascade_is_persistent():
    # the hub tells each leaf once, right after adopting; leaves only know the hub, so a one-shot star stops at step 1
    assert _star_cascade(False, rounds=5) == _star_cascade(False, rounds=1) < 21
    assert _star_cascade(True, rounds=5) > _star_cascade(True, rounds=1)  # a persistent hub keeps trying every step
