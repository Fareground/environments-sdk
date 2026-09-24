"""Checking effect lists: assignment statements and operation objects."""
from __future__ import annotations

import math
import re
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Set, Tuple

from .. import contract as C
from ..effects.chance import check_chance
from .params import check_entity_literals
from .roots import merge_types
from .state import check_delivery, check_link_fields
from ..effects.runner import POST_KEYS, REPEAT_CEILING, all_ops, registered_op, select_ops
from ..effects.statements import RESERVED_ROOTS, statement_parts
from ..expr import ExprError, compile_expr, is_expr
from ..registry import family_action_hint

if TYPE_CHECKING:
    from . import _Checker
    from .roots import Types

__all__ = ["EffectChecks"]

#: The kinds of value an assignment's text can make plain, as its messages name them.
_KIND_WORDS = {"number": "a number", "int": "a whole number", "bool": "true or false", "text": "text"}


class EffectChecks:
    """Effect lists and the operations in them (mixed into the contract checker)."""

    def effects(self: "_Checker", effects: Any, path: str, roots: Set[str], types: Types,  # type: ignore[misc]
                params: Optional[Mapping[str, C.ParamSpec]] = None) -> Set[str]:
        """Check an effect list; returns the roots available after it (locals included)."""
        roots = set(roots)
        if isinstance(effects, (str, dict)):
            effects = [effects]
        if not isinstance(effects, list):
            self.error(path, "must be a list of effects, or one effect", 'e.g. ["$actor.coins += 1"]')
            return roots
        for index, effect in enumerate(effects):
            where = f"{path}[{index}]"
            if isinstance(effect, str):
                self._statement(effect, where, roots, types, params)
            elif isinstance(effect, dict):
                self._keyed(effect, where, roots, types, params)
            else:
                self.error(where, "an effect is an assignment text or an operation object")
        return roots

    def _statement(self: "_Checker", source: str, path: str, roots: Set[str], types: Types,  # type: ignore[misc]
                   params: Optional[Mapping[str, C.ParamSpec]]) -> None:
        try:
            base, steps, local, op, right = statement_parts(source)
        except ExprError as exc:
            self.error(path, exc.detail, "write `$actor.cash -= 5`, `$world.board[$i][$j] = x` or `$total = 3`")
            return
        self.expr(right, path, roots, types, params)
        if re.search(r"'[^']*\{\$[^']*'|\"[^\"]*\{\$[^\"]*\"", right):
            self.warn(path, "stores `{$...}` literally: placeholders fill in only in templates (outcome, say, show, text)",
                      "build the text as an expression, e.g. `$text($params.n) + ': ' + $hint`, or store the values and "
                      "format them in a view's show")
        for kind, step in steps:
            if kind == "index":
                self.expr(step, path, roots, types, params)
        if local is not None:
            if local in RESERVED_ROOTS:
                self.error(path, f"${local} is a reserved name, so a local cannot be called that",
                           f"rename the local (e.g. ${local}_value), or assign to one of its fields (${local}.x = …)")
            elif op != "=" and local not in roots and not (local in self.c.defs and not self.c.defs[local].args):
                self.error(path, f"${local} has no initial value for `{op}`",
                           f"initialize it with `${local} = …` before updating it, or use `=` to set its value")
            alias = re.fullmatch(r"\$([A-Za-z_][A-Za-z0-9_]*)", right.strip()) if op == "=" else None
            hint = types.get(alias.group(1)) if alias is not None else None
            if hint is None:
                types.pop(local, None)
            else:
                types[local] = set(hint)
            roots.add(local)
            return
        assert base is not None
        simple = re.fullmatch(r"\$([A-Za-z_][A-Za-z0-9_]*)", base)
        if simple is None:
            self.expr(base, path, roots, types, params)
            return
        root = simple.group(1)
        leading: List[str] = []  # the property path up to the first element index
        for kind, step in steps:
            if kind != "field":
                break
            leading.append(step)
        fields = tuple(leading)
        if root not in roots:
            self.error(path, f"${root} is not available here", f"available: {', '.join('$' + r for r in sorted(roots))}")
            return
        if root in ("inputs", "metrics", "series", "clock", "round", "stage", "arm"):
            self.error(path, f"${root} is read-only", "assign to an entity's property, $world.x or $physics.x")
            return
        self._chain((root, *fields), path, types, params or {}, source)
        if len(fields) == len(steps):  # the property itself, not an element of it
            self._assigned_kind((root, *fields), op, right, path, types, params or {}, source)

    def _reaction_actions(self: "_Checker", effect: Dict[str, Any], path: str) -> None:  # type: ignore[misc]
        """A reaction (`wake` with `now`) names the actions it offers; without them it gets every action of the
        stage it happens in — the very action that woke it included — so it could act out of turn."""
        actions = effect.get("actions")
        if actions is None:
            if "now" in effect and effect["now"] is not False:
                self.warn(path, "this reaction is offered every action of the stage it happens in (the one that "
                                "woke it too), so it can act out of turn",
                          'name the answers it may give, e.g. "actions": ["accept", "reject"]')
            return
        if "now" not in effect:
            self.error(f"{path}.actions", "`actions` names what a reaction (`wake` with `now`) is offered",
                       'add "now": true, or remove `actions` (a later wake takes the stage\'s actions)')
        if not isinstance(actions, list) or not all(isinstance(name, str) for name in actions):
            self.error(f"{path}.actions", "must be a list of action names", 'e.g. "actions": ["accept", "reject"]')
            return
        for name in actions:
            if name not in self.c.actions:
                self.error(f"{path}.actions", f"'{name}' is not a declared action",
                           self._suggest(name, self.c.actions) or f"actions: {', '.join(self.c.actions) or 'none'}")

    def _assigned_kind(self: "_Checker", target: Tuple[str, ...], op: str, right: str, path: str,  # type: ignore[misc]
                       types: Types, params: Mapping[str, C.ParamSpec], source: str) -> None:
        """A value whose kind the text makes plain (a literal, or a property or argument read on its own) assigned to
        a property declared as another kind: it would fail every time the rule runs."""
        declared, given = self._spec_for(target, types, params), self._value_kind(right, types, params)
        if declared is None or given is None:
            return
        values, kind = declared
        got, word = given
        field = "$" + ".".join(target)
        if values and got == "text" and word is not None and word not in values:
            hint = get_close_matches(word, [str(v) for v in values], n=1)
            self.error(path, f"{field} is one of {', '.join(map(str, values))}; '{word}' is not",
                       (f"did you mean '{hint[0]}'?" if hint else "assign one of its values") + f" — in `{source}`")
        elif kind in ("number", "int") and got in ("text", "bool") \
                or op == "=" and (kind == "bool" and got != "bool" or kind == "text" and got in ("number", "bool")):
            self.error(path, f"{field} is declared as {kind}, but this assigns {_KIND_WORDS[got]}",
                       f"assign {_KIND_WORDS[kind]}, or declare the property with the type it holds — in `{source}`")

    def _value_kind(self: "_Checker", right: str, types: Types,  # type: ignore[misc]
                    params: Mapping[str, C.ParamSpec]) -> Optional[Tuple[str, Optional[str]]]:
        """``(kind, literal text)`` of a value that is a literal or one property or argument read on its own; None
        when the text does not make its kind plain."""
        text = right.strip()
        quoted = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", text)
        if quoted:
            return "text", quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
        if text in ("true", "false"):
            return "bool", None
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return "number", None
        chain = re.fullmatch(r"\$([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)", text)
        known = self._spec_for(tuple(chain.group(1).split(".")), types, params) if chain else None
        if known is None or known[1] not in _KIND_WORDS:
            return None
        return ("number" if known[1] == "int" else known[1]), None

    def _shadowed_it(self: "_Checker", type_name: str, path: str, types: Types) -> None:  # type: ignore[misc]
        """`$it` in a `create`'s props, where an enclosing loop also binds it: there it means the new entity, which is
        rarely what the author meant."""
        outer = "/".join(sorted(types.get("it") or ())) or "enclosing"
        self.error(path, f"`$it` here is the new {type_name} being created, not the {outer} item of the enclosing loop",
                   'to read the loop\'s item, name it: `"as": "src"` on the `each`, then `$src.id` here (in `create` '
                   f"props `$it` always means the new {type_name}, so its earlier props read as `$it.<prop>`)")

    def _keyed(self: "_Checker", effect: Dict[str, Any], path: str, roots: Set[str], types: Types,  # type: ignore[misc]
               params: Optional[Mapping[str, C.ParamSpec]]) -> None:
        known = all_ops()
        ops = select_ops(effect)
        if len(ops) > 1:
            self.error(path, f"an operation object names exactly one operation; this one names {', '.join(ops)}",
                       "split it into one object per operation, in the order they should run")
            return
        if not ops:
            key = next(iter(effect), "")
            hint = family_action_hint(effect) or self._suggest(key, known) or \
                'write an assignment as text ("$actor.price = 3", "$actor.cash -= 5"); guide("effects") lists the operations'
            self.error(path, f"`{key or '{}'}` is not an effect operation (got keys {', '.join(effect) or 'none'})", hint)
            return
        op = ops[0]
        allowed = set(known[op])
        native = registered_op(op)
        if native is not None and native.select is not None:  # a family op: check the action it names
            native, problem = native.select(effect, self.c.mechanisms or {})
            if problem is not None:
                self.error(f"{path}{problem[0]}", problem[1], problem[2])
                return
            assert native is not None
            op = native.name
        if native is not None:
            allowed = set(native.keys)
            for key in effect:
                if key not in allowed:
                    self.error(f"{path}.{key}", f"'{key}' is not part of `{op}`",
                               self._suggest(key, allowed) or f"`{op}` takes: {', '.join(sorted(allowed))}")
            for key in native.required:
                if key not in effect:
                    self.error(path, f"`{op}` needs `{key}`")
            for key, raw in effect.items():
                if key in native.literal or key in native.binds:
                    continue
                if key in native.templates:
                    self.template(raw, f"{path}.{key}", None, roots, types, params)
                else:
                    self.value(raw, f"{path}.{key}", roots, types, params)
            for key in native.binds:
                if key not in effect:
                    continue
                bound = effect[key]
                if not isinstance(bound, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bound):
                    self.error(f"{path}.{key}", f"`{key}` names a local, like \"picked\"", "use a plain name")
                elif bound in RESERVED_ROOTS:
                    self.error(f"{path}.{key}", f"'{bound}' is a built-in root", "choose another name")
                else:
                    roots.add(bound)
                    types.pop(bound, None)
            if native.check is not None:
                try:
                    findings = list(native.check(self, effect, path))
                except Exception as exc:  # a mechanism's check hook crashed: report it and keep checking the rest
                    findings = [(path, f"the `{op}` check failed: {type(exc).__name__}: {exc}",
                                 "this is a bug in the mechanism; report it with the contract")]
                for issue_path, message, fix in findings:
                    self.error(issue_path, message, fix)
            return
        if op != "post":
            for key in effect:
                if key not in allowed:
                    self.error(f"{path}.{key}", f"'{key}' is not part of `{op}`",
                               self._suggest(key, allowed) or f"`{op}` takes: {', '.join(sorted(allowed))}")
        check_entity_literals(self, op, effect, path)
        v = lambda key, r=roots: self.value(effect.get(key), f"{path}.{key}", r, types, params)
        if op == "if":
            self.condition(effect["if"], f"{path}.if", roots, types, params)
            then_types, else_types = dict(types), dict(types)
            then_roots = self.effects(effect.get("then", []), f"{path}.then", roots, then_types, params)
            else_roots = self.effects(effect.get("else", []), f"{path}.else", roots, else_types, params)
            merge_types(types, then_types, else_types)
            roots |= then_roots | else_roots
        elif op == "each":
            v("each")
            name = effect.get("as") or "it"
            inner = roots | {name, "i"}
            inner_types = dict(types)
            # The loop shadows its binding even when the collection's item type
            # cannot be inferred. Never reuse the enclosing entity's type.
            inner_types.pop(name, None)
            source = effect["each"]
            if isinstance(source, str) and not is_expr(source):
                if self._type(source, f"{path}.each"):
                    inner_types[name] = {source}
            self.condition(effect.get("where"), f"{path}.where", inner, inner_types, params)
            self.effects(effect.get("do", []), f"{path}.do", inner, inner_types, params)
            for binding in (name, "i"):
                inner_types.pop(binding, None)
                if binding in types:
                    inner_types[binding] = types[binding]
            merge_types(types, dict(types), inner_types)
        elif op == "create":
            type_name = effect["create"]
            if self._type(type_name, f"{path}.create"):
                for prop, raw in (effect.get("props") or {}).items():
                    if prop not in self.type_props[type_name]:
                        self.error(f"{path}.props.{prop}", f"'{type_name}' has no property '{prop}'",
                                   self._suggest(prop, self.type_props[type_name]))
                    if "it" in roots and _reads_it(raw):
                        self._shadowed_it(type_name, f"{path}.props.{prop}", types)
                        continue
                    self.value(raw, f"{path}.props.{prop}", roots | {"i", "it"},
                               {**types, "it": {type_name}}, params)
            v("count")
            count = effect.get("count")
            if isinstance(count, int) and not isinstance(count, bool) and count > C.MAX_CREATE:
                self.error(f"{path}.count", f"is {count:,}, above the ceiling of {C.MAX_CREATE:,}",
                           "create fewer entities at once")
            v("at")
            for key in ("id", "name"):
                self.template(effect.get(key), f"{path}.{key}", None, roots | {"i"}, types, params)
            if effect.get("as"):
                roots.add(effect["as"])
                types[effect["as"]] = {type_name}
        elif op in ("remove", "move", "wake"):
            v(op)
            if op == "move":
                v("to")
            if op == "wake":
                self.template(effect.get("why"), f"{path}.why", None, roots, types, params)
                v("in")
                v("now")
                if "in" in effect and "now" in effect:
                    self.error(path, "`wake` takes `now` or `in`, not both")
                self._reaction_actions(effect, path)
                if "in" in effect and self.c.clock.mode != "continuous":
                    self.error(f"{path}.in", "`in` needs a continuous clock", "set clock.mode to continuous")
                v("drop")
                check_delivery(self, op, effect, path)
        elif op == "transfer":
            prop = effect["transfer"]
            if not any(prop in props for props in self.type_props.values()):
                self.error(f"{path}.transfer", f"no type has a property '{prop}'")
            into = effect.get("into")
            if into is not None and not any(into in props for props in self.type_props.values()):
                self.error(f"{path}.into", f"no type has a property '{into}'")
            for key in ("from", "to", "amount"):
                if key not in effect:
                    self.error(path, f"`transfer` needs `{key}`")
                v(key)
        elif op in ("link", "unlink"):
            if effect[op] not in self.c.relations:
                self.error(f"{path}.{op}", f"'{effect[op]}' is not a declared relation",
                           self._suggest(effect[op], self.c.relations) or "declare it under `relations`")
            for key in ("from", "to"):
                if key not in effect:
                    self.error(path, f"`{op}` needs `{key}`")
                v(key)
            if op == "link":
                v("value")
                check_link_fields(self, effect[op], effect.get("props", {}), f"{path}.props", roots, types, params)
        elif op == "post":
            record = effect["post"]
            spec = self.c.records.get(record)
            if spec is None:
                self.error(f"{path}.post", f"'{record}' is not a declared record",
                           self._suggest(record, self.c.records) or "declare it under `records`")
            else:
                for key in effect:
                    if key not in POST_KEYS and key not in spec.fields:
                        self.error(f"{path}.{key}", f"record '{record}' has no field '{key}'",
                                   self._suggest(key, spec.fields) or f"fields: {', '.join(spec.fields)}")
            for key, raw in effect.items():
                if key != "post":
                    self.value(raw, f"{path}.{key}", roots, types, params)
            if spec is not None and spec.visible == "all" and "to" not in effect:  # an entry every agent reads
                for key in spec.fields:
                    self._shared_text(effect.get(key), f"{path}.{key}", types, params)
            check_delivery(self, op, effect, path)
        elif op == "emit":
            self.template(effect.get("say"), f"{path}.say", None, roots, types, params)
            if "to" not in effect:
                self._shared_text(effect.get("say"), f"{path}.say", types, params)
            v("to")
            v("data")
            v("delay")
            v("drop")
            check_delivery(self, op, effect, path)
        elif op == "fail":
            self.template(effect["fail"], f"{path}.fail", None, roots, types, params)
        elif op == "end":
            v("winner")
            self.template(effect.get("say"), f"{path}.say", None, roots, types, params)
        elif op == "after":
            v("after")
            delay = effect["after"]
            if not is_expr(delay):
                continuous = self.c.clock.mode == "continuous"
                if continuous:
                    try:
                        valid = not isinstance(delay, bool) and isinstance(delay, (int, float)) and math.isfinite(delay) and delay > 0
                    except OverflowError:
                        valid = False
                else:
                    valid = not isinstance(delay, bool) and isinstance(delay, int) and delay >= 1
                if not valid:
                    required = "a finite positive time" if continuous else "a whole number of rounds ≥ 1"
                    self.error(f"{path}.after", f"`after` needs {required}, got {delay!r}",
                               "use a positive delay; for immediate effects, put the `do` effects here without `after`")
            self.effects(effect.get("do", []), f"{path}.do", roots, dict(types), params)
        elif op == "block":
            block = self.c.blocks.get(effect["block"])
            given = effect.get("with") or {}
            if block is None:
                self.error(f"{path}.block", f"'{effect['block']}' is not a declared block",
                           self._suggest(effect["block"], self.c.blocks) or "declare it under `blocks`")
            elif not isinstance(given, dict):
                self.error(f"{path}.with", "`with` is an object of arguments")
            else:
                for name in sorted(set(block.args) - set(given)):
                    self.error(f"{path}.with", f"missing argument '{name}' for block '{effect['block']}'")
                for name in sorted(set(given) - set(block.args)):
                    self.error(f"{path}.with.{name}", f"block '{effect['block']}' has no argument '{name}'",
                               f"arguments: {', '.join(block.args) or 'none'}")
                for name, raw in given.items():
                    self.value(raw, f"{path}.with.{name}", roots, types, params)
        elif op == "repeat":
            v("repeat")
            limit = effect.get("repeat")
            if isinstance(limit, int) and not isinstance(limit, bool) and not 0 <= limit <= REPEAT_CEILING:
                fix = "use 0 to run nothing" if limit < 0 else "use a smaller limit; a loop that needs more never settles"
                self.error(f"{path}.repeat", f"is {limit:,}; a repeat limit runs from 0 (run nothing) to {REPEAT_CEILING:,}", fix)
            self.condition(effect.get("while"), f"{path}.while", roots, types, params)
            body_types = dict(types)
            roots |= self.effects(effect.get("do", []), f"{path}.do", roots, body_types, params)
            if effect.get("while") is None:
                merge_types(types, body_types)
            else:
                merge_types(types, dict(types), body_types)
        elif op == "chance":
            roots |= check_chance(self, effect, path, roots, types, params)


def _reads_it(raw: Any) -> bool:
    """Whether a value (an expression, or a list or object of them) reads `$it`."""
    if isinstance(raw, str):
        try:
            return is_expr(raw) and "it" in compile_expr(raw).roots
        except ExprError:
            return False  # reported where the value is checked
    if isinstance(raw, (list, dict)):
        return any(_reads_it(item) for item in (raw.values() if isinstance(raw, dict) else raw))
    return False
