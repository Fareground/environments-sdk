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
    {"wake": "$params.who", "why": "{$actor.name} asked you a question.", "now": true}
    {"repeat": 1000, "while": "$best_bid.price >= $best_ask.price", "do": [...]}
    {"call": "settle", "with": {"buyer": "$actor", "qty": "$params.qty"}}
    {"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails"}], "as": "coin"}

Everything an action does is atomic: ``fail`` (or any error) rolls every change back.
A ``repeat`` loop that is still running when its limit is reached is an error, so a
rule that never settles is reported instead of silently truncated.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from difflib import get_close_matches
from typing import Any

from ..contract import MAX_CREATE, one_or_many
from ..errors import RunError
from ..expr import EVERYONE, MAX_INT_BITS, ExprError, attr, check_size, compile_expr, map_key, resolve, truthy
from ..expr.objects import Entity, PropsView
from ..expr.template import compile_template, format_value
from ..expr.values import _eq
from ..registry import OPS, OpSpec, family_action_hint
from ..world.links import Link
from ..world.live import Abort, SdkWorld
from ..world.parts import PhysicsView
from .delivery import dropped, send
from .statements import Statement, capture_roots, compile_statement, structured_capture_roots
from .sync import run_synced

__all__ = ["EFFECT_OPS", "EffectRunner"]

EFFECT_OPS: dict[str, tuple[str, ...]] = {
    "if": ("if", "then", "else"),
    "each": ("each", "where", "do", "as", "sync"),
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
    "wake": ("wake", "why", "now", "actions"),
    "repeat": ("repeat", "while", "do"),
    "call": ("call", "with"),
    "chance": ("chance", "outcomes", "weight", "as", "do"),
}

#: ``post`` keys that are not record fields.
POST_KEYS = frozenset(EFFECT_OPS["post"])

#: Hard ceiling for one ``repeat`` loop, whatever the contract asks for.
REPEAT_CEILING = 100_000


_NOT_ASSIGNABLE = "can only assign to an entity's property, a link's field, $world.x or $physics.x (`{source}`)"
#: Exactly these types take the numeric path of `+=`, `-=`, `*=`, `/=` (a bool is not a number there).
_NUMBERS = (int, float)


def _to_ids(value: Any, where: str) -> tuple[str, ...] | None:
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


def each_items(value: Any, world: SdkWorld, where: str) -> list[Any]:
    """What an `each` (of an effect, an event or a policy rule) goes over: a type's entities, a list, one entity."""
    if isinstance(value, str) and world.is_type(value):
        return list(world.entities_of(value))
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, Entity):
        return [value]
    raise RunError(f"`each` must be a type name or a list, got {value!r}", where)


def removed_since(items: Sequence[Any]) -> Callable[[int], bool]:
    """For a loop over ``items``: whether the item at a position is an entity that was active when the loop began and
    has been removed since (by an earlier item's rules), so the loop skips it instead of writing to it."""
    active = [isinstance(item, Entity) and item.alive for item in items]
    return lambda position: active[position] and not items[position].alive


class EffectRunner:
    """Applies effect lists to one world, and fires the events on ``create.<type>`` and ``remove.<type>`` when entities
    are created or removed (inside whatever change made them, so they commit or roll back with it)."""

    #: How deep create and remove events may set off further ones.
    HOOK_DEPTH = 16

    def __init__(self, world: SdkWorld):
        self.world = world
        self._hook_depth = 0
        self._hooks: dict[tuple[str, str], list[tuple[int, Any]]] = {}
        world.lifecycle = self.lifecycle

    def lifecycle(self, kind: str, entity: Entity, where: str) -> None:
        """Fire the events on ``<kind>.<type>`` (create / remove) for the entity's type and its ancestors, root first,
        with ``$it`` the entity."""
        key = (entity.entity_type, kind)
        events = self._hooks.get(key)
        if events is None:
            contract = self.world.contract
            events = self._hooks[key] = [found for name in contract.lineage(entity.entity_type)
                                         for found in contract.events_on(f"{kind}.{name}")]
        if not events:
            return
        if self._hook_depth >= self.HOOK_DEPTH:
            raise RunError(f"events on {kind} set each other off more than {self.HOOK_DEPTH} levels deep (does "
                           f"creating or removing a {entity.entity_type} {kind} another?)", where)
        self._hook_depth += 1
        try:
            for index, event in events:
                path = f"events[{index}]"
                try:
                    if event.when is not None and not self._condition(event.when, {"it": entity}):
                        continue
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}.when") from None
                self.run(event.do, {"it": entity}, f"{path}.do")
                if event.say:
                    text = self.text(event.say, {"it": entity, "viewer": EVERYONE})
                    if text.strip():
                        self.world.emit("news", text, data={"event": event.name or index})
        finally:
            self._hook_depth -= 1

    def run(self, effects: list[Any], vars: dict[str, Any], path: str) -> None:
        for index, effect in enumerate(one_or_many(effects) or []):
            try:
                if isinstance(effect, str):
                    self._statement(effect, vars, path, index)  # its path is spelled out only if it is reported
                elif isinstance(effect, dict):
                    self._keyed(effect, vars, f"{path}[{index}]")
                else:
                    raise RunError(f"an effect is text or an object, got {effect!r}", f"{path}[{index}]")
            except ExprError as exc:
                raise RunError(str(exc), f"{path}[{index}]") from None
            except OverflowError:  # its own text varies by platform
                raise RunError("arithmetic failed: the result is too large", f"{path}[{index}]") from None
            # a contract rule's arithmetic failed: the rule's fault, never the participant's
            except ArithmeticError as exc:
                raise RunError(f"arithmetic failed: {exc}", f"{path}[{index}]") from None
            # values the rule combines that do not fit: the rule's fault, never the participant's
            except TypeError as exc:
                raise RunError(f"could not apply: {exc}", f"{path}[{index}]") from None

    # -- statements ------------------------------------------------------------

    def _statement(self, source: str, vars: dict[str, Any], path: str, index: int) -> None:
        stmt = compile_statement(source)
        scope = self.world.scope(**vars)
        value = stmt.value(scope)
        if stmt.local is not None:
            if stmt.op != "=":
                value = self._combine(stmt.op, scope.root(stmt.local, source), value, source)
            vars[stmt.local] = value
            return
        assert stmt.base is not None
        if len(stmt.steps) == 1:  # `$x.prop op value`, the common shape: the base itself owns the property
            owner = stmt.base(scope)
            if not isinstance(owner, (Entity, Link, PropsView, PhysicsView)):
                raise RunError(_NOT_ASSIGNABLE.format(source=source), f"{path}[{index}]")
            prop = stmt.steps[0][1]
            rest: list[tuple[str, Any]] = []
        else:
            owner, prop, rest = self._owner(stmt, scope, source, f"{path}[{index}]")
        if rest:
            value = self._set_in(attr(owner, prop, source), rest, stmt.op, value, source, prop)
        elif stmt.op != "=":
            value = self._combine(stmt.op, attr(owner, prop, source), value, source)
        if stmt.op == "=" and self.world.watched_writes is not None and isinstance(owner, (Entity, PropsView)):
            self.world.watched_writes.assigned(owner, prop, [key for _, key in rest], value, source)
        if isinstance(owner, Entity):
            self.world.set_prop(owner, prop, value)
        elif isinstance(owner, Link):
            self.world.set_link_field(owner, prop, value, f"{path}[{index}]")
        elif isinstance(owner, PropsView):
            self.world.set_world(prop, value)
        else:
            self.world.set_physics(prop, value)

    def _owner(self, stmt: Statement, scope: Any, source: str, where: str) -> tuple[Any, str, list[tuple[str, Any]]]:
        """The deepest entity / link / $world / $physics on the target path, the property written on it, and
        the element path (resolved keys) inside that property's value."""
        current = stmt.base(scope)  # type: ignore[misc]
        found: tuple[Any, int] | None = None
        resolved: list[tuple[str, Any]] = []
        for position, (kind, step) in enumerate(stmt.steps):
            key = step(scope) if kind == "index" else step
            resolved.append((kind, key))
            if kind == "field" and isinstance(current, (Entity, Link, PropsView, PhysicsView)):
                found = (current, position)
            if position == len(stmt.steps) - 1:
                break
            current = attr(current, key, source) if kind == "field" else self._element(current, key, source)
        if found is None:
            raise RunError(_NOT_ASSIGNABLE.format(source=source), where)
        owner, position = found
        prop = stmt.steps[position][1]
        rest = resolved[position + 1:]
        return owner, prop, rest

    @staticmethod
    def _element(container: Any, key: Any, source: str) -> Any:
        if isinstance(container, list):
            if isinstance(key, bool) or not isinstance(key, int) or not -len(container) <= key < len(container):
                raise ExprError(f"index {key!r} is out of range for a list of {len(container)}", source)
            return container[key]
        if isinstance(container, dict):
            name = str(map_key(key))
            if name not in container:
                raise ExprError(f"no key {name!r} (keys: {', '.join(map(str, list(container)[:12]))})", source)
            return container[name]
        return attr(container, str(key), source)

    def _set_in(self, container: Any, path: list[tuple[str, Any]], op: str, value: Any, source: str,
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
            key = str(map_key(key))
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
            raise ExprError(f"a whole number of {result.bit_length():,} bits is past the limit of {MAX_INT_BITS:,} "
                            "bits",
                            source)
        return check_size(result, source)

    @staticmethod
    def _combine_raw(op: str, current: Any, value: Any, source: str) -> Any:
        if type(current) in _NUMBERS and type(value) in _NUMBERS:  # plain numbers, the common case, first
            if op == "+=":
                return current + value
            if op == "-=":
                return current - value
            if op == "*=":
                return current * value
            if value == 0:
                raise ExprError("division by zero", source)
            return current / value
        if op == "+=" and isinstance(current, list):
            return current + (list(value) if isinstance(value, list) else [value])
        if op == "-=" and isinstance(current, list):
            return _without_one_each(current, value if isinstance(value, list) else [value])
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

    def _keyed(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        ops = select_ops(effect)
        if len(ops) != 1:
            if not ops:
                keys = ", ".join(effect)
                action = family_action_hint(effect)
                hint = get_close_matches(next(iter(effect), ""), list(all_ops()), n=1)
                raise RunError(
                    f"unknown effect with keys ({keys})"
                    + (f" — {action}" if action else f" — did you mean '{hint[0]}'?" if hint else "")
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

    def eval(self, value: Any, vars: dict[str, Any]) -> Any:
        """Evaluate an expression (or a structure of them) with these locals."""
        return resolve(value, self.world.scope(**vars))

    def text(self, template: str | None, vars: dict[str, Any]) -> str:
        """Render a template with these locals."""
        if not template:
            return ""
        return compile_template(template, None).render(self.world.scope(**vars))

    def said(self, template: str | None, vars: dict[str, Any], to: Sequence[str] | None) -> str:
        """Render text sent ``to`` these entity ids (None: everyone), in which only its one recipient's private
        properties may show."""
        viewer = self.world.entities.get(to[0]) if to is not None and len(to) == 1 else None
        return self.text(template, {**vars, "viewer": viewer or EVERYONE})

    _eval = eval
    _text = text

    def _condition(self, value: Any, vars: dict[str, Any]) -> bool:
        # These fields are checked as expressions, even without a $ reference.
        return truthy(compile_expr(value)(self.world.scope(**vars)) if isinstance(value, str) else value)

    def _op_if(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        branch = "then" if self._condition(effect["if"], vars) else "else"
        self.run(effect.get(branch) or [], vars, f"{where}.{branch}")

    def _op_each(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        name = effect.get("as") or "it"
        items = each_items(self._eval(effect["each"], vars), self.world, where)
        where_expr = effect.get("where")
        if effect.get("sync"):
            def run_item(position: int, item: Any) -> bool:
                inner = {**vars, name: item, "i": position}
                if where_expr is not None and not self._condition(where_expr, inner):
                    return False
                self.run(effect.get("do") or [], inner, f"{where}.do")
                return True

            run_synced(self.world, items, run_item, where)
            return
        from ..runtime.diagnosis import LoopWrites  # run_diagnosis reads actions, which run effects

        watch = LoopWrites.start(self.world, effect, where)
        removed = removed_since(items)
        try:
            for position, item in enumerate(items):
                if removed(position):
                    continue
                inner = {**vars, name: item, "i": position}
                if where_expr is not None and not self._condition(where_expr, inner):
                    continue
                if watch is not None:
                    watch.item, watch.position = item, position
                self.run(effect.get("do") or [], inner, f"{where}.do")
                # Locals assigned in the body (running totals, a best-so-far) stay assigned after it;
                # only the loop's own names are scoped to it.
                vars.update((key, value) for key, value in inner.items() if key not in (name, "i"))
        finally:
            if watch is not None:
                self.world.watched_writes = None

    def _op_create(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        count = self._eval(effect.get("count", 1), vars)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RunError(f"count must be a whole number ≥ 0, got {count!r}", where)
        if count > MAX_CREATE:
            raise RunError(f"count {count:,} is more than the limit of {MAX_CREATE:,} entities per create", where)
        made: list[Entity] = []
        for n in range(count):
            inner = {**vars, "i": n + 1}
            entity_id = self._text(effect.get("id"), inner) or None
            name = self._text(effect.get("name"), inner) or None
            at = self._eval(effect.get("at"), inner)
            made.append(self.world.create(effect["create"], entity_id, name, effect.get("props") or {},
                                          at, self.world.scope(**inner), where))
        if effect.get("as"):
            vars[effect["as"]] = made[0] if count == 1 else made

    def _op_remove(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        value = self._eval(effect["remove"], vars)
        for item in value if isinstance(value, list) else [value]:
            self.world.remove(_entity(item, self.world, where), where)

    def _op_transfer(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        prop = effect["transfer"]
        source = _entity(self._eval(effect.get("from"), vars), self.world, where, "a `from` entity")
        target = _entity(self._eval(effect.get("to"), vars), self.world, where, "a `to` entity")
        amount = self._eval(effect.get("amount"), vars)
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
            raise RunError(f"transfer amount must be a number ≥ 0, got {amount!r}", where)
        into = effect.get("into") or prop
        have = _amount_held(source, prop, where)
        held = _amount_held(target, into, where)
        # A transfer moves value; it never creates or destroys it. Limits that would clamp
        # either side refuse the transfer instead, never telling an amount hidden from the actor (world.refusal).
        low, high = self.world.prop_spec(source, prop).min, self.world.prop_spec(target, into).max
        instead = "That transfer cannot be made."
        if have < amount:
            raise self.world.refusal(source, prop, f"{source.name} has only {format_value(have)} {prop}; "
                                                   f"{format_value(amount)} is needed.", instead)
        if low is not None and have - amount < low:
            raise self.world.refusal(source, prop, f"{source.name} cannot go below {format_value(low)} {prop}; "
                                                   f"at most {format_value(have - low)} can be given.", instead)
        if high is not None and held + amount > high:
            raise self.world.refusal(target, into, f"{target.name} can hold at most {format_value(high)} {into}; "
                                                   f"at most {format_value(max(0, high - held))} more fits.", instead)
        self.world.set_prop(source, prop, have - amount)
        self.world.set_prop(target, into, _amount_held(target, into, where) + amount)

    def _op_link(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        value = self._eval(effect["value"], vars) if "value" in effect else None
        fields = effect.get("props") or {}
        if not isinstance(fields, dict):
            raise RunError(f"`props` is an object of link fields, got {fields!r}", where)
        self.world.link(effect["link"], self._eval(effect.get("from"), vars), self._eval(effect.get("to"), vars),
                        value, where, {name: self._eval(raw, vars) for name, raw in fields.items()})

    def _op_unlink(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        self.world.unlink(effect["unlink"], self._eval(effect.get("from"), vars), self._eval(effect.get("to"), vars),
                          where)

    def _op_move(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        entity = _entity(self._eval(effect["move"], vars), self.world, where)
        self.world.move(entity, self._eval(effect.get("to"), vars), where)

    def _op_post(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        if self._dropped(effect, vars, where):
            return
        if "author" in effect:
            author_value = self._eval(effect["author"], vars)
            author = _entity(author_value, self.world, where).id if author_value is not None else None
        else:
            actor = vars.get("actor")
            author = actor.id if isinstance(actor, Entity) else None
        to = _to_ids(self._eval(effect.get("to"), vars), where) if "to" in effect else None
        read = {**vars, "viewer": self._entry_reader(effect["post"], author, to)}
        fields = {k: _plain_value(self._eval(v, read)) for k, v in effect.items() if k not in POST_KEYS}
        send(self.world, self._eval(effect["delay"], vars) if "delay" in effect else None,
             {"kind": "post", "record": effect["post"], "fields": fields, "author": author,
              "to": list(to) if to is not None else None}, where)

    def _entry_reader(self, record: str, author: str | None, to: Sequence[str] | None) -> Any:
        """Who an entry is shown to, as its fields are worked out: its one reader (its author and whom it is sent `to`),
        whose own private properties it may carry; everyone when several read it; None — game logic reading the true
        state — when the record's `visible` rule decides."""
        if to is not None:
            readers = {*to, *([author] if author is not None else [])}
            return self.world.entities.get(next(iter(readers))) if len(readers) == 1 else EVERYONE
        spec = self.world.contract.records.get(record)
        return EVERYONE if spec is not None and spec.visible == "all" else None

    def _op_emit(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        if self._dropped(effect, vars, where):
            return
        to = _to_ids(self._eval(effect.get("to"), vars), where) if "to" in effect else None
        actor = vars.get("actor")
        data = self._eval(effect.get("data") or {}, vars)
        send(self.world, self._eval(effect["delay"], vars) if "delay" in effect else None,
             {"kind": "emit", "event": str(effect["emit"]), "text": self.said(effect.get("say"), vars, to),
              "actor": actor.id if isinstance(actor, Entity) else None, "to": list(to) if to is not None else None,
              "data": {k: _plain_value(v) for k, v in data.items()}}, where)

    def _dropped(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> bool:
        """Roll the effect's ``drop`` chance (a lossy channel): True when the message is lost."""
        return "drop" in effect and dropped(self.world, self._eval(effect["drop"], vars), f"{where}.drop")

    def _op_fail(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        actor = vars.get("actor")  # the refusal is text the actor is shown
        text = self.text(effect["fail"], {**vars, "viewer": actor} if isinstance(actor, Entity) else vars)
        raise Abort(text or "That is not possible right now.")

    def _op_end(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        winner = self._eval(effect.get("winner"), vars) if "winner" in effect else None
        self.world.request_end(str(effect["end"]), _plain_value(winner), self._text(effect.get("say"), vars))

    def _op_after(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        delay = self._eval(effect["after"], vars)
        world = self.world
        if isinstance(delay, bool) or not isinstance(delay, int) or delay < 1:
            raise RunError(f"`after` needs a whole number of rounds ≥ 1, got {delay!r}", where)
        effects = one_or_many(effect.get("do")) or []
        captured = vars
        if all(isinstance(item, str) for item in effects):
            roots = capture_roots(tuple(effects))
        else:
            try:
                roots = structured_capture_roots(json.dumps(effects, sort_keys=True))
            except (TypeError, ValueError, RecursionError):
                roots = None
        if roots is not None and not roots.intersection(world.contract.defs):
            captured = {name: value for name, value in vars.items() if name in roots}
        world.schedule(world.round + delay, effects, captured, f"{where}.do")

    def _op_wake(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        why = self._text(effect.get("why"), vars) or "You were asked to act."
        now = truthy(self._eval(effect["now"], vars)) if "now" in effect else False
        for entity_id in _to_ids(self._eval(effect["wake"], vars), where) or ():
            if now:
                self.world.request_reaction(entity_id, why, effect.get("actions"))
            else:
                self.world.request_wake(entity_id, why)

    def _op_call(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        name = effect["call"]
        defs = self.world.contract.defs
        spec = defs.get(name)
        if spec is None or spec.do is None:
            raise RunError(f"'{name}' is not a def with `do` (effect defs: "
                           f"{', '.join(n for n, d in defs.items() if d.do is not None) or 'none'})", where)
        given = effect.get("with") or {}
        if set(given) != set(spec.args):
            raise RunError(f"def '{name}' takes arguments {spec.args}, got {sorted(given)}", where)
        depth = getattr(self, "_depth", 0)
        if depth >= 16:
            raise RunError(f"def '{name}' calls defs too deeply (recursion?)", where)
        inner = {key: self._eval(value, vars) for key, value in given.items()}
        self._depth = depth + 1
        try:
            self.run(spec.do, inner, f"defs.{name}.do")
        finally:
            self._depth = depth

    def _op_chance(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        from .chance import run_chance

        run_chance(self, effect, vars, where)

    def _op_repeat(self, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        limit = self._eval(effect["repeat"], vars)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= REPEAT_CEILING:
            raise RunError(f"`repeat` needs a whole-number limit from 0 to {REPEAT_CEILING}, got {limit!r}", where)
        condition = effect.get("while")
        for _ in range(limit):
            if condition is not None and not self._condition(condition, vars):
                return
            self.run(effect.get("do") or [], vars, f"{where}.do")
        if limit and condition is not None and self._condition(condition, vars):
            raise RunError(f"`repeat` reached its limit of {limit} while `{condition}` still holds", where)


def _without_one_each(items: list[Any], drop: list[Any]) -> list[Any]:
    """``items`` with one copy removed for each item of ``drop`` that is there (``[1, 2, 2] -= 2`` leaves
    ``[1, 2]``), compared as ``==`` compares: entities by id, maps and lists by content."""
    kept = list(items)
    for item in drop:
        found = next((at for at, held in enumerate(kept) if _eq(held, item)), None)
        if found is not None:
            del kept[found]
    return kept


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


def select_ops(effect: dict[str, Any]) -> list[str]:
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


def all_ops() -> dict[str, tuple[str, ...]]:
    """Every effect operation and the keys it takes: the core ones, then registered native ops."""
    return {**EFFECT_OPS, **{name: spec.keys for name, spec in OPS.items()}}


def registered_op(name: str) -> OpSpec | None:
    return OPS.get(name)



from .. import mechanisms as _mechanisms  # noqa: E402,F401  (registers native ops and mechanism kinds)

assert not set(EFFECT_OPS) & set(OPS), "a registered op shadows a core effect"
