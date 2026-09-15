"""The core entry points take hosts themselves: fg_env.load / Env.run / Env.restore with hosts= behave
exactly like the host namespace, and every run offers the contract's in-turn host tools."""
import json
from pathlib import Path

import fg_env
from fg_env.sdk import host
from fg_env.sdk.host.stubs import StubTools
from fg_env.sdk.runtime import Env

COUNCIL = json.loads((Path(__file__).parents[2] / "examples" / "contracts" / "host" / "research_council.json").read_text())


def researcher(wake):
    wake.call("search", {"query": f"metro delays {wake.stage}"})
    if wake.stage == "discuss":
        wake.call("post", {"text": f"{wake.name}: delays are common."})
    else:
        wake.call("forecast", {"p": 0.2 + 0.1 * int(wake.entity_id[-1])})
    wake.end()


def _state(env):
    return {key: value for key, value in env.snapshot().items() if key != "stats"}


def test_core_load_and_run_with_hosts_match_the_host_namespace():
    tools = StubTools()
    core = fg_env.load(COUNCIL, hosts={"web_search": tools}, seed=1)
    core_result = core.run(researcher)
    via_host = host.load(COUNCIL, hosts={"web_search": StubTools()}, seed=1)
    host_result = host.run(via_host, researcher)
    assert core_result.status == "completed", core_result.error
    assert core_result.outputs == host_result.outputs and core_result.outputs["searches"] == len(tools.calls) > 0
    assert _state(core) == _state(via_host)


def test_hosts_can_be_given_to_run_and_restore():
    env = fg_env.load(COUNCIL, seed=1)
    env.run(researcher, rounds=1, hosts={"web_search": StubTools()})
    snapshot = env.snapshot()
    restored = Env.restore(COUNCIL, snapshot, hosts={"web_search": StubTools()})
    finished = restored.run(researcher)
    assert finished.status == "completed", finished.error
    assert finished.outputs["searches"] > 0
