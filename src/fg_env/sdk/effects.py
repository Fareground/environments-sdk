"""Effects: how actions, events and stages change the world.

An effect list mixes assignment statements and keyed operations::

    "$actor.cash -= $params.qty * $params.offer.price"
    "$total = $params.qty * $params.offer.price"          (a local, usable below)
    {"if": "$actor.cash < 0", "then": [...], "else": [...]}
    {"each": "offer", "where": "$it.stock == 0", "do": ["$it.listed = false"]}
    {"create": "review", "props": {"stars": "$params.stars"}, "as": "made"}
    {"remove": "$params.target"}
    {"transfer": "cash", "from": "$actor", "to": "$params.seller", "amount": 10, "into": "cash"}
    {"link": "follows", "from": "$actor", "to": "$params.who", "value": 1, "props": {"since": "$round"}}
    "$link($actor, $params.who, follows).since = $round"
    {"unlink": "follows", "from": "$actor", "to": "$params.who"}
    {"move": "$actor", "to": "$params.place"}
    {"post": "chat", "text": "$params.text", "to": "$params.who", "delay": 2, "drop": 0.1}
    {"emit": "shock", "say": "Prices jump {$world.inflation|pct}.", "to": "$filter(buyer, $it.vip)"}
    {"fail": "You cannot afford that."}
    {"end": "bankrupt", "winner": "$top(player, $it.score, 1)", "say": "..."}
    {"after": 3, "do": [...]}
    {"wake": "$params.who", "why": "{$actor.name} asked you a question."}
    {"repeat": 1000, "while": "$best_bid.price >= $best_ask.price", "do": [...]}
    {"block": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}
    {"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails"}], "as": "coin"}

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
from .delivery import dropped, send
from .expr import MAX_INT_BITS, Expr, ExprError, attr, check_size, compile_expr, is_expr, resolve, truthy
from .template import compile_template, format_value
from .links import Link
from .registry import OPS, OpSpec, renamed_op_hint
from .world import Abort, SdkWorld
from .world_parts import PhysicsView, PropsView

from .contract import one_or_many

__all__ = ["EFFECT_OPS", "RESERVED_ROOTS", "Statement", "compile_statement", "statement_parts", "split_statement", "EffectRunner"]

EFFECT_OPS: Dict[str, Tuple[str, ...]] = {
    "if": ("if", "then", "else"),
    "each": ("each", "where", "do", "as"),
    "create": ("create", "count", "id", "name", "props", "at", "as"),
    "remove": ("remove",),
    "transfer": ("transfer", "from", "to", "amount", "into"),
    "link": ("link", "from", "to", "value", "props"),
    "unlink": ("unlink", "from", "to"),
    "move": ("move", "to"),
    "post": ("post", "to", "author", "delay", "drop"),  # plus the record's fields
    "emit": ("emit", "say", "to", "data", "delay", "drop"),
    "fail": ("fail",),
    "end": ("end", "winner", "say"),
    "after": ("after", "do"),
    "wake": ("wake", "why", "in", "now", "drop"),
    "repeat": ("repeat", "while", "do"),
    "block": ("block", "with"),
    "chance": ("chance", "outcomes", "weight", "as", "do"),
}

#: ``post`` keys that are not record fields.
POST_KEYS = frozenset(EFFECT_OPS["post"])

#: Hard ceiling for one ``repeat`` loop, whatever the contract asks for.
REPEAT_CEILING = 100_000

RESERVED_ROOTS = frozenset({
    "actor", "params", "it", "i", "row", "inputs", "world", "physics", "clock", "round",
    "stage", "metrics", "series", "arm", "viewer", "event", "outer", "pending", "result",
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


def statement_parts(source: str) -> Tuple[Optional[str], Tuple[Tuple[str, str], ...], Optional[str], str, str]:
    """``(base, steps, local, op, value)`` of an assignment; raises :class:`ExprError` if malformed.

    ``$total = 3`` → local ``total``. ``$world.board[$r][$c] = x`` → base ``$world`` and steps
    ``(("field", "board"), ("index", "$r"), ("index", "$c"))``. ``$entity(x).bag.apples += 1`` →
    base ``$entity(x)`` and two field steps.
    """
    parts = split_statement(source)
    if parts is None or not parts[0].startswith("$") or not parts[2]:
        raise ExprError(
            "an effect text must be an assignment like `$actor.cash -= 5` or `$total = $params.qty * 2`",
            source,
        )
    left, op, right = parts
    if _NAME.match(left[1:]):
        return None, (), left[1:], op, right
    base, steps = _target_steps(left, source)
    if not steps or steps[0][0] != "field":
        raise ExprError("the left side must name a property, like `$actor.cash`, `$entity(x).cash` or "
                        "`$world.board[$i][$j]`", source)
    return base, steps, None, op, right


def _target_steps(left: str, source: str) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
    match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*", left)
    if match is None:
        raise ExprError("the left side must start with a root like `$actor` or `$world`", source)
    i = match.end()
    if i < len(left) and left[i] == "(":
        i = _closing(left, i, "(", ")", source) + 1
    base = left[:i]
    steps: List[Tuple[str, str]] = []
    while i < len(left):
        ch = left[i]
        if ch == ".":
            field = re.match(r"[A-Za-z_][A-Za-z0-9_]*", left[i + 1:])
            if field is None:
                raise ExprError("a `.` must be followed by a property name", source)
            steps.append(("field", field.group(0)))
            i += 1 + field.end()
        elif ch == "[":
            close = _closing(left, i, "[", "]", source)
            index = left[i + 1:close].strip()
            if not index:
                raise ExprError("an element assignment looks like `$world.board[$i] = x`", source)
            steps.append(("index", index))
            i = close + 1
        elif ch.isspace():
            i += 1
        else:
            raise ExprError(f"unexpected `{ch}` on the left side of the assignment", source)
    return base, tuple(steps)


def _closing(text: str, start: int, opening: str, closing: str, source: str) -> int:
    depth, quote = 0, None
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return i
    raise ExprError(f"`{opening}` is never closed on the left side of the assignment", source)


@dataclass(frozen=True)
class Statement:
    source: str
    base: Optional[Expr]
    #: ``("field", name)`` or ``("index", compiled expression)`` steps after the base.
    steps: Tuple[Tuple[str, Any], ...]
    local: Optional[str]
    op: str
    value: Expr


@lru_cache(maxsize=8_192)
def compile_statement(source: str) -> Statement:
    base, steps, local, op, right = statement_parts(source)
    value = compile_expr(right)
    if local is not None:
        if local in RESERVED_ROOTS:
            raise ExprError(f"${local} is a reserved name, so a local cannot be called that; rename the local "
                            f"(e.g. ${local}_value) or assign to one of its fields", source)
        return Statement(source, None, (), local, op, value)
    assert base is not None
    compiled = tuple((kind, compile_expr(text) if kind == "index" else text) for kind, text in steps)
    return Statement(source, compile_expr(base), compiled, None, op, value)


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
    """Applies effect lists to one world, and runs the types' lifecycle hooks when entities are
    created or removed (inside whatever change made them, so they commit or roll back with it)."""

    #: How deep lifecycle hooks may set off further hooks.
    HOOK_DEPTH = 16

    def __init__(self, world: SdkWorld):
        self.world = world
        self._hook_depth = 0
        self._hooks: Dict[Tuple[str, str], List[Tuple[str, List[Any]]]] = {}
        world.lifecycle = self.lifecycle

    def lifecycle(self, hook: str, entity: Entity, where: str) -> None:
        """Run ``hook`` (on_create / on_remove) of the entity's type and its ancestors, root first ($it)."""
        key = (entity.entity_type, hook)
        hooks = self._hooks.get(key)
        if hooks is None:
            hooks = self._hooks[key] = self.world.contract.hooks_of(entity.entity_type, hook)
        if not hooks:
            return
        if self._hook_depth >= self.HOOK_DEPTH:
            raise RunError(f"{hook} hooks set each other off more than {self.HOOK_DEPTH} levels deep "
                           f"(does {entity.entity_type}'s {hook} create or remove another {entity.entity_type}?)", where)
        self._hook_depth += 1
        try:
            for type_name, effects in hooks:
                self.run(effects, {"it": entity}, f"types.{type_name}.{hook}")
        finally:
            self._hook_depth -= 1

    def run(self, effects: List[Any], vars: Dict[str, Any], path: str) -> None:
        for index, effect in enumerate(one_or_many(effects) or []):
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
            except ArithmeticError as exc:  # a contract rule's arithmetic failed: the rule's fault, never the participant's
                raise RunError(f"arithmetic failed: {exc}", where) from None

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
        assert stmt.base is not None
        owner, prop, rest = self._owner(stmt, scope, source, where)
        if rest:
            value = self._set_in(attr(owner, prop, source), rest, stmt.op, value, source, prop)
        elif stmt.op != "=":
            value = self._combine(stmt.op, attr(owner, prop, source), value, source)
        if stmt.op == "=" and self.world.sealed_writes is not None and isinstance(owner, (Entity, PropsView)):
            self.world.sealed_writes.assigned(owner, prop, [key for _, key in rest], value, source)
        if isinstance(owner, Entity):
            self.world.set_prop(owner, prop, value)
        elif isinstance(owner, Link):
            self.world.set_link_field(owner, prop, value, where)
        elif isinstance(owner, PropsView):
            self.world.set_world(prop, value)
        else:
            self.world.set_physics(prop, value)

    def _owner(self, stmt: Statement, scope: Any, source: str, where: str) -> Tuple[Any, str, List[Tuple[str, Any]]]:
        """The deepest entity / link / $world / $physics on the target path, the property written on it, and
        the element path (resolved keys) inside that property's value."""
        current = stmt.base(scope)  # type: ignore[misc]
        found: Optional[Tuple[Any, int]] = None
        for position, (kind, step) in enumerate(stmt.steps):
            if kind == "field" and isinstance(current, (Entity, Link, PropsView, PhysicsView)):
                found = (current, position)
            if position == len(stmt.steps) - 1:
                break
            current = attr(current, step, source) if kind == "field" else self._element(current, step(scope), source)
        if found is None:
            raise RunError(f"can only assign to an entity's property, a link's field, $world.x or $physics.x (`{source}`)", where)
        owner, position = found
        prop = stmt.steps[position][1]
        rest = [(kind, step(scope) if kind == "index" else step) for kind, step in stmt.steps[position + 1:]]
        return owner, prop, rest

    @staticmethod
    def _element(container: Any, key: Any, source: str) -> Any:
        if isinstance(container, list):
            if isinstance(key, bool) or not isinstance(key, int) or not -len(container) <= key < len(container):
                raise ExprError(f"index {key!r} is out of range for a list of {len(container)}", source)
            return container[key]
        if isinstance(container, dict):
            name = str(key)
            if name not in container:
                raise ExprError(f"no key {name!r} (keys: {', '.join(map(str, list(container)[:12]))})", source)
            return container[name]
        return attr(container, str(key), source)

    def _set_in(self, container: Any, path: List[Tuple[str, Any]], op: str, value: Any, source: str,
                label: str) -> Any:
        """A copy of ``container`` with the element at ``path`` assigned (or combined with ``op``)."""
        kind, key = path[0]
        last = len(path) == 1
        if isinstance(container, list):
            if kind == "field" or isinstance(key, bool) or not isinstance(key, int) \
                    or not -len(container) <= key < len(container):
                shown = key if kind == "index" else f".{key}"
                raise ExprError(f"`{label}` is a list of {len(container)}; {shown!r} is not a valid index", source)
            updated: Any = list(container)
        elif isinstance(container, dict):
            key = str(key)
            updated = dict(container)
        elif container is None and not last:
            raise ExprError(f"`{label}` has no value to assign into", source)
        else:
            raise ExprError(f"`{label}` is not a list or map, so it has no elements to assign", source)
        exists = isinstance(updated, list) or key in updated
        if last:
            if op != "=":
                current = updated[key] if exists else ([] if isinstance(value, list) else 0)
                value = self._combine(op, current, value, source)
            updated[key] = value
        else:
            if not exists:
                if op != "=" and not isinstance(updated, dict):
                    raise ExprError(f"no element {key!r} in `{label}`", source)
                updated[key] = {}
            updated[key] = self._set_in(updated[key], path[1:], op, value, source, f"{label}[{key!r}]")
        return check_size(updated, source)

    @staticmethod
    def _combine(op: str, current: Any, value: Any, source: str) -> Any:
        """``current op value`` for +=, -=, *=, /=, refusing results past the size limits."""
        try:
            result = EffectRunner._combine_raw(op, current, value, source)
        except OverflowError:
            raise ExprError(f"`{op}` gives a result too large to represent", source) from None
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
        ops = select_ops(effect)
        if len(ops) != 1:
            if not ops:
                keys = ", ".join(effect)
                renamed = renamed_op_hint(effect)
                hint = get_close_matches(next(iter(effect), ""), list(all_ops()), n=1)
                raise RunError(
                    f"unknown effect with keys ({keys})"
                    + (f" — {renamed}" if renamed else f" — did you mean '{hint[0]}'?" if hint else "")
                    + f"; effects are: {', '.join(all_ops())}",
                    where,
                )
            raise RunError(f"an effect object names exactly one operation, got {ops}", where)
        registered = OPS.get(ops[0])
        if registered is None:
            getattr(self, "_op_" + ops[0])(effect, vars, where)
            return
        try:
            registered.run(self, effect, vars, where)
        except (Abort, RunError, ExprError, ArithmeticError):
            raise
        except Exception as exc:  # a mechanism op crashed: the op's fault at this path, never the participant's
            raise RunError(f"`{ops[0]}` failed: {type(exc).__name__}: {exc}", where) from exc

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
            self.world.remove(_entity(item, self.world, where), where)

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
        value = self._eval(effect["value"], vars) if "value" in effect else None
        fields = effect.get("props") or {}
        if not isinstance(fields, dict):
            raise RunError(f"`props` is an object of link fields, got {fields!r}", where)
        self.world.link(effect["link"], self._eval(effect.get("from"), vars), self._eval(effect.get("to"), vars),
                        value, where, {name: self._eval(raw, vars) for name, raw in fields.items()})

    def _op_unlink(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        self.world.unlink(effect["unlink"], self._eval(effect.get("from"), vars), self._eval(effect.get("to"), vars),
                          where)

    def _op_move(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        entity = _entity(self._eval(effect["move"], vars), self.world, where)
        self.world.move(entity, self._eval(effect.get("to"), vars), where)

    def _op_post(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        if self._dropped(effect, vars, where):
            return
        fields = {k: _plain_value(self._eval(v, vars)) for k, v in effect.items() if k not in POST_KEYS}
        if "author" in effect:
            author_value = self._eval(effect["author"], vars)
            author = _entity(author_value, self.world, where).id if author_value is not None else None
        else:
            actor = vars.get("actor")
            author = actor.id if isinstance(actor, Entity) else None
        to = _to_ids(self._eval(effect.get("to"), vars), where) if "to" in effect else None
        send(self.world, self._eval(effect["delay"], vars) if "delay" in effect else None,
             {"kind": "post", "record": effect["post"], "fields": fields, "author": author,
              "to": list(to) if to is not None else None}, where)

    def _op_emit(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        if self._dropped(effect, vars, where):
            return
        to = _to_ids(self._eval(effect.get("to"), vars), where) if "to" in effect else None
        actor = vars.get("actor")
        data = self._eval(effect.get("data") or {}, vars)
        send(self.world, self._eval(effect["delay"], vars) if "delay" in effect else None,
             {"kind": "emit", "event": str(effect["emit"]), "text": self._text(effect.get("say"), vars),
              "actor": actor.id if isinstance(actor, Entity) else None, "to": list(to) if to is not None else None,
              "data": {k: _plain_value(v) for k, v in data.items()}}, where)

    def _dropped(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> bool:
        """Roll the effect's ``drop`` chance (a lossy channel): True when the message is lost."""
        return "drop" in effect and dropped(self.world, self._eval(effect["drop"], vars), f"{where}.drop")

    def _op_fail(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        raise Abort(self._text(effect["fail"], vars) or "That is not possible right now.")

    def _op_end(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        winner = self._eval(effect.get("winner"), vars) if "winner" in effect else None
        self.world.request_end(str(effect["end"]), _plain_value(winner), self._text(effect.get("say"), vars))

    def _op_after(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        delay = self._eval(effect["after"], vars)
        world = self.world
        if world.continuous:
            if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not delay > 0:
                raise RunError(f"`after` needs a time greater than 0 on a continuous clock, got {delay!r}", where)
            world.schedule(world.time + delay, effect.get("do") or [], vars, f"{where}.do")
            return
        if isinstance(delay, bool) or not isinstance(delay, int) or delay < 1:
            raise RunError(f"`after` needs a whole number of rounds ≥ 1, got {delay!r}", where)
        world.schedule(world.round + delay, effect.get("do") or [], vars, f"{where}.do")

    def _op_wake(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        why = self._text(effect.get("why"), vars) or "You were asked to act."
        world = self.world
        delay = self._eval(effect["in"], vars) if "in" in effect else 0
        if "in" in effect and not world.continuous:
            raise RunError("`in` needs a continuous clock (clock.mode: continuous)", where)
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < 0:
            raise RunError(f"`in` must be a time ≥ 0, got {delay!r}", where)
        now = truthy(self._eval(effect["now"], vars)) if "now" in effect else False
        if now and "in" in effect:
            raise RunError("`wake` takes `now` or `in`, not both", where)
        if self._dropped(effect, vars, where):
            return
        for entity_id in _to_ids(self._eval(effect["wake"], vars), where) or ():
            if now:
                world.request_reaction(entity_id, why)
                continue
            world.request_wake(entity_id, why)
            if world.continuous:
                world.set_wake_at(entity_id, world.time + delay)

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

    def _op_chance(self, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        from .chance import run_chance

        run_chance(self, effect, vars, where)

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
    if isinstance(value, Link):
        return _plain_value(value.as_dict())
    if isinstance(value, list):
        return [_plain_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain_value(v) for k, v in value.items()}
    return value


def select_ops(effect: Dict[str, Any]) -> List[str]:
    """The operation(s) an effect object names; anything but exactly one is an error for the caller.

    * A ``post``'s other keys are record fields, whatever they are called (a field may be named
      like a native op, e.g. ``deal``).
    * A single family op wins over keys named like core ops that its actions declare themselves.
    * Otherwise every key that names an operation counts.
    """
    core = [key for key in EFFECT_OPS if key in effect]
    if core == ["post"]:
        return core
    native = [key for key in OPS if key in effect]
    if len(native) == 1 and all(key in OPS[native[0]].keys for key in core):
        return native
    return core + native


def all_ops() -> Dict[str, Tuple[str, ...]]:
    """Every effect operation and the keys it takes: the core ones, then registered native ops."""
    return {**EFFECT_OPS, **{name: spec.keys for name, spec in OPS.items()}}


def registered_op(name: str) -> Optional[OpSpec]:
    return OPS.get(name)


def is_statement(value: Any) -> bool:
    return isinstance(value, str) and is_expr(value) and split_statement(value) is not None


from . import mechanisms as _mechanisms  # noqa: E402,F401  (registers native ops and mechanism kinds)
assert not set(EFFECT_OPS) & set(OPS), "a registered op shadows a core effect"
