"""The ``host`` family's ``feed`` mode: external data written into the world at the start of a round.

.. code-block:: json

    "mechanisms": {"oil": {"kind": "host", "mode": "feed", "host": "market", "into": "world.oil_price",
                           "query": {"symbol": "BRENT", "date": "{$clock.date}"}, "fallback": 80}}

The config is :class:`~fg_env.contract.FeedSpec`; :mod:`fg_env.runtime.feeds` asks the host and writes the answer.
Every answer is recorded on the host tape, so snapshots, restores and replays never ask again.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contract import FeedSpec
from ..contract.base import tape_prop
from ..registry import mode


@mode("host", "feed", FeedSpec,
      "External data — live or historical prices, news, weather — answered by a host adapter (`fetch(request)`) at "
      "the start of a round, before events and physics, and written into a world property or a record. Without a "
      "host bound, `fallback` stands in; without one, the run stops and names the host it needs. Text from a host "
      "is marked untrusted.",
      example={"host": "weather", "into": "world.temperature", "query": {"city": "Millbrook", "date": "{$clock.date}"},
               "fallback": "$round($normal(9, 4), 1)"},
      context={"world": {"temperature": 10.0}, "clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}})
def _expand(name: str, config: FeedSpec, contract: Mapping[str, Any]) -> dict[str, Any]:
    return {"world": {"host_tape": tape_prop()}}
