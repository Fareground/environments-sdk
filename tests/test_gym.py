"""`fg_env.rl.gym`: one agent of a contract as a Gymnasium-style environment."""
import copy
import importlib
import sys
import types

import pytest

import fg_env
from fg_env import ContractError
from game_contracts import NIM


def _episode(env, seed, action):
    observation, info = env.reset(seed=seed)
    texts, total, done = [observation["text"]], 0.0, False
    while not done:
        observation, reward, terminated, truncated, info = env.step(action)
        texts.append(observation["text"])
        total += reward
        done = terminated or truncated
    return texts, total, info


def test_an_episode_rewards_add_up_to_the_agents_return():
    env = fg_env.rl.gym(NIM, "a", others="random", seed=3)
    _, total, info = _episode(env, 5, {"tool": "take", "args": {"count": 1}})
    assert total == info["returns"]["a"] and total in (1.0, -1.0)
    env.close()


def test_episodes_replay_from_their_seed():
    one = fg_env.rl.gym(NIM, "a", others="random")
    two = fg_env.rl.gym(NIM, "a", others="random")
    assert _episode(one, 9, ("take", {"count": 2})) == _episode(two, 9, ("take", {"count": 2}))


def test_observations_carry_the_update_the_tools_and_the_last_result():
    env = fg_env.rl.gym(NIM, "a", others="random", seed=1)
    observation, info = env.reset()
    assert "Stones left: 5." in observation["text"] and observation["stage"] == "move"
    assert "take" in [tool["name"] for tool in observation["tools"]]
    assert info["result"] is None and info["returns"] == {"a": 0.0, "b": 0.0}
    observation, reward, terminated, truncated, info = env.step({"tool": "take", "args": {"count": 9}})
    assert not info["result"]["ok"] and reward == 0 and not terminated and not truncated
    assert observation["text"] == info["result"]["text"]  # the same turn goes on
    assert "Stones left" in env.render()


def test_max_steps_truncates_an_episode():
    env = fg_env.rl.gym(NIM, "a", others="idle", inputs={"stones": 9}, max_steps=1, seed=2)
    env.reset()
    _, _, terminated, truncated, _ = env.step({"tool": "take", "args": {"count": 9}})
    assert truncated and not terminated


def test_action_ids_come_with_a_legal_mask():
    env = fg_env.rl.gym(NIM, "a", others="random", action_ids=True, seed=4)
    _, info = env.reset()
    assert info["legal_actions"] == [1, 2, 3] and info["action_mask"] == [0, 1, 1, 1]
    _, _, _, _, info = env.step(3)
    assert info["result"]["ok"]


def test_a_gym_needs_a_seat_that_has_returns():
    plain = copy.deepcopy(NIM)
    del plain["game"]
    with pytest.raises(ContractError, match="game.returns"):
        fg_env.rl.gym(plain, "a")
    with pytest.raises(ContractError, match="not an agent"):
        fg_env.rl.gym(NIM, "nobody")
    with pytest.raises(RuntimeError, match="reset"):
        fg_env.rl.gym(NIM, "a").step("take")


def test_it_is_a_gymnasium_env_when_gymnasium_is_installed(monkeypatch):
    fake = types.ModuleType("gymnasium")

    class FakeEnv:
        pass

    fake.Env = FakeEnv
    monkeypatch.setitem(sys.modules, "gymnasium", fake)
    module = importlib.import_module("fg_env.gym")
    try:
        reloaded = importlib.reload(module)
        assert issubclass(reloaded.GymEnv, FakeEnv)
    finally:
        monkeypatch.delitem(sys.modules, "gymnasium")
        importlib.reload(module)
