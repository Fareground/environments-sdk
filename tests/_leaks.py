"""Test-side use of the SDK's leak scanner (:mod:`fg_env.analysis.leaks`): the example contracts, and inputs that make
the large ones small enough to play quickly."""
from pathlib import Path

from fg_env.analysis.leaks import LeakScanner as Scanner
from fg_env.analysis.leaks import scan_leaks as scan

__all__ = ["Scanner", "scan", "SMALL", "EXAMPLES"]

#: Inputs that make the large examples and engine starters small enough to play quickly.
SMALL = {
    "coffee_market": {"sample_size": 80}, "epidemic_shocks": {"residents": 20}, "town_epidemic": {"residents": 20},
    "exchange_flagship": {"participants": 20, "seed_bars": 20}, "misinformation": {"crowd_users": 10},
    "outbreak_network": {"residents": 10}, "social_network": {"accounts": 10}, "ride_hailing": {"drivers": 3},
    "corner_shop_town": {"households": 3},
    "market": {"sample_size": 80, "days": 7}, "exchange": {"participants": 20, "seed_bars": 20, "bars": 5},
}
#: Every example contract.
EXAMPLES = sorted((Path(__file__).parents[1] / "examples" / "contracts").glob("*.json"))
