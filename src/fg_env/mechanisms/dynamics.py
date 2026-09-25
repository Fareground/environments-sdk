"""The ``dynamics`` family's ``ode`` mode: the world's continuous variables, integrated every round.

.. code-block:: json

    "mechanisms": {"physics": {"kind": "dynamics", "mode": "ode", "params": {"beta": 0.3, "gamma": 0.1},
                               "read": {"mixing": "$world.mixing"},
                               "vars": {"S": {"start": 990, "rate": "-beta*mixing*S*I/(S+I+R)"},
                                        "I": {"start": 10, "rate": "beta*mixing*S*I/(S+I+R) - gamma*I"},
                                        "R": {"start": 0, "rate": "gamma*I"}}}}

Read as ``$physics.<name>``. The config is :class:`~fg_env.contract.PhysicsSpec`; :mod:`fg_env.physics` integrates it
each round after the start events, before the stages. The mechanism is named ``physics``, as its root is, so a
contract has at most one.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contract import PhysicsSpec
from ..registry import mode

__all__ = ["NAME"]

#: The dynamics mechanism's name: the root it is read with.
NAME = "physics"


@mode("dynamics", "ode", PhysicsSpec,
      "Continuous variables integrated every round before agents act (RK4, with Euler–Maruyama noise): world "
      "variables with a `rate` over variable, param and `read` names, and per-type entity dynamics (`per`); `write` "
      "copies results into props. Read as $physics.<name>. Name the mechanism `physics`.",
      example={"params": {"beta": 0.3, "gamma": 0.1}, "read": {"mixing": "$world.mixing"},
               "vars": {"S": {"start": 990, "rate": "-beta*mixing*S*I/(S+I+R)", "min": 0},
                        "I": {"start": 10, "rate": "beta*mixing*S*I/(S+I+R) - gamma*I", "min": 0},
                        "R": {"start": 0, "rate": "gamma*I", "min": 0}},
               "write": {"world.infected": "I"}}, name=NAME,
      context={"world": {"infected": 0.0, "mixing": 1.0}})
def _expand(name: str, config: PhysicsSpec, contract: Mapping[str, Any]) -> dict[str, Any]:
    return {}
