"""Guest exit releases service capacity without inventing a refund policy."""
import copy
import json

import pytest

import fg_env

CONTRACT = {
    "name": "Shared service capacity",
    "clock": {"rounds": 4},
    "types": {"guest": {"agent": True}, "provider": {}},
    "entities": {**{name: {"type": "guest", "props": {"cash": 20}} for name in ("a", "b", "c")},
                 "provider": {"type": "provider"}},
    "mechanisms": {
        "money": {"kind": "economy", "mode": "ledger", "who": ["guest", "provider"], "currencies": {"cash": {}}},
        "seats": {"kind": "agreements", "mode": "bookings", "who": "guest", "currency": "cash",
                  "resources": {"room": {"provider": "provider", "capacity": 1, "price": 5}}},
    },
    "actions": {"leave": {"by": "guest", "do": [
        {"transfer": "cash", "from": "$actor", "to": "$entity(provider)", "amount": "$actor.cash"},
        {"remove": "$actor"}]}},
    "stages": [{"name": "decide", "turns": "sequential", "max_actions": 3}],
    "outputs": {"conserved": "$conserved(money)"},
}


@pytest.mark.parametrize("format", ["slots", "queue"])
@pytest.mark.parametrize("ahead", [1, 2])
def test_departed_guest_is_abandoned_and_live_guests_get_service(format, ahead):
    contract = copy.deepcopy(CONTRACT)
    contract["mechanisms"]["seats"]["format"] = format

    def actor(wake):
        if wake.round == 1:
            params = {"resource": "room", **({"ahead": ahead} if format == "slots" else {})}
            assert wake.call("seats_book", params).ok
            if wake.entity_id == "a":
                assert wake.call("leave", {}).ok
        wake.end()

    env = fg_env.load(contract)
    first = env.run(actor, rounds=1)
    assert first.status != "failed", first.error
    snapshot = json.loads(json.dumps(env.snapshot()))
    result = env.run("idle")
    assert result.status != "failed", result.error
    bookings = {row["props"]["guest"]: row["props"] for row in env.entities("seats_booking")}
    assert bookings["a"]["status"] == "abandoned"
    assert bookings["b"]["status"] == "served"
    assert bookings["c"]["status"] == ("served" if format == "queue" else "turned_away")
    assert env.props["seats_stats"]["abandoned"] == 1
    assert env.props["seats_stats"]["served"] == (2 if format == "queue" else 1)
    assert env.props["seats_stats"]["promoted"] == (0 if format == "queue" else 1)
    # Exit settles remaining money separately; it is not a cancellation/refund.
    assert bookings["a"]["paid"] == (0 if format == "queue" else 5)
    assert env.props["seats_stats"]["revenue"] == 10
    assert result.outputs["conserved"] is True
    assert result.to_dict() == fg_env.Env.restore(contract, snapshot).run("idle").to_dict()


@pytest.mark.parametrize("waiter_exits", [False, True])
def test_promotion_skips_departed_and_expired_waiters_before_charging(waiter_exits):
    contract = copy.deepcopy(CONTRACT)
    contract["types"]["guest"]["props"] = {"patience": 2}
    contract["entities"]["b"]["props"]["patience"] = 0
    contract["mechanisms"]["seats"]["patience"] = "$it.patience"

    def actor(wake):
        if wake.round == 1:
            assert wake.call("seats_book", {"resource": "room", "ahead": 2}).ok
            if wake.entity_id == "a" or (wake.entity_id == "b" and waiter_exits):
                assert wake.call("leave", {}).ok
        wake.end()

    env = fg_env.load(contract)
    result = env.run(actor)
    assert result.status != "failed", result.error
    rows = {row["props"]["guest"]: row["props"] for row in env.entities("seats_booking")}
    assert {who: row["status"] for who, row in rows.items()} == {"a": "abandoned", "b": "abandoned", "c": "served"}
    assert rows["b"]["paid"] == 0
    assert env.props["seats_stats"]["abandoned"] == 2
    assert env.props["seats_stats"]["promoted"] == 1
    assert env.props["seats_stats"]["revenue"] == 10
    assert result.outputs["conserved"] is True


def test_explicit_cancellation_refunds_once_before_guest_exit():
    contract = copy.deepcopy(CONTRACT)
    contract["mechanisms"]["seats"]["refund"] = 0.5

    def actor(wake):
        if wake.round == 1:
            assert wake.call("seats_book", {"resource": "room", "ahead": 2}).ok
        if wake.round == 2 and wake.entity_id == "a":
            assert wake.call("seats_cancel", {"booking": "seats_booking_1"}).ok
            assert wake.call("leave", {}).ok
        wake.end()

    env = fg_env.load(contract)
    result = env.run(actor)
    assert result.status != "failed", result.error
    stats = env.props["seats_stats"]
    assert stats["cancelled"] == 1 and stats["abandoned"] == 0
    assert stats["promoted"] == 1 and stats["served"] == 1
    assert stats["revenue"] == 7.5
    assert result.outputs["conserved"] is True
