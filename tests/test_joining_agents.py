"""An agent created during a run hears the news from its arrival on, not the whole backlog before it."""
import json

import fg_env

JOIN = {"name": "Join", "clock": {"rounds": 8}, "types": {"p": {"agent": True, "props": {"n": 0}}},
        "entities": {"ann": {"type": "p"}, "bob": {"type": "p"}},
        "actions": {"go": {"by": "p", "do": "$actor.n += 1", "announce": "{$actor.name} went in round {$round}."}},
        "events": [{"phase": "start", "at": 6, "do": [{"create": "p", "id": "newbie", "name": "Newbie"}]}],
        "outputs": {"n": "$sum(p, $it.n)"}}


def _first_updates(env, rounds=None):
    seen = {}

    def play(wake):
        seen.setdefault(wake.entity_id, wake.update)
        wake.call("go")

    env.run(play, rounds=rounds)
    return seen


def test_a_newcomer_hears_only_news_from_its_arrival():
    update = _first_updates(fg_env.load(JOIN, seed=1))["newbie"]
    assert "went in round 6" in update
    assert not any(f"went in round {r}." in update for r in range(1, 6))


def test_a_run_resumed_between_the_arrival_and_the_first_turn_tells_the_newcomer_the_same():
    late = {**JOIN, "events": [{"phase": "end", "at": 6, "do": [{"create": "p", "id": "newbie", "name": "Newbie"}]}]}
    straight = _first_updates(fg_env.load(late, seed=1))["newbie"]
    env = fg_env.load(late, seed=1)
    _first_updates(env, rounds=6)
    resumed = fg_env.Env.restore(late, json.loads(json.dumps(env.snapshot())))
    assert _first_updates(resumed)["newbie"] == straight and "went in round 5." not in straight
