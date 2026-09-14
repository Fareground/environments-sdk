"""Markets: native order books, automated market makers, auctions, posted-price markets and analytics.

Importing this module registers every market function, effect op and mechanism kind:

* :mod:`.market_stats` — ``$realized_vol``, ``$excess_kurtosis``,
  ``$vol_clustering``, ``$volume_vol_corr``, ``$market_stats``, ``$market_realism``.
* :mod:`.order_book` / :mod:`.book_mechanism` / :mod:`.traders` — the ``order_book`` mechanism,
  the ``book`` op, ``$book*`` functions and coded trader strategies.
* :mod:`.amm` — the ``prediction_market`` mechanism (LMSR and CPMM), the ``amm`` op, ``$amm*``,
  ``$lmsr_*`` and ``$cpmm_prices``.
* :mod:`.auctions` — the ``auction`` mechanism (first/second price, English, Dutch, double,
  uniform), the ``auction`` op, ``$auction*``.
* :mod:`.posted` — the ``posted_market`` mechanism (listings, promotions, haggling, ranking), the
  ``posted`` op, ``$shelf`` and ``$posted_*``.

All of them keep state in journaled world props, entity props and records and move value only
with conserved :func:`.ledger.move` calls.
"""
from __future__ import annotations

from . import market_stats  # noqa: F401
from . import order_book  # noqa: F401
from . import book_mechanism  # noqa: F401
from . import traders  # noqa: F401
from . import amm  # noqa: F401
from . import auctions  # noqa: F401
from . import posted  # noqa: F401

__all__ = ["market_stats", "order_book", "book_mechanism", "traders", "amm", "auctions", "posted"]
