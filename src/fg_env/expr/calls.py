"""Built-in expression functions: the registry, the :class:`Call` a function reads its arguments from, and the
equality guard that lets collection functions skip items a condition certainly rules out."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Mapping, Optional, Sequence, Tuple

from .base import _BUDGET, ExprError, charge, truthy
from .scope import Scope
from .values import _ENTITY_FIELDS, _Entity, _describe, _entity_id, _number

__all__ = ["Evaluator", "EqualityGuard", "Call", "FunctionSpec", "FUNCTIONS", "function"]


#: Names authors reach for that are not functions, and the one way the language says each. A near spelling would
#: suggest something else ($mean → $median, $bottom → $book, $log_base → $log_loss).
_SAY_INSTEAD = {
    "mean": "$avg", "bottom": "$sort", "log_base": "$log(x, base)", "pow": "x ** y", "e": "$exp(1)",
    "lerp": "a + (b - a) * t", "hypot": "$sqrt(x ** 2 + y ** 2)", "char_at": "$chars(text)[i]",
    "count_of": "$count(list, $it == value)", "enumerate": "$map(list, [$i, $it])", "is_subset": "$all(a, $it in b)",
    "argmax": "$index(xs, $max(xs))", "argmin": "$index(xs, $min(xs))", "chance_for": "$random_for(key) < p",
    "realized_vol": "$market_stats(prices).sigma", "vol_clustering": "$market_stats(prices).acf_abs",
    "volume_vol_corr": "$market_stats(prices, volumes).vol_volume_corr", "lmsr_prices": "$softmax(q, b)",
    "lmsr_cost": "b * $logsumexp($map(q, $it / b))", "cpmm_prices": "$amm(name).prices",
}


def suggest_function(name: str, candidates: Sequence[str]) -> Optional[str]:
    """What to write instead of the unknown function ``name``: the one way the language says it, or the closest
    known name (a built-in or a def among ``candidates``), with its ``$``."""
    if name in _SAY_INSTEAD:
        return _SAY_INSTEAD[name]
    matches = get_close_matches(name, candidates, n=1)
    return f"${matches[0]}" if matches else None


Evaluator = Callable[[Scope], Any]

#: An :class:`EqualityGuard` key that could not be worked out up front.
_NO_KEY = object()
#: The attribute holding an entity field whose name differs from it.
_FIELD_ATTRS = {"at": "location_id", "type": "entity_type"}


@dataclass(frozen=True)
class EqualityGuard:
    """``$it.field == value`` opening a condition (alone or as the first of an ``and``), where ``value``
    reads no item (no ``$it`` or ``$i``) and calls no function. That value is the same for
    every item and draws nothing, so an entity whose field differs makes the whole condition false: it
    can be skipped without evaluating the condition, with the same result, errors and random draws."""

    field: str
    value: Evaluator
    roots: FrozenSet[str]

    def key(self, scope: Scope) -> Any:
        """The value every item is compared with, or ``_NO_KEY`` when it cannot be known up front."""
        if not all(root in scope.vars for root in self.roots):
            return _NO_KEY  # a missing root may be a def, which is evaluated per item
        try:
            return _entity_id(self.value(scope))
        except Exception:  # evaluating per item raises the same way; nothing is skipped
            return _NO_KEY

    def rules_out(self, item: Any, key: Any) -> bool:
        """True when ``item`` is an entity whose field is certainly not ``key``."""
        if type(item) is not _Entity:
            return False
        field = self.field
        if field in _ENTITY_FIELDS:  # text, a flag or null: each its own id
            return bool(getattr(item, _FIELD_ATTRS.get(field, field)) != key)
        if field in item.properties:
            value = item.properties[field]
        else:
            return False  # evaluating it reports the missing property
        return bool(_entity_id(value) != key)


class Call:
    """Arguments of one function call: evaluate eagerly or per item (lazily)."""

    __slots__ = ("name", "nodes", "scope", "source")

    def __init__(self, name: str, nodes: Sequence[Evaluator], scope: Scope, source: str):
        self.name = name
        self.nodes = nodes
        self.scope = scope
        self.source = source

    def __len__(self) -> int:
        return len(self.nodes)

    def arg(self, index: int, default: Any = None) -> Any:
        if index >= len(self.nodes):
            return default
        return self.nodes[index](self.scope)

    def each(self, index: int, item: Any, position: int = 0) -> Any:
        """Evaluate argument ``index`` with ``$it`` bound to ``item`` (the enclosing ``$it`` is ``$outer``)."""
        # Not charged: per-item arguments run over a collection whose length was charged.
        scope = self.scope
        vars = scope.vars  # the child scope built in one step: every collection function runs this per item
        return self.nodes[index](Scope({**vars, "it": item, "i": position, "outer": vars.get("it")}, scope.world))

    def collection(self, index: int = 0) -> List[Any]:
        return self._items(self.arg(index), copy=True)  # type: ignore[return-value]

    def members(self, index: int = 0) -> Sequence[Any]:
        """Like :meth:`collection`, but a type's living entities come as the world's shared list: read only."""
        return self._items(self.arg(index), copy=False)

    def _items(self, value: Any, copy: bool) -> Sequence[Any]:
        items: Sequence[Any]
        if isinstance(value, str):
            if not self.scope.world.is_type(value):
                raise ExprError(
                    f"${self.name}: '{value}' is not an entity type (pass a type name or a list)",
                    self.source,
                )
            items = list(self.scope.world.alive_of(value)) if copy else self.scope.world.alive_of(value)
        elif value is None:
            return []
        elif isinstance(value, (list, tuple)):  # before Mapping: an ABC check costs far more
            items = list(value)
        elif isinstance(value, Mapping):
            items = list(value.values())
        elif hasattr(value, "entity_type"):
            return [value]
        else:
            raise ExprError(f"${self.name}: expected an entity type or a list, got {_describe(value)}", self.source)
        budget = _BUDGET  # inlined charge(len(items)): every collection function passes here
        budget.used += len(items)
        if budget.used > budget.limit:
            charge(0, self.source)
        return items

    def filtered(self, index: int = 0, where: Optional[int] = None) -> List[Any]:
        items = self.collection(index)
        if where is None or where >= len(self.nodes):
            return items
        return [item for pos, item in self.candidates(items, where) if truthy(self.each(where, item, pos))]

    def candidates(self, items: Sequence[Any], where: int) -> Iterator[Tuple[int, Any]]:
        """``(position, item)`` for the items argument ``where`` may hold for. Items it cannot hold for are
        left out only when that is certain without evaluating it (see :class:`EqualityGuard`)."""
        guard: Optional[EqualityGuard] = getattr(self.nodes[where], "guard", None)
        key = guard.key(self.scope.child(outer=self.scope.vars.get("it"))) if guard is not None else _NO_KEY
        if key is _NO_KEY:
            return enumerate(items)
        assert guard is not None
        return ((pos, item) for pos, item in enumerate(items) if not guard.rules_out(item, key))

    def number(self, index: int, default: Any = None) -> Any:
        value = self.arg(index, default)
        return _number(value, self.source, f"a number for argument {index + 1} of ${self.name}")

    @property
    def rng(self) -> Any:
        rng = self.scope.world.rng
        if rng is None:
            raise ExprError(f"${self.name} needs randomness, which is not available here", self.source)
        return rng


@dataclass(frozen=True)
class FunctionSpec:
    """A built-in function available as ``$name(...)`` in every expression."""

    name: str
    impl: Callable[[Call], Any]
    signature: str
    doc: str
    min_args: int = 0
    max_args: Optional[int] = None
    lazy: FrozenSet[int] = frozenset()


FUNCTIONS: Dict[str, FunctionSpec] = {}


def function(
    signature: str,
    doc: str,
    *,
    min_args: int = 0,
    max_args: Optional[int] = None,
    lazy: Sequence[int] = (),
) -> Callable[[Callable[[Call], Any]], Callable[[Call], Any]]:
    """Register a built-in expression function. ``signature`` starts with its name."""

    def register(impl: Callable[[Call], Any]) -> Callable[[Call], Any]:
        name = signature.split("(", 1)[0]
        if name in FUNCTIONS:
            raise ValueError(f"built-in function ${name} is registered twice")
        FUNCTIONS[name] = FunctionSpec(name, impl, signature, doc, min_args, max_args, frozenset(lazy))
        return impl

    return register
