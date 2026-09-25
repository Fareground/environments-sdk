"""Host-evaluated intelligence: judgment the engine cannot compute, supplied by the host.

A contract declares what it needs — a rubric ``host.judge``, a ``host.game_master`` that resolves
free-text attempts within an allow-list, agent ``host.memory`` with recall, ``host.tool`` services
such as web search, ``host.personas`` written at build time, ``host.recap`` summaries of long records,
``feeds`` of external data written into the world, descriptions of an asset's file (``describe``) —
and names the host that answers (``"host": "judge"``). The host is any object implementing the
small protocols in :mod:`.protocols`; your own model client plugs in through :mod:`.adapters`.

Every answer is recorded in world state the moment it is produced, so snapshots, restores,
previews and replays without the host are identical::

    from fg_env import host

    env = host.load("debate_judged.json", hosts={"judge": host.adapters.anthropic(client, model)}, seed=1)
    result = host.run(env, {"debater": my_llm})
    replay = host.load("debate_judged.json", hosts=host.Hosts.replaying(host.tape_of(env)), seed=1)
    assert host.run(replay, {"debater": my_llm}).outputs == result.outputs
"""
from typing import TYPE_CHECKING, Any

from ..contract.base import TAPE
from . import adapters, stubs
from .hosts import Hosts, bind, hosts_for
from .protocols import Describer, Evaluator, Feed, GameMaster, HostError, Ranker, Tools, Writer
from .tape import consult, tape_of

if TYPE_CHECKING:
    from ..runtime.hosted import load, restore, run, wrap

__all__ = [
    "Hosts", "HostError", "Evaluator", "GameMaster", "Tools", "Writer", "Ranker", "Feed", "Describer",
    "load", "restore", "run", "wrap", "bind", "hosts_for", "tape_of", "consult", "TAPE",
    "adapters", "stubs",
]


#: The run-level calls, kept with runs (:mod:`fg_env.runtime.hosted`): loaded on first use, since this package sits
#: below runs.
_RUN_LEVEL = ("load", "restore", "run", "wrap")


def __getattr__(name: str) -> Any:
    if name in _RUN_LEVEL:
        from ..runtime import hosted

        return getattr(hosted, name)
    raise AttributeError(f"module 'fg_env.host' has no attribute {name!r}")
