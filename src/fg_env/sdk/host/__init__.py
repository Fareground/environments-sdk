"""Host-evaluated intelligence: judgment the engine cannot compute, supplied by the host.

A contract declares what it needs — a rubric ``judge``, a ``game_master`` that resolves
free-text attempts within an allow-list, agent ``memory`` with recall, ``host_tool`` services
such as web search, ``personas`` written at build time, ``recap`` summaries of long records —
and names the host that answers (``"host": "judge"``). The host is any object implementing the
small protocols in :mod:`.protocols`; your own model client plugs in through :mod:`.adapters`.

Every answer is recorded in world state the moment it is produced, so snapshots, restores,
previews and replays without the host are identical::

    from fg_env.sdk import host

    env = host.load("debate_judged.json", hosts={"judge": host.adapters.anthropic(client, model)}, seed=1)
    result = host.run(env, {"debater": my_llm})
    replay = host.load("debate_judged.json", hosts=host.Hosts.replaying(host.tape_of(env)), seed=1)
    assert host.run(replay, {"debater": my_llm}).outputs == result.outputs
"""
from . import adapters, stubs
from .api import load, restore, run, wrap
from .hosts import Hosts, bind, hosts_for
from .protocols import Evaluator, GameMaster, HostError, Ranker, Tools, Writer
from .tape import TAPE, consult, tape_of

__all__ = [
    "Hosts", "HostError", "Evaluator", "GameMaster", "Tools", "Writer", "Ranker",
    "load", "restore", "run", "wrap", "bind", "hosts_for", "tape_of", "consult", "TAPE",
    "adapters", "stubs",
]
