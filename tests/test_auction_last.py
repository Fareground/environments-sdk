"""`$auction(name).last` keeps the latest closed lot's result readable after the lot closes."""
import fg_env
from fg_env.expr import compile_expr

SALE = {
    "name": "Painting sale",
    "clock": {"rounds": 3},
    "types": {"bidder": {"agent": True, "props": {"cash": 200}}},
    "population": [{"type": "bidder", "count": 3}],
    "mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder", "stock": 2}},
    "outputs": {"winner": "$auction(sale).last.winner if $auction(sale).last else 'unsold'",
                "price": "$auction(sale).last.price if $auction(sale).last else 0"},
}


def _read(env, expression):
    return compile_expr(expression)(env.world.evaluation.scope())


def _bid(prices):
    def play(wake):
        wake.call("sale_bid", {"price": prices[wake.entity_id], "qty": 1})
        wake.end()
    return play


def test_last_is_null_before_any_lot_closes_and_holds_the_result_after():
    env = fg_env.load(SALE, seed=1)
    assert _read(env, "$auction(sale).last") is None
    ids = [e["id"] for e in env.entities("bidder")]
    env.run(_bid(dict(zip(ids, [50, 90, 70]))), rounds=1)
    last = _read(env, "$auction(sale).last")
    assert not _read(env, "$auction(sale).open")
    assert last == {"lot": 1, "winner": ids[1], "winners": [ids[1]], "price": 90, "qty": 1, "note": "pays its bid"}


def test_last_moves_to_the_next_closed_lot_and_reports_an_unsold_one():
    env = fg_env.load(SALE, seed=1)
    ids = [e["id"] for e in env.entities("bidder")]
    env.run(_bid(dict(zip(ids, [50, 90, 70]))), rounds=1)
    env.run("idle", rounds=1)
    assert _read(env, "$auction(sale).last") == {"lot": 2, "winner": "", "winners": [], "price": 0, "qty": 0,
                                                 "note": "no bids"}


def test_a_uniform_lot_lists_every_winner_at_the_one_clearing_price():
    uniform = {**SALE,
               "mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "uniform", "who": "bidder",
                                       "stock": 3, "units": 3}}}
    env = fg_env.load(uniform, seed=1)
    ids = [e["id"] for e in env.entities("bidder")]
    prices = dict(zip(ids, [50, 90, 70]))

    def bid(wake):
        wake.call("sale_bid", {"price": prices[wake.entity_id], "qty": 2 if wake.entity_id == ids[1] else 1})
        wake.end()

    result = env.run(bid, rounds=1)
    last = _read(env, "$auction(sale).last")
    assert last["winners"] == [ids[1], ids[2]] and last["winner"] == ids[1] and last["qty"] == 3
    assert result.outputs["sale_prices"] == [last["price"], last["price"]]  # one price per winner, not per unit
