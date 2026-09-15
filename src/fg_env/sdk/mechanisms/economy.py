"""The ``economy`` family (money, goods, production and supply chains) and the modes that the
``agreements`` family builds on it (subscriptions, bookings, negotiation, labor).

Every mode expands into ordinary contract sections and keeps its state in journaled world
props and entities, so checking, previews, atomic actions, snapshots and determinism apply
unchanged. Value is conserved: money and goods only move, except through named sources and
sinks that update ``$world.<use>_supply`` and ``$world.<use>_flows``; each ledger and
inventory adds the invariant ``$conserved(<use>)``.

Modules:

* :mod:`.econ_assets` — the asset primitives, expression functions and the ``economy`` op's
  money and goods actions (``pay``, ``mint``, ``burn``; ``give``, ``make``, ``use``, ``drop``, ``pickup``).
* :mod:`.econ_inventory` — ``inventory``; :mod:`.econ_ledger` — ``ledger`` (with loans).
* :mod:`.econ_production` — ``production`` (recipes, jobs, skills).
* :mod:`.econ_subscriptions` — ``subscriptions``; :mod:`.econ_bookings` — ``bookings`` (slots, queues, waitlists).
* :mod:`.econ_negotiation` — ``negotiation`` (multi-issue offers, binding deals, duties, breach).
* :mod:`.econ_labor` — ``labor`` (postings, hiring, wages, firms); :mod:`.econ_supply_chain` — ``supply_chain``.
"""
from __future__ import annotations

from . import (  # noqa: F401  (register ops, functions and kinds)
    econ_assets, econ_bookings, econ_inventory, econ_labor, econ_ledger, econ_negotiation, econ_production,
    econ_subscriptions, econ_supply_chain,
)
