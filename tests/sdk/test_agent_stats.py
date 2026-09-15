"""Per-agent statistics: every turn and committed action is billed to its agent, and snapshots keep the split."""
import fg_env

from test_runtime import SHOP

#: Two agents; `a` acts in a sequential stage, both submit in a simultaneous stage.
DUEL = {
    "name": "Duel",
    "clock": {"rounds": 2},
    "types": {"player": {"agent": True, "props": {"n": 0}}},
    "entities": {"a": {"type": "player"}, "b": {"type": "player"}},
    "actions": {"poke": {"by": "player", "do": ["$actor.n += 1"], "terminal": True},
                "shout": {"by": "player", "do": ["$actor.n += 10"], "terminal": True}},
    "stages": [{"name": "solo", "who": "$it.id == a", "actions": ["poke"]},
               {"name": "together", "turns": "simultaneous", "actions": ["shout"]}],
}


def _sum(agent_stats, key):
    return sum(stats[key] for stats in agent_stats.values())


def test_agent_stats_add_up_to_the_run_totals_in_sequential_and_simultaneous_stages():
    result = fg_env.run(DUEL, seed=1)
    assert result.ok, result.summary()
    assert list(result.agent_stats) == ["a", "b"]
    assert result.agent_stats["a"]["wakes"] == 4 and result.agent_stats["b"]["wakes"] == 2  # a: 2 solo + 2 together
    assert result.agent_stats["a"]["actions"] == 4 and result.agent_stats["b"]["actions"] == 2  # sealed commits count too
    for key in ("wakes", "calls", "actions", "invalid_calls", "idle_turns"):
        assert _sum(result.agent_stats, key) == result.stats[key], key


def test_model_usage_is_billed_to_the_agent_that_reported_it():
    def spender(wake):
        wake.record_usage(llm_calls=1, input_tokens=100, output_tokens=7)
        wake.call(wake.tools[0].name, {})

    result = fg_env.run(DUEL, {"a": spender, "b": "random"}, seed=1)
    assert result.agent_stats["a"]["input_tokens"] == 400 and result.agent_stats["a"]["llm_calls"] == 4
    assert result.agent_stats["b"]["input_tokens"] == 0
    assert result.stats["input_tokens"] == 400


def test_snapshot_restore_keeps_agent_stats():
    env = fg_env.load(SHOP, seed=3)
    env.run(rounds=1)
    restored = fg_env.Env.restore(SHOP, env.snapshot())
    assert restored.result().agent_stats == env.result().agent_stats != {}
    env.run()
    restored.run()
    assert restored.result().agent_stats == env.result().agent_stats
