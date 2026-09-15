"""Markets: the ``market`` family (order books, automated market makers, auctions, posted prices) and analytics.

Importing this module registers every market function, the family's modes and the actions of its
``market`` op (``{"market": <mechanism>, "action": ..., "who": ...}``):

* :mod:`.market_stats` — ``$realized_vol``, ``$excess_kurtosis``,
  ``$vol_clustering``, ``$volume_vol_corr``, ``$market_stats``, ``$market_realism``.
* :mod:`.order_book` / :mod:`.book_mechanism` / :mod:`.traders` — the ``order_book`` mode,
  ``$book*`` functions and coded trader strategies.
* :mod:`.amm` — the ``prediction`` mode (LMSR and CPMM), ``$amm*``, ``$lmsr_*`` and ``$cpmm_prices``.
* :mod:`.auctions` — the ``auction`` mode (first/second price, English, Dutch, double,
  uniform, combinatorial), ``$auction*``; :mod:`.package_auction` — exact
  package winner determination with VCG payments and ``$package_winners``.
* :mod:`.posted` — the ``posted`` mode (listings, promotions, haggling, ranking), ``$shelf`` and
  ``$posted_*``.

All of them keep state in journaled world props, entity props and records and move value only
with conserved :func:`.ledger.move` calls.
"""
from __future__ import annotations

from . import market_stats  # noqa: F401
from . import order_book  # noqa: F401
from . import book_mechanism  # noqa: F401
from . import traders  # noqa: F401
from . import amm  # noqa: F401
from . import package_auction  # noqa: F401
from . import auctions  # noqa: F401
from . import posted  # noqa: F401

__all__ = ["market_stats", "order_book", "book_mechanism", "traders", "amm", "package_auction", "auctions", "posted"]
