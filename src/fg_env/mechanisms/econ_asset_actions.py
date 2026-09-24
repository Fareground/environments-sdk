"""The ``economy`` op's money and goods actions: ``pay``, ``mint``, ``burn`` on a ledger; ``give``, ``make``, ``use``,
``drop``, ``pickup`` on an inventory. Each goes through the asset primitives of :mod:`.econ_assets`."""
from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from ..errors import RunError
from ..expr import is_expr
from ..registry import family_action
from ..world.live import Abort
from ._common import entity_of
from .econ_assets import (
    _item,
    _ledger,
    assets,
    balance,
    burn_money,
    credit_of,
    destroy_items,
    make_items,
    mint_money,
    move_items,
    move_money,
    place_key,
    put_items,
    take_items,
)
from .econ_base import EPS, amount, checked_config, money, whole


def _qty(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> int:
    return whole(runner.eval(effect.get("qty", 1), vars), where, "qty")


def _named(effect: dict[str, Any], key: str, where: str) -> str:
    value = effect.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RunError(f"`{key}` names where value comes from or goes to, e.g. \"{key}\": \"harvest\"", where)
    return value


def _currency(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> str:
    """The currency an action names, which must be one of its ledger's; a ledger with one currency needs none named."""
    name = effect["economy"]
    ledger = assets(runner.world).ledgers[name]
    if "currency" not in effect:
        if len(ledger.currencies) != 1:
            raise RunError(f"ledger '{name}' has several currencies: name one with `currency` "
                           f"({', '.join(ledger.currencies)})", where)
        return str(next(iter(ledger.currencies)))
    currency = runner.eval(effect["currency"], vars)
    if not isinstance(currency, str) or currency not in ledger.currencies:
        raise RunError(f"'{currency}' is not a currency of ledger '{name}' (currencies: "
                       f"{', '.join(ledger.currencies)})",
                       f"{where}.currency")
    return currency


def _goods(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> Any:
    """The item (a name, or a unique item's instance) an action names, which must be one of its inventory's."""
    item = runner.eval(effect["item"], vars)
    name, inventory, _ = _item(runner.world, item, f"{where}.item")
    if inventory != effect["economy"]:
        raise RunError(f"'{name}' is an item of inventory '{inventory}', not of '{effect['economy']}'", f"{where}.item")
    return item


def _check_currency(checker: Any, effect: dict[str, Any], path: str) -> list:
    config = checked_config(checker, effect, "economy")
    if config is None:
        return []
    listed = ", ".join(config.currencies)
    currency = effect.get("currency")
    if currency is None:
        if len(config.currencies) != 1:
            return [(path, f"`{effect['economy']}` has several currencies: name one with `currency`",
                     f"currencies: {listed}")]
        return []
    if isinstance(currency, str) and not is_expr(currency) and currency not in config.currencies:
        hint = get_close_matches(currency, list(config.currencies), n=1)
        return [(f"{path}.currency", f"'{currency}' is not a currency of {effect['economy']}",
                 f"did you mean '{hint[0]}'?" if hint else f"currencies: {listed}")]
    return []


def _check_pay(checker: Any, effect: dict[str, Any], path: str) -> list:
    issues = _check_currency(checker, effect, path)
    config = checked_config(checker, effect, "economy")
    tax = effect.get("tax")
    if config is not None and tax is not None and tax not in config.taxes:
        issues.append((f"{path}.tax", f"'{tax}' is not a tax of {effect['economy']}",
                       f"taxes: {', '.join(config.taxes) or 'none declared'}"))
    return issues


def _check_item(checker: Any, effect: dict[str, Any], path: str) -> list:
    config = checked_config(checker, effect, "economy")
    item = effect.get("item")
    if config is None or not isinstance(item, str) or is_expr(item) or item in config.items:
        return []
    if item in (checker.c.entities or {}):  # a unique item's instance, named by its id
        return []
    hint = get_close_matches(item, list(config.items), n=1)
    return [(f"{path}.item", f"'{item}' is not an item of {effect['economy']}",
             f"did you mean '{hint[0]}'?" if hint else f"items: {', '.join(config.items)}")]


def _check_ground(checker: Any, effect: dict[str, Any], path: str) -> list:
    issues = _check_item(checker, effect, path)
    config = checked_config(checker, effect, "economy")
    if config is not None and not {"drop", "pickup"} & set(config.actions):
        issues.append((path, f"inventory {effect['economy']} has no ground", "add drop or pickup to its `actions`"))
    return issues


@family_action("economy", ("ledger",), "pay", keys=("currency", "from", "to", "amount", "tax"),
               required=("from", "to", "amount"), literal=("tax",), check=_check_pay,
               example='{"economy": "money", "action": "pay", "from": "$actor", "to": "$params.shop", "amount": 12, '
                       '"tax": "sales_tax"}  (moves money, using credit; a declared tax is withheld; fails the '
                       'action if short; `currency` only when the ledger has several)')
def _pay(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    currency = _currency(runner, effect, vars, where)
    source = entity_of(world, runner.eval(effect["from"], vars), where, "a `from` entity")
    target = entity_of(world, runner.eval(effect["to"], vars), where, "a `to` entity")
    value = amount(runner.eval(effect["amount"], vars), where)
    tax_name = effect.get("tax")
    if not tax_name:
        move_money(world, currency, source, target, value, where)
        return
    _, ledger = _ledger(world, currency, where)
    tax = ledger.taxes.get(tax_name)
    if tax is None:
        raise RunError(f"'{tax_name}' is not a tax of this ledger (taxes: {', '.join(ledger.taxes) or 'none'})", where)
    rate = runner.eval(tax.rate, {**vars, "payer": source, "payee": target, "amount": value})
    levy = round(value * amount(rate, where, "a tax rate"), 9)
    need = value + levy if tax.on == "payer" else value
    have, limit = balance(world, source, currency, where), credit_of(world, source, currency)
    if have - need < -limit - EPS:
        extra = f" (credit {money(limit)})" if limit else ""
        raise Abort(f"{source.name} has only {money(have)} {currency}{extra}; {money(need)} is needed.")
    # A payee tax is withheld from what the payee receives; a payer tax is added on top. The payer pays the levy.
    move_money(world, currency, source, target, value if tax.on == "payer" else value - levy, where)
    if tax.to:
        move_money(world, currency, source, entity_of(world, tax.to, where, "the tax collector"), levy, where)
    else:
        burn_money(world, currency, source, levy, tax_name, where)


@family_action("economy", ("ledger",), "mint", keys=("currency", "to", "amount", "source"),
               required=("to", "amount", "source"), literal=("source",), check=_check_currency,
               example='{"economy": "money", "action": "mint", "to": "$it", "amount": 50, "source": "subsidy"}  '
                       '(new money from a named source)')
def _mint(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    target = entity_of(world, runner.eval(effect["to"], vars), where, "a `to` entity")
    mint_money(world, _currency(runner, effect, vars, where), target, runner.eval(effect["amount"], vars),
               _named(effect, "source", where), where)


@family_action("economy", ("ledger",), "burn", keys=("currency", "from", "amount", "sink"),
               required=("from", "amount", "sink"), literal=("sink",), check=_check_currency,
               example='{"economy": "money", "action": "burn", "from": "$actor", "amount": 3, "sink": "fees"}  '
                       '(money leaves the economy to a named sink; fails if short)')
def _burn(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    holder = entity_of(world, runner.eval(effect["from"], vars), where, "a `from` entity")
    burn_money(world, _currency(runner, effect, vars, where), holder, runner.eval(effect["amount"], vars),
               _named(effect, "sink", where), where)


@family_action("economy", ("inventory",), "give", keys=("item", "from", "to", "qty"), required=("item", "from", "to"),
               check=_check_item,
               example='{"economy": "goods", "action": "give", "item": "$params.item", "from": "$actor", "to": '
                       '"$params.to", "qty": 2}  (moves goods; a unique item by kind or instance id; fails if short '
                       'or the receiver is full)')
def _give(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    item = _goods(runner, effect, vars, where)
    source = entity_of(world, runner.eval(effect["from"], vars), where, "a `from` entity")
    target = entity_of(world, runner.eval(effect["to"], vars), where, "a `to` entity")
    move_items(world, item, source, target, _qty(runner, effect, vars, where), where)


@family_action("economy", ("inventory",), "make", keys=("item", "to", "qty", "source", "props"),
               required=("item", "to", "source"), literal=("source",), check=_check_item,
               example='{"economy": "goods", "action": "make", "item": "bread", "to": "$actor", "qty": 3, "source": '
                       '"baking"}  (new goods from a named source; unique items take props)')
def _make(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    item = _goods(runner, effect, vars, where)
    target = entity_of(world, runner.eval(effect["to"], vars), where, "a `to` entity")
    values = runner.eval(effect.get("props") or {}, vars)
    make_items(world, item, target, _qty(runner, effect, vars, where), _named(effect, "source", where), where, values,
               world.scope(**vars))


@family_action("economy", ("inventory",), "use", keys=("item", "from", "qty", "sink"),
               required=("item", "from", "sink"), literal=("sink",), check=_check_item,
               example='{"economy": "goods", "action": "use", "item": "flour", "from": "$actor", "qty": 2, "sink": '
                       '"baking"}  (goods used up into a named sink; fails if short)')
def _use(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    item = _goods(runner, effect, vars, where)
    holder = entity_of(world, runner.eval(effect["from"], vars), where, "a `from` entity")
    destroy_items(world, item, holder, _qty(runner, effect, vars, where), _named(effect, "sink", where), where)


def _ground(world: Any, item: Any, where: str) -> tuple[str, str, dict[str, Any]]:
    name, inventory, spec = _item(world, item, where)
    if spec.unique:
        raise RunError(f"unique items ({name}) change hands with `give`; only stackable goods lie on the ground", where)
    prop = f"{inventory}_ground"
    if prop not in world.props:
        raise RunError(f"inventory '{inventory}' has no ground (add drop or pickup to its actions)", where)
    return name, prop, dict(world.props[prop] or {})


@family_action("economy", ("inventory",), "drop", keys=("item", "from", "qty"), required=("item", "from"),
               check=_check_ground,
               example='{"economy": "goods", "action": "drop", "item": "$params.item", "from": "$actor", "qty": 1}  '
                       '(leave goods on the ground where the holder stands)')
def _drop(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    holder = entity_of(world, runner.eval(effect["from"], vars), where, "a `from` entity")
    name, prop, ground = _ground(world, _goods(runner, effect, vars, where), where)
    qty = _qty(runner, effect, vars, where)
    take_items(world, holder, name, qty, where)
    key = place_key(holder.location_id)
    spot = dict(ground.get(key) or {})
    spot[name] = spot.get(name, 0) + qty
    ground[key] = spot
    world.set_world(prop, ground)


@family_action("economy", ("inventory",), "pickup", keys=("item", "to", "qty"), required=("item", "to"),
               check=_check_ground,
               example='{"economy": "goods", "action": "pickup", "item": "$params.item", "to": "$actor", "qty": 1}  '
                       '(take goods lying where the holder stands)')
def _pickup(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    holder = entity_of(world, runner.eval(effect["to"], vars), where, "a `to` entity")
    name, prop, ground = _ground(world, _goods(runner, effect, vars, where), where)
    qty = _qty(runner, effect, vars, where)
    key = place_key(holder.location_id)
    spot = dict(ground.get(key) or {})
    if spot.get(name, 0) < qty:
        raise Abort(f"Only {spot.get(name, 0)} {name} lie here; {qty} were asked for.")
    if spot[name] == qty:
        spot.pop(name)
    else:
        spot[name] -= qty
    if spot:
        ground[key] = spot
    else:
        ground.pop(key, None)
    world.set_world(prop, ground)
    put_items(world, holder, name, qty, where)
