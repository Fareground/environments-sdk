"""An order book's coded crowd is calibrated: market makers earn the spread as a class net of what informed traders take
from them, and the price follows the hidden fundamental at about the volatility the book is configured with."""
import math
import statistics

import fg_env

#: The audit's crowd: every strategy, (count, cash, shares) each.
CROWD = {"market_maker": (2, 50000, 500), "momentum": (4, 10000, 100), "mean_reversion": (4, 10000, 100),
         "fundamentalist": (4, 10000, 100), "noise": (8, 10000, 100), "passive": (4, 10000, 100)}
VOLATILITY = 0.02  # the book's default: the fundamental's per-round volatility
SEEDS, ROUNDS = 6, 120


def _session(seed):
    last = "$book(x).last"
    # Profit against simply holding the starting cash and shares, marked at the last price.
    edge = {kind: f"$avg($map(x_{kind}, $it.cash + $it.x_reserved_cash + ($it.x_shares + $it.x_reserved_shares) * {last}"
                  f" - {cash} - {shares} * {last}))" for kind, (_, cash, shares) in CROWD.items()}
    contract = {"name": "Crowd", "clock": {"rounds": ROUNDS}, "types": {"trader": {"agent": True}},
                "mechanisms": {"x": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 100, "conserve": "round",
                                     "crowd": {kind: {"count": n, "cash": cash, "shares": shares}
                                               for kind, (n, cash, shares) in CROWD.items()}}},
                "metrics": {"value": "$world.x_value"},
                "outputs": {**edge, "prices": "$series.x_price", "values": "$series.value",
                            "mid_volatility": "$market_stats($series.x_mid).sigma"}}
    result = fg_env.run(contract, None, seed=seed)
    assert result.status == "completed", result.error
    return result.outputs


def test_market_makers_earn_as_a_class_and_the_price_tracks_the_fundamental_across_seeds():
    runs = [_session(seed) for seed in range(SEEDS)]
    makers = [run["market_maker"] for run in runs]
    assert statistics.mean(makers) > 0 and sum(edge > 0 for edge in makers) >= SEEDS - 2, makers
    assert statistics.mean(run["fundamentalist"] for run in runs) > 0  # informed traders still profit
    assert statistics.mean(run["noise"] for run in runs) < 0  # and uninformed ones pay for it
    gaps = [statistics.mean(abs(math.log(p / v)) for p, v in zip(run["prices"], run["values"])) for run in runs]
    assert statistics.mean(gaps) < 0.07, gaps  # the price stays near the value it cannot see
    volatility = statistics.mean(run["mid_volatility"] for run in runs)
    assert 0.5 * VOLATILITY < volatility < 1.6 * VOLATILITY, volatility
