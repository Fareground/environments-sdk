"""Effects: how actions, events and stages change the world.

An effect list mixes assignment statements and keyed operations::

    "$actor.cash -= $params.qty * $params.offer.price"
    "$total = $params.qty * $params.offer.price"          (a local, usable below)
    {"if": "$actor.cash < 0", "then": [...], "else": [...]}
    {"each": "offer", "where": "$it.stock == 0", "do": ["$it.listed = false"]}
    {"create": "review", "props": {"stars": "$params.stars"}, "as": "made"}
    {"remove": "$params.target"}
    {"transfer": "cash", "from": "$actor", "to": "$params.seller", "amount": 10, "into": "cash"}
    {"link": "follows", "from": "$actor", "to": "$params.who", "value": 1}
    {"unlink": "follows", "from": "$actor", "to": "$params.who"}
    {"move": "$actor", "to": "$params.place"}
    {"post": "chat", "text": "$params.text", "to": "$params.who"}
    {"emit": "shock", "say": "Prices jump {$world.inflation|pct}.", "to": "$filter(buyer, $it.vip)"}
    {"fail": "You cannot afford that."}
    {"end": "bankrupt", "winner": "$top(player, $it.score, 1)", "say": "..."}
    {"after": 3, "do": [...]}
    {"wake": "$params.who", "why": "{$actor.name} asked you a question."}
    {"repeat": 1000, "while": "$best_bid.price >= $best_ask.price", "do": [...]}
    {"block": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}

Everything an action does is atomic: ``fail`` (or any error) rolls every change back.
A ``repeat`` loop that is still running when its limit is reached is an error, so a
rule that never settles is reported instead of silently truncated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from ..entity import Entity
from .errors import RunError
from .contract import MAX_CREATE
from .expr import MAX_INT_BITS, Expr, ExprError, attr, check_size, compile_expr, is_expr, resolve, truthy
from .template import compile_template, format_value
from .registry import OPS, OpSpec
from .world import Abort, SdkWorld, _Physics, _Props

__all__ = ["EFFECT_OPS", "RESERVED_ROOTS", "Statement", "compile_statement", "statement_parts", "split_statement", "EffectRunner"]

EFFECT_OPS: Dict[str, Tuple[str, ...]] = {
    "if": ("if", "then", "else"),
    "each": ("each", "where", "do", "as"),
    "create": ("create", "count", "id", "name", "props", "at", "as"),
    "remove": ("remove",),
    "transfer": ("transfer", "from", "to", "amount", "into"),
    "link": ("link", "from", "to", "value"),
    "unlink": ("unlink", "from", "to"),
    "move": ("move", "to"),
    "post": ("post", "to", "author"),  # plus the record's fields
    "emit": ("emit", "say", "to", "data"),
    "fail": ("fail",),
    "end": ("end", "winner", "say"),
    "after": ("after", "do"),
    "wake": ("wake", "why"),
    "repeat": ("repeat", "while", "do"),
    "block": ("block", "with"),
}

#: Hard ceiling for one ``repeat`` loop, whatever the contract asks for.
REPEAT_CEILING = 100_000

RESERVED_ROOTS = frozenset({
    "actor", "params", "it", "i", "row", "inputs", "world", "physics", "clock", "round",
    "stage", "metrics", "series", "arm", "viewer", "event", "outer", "pending",
})

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


def split_statement(source: str) -> Optional[Tuple[str, str, str]]:
    """``(left, operator, right)`` for an assignment text, or None when it is not one."""
    depth, quote, i, n = 0, None, 0, len(source)
    while i < n:
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            for op in ("+=", "-=", "*=", "/="):
                if source.startswith(op, i):
                    return source[:i].strip(), op, source[i + 2:].strip()
            if ch == "=" and not source.startswith("==", i) and (i == 0 or source[i - 1] not in "=!<>"):
                return source[:i].strip(), "=", source[i + 1:].strip()
        i += 1
    return None


def statement_parts(source: str) -> Tuple[Optional[str], Optional[str], Optional[str], str, str, Optional[str]]:
    """``(target, prop, local, op, value, index)`` of an assignment; raises :class:`ExprError` if malformed.

    ``index`` is set for element assignment: ``$world.board[$i] = x`` → target ``$world``,
    prop ``board``, index ``$i``.
    """
    parts = split_statement(source)
    if parts is None or not parts[0].startswith("$") or not parts[2]:
        raise ExprError(
            "an effect text must be an assignment like `$actor.cash -= 5` or `$total = $params.qty * 2`",
            source,
        )
    left, op, right = parts
    if _NAME.match(left[1:]):
        return None, None, left[1:], op, right, None
    index: Optional[str] = None
    if left.endswith("]"):
        depth, opening = 0, None
        for i in range(len(left) - 1, -1, -1):
            if left[i] == "]":
                depth += 1
            elif left[i] == "[":
                depth -= 1
                if depth == 0:
                    opening = i
                    break
        if opening is None or not left[opening + 1:-1].strip():
            raise ExprError("an element assignment looks like `$world.board[$i] = x`", source)
        index = left[opening + 1:-1].strip()
        left = left[:opening]
    depth, quote, dot = 0, None, None
    for i, ch in enumerate(left):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "." and depth == 0:
            dot = i
    if dot is None or not _NAME.match(left[dot + 1:]):
        raise ExprError("the left side must end in a property, like `$actor.cash`, `$entity(x).cash` or `$world.board[$i]`", source)
    return left[:dot], left[dot + 1:], None, op, right, index


@dataclass(frozen=True)
class Statement:
    source: str
    target: Optional[Expr]
    prop: Optional[str]
    local: Optional[str]
    op: str
    value: Expr
    index: Optional[Expr] = None


@lru_cache(maxsize=8_192)
def compile_statement(source: str) -> Statement:
    target, prop, local, op, right, index = statement_parts(source)
    value = compile_expr(right)
    if local is not None:
        if local in RESERVED_ROOTS:
            raise ExprError(f"${local} cannot be reassigned; assign to one of its fields instead", source)
        return Statement(source, compile_expr(f"${local}") if op != "=" else None, None, local, op, value)
    assert target is not None
    return Statement(source, compile_expr(target), prop, None, op, value, compile_expr(index) if index else None)


def _to_ids(value: Any, where: str) -> Optional[Tuple[str, ...]]:
    if value is None:
        return None
    if isinstance(value, Entity):
        return (value.id,)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            if isinstance(item, Entity):
                out.append(item.id)
            elif isinstance(item, str):
                out.append(item)
            else:
                raise RunError(f"recipients must be entities or ids, got {item!r}", where)
        return tuple(out)
    raise RunError(f"recipients must be entities or ids, got {value!r}", where)


def _entity(value: Any, world: SdkWorld, where: str, what: str = "an entity") -> Entity:
    if isinstance(value, str):
        found = world.entities.get(value)
        if found is not None:
            return found
    if isinstance(value, Entity):
        return value
    raise RunError(f"expected {what}, got {value!r}", where)


def _items(value: Any, world: SdkWorld, where: str) -> List[Any]:
    if isinstance(value, str) and world.is_type(value):
        return list(world.entities_of(value))
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, Entity):
        return [value]
    raise RunError(f"`each` needs a type name or a list, got {value!r}", where)


class EffectRunner:
    """Applies effect lists to one world."""

    def __init__(self, world: SdkWorld):
        self.world = world

    def run(self, effects: List[Any], vars: Dict[str, Any], path: str) -> None:
        for index, effect in enumerate(effects or []):
            where = f"{path}[{index}]"
            try:
                if isinstance(effect, str):
                    self._statement(effect, vars, where)
                elif isinstance(effect, dict):
                    self._keyed(effect, vars, where)
                else:
                    raise RunError(f"an effect is text or an object, got {effect!r}", where)
            except ExprError as exc:
                raise RunError(str(exc), where) from None

    # -- statements ------------------------------------------------------------

    def _statement(self, source: str, vars: Dict[str, Any], where: str) -> None:
        stmt = compile_statement(source)
        scope = self.world.scope(**vars)
        value = stmt.value(scope)
        if stmt.local is not None:
            if stmt.op != "=":
                value = self._combine(stmt.op, scope.root(stmt.local, source), value, source)
            vars[stmt.local] = value
            return
        assert stmt.target is not None and stmt.prop is not None
        owner = stmt.target(scope)
        if stmt.index is not None:
            container = attr(owner, stmt.prop, source)
            key = stmt.index(scope)
            if isinstance(container, list):
                if isinstance(key, bool) or not isinstance(key, int) or not -len(container) <= key < len(container):
                    raise ExprError(f"index {key!r} is out of range for a list of {len(container)}", source)
                updated: Any = list(container)
            elif isinstance(container, dict):
                key = str(key)
                updated = dict(container)
            else:
                raise ExprError(f"`{stmt.prop}` is not a list or map, so it has no elements to assign", source)
            if stmt.op != "=":
                if isinstance(updated, list) or key in updated:
                    current = updated[key]
                else:  # a new map key counts from nothing: tallies, running totals
                    current = [] if isinstance(value, list) else 0
                value = self._combine(stmt.op, current, value, source)
            updated[key] = value
            value = updated
        elif stmt.op != "=":
            value = self._combine(stmt.op, attr(owner, stmt.prop, source), value, source)
        if isinstance(owner, Entity):
            self.world.set_prop(owner, stmt.prop, value)
        elif isinstance(owner, _Props):
            self.world.set_world(stmt.prop, value)
        elif isinstance(owner, _Physics):
            self.world.set_physics(stmt.prop, value)
        else:
            raise RunError(f"can only assign to an entity's property, $world.x or $physics.x (`{source}`)", where)

    @staticmethod
    def _combine(op: str, current: Any, value: Any, source: str) -> Any:
        """``current op value`` for +=, -=, *=, /=, refusing results past the size limits."""
        result = EffectRunner._combine_raw(op, current, value, source)
        if isinstance(result, int) and not isinstance(result, bool) and result.bit_length() > MAX_INT_BITS:
            raise ExprError(f"a whole number of {result.bit_length():,} bits is past the limit of {MAX_INT_BITS:,} bits",
                            source)
        return check_size(result, source)

    @staticmethod
    def _combine_raw(op: str, current: Any, value: Any, source: str) -> Any:
        if op == "+=" and isinstance(current, list):
            return current + (list(value) if isinstance(value, list) else [value])
        if op == "-=" and isinstance(current, list):
            drop = value if isinstance(value, list) else [value]
            drop_ids = {getattr(d, "id", d) for d in drop}
            return [x for x in current if x not in drop_ids]
        numbers = [v for v in (current, value) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(numbers) != 2:
            raise ExprError(f"`{op}` needs numbers (current {current!r}, value {value!r})", source)
        if op == "+=":
            return current + value
        if op == "-=":
            return current - value
        if op == "*=":
            return current * value
        if value == 0:
            raise ExprError("division by zero", source)
        return current / value

    # -- keyed operations ------------------------------------------------------

    def _keyed(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        ops = [key for key in all_ops() if key in effect]
        if len(ops) != 1:
            if not ops:
                keys = ", ".join(effect)
                hint = get_close_matches(next(iter(effect), ""), list(EFFECT_OPS), n=1)
                raise RunError(
                    f"unknown effect with keys ({keys})" + (f" — did you mean '{hint[0]}'?" if hint else "")
                    + f"; effects are: {', '.join(all_ops())}",
                    where,
                )
            raise RunError(f"an effect object names exactly one operation, got {ops}", where)
        registered = OPS.get(ops[0])
        if registered is not None:
            registered.run(self, effect, vars, where)
        else:
            getattr(self, "_op_" + ops[0])(effect, vars, where)

    def eval(self, value: Any, vars: Dict[str, Any]) -> Any:
        """Evaluate an expression (or a structure of them) with these locals."""
        return resolve(value, self.world.scope(**vars))

    def text(self, template: Optional[str], vars: Dict[str, Any]) -> str:
        """Render a template with these locals."""
        if not template:
            return ""
        return compile_template(template, None).render(self.world.scope(**vars))

    _eval = eval
    _text = text

    def _op_if(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        branch = "then" if truthy(self._eval(effect["if"], vars)) else "else"
        self.run(effect.get(branch) or [], vars, f"{where}.{branch}")

    def _op_each(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        name = effect.get("as") or "it"
        items = _items(self._eval(effect["each"], vars), self.world, where)
        where_expr = effect.get("where")
        for position, item in enumerate(items):
            inner = {**vars, name: item, "i": position}
            if where_expr is not None and not truthy(self._eval(where_expr, inner)):
                continue
            self.run(effect.get("do") or [], inner, f"{where}.do")
            # Locals assigned in the body (running totals, a best-so-far) stay assigned after it;
            # only the loop's own names are scoped to it.
            vars.update((key, value) for key, value in inner.items() if key not in (name, "i"))

    def _op_create(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        count = self._eval(effect.get("count", 1), vars)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RunError(f"count must be a whole number ≥ 0, got {count!r}", where)
        if count > MAX_CREATE:
            raise RunError(f"count {count:,} is more than the limit of {MAX_CREATE:,} entities per create", where)
        made: List[Entity] = []
        for n in range(count):
            inner = {**vars, "i": n + 1}
            entity_id = self._text(effect.get("id"), inner) or None
            name = self._text(effect.get("name"), inner) or None
            at = self._eval(effect.get("at"), inner)
            made.append(self.world.create(effect["create"], entity_id, name, effect.get("props") or {},
                                          at, self.world.scope(**inner), where))
        if effect.get("as"):
            vars[effect["as"]] = made[0] if count == 1 else made

    def _op_remove(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        value = self._eval(effect["remove"], vars)
        for item in value if isinstance(value, list) else [value]:
            self.world.remove(_entity(item, self.world, where))

    def _op_transfer(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        prop = effect["transfer"]
        source = _entity(self._eval(effect.get("from"), vars), self.world, where, "a `from` entity")
        target = _entity(self._eval(effect.get("to"), vars), self.world, where, "a `to` entity")
        amount = self._eval(effect.get("amount"), vars)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
            raise RunError(f"transfer amount must be a number ≥ 0, got {amount!r}", where)
        into = effect.get("into") or prop
        have = _amount_held(source, prop, where)
        held = _amount_held(target, into, where)
        if have < amount:
            raise Abort(f"{source.name} has only {format_value(have)} {prop}; {format_value(amount)} is needed.")
        # A transfer moves value; it never creates or destroys it. Limits that would clamp
        # either side refuse the transfer instead.
        low = self.world.prop_spec(source, prop).min
        if low is not None and have - amount < low:
            raise Abort(f"{source.name} cannot go below {format_value(low)} {prop}; "
                        f"at most {format_value(have - low)} can be given.")
        high = self.world.prop_spec(target, into).max
        if high is not None and held + amount > high:
            raise Abort(f"{target.name} can hold at most {format_value(high)} {into}; "
                        f"at most {format_value(max(0, high - held))} more fits.")
        self.world.set_prop(source, prop, have - amount)
        self.world.set_prop(target, into, _amount_held(target, into, where) + amount)

    def _op_link(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        value = self._eval(effect.get("value", 1), vars)
        self.world.link(effect["link"], self._eval(effect.get("from"), vars), self._eval(effect.get("to"), vars),
                        value, where)

    def _op_unlink(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        self.world.unlink(effect["unlink"], self._eval(effect.get("from"), vars), self._eval(effect.get("to"), vars),
                          where)

    def _op_move(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        entity = _entity(self._eval(effect["move"], vars), self.world, where)
        self.world.move(entity, self._eval(effect.get("to"), vars), where)

    def _op_post(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        fields = {k: self._eval(v, vars) for k, v in effect.items() if k not in ("post", "to", "author")}
        if "author" in effect:
            author_value = self._eval(effect["author"], vars)
            author = _entity(author_value, self.world, where).id if author_value is not None else None
        else:
            actor = vars.get("actor")
            author = actor.id if isinstance(actor, Entity) else None
        to = _to_ids(self._eval(effect.get("to"), vars), where) if "to" in effect else None
        self.world.post(effect["post"], fields, author, to, where)

    def _op_emit(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        to = _to_ids(self._eval(effect.get("to"), vars), where) if "to" in effect else None
        actor = vars.get("actor")
        data = self._eval(effect.get("data") or {}, vars)
        self.world.emit(str(effect["emit"]), self._text(effect.get("say"), vars),
                        actor=actor.id if isinstance(actor, Entity) else None, to=to,
                        data={k: _plain_value(v) for k, v in data.items()})

    def _op_fail(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        raise Abort(self._text(effect["fail"], vars) or "That is not possible right now.")

    def _op_end(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        winner = self._eval(effect.get("winner"), vars) if "winner" in effect else None
        self.world.request_end(str(effect["end"]), _plain_value(winner), self._text(effect.get("say"), vars))

    def _op_after(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        rounds = self._eval(effect["after"], vars)
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
            raise RunError(f"`after` needs a whole number of rounds ≥ 1, got {rounds!r}", where)
        self.world.schedule(self.world.round + rounds, effect.get("do") or [], vars, f"{where}.do")

    def _op_wake(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        why = self._text(effect.get("why"), vars) or "You were asked to act."
        for entity_id in _to_ids(self._eval(effect["wake"], vars), where) or ():
            self.world.request_wake(entity_id, why)

    def _op_block(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        name = effect["block"]
        spec = self.world.contract.blocks.get(name)
        if spec is None:
            raise RunError(f"'{name}' is not a declared block (blocks: {', '.join(self.world.contract.blocks) or 'none'})", where)
        given = effect.get("with") or {}
        if set(given) != set(spec.args):
            raise RunError(f"block '{name}' takes arguments {spec.args}, got {sorted(given)}", where)
        depth = getattr(self, "_depth", 0)
        if depth >= 16:
            raise RunError(f"block '{name}' runs blocks too deeply (recursion?)", where)
        inner = {key: self._eval(value, vars) for key, value in given.items()}
        self._depth = depth + 1
        try:
            self.run(spec.do, inner, f"blocks.{name}.do")
        finally:
            self._depth = depth

    def _op_repeat(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        limit = self._eval(effect["repeat"], vars)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= REPEAT_CEILING:
            raise RunError(f"`repeat` needs a whole-number limit from 1 to {REPEAT_CEILING}, got {limit!r}", where)
        condition = effect.get("while")
        for _ in range(limit):
            if condition is not None and not truthy(self._eval(condition, vars)):
                return
            self.run(effect.get("do") or [], vars, f"{where}.do")
        if condition is not None and truthy(self._eval(condition, vars)):
            raise RunError(f"`repeat` reached its limit of {limit} while `{condition}` still holds", where)


def _amount_held(entity: Entity, prop: str, where: str) -> float:
    value = attr(entity, prop, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunError(f"`transfer` moves numbers, but {entity.id}.{prop} is {value!r}", where)
    return value


def _plain_value(value: Any) -> Any:
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, list):
        return [_plain_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain_value(v) for k, v in value.items()}
    return value


def all_ops() -> Dict[str, Tuple[str, ...]]:
    """Every effect operation and the keys it takes: the core ones, then registered native ops."""
    return {**EFFECT_OPS, **{name: spec.keys for name, spec in OPS.items()}}


def registered_op(name: str) -> Optional[OpSpec]:
    return OPS.get(name)


def is_statement(value: Any) -> bool:
    return isinstance(value, str) and is_expr(value) and split_statement(value) is not None


from . import mechanisms as _mechanisms  # noqa: E402,F401  (registers native ops and mechanism kinds)
assert not set(EFFECT_OPS) & set(OPS), "a registered op shadows a core effect"
