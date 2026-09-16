"""Assets: money and goods that are only ever moved, never silently created or destroyed.

Money is a numeric property per currency on every holder (``$actor.cash``), declared by an
``economy`` ledger. Goods are declared by an ``economy`` inventory: stackable items live in a map
property (``$actor.goods.bread``), unique items are entities of a type named after the item with an
``owner``. Every change goes through these primitives, offered as actions of the ``economy`` op
(registered in :mod:`.econ_asset_actions`):

* moves (``pay``; ``give``, ``drop``, ``pickup``) conserve value exactly and refuse — never clamp —
  when the giver is short or the receiver is full;
* creation and destruction (``mint``/``burn``; ``make``/``use``) name a source or a sink and update
  ``$world.<use>_supply`` and ``$world.<use>_flows``, so ``$conserved(<use>)`` can prove after every
  action that holdings equal the supply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function
from ..world import Abort
from .econ_base import EPS, INVENTORY, LEDGER, SUPPLY_CHAIN, amount, bump, cached, maybe_entity, money, props, uses_of

__all__ = ["Assets", "assets", "move_money", "mint_money", "burn_money", "held", "put_items", "take_items",
           "make_items", "destroy_items", "place_key", "is_holder", "inventory_prop", "balance", "credit_of"]


@dataclass
class Assets:
    """Where every asset of a contract lives: built once per contract from its mechanisms."""

    currencies: Dict[str, str] = field(default_factory=dict)  # currency → ledger
    items: Dict[str, str] = field(default_factory=dict)  # item → inventory
    props: Dict[str, str] = field(default_factory=dict)  # inventory → holder map property
    pipes: Dict[str, List[str]] = field(default_factory=dict)  # item → world props holding pipelines of it
    ledgers: Dict[str, Any] = field(default_factory=dict)
    inventories: Dict[str, Any] = field(default_factory=dict)


def inventory_prop(name: str, config: Any) -> str:
    return config.prop or name


def assets(world: Any) -> Assets:
    def build() -> Assets:
        out = Assets(ledgers=uses_of(world, LEDGER), inventories=uses_of(world, INVENTORY))
        for name, ledger in out.ledgers.items():
            for currency in ledger.currencies:
                out.currencies[currency] = name
        for name, inventory in out.inventories.items():
            out.props[name] = inventory_prop(name, inventory)
            for item in inventory.items:
                out.items[item] = name
        for name, chain in uses_of(world, SUPPLY_CHAIN).items():
            out.pipes.setdefault(chain.item, []).append(f"{name}_pipes")
        return out

    return cached(world, "assets", build)  # type: ignore[no-any-return]


def _item(world: Any, item: Any, where: str) -> Tuple[str, str, Any]:
    """``(item, inventory, item spec)`` for an item name or a unique item's instance."""
    index = assets(world)
    if isinstance(item, Entity):
        item = item.entity_type if item.entity_type in index.items else item.id
    name = str(item)
    if name not in index.items:
        instance = world.entities.get(name)
        if instance is not None and instance.entity_type in index.items:
            name = instance.entity_type
        else:
            raise RunError(f"'{item}' is not a declared item (items: {', '.join(index.items) or 'none'})", where)
    inventory = index.items[name]
    return name, inventory, index.inventories[inventory].items[name]


def is_holder(world: Any, entity: Entity, prop: str) -> bool:
    return prop in world._type_props.get(entity.entity_type, {})


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def _ledger(world: Any, currency: Any, where: str) -> Tuple[str, Any]:
    index = assets(world)
    if not isinstance(currency, str) or currency not in index.currencies:
        raise RunError(f"'{currency}' is not a declared currency (currencies: {', '.join(index.currencies) or 'none'})", where)
    name = index.currencies[currency]
    return name, index.ledgers[name]


def balance(world: Any, entity: Entity, currency: str, where: str) -> float:
    if not is_holder(world, entity, currency):
        raise RunError(f"{entity.name} ({entity.entity_type}) does not hold {currency}", where)
    value = props(entity).get(currency)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def credit_of(world: Any, entity: Entity, currency: str) -> float:
    value = props(entity).get(f"{currency}_credit") if is_holder(world, entity, f"{currency}_credit") else 0
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0.0


def _next_balance(have: float, delta: float, currency: str, where: str) -> float:
    rounded = round(have + delta, 9)
    result = 0.0 if abs(rounded) < EPS else rounded
    if (delta > 0 and result <= have) or (delta < 0 and result >= have):
        raise RunError(f"{currency} change {delta!r} cannot be represented at balance {have!r}; "
                       "rescale the currency or amounts", where)
    return result


def move_money(world: Any, currency: str, source: Entity, target: Entity, value: float, where: str,
               use_credit: bool = True) -> None:
    """Move ``value`` of ``currency``; refuses (Abort) when the payer's balance plus credit is short."""
    _ledger(world, currency, where)
    value = amount(value, where)
    if value == 0 or source is target:
        return
    have = balance(world, source, currency, where)
    receiving = balance(world, target, currency, where)
    limit = credit_of(world, source, currency) if use_credit else 0.0
    if have - value < -limit - EPS:
        extra = f" (credit {money(limit)})" if limit else ""
        raise Abort(f"{source.name} has only {money(have)} {currency}{extra}; {money(value)} is needed.")
    paid = _next_balance(have, -value, currency, where)
    received = _next_balance(receiving, value, currency, where)
    world.set_prop(source, currency, paid)
    world.set_prop(target, currency, received)


def mint_money(world: Any, currency: str, target: Entity, value: float, source: str, where: str) -> None:
    name, _ = _ledger(world, currency, where)
    value = amount(value, where)
    if value == 0:
        return
    world.set_prop(target, currency, _next_balance(balance(world, target, currency, where), value, currency, where))
    bump(world, f"{name}_supply", currency, value)
    bump(world, f"{name}_flows", currency, value, group=source)


def burn_money(world: Any, currency: str, holder: Entity, value: float, sink: str, where: str,
               use_credit: bool = True) -> None:
    name, _ = _ledger(world, currency, where)
    value = amount(value, where)
    if value == 0:
        return
    have = balance(world, holder, currency, where)
    limit = credit_of(world, holder, currency) if use_credit else 0.0
    if have - value < -limit - EPS:
        raise Abort(f"{holder.name} has only {money(have)} {currency}; {money(value)} is needed.")
    world.set_prop(holder, currency, _next_balance(have, -value, currency, where))
    bump(world, f"{name}_supply", currency, -value)
    bump(world, f"{name}_flows", currency, -value, group=sink)


# ---------------------------------------------------------------------------
# Goods
# ---------------------------------------------------------------------------


def _instances(world: Any, item: str, owner: Optional[str] = None) -> List[Entity]:
    return [e for e in world.entities.values() if e.alive and e.entity_type == item
            and (owner is None or props(e).get("owner") == owner)]


def held(world: Any, entity: Entity, item: str, where: str = "") -> int:
    """How many of ``item`` the entity holds (0 for an entity that holds no goods of that kind)."""
    name, inventory, spec = _item(world, item, where)
    if spec.unique:
        return len(_instances(world, name, entity.id))
    prop = assets(world).props[inventory]
    if not is_holder(world, entity, prop):
        return 0
    value = (props(entity).get(prop) or {}).get(name, 0)
    return int(value)


def _used_space(world: Any, entity: Entity, inventory: str) -> float:
    index = assets(world)
    config = index.inventories[inventory]
    stock = props(entity).get(index.props[inventory]) or {}
    used = 0.0
    for item, spec in config.items.items():
        count = len(_instances(world, item, entity.id)) if spec.unique else stock.get(item, 0)
        used += spec.size * count
    return used


def space_left(world: Any, entity: Entity, inventory: str) -> Optional[float]:
    prop = f"{assets(world).props[inventory]}_capacity"
    if not is_holder(world, entity, prop):
        return None
    capacity = props(entity).get(prop)
    if capacity is None:
        return None
    return max(0.0, float(capacity) - _used_space(world, entity, inventory))


def _require_holder(world: Any, entity: Entity, inventory: str, where: str) -> str:
    prop = assets(world).props[inventory]
    if not is_holder(world, entity, prop):
        holders = assets(world).inventories[inventory].who
        raise RunError(f"{entity.name} ({entity.entity_type}) cannot hold {inventory} (who: {holders})", where)
    return prop


def _check_space(world: Any, entity: Entity, inventory: str, spec: Any, qty: int) -> None:
    left = space_left(world, entity, inventory)
    if left is not None and spec.size * qty > left + EPS:
        fits = int(left // spec.size) if spec.size > 0 else qty
        raise Abort(f"{entity.name} has room for only {fits} more of that; {qty} do not fit.")


def put_items(world: Any, entity: Entity, item: str, qty: int, where: str,
              batches: Optional[List[List[int]]] = None) -> None:
    """Add stackable items to a holder (capacity enforced). ``batches`` keeps the age of spoiling goods."""
    name, inventory, spec = _item(world, item, where)
    prop = _require_holder(world, entity, inventory, where)
    if qty == 0:
        return
    _check_space(world, entity, inventory, spec, qty)
    stock = dict(props(entity).get(prop) or {})
    stock[name] = stock.get(name, 0) + qty
    world.set_prop(entity, prop, stock)
    if spec.shelf_life is not None:
        ages = dict(props(entity).get(f"{prop}_batches") or {})
        rows = [list(row) for row in ages.get(name, [])]
        for made, count in (batches or [[world.round, qty]]):
            if rows and rows[-1][0] == made:
                rows[-1][1] += count
            else:
                rows.append([made, count])
        rows.sort(key=lambda row: row[0])
        ages[name] = rows
        world.set_prop(entity, f"{prop}_batches", ages)


def take_items(world: Any, entity: Entity, item: str, qty: int, where: str) -> List[List[int]]:
    """Remove stackable items (oldest first); refuses when short. Returns the batches taken."""
    name, inventory, spec = _item(world, item, where)
    prop = _require_holder(world, entity, inventory, where)
    if qty == 0:
        return []
    stock = dict(props(entity).get(prop) or {})
    have = stock.get(name, 0)
    if have < qty:
        raise Abort(f"{entity.name} has only {have} {name}; {qty} are needed.")
    if have == qty:
        stock.pop(name, None)
    else:
        stock[name] = have - qty
    world.set_prop(entity, prop, stock)
    taken: List[List[int]] = []
    if spec.shelf_life is not None:
        ages = dict(props(entity).get(f"{prop}_batches") or {})
        rows = [list(row) for row in ages.get(name, [])]
        left = qty
        while left and rows:
            made, count = rows[0]
            take = min(count, left)
            taken.append([made, take])
            left -= take
            if take == count:
                rows.pop(0)
            else:
                rows[0][1] = count - take
        if rows:
            ages[name] = rows
        else:
            ages.pop(name, None)
        world.set_prop(entity, f"{prop}_batches", ages)
    return taken


def _pick_instances(world: Any, owner: Entity, item: Any, qty: int, where: str) -> List[Entity]:
    """Unique instances to hand over: the named instance, or the ``qty`` oldest of a kind."""
    instance = maybe_entity(world, item)
    if instance is not None and instance.alive and instance.entity_type != str(item):
        if props(instance).get("owner") != owner.id:
            raise Abort(f"{owner.name} does not own {instance.name}.")
        if qty != 1:
            raise RunError(f"a named item instance moves one at a time, got qty {qty}", where)
        return [instance]
    name, _, _ = _item(world, item, where)
    mine = _instances(world, name, owner.id)
    if len(mine) < qty:
        raise Abort(f"{owner.name} has only {len(mine)} {name}; {qty} are needed.")
    return mine[:qty]


def move_items(world: Any, item: Any, source: Entity, target: Entity, qty: int, where: str) -> None:
    name, inventory, spec = _item(world, item, where)
    if spec.unique:
        chosen = _pick_instances(world, source, item, qty, where)
        _require_holder(world, target, inventory, where)
        if source is not target:
            _check_space(world, target, inventory, spec, len(chosen))
            for instance in chosen:
                world.set_prop(instance, "owner", target.id)
        return
    batches = take_items(world, source, name, qty, where)
    put_items(world, target, name, qty, where, batches or None)


def make_items(world: Any, item: Any, target: Entity, qty: int, source: str, where: str,
               props: Optional[Dict[str, Any]] = None, runner_scope: Any = None) -> List[Entity]:
    name, inventory, spec = _item(world, item, where)
    made: List[Entity] = []
    if qty == 0:
        return made
    if spec.unique:
        _require_holder(world, target, inventory, where)
        _check_space(world, target, inventory, spec, qty)
        for _ in range(qty):
            values = {"owner": target.id, "made": world.round, **(props or {})}
            made.append(world.create(name, None, None, values, None, runner_scope or world.scope(), where))
    else:
        put_items(world, target, name, qty, where)
    bump(world, f"{inventory}_supply", name, qty)
    bump(world, f"{inventory}_flows", name, qty, group=source)
    return made


def destroy_items(world: Any, item: Any, holder: Entity, qty: int, sink: str, where: str) -> None:
    name, inventory, spec = _item(world, item, where)
    if qty == 0:
        return
    if spec.unique:
        for instance in _pick_instances(world, holder, item, qty, where):
            world.remove(instance)
    else:
        take_items(world, holder, name, qty, where)
    bump(world, f"{inventory}_supply", name, -qty)
    bump(world, f"{inventory}_flows", name, -qty, group=sink)


def place_key(at: Any) -> str:
    if isinstance(at, (list, tuple)):
        return ",".join(str(v) for v in at)
    return str(at)


# ---------------------------------------------------------------------------
# Totals and conservation
# ---------------------------------------------------------------------------


def _in_transit(world: Any, item: str, entity_ids: Optional[set]) -> int:
    total = 0
    for prop in assets(world).pipes.get(item, []):
        for node, pipes in (world.props.get(prop) or {}).items():
            if entity_ids is None or node in entity_ids:
                total += sum(pipes.get("inbound") or [])
    return total


def _money_sum(values: Iterable[float]) -> float:
    """Accurate across many holders; non-finite totals are rejected by conservation."""
    try:
        return math.fsum(values)
    except (OverflowError, ValueError):
        return math.inf


def total_of(world: Any, members: List[Entity], asset: str, where: str) -> float:
    index = assets(world)
    if asset in index.currencies:
        return _money_sum(balance(world, e, asset, where) for e in members if is_holder(world, e, asset))
    name, inventory, spec = _item(world, asset, where)
    ids = {e.id for e in members}
    if spec.unique:
        return sum(1 for e in _instances(world, name) if props(e).get("owner") in ids)
    prop = index.props[inventory]
    return sum(int((props(e).get(prop) or {}).get(name, 0)) for e in members if is_holder(world, e, prop)) \
        + _in_transit(world, name, ids)


def loose_total(world: Any, inventory: str, item: str, where: str) -> int:
    """Goods held by nobody: on the ground, or unique instances without a living owner."""
    name, _, spec = _item(world, item, where)
    ground = sum(int(stock.get(name, 0)) for stock in (world.props.get(f"{inventory}_ground") or {}).values())
    if spec.unique:
        orphans = sum(1 for e in _instances(world, name) if maybe_entity(world, props(e).get("owner")) is None
                      or not maybe_entity(world, props(e).get("owner")).alive)  # type: ignore[union-attr]
        return ground + orphans
    return ground


def _holder_types(world: Any, prop: str) -> frozenset:
    """Entity types that declare ``prop`` (cached per contract)."""
    return cached(world, ("holders", prop),  # type: ignore[no-any-return]
                  lambda: frozenset(t for t, specs in world._type_props.items() if prop in specs))


def _number_or_zero(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def conserved(world: Any, name: str, where: str) -> Tuple[bool, str]:
    """Whether holdings of a ledger or inventory match its supply; a reason when they do not.

    It is an invariant, checked after every action, so it makes one pass over the entities."""
    index = assets(world)
    supply = world.props.get(f"{name}_supply") or {}
    if name in index.ledgers:
        for currency in index.ledgers[name].currencies:
            holders, credited = _holder_types(world, currency), _holder_types(world, f"{currency}_credit")
            balances = []
            for entity in world.entities.values():
                if not entity.alive or entity.entity_type not in holders:
                    continue
                values = props(entity)
                value = _number_or_zero(values.get(currency))
                balances.append(value)
                limit = _number_or_zero(values.get(f"{currency}_credit")) if entity.entity_type in credited else 0.0
                if value < -max(0.0, limit) - 1e-6:
                    return False, f"{entity.name} is below its {currency} credit limit"
            total = _money_sum(balances)
            if not math.isfinite(total):
                return False, f"{currency} held has a non-finite total; reduce the monetary scale"
            raw_expected = supply.get(currency, 0)
            try:
                expected = float(raw_expected)
            except (TypeError, ValueError, OverflowError):
                return False, f"{currency} supply must be a finite number"
            if isinstance(raw_expected, bool) or not isinstance(raw_expected, (int, float)) or not math.isfinite(expected):
                return False, f"{currency} supply must be a finite number"
            # Permit floating-point roundoff, not a fraction of a firm's entire balance.
            scale = max(abs(total), abs(expected), max((abs(value) for value in balances), default=0.0))
            tolerance = max(1e-6, 8 * math.ulp(scale))
            if abs(total - expected) > tolerance:
                return False, f"{currency} held is {money(total)} but the supply is {money(expected)}"
        return True, ""
    if name in index.inventories:
        config = index.inventories[name]
        prop = index.props[name]
        holders = _holder_types(world, prop)
        totals: Dict[str, int] = {}
        for entity in world.entities.values():
            if not entity.alive or entity.entity_type not in holders:
                continue
            for item, qty in (props(entity).get(prop) or {}).items():
                if item not in config.items or config.items[item].unique:
                    return False, f"{entity.name} holds '{item}', which is not a stackable item of {name}"
                if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
                    return False, f"{entity.name} holds {qty!r} {item}; quantities are whole numbers ≥ 0"
                totals[item] = totals.get(item, 0) + qty
        for item, spec in config.items.items():
            if spec.unique:
                total = len(_instances(world, item))
            else:
                total = totals.get(item, 0) + loose_total(world, name, item, where) + _in_transit(world, item, None)
            if total != supply.get(item, 0):
                return False, f"{total} {item} exist but the supply is {supply.get(item, 0)}"
        return True, ""
    raise RunError(f"'{name}' is not a declared ledger or inventory", where)


# ---------------------------------------------------------------------------
# Expression functions
# ---------------------------------------------------------------------------


def _agent(call: Call, index: int = 0) -> Entity:
    value = call.arg(index)
    found = maybe_entity(call.scope.world, value)
    if found is None:
        raise ExprError(f"${call.name}: expected an entity or id, got {value!r}", call.source)
    return found


def _guard(call: Call, run: Any) -> Any:
    try:
        return run()
    except RunError as exc:
        raise ExprError(f"${call.name}: {exc.args[0] if exc.args else exc}", call.source) from None


@function("has(agent, asset, qty?)",
          "True when the agent holds at least `qty` (default 1) of an item or currency: $has($actor, bread, 2), $has($actor, cash, 5).",
          min_args=2, max_args=3)
def _has(call: Call) -> bool:
    world, agent, asset, qty = call.scope.world, _agent(call), call.arg(1), call.number(2, 1)
    if isinstance(asset, str) and asset in assets(world).currencies:
        return _guard(call, lambda: balance(world, agent, asset, call.source) + EPS >= qty)  # type: ignore[no-any-return]
    return _guard(call, lambda: held(world, agent, asset, call.source) >= qty)  # type: ignore[no-any-return]


@function("count_items(agent, item?)",
          "How many of `item` the agent holds; without `item`, how many goods of every kind.", min_args=1, max_args=2)
def _count_items(call: Call) -> int:
    world, agent = call.scope.world, _agent(call)
    if len(call) > 1:
        return _guard(call, lambda: held(world, agent, call.arg(1), call.source))  # type: ignore[no-any-return]
    return sum(held(world, agent, item) for item in assets(world).items)


def owned(world: Any, agent: Entity, inventory: Optional[str], where: str) -> List[str]:
    index = assets(world)
    if inventory is not None and inventory not in index.inventories:
        raise RunError(f"'{inventory}' is not a declared inventory (inventories: {', '.join(index.inventories) or 'none'})", where)
    out: List[str] = []
    for item, inv in index.items.items():
        if inventory is not None and inv != inventory:
            continue
        if index.inventories[inv].items[item].unique:
            out.extend(e.id for e in _instances(world, item, agent.id))
        elif held(world, agent, item) > 0:
            out.append(item)
    return out


@function("owned_items(agent, inventory?)",
          "Items the agent holds now: stackable item names with a quantity above 0 and the ids of unique items it owns.",
          min_args=1, max_args=2)
def _owned_items(call: Call) -> List[str]:
    world = call.scope.world
    return _guard(call, lambda: owned(world, _agent(call), call.arg(1), call.source))  # type: ignore[no-any-return]


def items_text(world: Any, agent: Entity, inventory: Optional[str], where: str) -> str:
    index = assets(world)
    names = [inventory] if inventory else list(index.inventories)
    parts: List[str] = []
    for inv in names:
        for item, spec in index.inventories[inv].items.items():
            count = held(world, agent, item, where)
            if count:
                unit = f" {spec.unit}" if spec.unit else ""
                parts.append(f"{count}{unit} {item}")
        left = space_left(world, agent, inv)
        if left is not None:
            parts.append(f"room for {money(left)} more")
    return ", ".join(parts) or "nothing"


@function("items_text(agent, inventory?)", "The agent's goods as plain words: '3 bread, 2 flour, room for 5 more'.",
          min_args=1, max_args=2)
def _items_text(call: Call) -> str:
    world = call.scope.world
    return _guard(call, lambda: items_text(world, _agent(call), call.arg(1), call.source))  # type: ignore[no-any-return]


@function("space_left(agent, inventory)", "Capacity the agent has left in an inventory, or null when unlimited.",
          min_args=2, max_args=2)
def _space_left(call: Call) -> Optional[float]:
    world, inventory = call.scope.world, call.arg(1)
    if inventory not in assets(world).inventories:
        raise ExprError(f"$space_left: '{inventory}' is not a declared inventory", call.source)
    return space_left(world, _agent(call), inventory)


@function("net_worth(agent, prices?)",
          "Money (at each currency's value) plus goods (at `prices` {item: price}, else each item's value) plus loans owed to the agent, minus loans it owes.",
          min_args=1, max_args=2)
def _net_worth(call: Call) -> float:
    world, agent, prices = call.scope.world, _agent(call), call.arg(1) or {}
    if not isinstance(prices, dict):
        raise ExprError(f"$net_worth: prices must be a map {{item: price}}, got {prices!r}", call.source)
    index = assets(world)
    total = 0.0
    for name, ledger in index.ledgers.items():
        for currency, spec in ledger.currencies.items():
            if is_holder(world, agent, currency):
                total += balance(world, agent, currency, call.source) * spec.value
        if ledger.loans is not None and world.is_type(f"{name}_loan"):
            for loan in world.entities_of(f"{name}_loan"):
                if props(loan).get("status") != "active":
                    continue
                worth = float(props(loan).get("owed") or 0) * ledger.currencies[props(loan)["currency"]].value
                if props(loan).get("lender") == agent.id:
                    total += worth
                if props(loan).get("borrower") == agent.id:
                    total -= worth
    for item, inventory in index.items.items():
        count = held(world, agent, item)
        if count:
            price = prices.get(item, index.inventories[inventory].items[item].value)
            total += count * float(price)
    return round(total, 9)


@function("total_held(type, asset)",
          "Total of a currency or item held by every entity of `type` (goods on their way to them included).",
          min_args=2, max_args=2)
def _total_held(call: Call) -> float:
    members = call.collection(0)
    world = call.scope.world
    return _guard(call, lambda: total_of(world, members, str(call.arg(1)), call.source))  # type: ignore[no-any-return]


@function("loose_total(inventory, item)", "Items nobody holds: on the ground, or unique items whose owner is gone.",
          min_args=2, max_args=2)
def _loose_total(call: Call) -> int:
    world = call.scope.world
    return _guard(call, lambda: loose_total(world, str(call.arg(0)), str(call.arg(1)), call.source))  # type: ignore[no-any-return]


@function("ground_items(inventory, place)", "Stackable items lying at a place: {item: qty}.", min_args=2, max_args=2)
def _ground_items(call: Call) -> Dict[str, int]:
    world: Any = call.scope.world
    inventory = call.arg(0)
    if f"{inventory}_ground" not in world.props:
        raise ExprError(f"$ground_items: inventory '{inventory}' has no ground (add drop or pickup to its actions)", call.source)
    return dict((world.props[f"{inventory}_ground"] or {}).get(place_key(call.arg(1))) or {})


@function("conserved(ledger_or_inventory)",
          "True while every currency or item of a ledger or inventory adds up to its supply (and no balance passes its credit limit).",
          min_args=1, max_args=1)
def _conserved(call: Call) -> bool:
    world = call.scope.world
    ok, _ = _guard(call, lambda: conserved(world, str(call.arg(0)), call.source))
    return ok  # type: ignore[no-any-return]
