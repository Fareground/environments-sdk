"""Checking effect lists: assignment statements and operation objects."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Set

from . import contract as C
from .chance import check_chance
from .check_params import check_entity_literals
from .check_state import check_delivery, check_link_fields
from .effects import (
    POST_KEYS,
    REPEAT_CEILING,
    RESERVED_ROOTS,
    all_ops,
    registered_op,
    select_ops,
    statement_parts,
)
from .expr import ExprError, is_expr
from .registry import renamed_op_hint

if TYPE_CHECKING:
    from .check import _Checker
    from .check_roots import Types

__all__ = ["EffectChecks"]


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
            base, steps, local, _, right = statement_parts(source)
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

    def _keyed(self: "_Checker", effect: Dict[str, Any], path: str, roots: Set[str], types: Types,  # type: ignore[misc]
               params: Optional[Mapping[str, C.ParamSpec]]) -> None:
        known = all_ops()
        ops = select_ops(effect)
        if len(ops) != 1:
            keys = ", ".join(effect) or "none"
            hint = renamed_op_hint(effect) or self._suggest(next(iter(effect), ""), known)
            self.error(path, f"an operation object names exactly one of: {', '.join(known)} (got keys {keys})", hint)
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
            self.expr(effect["if"], f"{path}.if", roots, types, params)
            roots |= self.effects(effect.get("then", []), f"{path}.then", roots, types, params)
            roots |= self.effects(effect.get("else", []), f"{path}.else", roots, types, params)
        elif op == "each":
            v("each")
            name = effect.get("as") or "it"
            inner = roots | {name, "i"}
            inner_types = dict(types)
            source = effect["each"]
            if isinstance(source, str) and not is_expr(source):
                if self._type(source, f"{path}.each"):
                    inner_types[name] = {source}
            self.expr(effect.get("where"), f"{path}.where", inner, inner_types, params)
            self.effects(effect.get("do", []), f"{path}.do", inner, inner_types, params)
        elif op == "create":
            type_name = effect["create"]
            if self._type(type_name, f"{path}.create"):
                for prop, raw in (effect.get("props") or {}).items():
                    if prop not in self.type_props[type_name]:
                        self.error(f"{path}.props.{prop}", f"'{type_name}' has no property '{prop}'",
                                   self._suggest(prop, self.type_props[type_name]))
                    self.value(raw, f"{path}.props.{prop}", roots | {"i"}, types, params)
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
            check_delivery(self, op, effect, path)
        elif op == "emit":
            self.template(effect.get("say"), f"{path}.say", None, roots, types, params)
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
            self.effects(effect.get("do", []), f"{path}.do", roots, types, params)
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
            if isinstance(limit, int) and not isinstance(limit, bool) and not 1 <= limit <= REPEAT_CEILING:
                self.error(f"{path}.repeat", f"is {limit:,}; a repeat limit runs from 1 to {REPEAT_CEILING:,}",
                           "use a smaller limit; a loop that needs more never settles")
            self.expr(effect.get("while"), f"{path}.while", roots, types, params)
            roots |= self.effects(effect.get("do", []), f"{path}.do", roots, types, params)
        elif op == "chance":
            roots |= check_chance(self, effect, path, roots, types, params)
