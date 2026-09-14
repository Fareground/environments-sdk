"""Built-in expression functions. Each is documented in the generated authoring guide."""
from __future__ import annotations

import math
from typing import Any, List

from .expr import Call, ExprError, _describe, _entity_id, _number, attr, function, truthy

# ---------------------------------------------------------------------------
# Collections — first argument is an entity type name or a list
# ---------------------------------------------------------------------------


def _numbers(call: Call, values: List[Any]) -> List[Any]:
    return [_number(v, call.source, f"numbers in ${call.name}") for v in values]


def _is_scalar_form(call: Call) -> bool:
    if len(call) < 2:
        return False
    first = call.arg(0)
    return isinstance(first, (int, float)) and not isinstance(first, bool)


@function("count(items, where?)", "How many items (entities of a type, or a list) match `where`.",
          min_args=1, max_args=2, lazy=[1])
def _count(call: Call) -> int:
    return len(call.filtered(0, 1))


@function("sum(items, value, where?)", "Total of `value` over items matching `where`.",
          min_args=2, max_args=3, lazy=[1, 2])
def _sum(call: Call) -> Any:
    items = call.filtered(0, 2)
    return sum(_numbers(call, [call.each(1, it, i) for i, it in enumerate(items)]))


@function("avg(items, value, where?)", "Mean of `value` over matching items; null when none match.",
          min_args=2, max_args=3, lazy=[1, 2])
def _avg(call: Call) -> Any:
    items = call.filtered(0, 2)
    values = _numbers(call, [call.each(1, it, i) for i, it in enumerate(items)])
    return sum(values) / len(values) if values else None


def _extreme(call: Call, pick: Any) -> Any:
    if _is_scalar_form(call):
        return pick(_numbers(call, [call.arg(i) for i in range(len(call))]))
    items = call.filtered(0, 2)
    values = _numbers(call, [call.each(1, it, i) for i, it in enumerate(items)])
    return pick(values) if values else None


@function("min(items, value, where?) | min(a, b, ...)",
          "Smallest `value` over matching items, or the smallest of the numbers given.",
          min_args=1, lazy=[1, 2])
def _min(call: Call) -> Any:
    return _extreme(call, min)


@function("max(items, value, where?) | max(a, b, ...)",
          "Largest `value` over matching items, or the largest of the numbers given.",
          min_args=1, lazy=[1, 2])
def _max(call: Call) -> Any:
    return _extreme(call, max)


def _sorted(call: Call, descending: bool) -> List[Any]:
    items = call.filtered(0, 3)
    keyed = [(call.each(1, it, i), i, it) for i, it in enumerate(items)]
    for key, _, _ in keyed:
        if not isinstance(key, (int, float, str)) or isinstance(key, bool):
            raise ExprError(f"${call.name}: sort key must be a number or text, got {_describe(key)}", call.source)
    keyed.sort(key=lambda t: (t[0], t[1]), reverse=descending)
    ordered = [it for _, _, it in keyed]
    if len(call) > 2 and call.arg(2) is not None:
        ordered = ordered[: int(call.number(2))]
    return ordered


@function("top(items, by, n?, where?)", "Items sorted by `by`, highest first; the first `n` when given.",
          min_args=2, max_args=4, lazy=[1, 3])
def _top(call: Call) -> List[Any]:
    return _sorted(call, True)


@function("bottom(items, by, n?, where?)", "Items sorted by `by`, lowest first; the first `n` when given.",
          min_args=2, max_args=4, lazy=[1, 3])
def _bottom(call: Call) -> List[Any]:
    return _sorted(call, False)


@function("filter(items, where)", "The items for which `where` holds.", min_args=2, max_args=2, lazy=[1])
def _filter(call: Call) -> List[Any]:
    return call.filtered(0, 1)


@function("map(items, value)", "`value` computed for each item.", min_args=2, max_args=2, lazy=[1])
def _map(call: Call) -> List[Any]:
    return [call.each(1, it, i) for i, it in enumerate(call.collection(0))]


@function("pick(items, where?)", "The first matching item, or null.", min_args=1, max_args=2, lazy=[1])
def _pick(call: Call) -> Any:
    for i, item in enumerate(call.collection(0)):
        if len(call) < 2 or truthy(call.each(1, item, i)):
            return item
    return None


@function("any(items, where)", "True when at least one item matches.", min_args=2, max_args=2, lazy=[1])
def _any(call: Call) -> bool:
    return any(truthy(call.each(1, it, i)) for i, it in enumerate(call.collection(0)))


@function("all(items, where)", "True when every item matches (and for no items).",
          min_args=2, max_args=2, lazy=[1])
def _all(call: Call) -> bool:
    return all(truthy(call.each(1, it, i)) for i, it in enumerate(call.collection(0)))


@function("ids(items)", "The ids of the entities given.", min_args=1, max_args=1)
def _ids(call: Call) -> List[Any]:
    return [_entity_id(it) for it in call.collection(0)]


@function("len(value)", "Length of a list or text.", min_args=1, max_args=1)
def _len(call: Call) -> int:
    value = call.arg(0)
    if value is None:
        return 0
    if isinstance(value, (list, tuple, str, dict)):
        return len(value)
    raise ExprError(f"$len needs a list or text, got {_describe(value)}", call.source)


@function("first(list)", "First element, or null for an empty list.", min_args=1, max_args=1)
def _first(call: Call) -> Any:
    items = call.collection(0)
    return items[0] if items else None


@function("last(list)", "Last element, or null for an empty list.", min_args=1, max_args=1)
def _last(call: Call) -> Any:
    items = call.collection(0)
    return items[-1] if items else None


@function("unique(list)", "The list with duplicates removed, order kept.", min_args=1, max_args=1)
def _unique(call: Call) -> List[Any]:
    seen: set = set()
    out = []
    for item in call.collection(0):
        key = _entity_id(item)
        if isinstance(key, (list, dict)):
            key = repr(key)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


@function("tally(list)", "Counts of each distinct value, as a {value: count} map (order of first appearance).",
          min_args=1, max_args=1)
def _tally(call: Call) -> dict:
    out: dict = {}
    for item in call.collection(0):
        key = _entity_id(item)
        out[key] = out.get(key, 0) + 1
    return out


@function("mode(list)", "The most frequent value (first seen wins ties), or null for an empty list.",
          min_args=1, max_args=1)
def _mode(call: Call) -> Any:
    counts = _tally(call)
    if not counts:
        return None
    best = max(counts.values())
    return next(k for k, v in counts.items() if v == best)


@function("get(object, key, default?)", "Field `key` of an entity, map or record; `default` when missing.",
          min_args=2, max_args=3)
def _get(call: Call) -> Any:
    obj, key = call.arg(0), call.arg(1)
    if isinstance(obj, dict):
        return obj.get(key, call.arg(2))
    if obj is None:
        return call.arg(2)
    try:
        return attr(obj, str(key), call.source)
    except ExprError:
        if len(call) > 2:
            return call.arg(2)
        raise


# ---------------------------------------------------------------------------
# World queries
# ---------------------------------------------------------------------------


@function("entity(id)", "The entity with this id, or null.", min_args=1, max_args=1)
def _entity(call: Call) -> Any:
    return call.scope.world.entity(_entity_id(call.arg(0)))


@function("exists(id)", "True when an alive entity with this id exists.", min_args=1, max_args=1)
def _exists(call: Call) -> bool:
    found = call.scope.world.entity(_entity_id(call.arg(0)))
    return bool(found is not None and found.alive)


@function("records(name, where?)", "Entries of a declared record, oldest first.",
          min_args=1, max_args=2, lazy=[1])
def _records(call: Call) -> List[Any]:
    rows = call.scope.world.records(str(call.arg(0)))
    if len(call) < 2:
        return list(rows)
    return [row for i, row in enumerate(rows) if truthy(call.each(1, row, i))]


@function("events(kind?, where?)", "Events so far (optionally of one kind), oldest first.",
          min_args=0, max_args=2, lazy=[1])
def _events(call: Call) -> List[Any]:
    kind = call.arg(0) if len(call) else None
    rows = call.scope.world.events(kind)
    if len(call) < 2:
        return list(rows)
    return [row for i, row in enumerate(rows) if truthy(call.each(1, row, i))]


@function("relation(a, b, kind)", "Value of the `kind` link from a to b, or null when not linked.",
          min_args=3, max_args=3)
def _relation(call: Call) -> Any:
    return call.scope.world.relation(call.arg(0), call.arg(1), str(call.arg(2)))


@function("linked(a, b, kind)", "True when a has a `kind` link to b.", min_args=3, max_args=3)
def _linked(call: Call) -> bool:
    return call.scope.world.relation(call.arg(0), call.arg(1), str(call.arg(2))) is not None


@function("neighbors(entity, kind)", "Entities linked to `entity` by `kind` (either direction).",
          min_args=2, max_args=2)
def _neighbors(call: Call) -> List[Any]:
    return list(call.scope.world.neighbors(call.arg(0), str(call.arg(1))))


@function("distance(a, b)", "Distance between two entities or places in the declared space.",
          min_args=2, max_args=2)
def _distance(call: Call) -> float:
    return call.scope.world.distance(call.arg(0), call.arg(1))


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------


def _unary(name: str, doc: str, fn: Any) -> None:
    def impl(call: Call) -> Any:
        value = call.number(0)
        try:
            return fn(value)
        except (ValueError, OverflowError) as exc:
            raise ExprError(f"${name}({value}) failed: {exc}", call.source) from None

    function(f"{name}(x)", doc, min_args=1, max_args=1)(impl)


_unary("abs", "Absolute value.", abs)
_unary("floor", "Round down to a whole number.", math.floor)
_unary("ceil", "Round up to a whole number.", math.ceil)
_unary("sqrt", "Square root.", math.sqrt)
_unary("exp", "e to the power x.", math.exp)
_unary("log", "Natural logarithm.", math.log)


@function("round(x, digits?)", "Round to `digits` decimals (default 0 → whole number).", min_args=1, max_args=2)
def _round(call: Call) -> Any:
    digits = int(call.number(1, 0))
    value = round(call.number(0), digits)
    return int(value) if digits == 0 else value


@function("clamp(x, low, high)", "x limited to the range [low, high].", min_args=3, max_args=3)
def _clamp(call: Call) -> Any:
    low, high = call.number(1), call.number(2)
    if low > high:
        raise ExprError(f"$clamp: low {low} is above high {high}", call.source)
    return max(low, min(high, call.number(0)))


@function("pct(part, whole)", "part / whole, or 0 when whole is 0.", min_args=2, max_args=2)
def _pct(call: Call) -> float:
    whole = call.number(1)
    return call.number(0) / whole if whole else 0.0


# ---------------------------------------------------------------------------
# Randomness — always drawn from the run's seeded generator
# ---------------------------------------------------------------------------


@function("random()", "Uniform number in [0, 1).", max_args=0)
def _random(call: Call) -> float:
    return call.rng.random()


@function("chance(p)", "True with probability p.", min_args=1, max_args=1)
def _chance(call: Call) -> bool:
    return call.rng.random() < call.number(0)


@function("uniform(low, high)", "Uniform number between low and high.", min_args=2, max_args=2)
def _uniform(call: Call) -> float:
    return call.rng.uniform(call.number(0), call.number(1))


@function("randint(low, high)", "Whole number between low and high inclusive.", min_args=2, max_args=2)
def _randint(call: Call) -> int:
    return call.rng.randint(int(call.number(0)), int(call.number(1)))


@function("normal(mean, sd)", "Normally distributed number.", min_args=2, max_args=2)
def _normal(call: Call) -> float:
    return call.rng.gauss(call.number(0), call.number(1))


@function("lognormal(mu, sigma)", "Log-normally distributed number.", min_args=2, max_args=2)
def _lognormal(call: Call) -> float:
    return call.rng.lognormvariate(call.number(0), call.number(1))


@function("beta(a, b)", "Beta-distributed number in [0, 1].", min_args=2, max_args=2)
def _beta(call: Call) -> float:
    return call.rng.betavariate(call.number(0), call.number(1))


@function("exponential(rate)", "Exponentially distributed number.", min_args=1, max_args=1)
def _exponential(call: Call) -> float:
    return call.rng.expovariate(call.number(0))


@function("poisson(mean)", "Poisson-distributed whole number.", min_args=1, max_args=1)
def _poisson(call: Call) -> int:
    mean = call.number(0)
    if mean < 0:
        raise ExprError("$poisson mean must be ≥ 0", call.source)
    if mean > 500:
        return max(0, round(call.rng.gauss(mean, math.sqrt(mean))))
    limit, k, p = math.exp(-mean), 0, 1.0
    while True:
        p *= call.rng.random()
        if p <= limit:
            return k
        k += 1


@function("choice(items, weights?)", "One item picked at random (optionally weighted by `weights`).",
          min_args=1, max_args=2, lazy=[1])
def _choice(call: Call) -> Any:
    items = call.collection(0)
    if not items:
        return None
    if len(call) < 2:
        return items[call.rng.randrange(len(items))]
    weights = _numbers(call, [call.each(1, it, i) for i, it in enumerate(items)])
    if any(w < 0 for w in weights) or sum(weights) <= 0:
        raise ExprError("$choice weights must be ≥ 0 and not all zero", call.source)
    return call.rng.choices(items, weights=weights, k=1)[0]


@function("sample(items, k)", "k distinct items picked at random (all of them when fewer exist).",
          min_args=2, max_args=2)
def _sample(call: Call) -> List[Any]:
    items = call.collection(0)
    k = min(len(items), int(call.number(1)))
    return call.rng.sample(items, k)


@function("shuffle(items)", "The items in random order.", min_args=1, max_args=1)
def _shuffle(call: Call) -> List[Any]:
    items = list(call.collection(0))
    call.rng.shuffle(items)
    return items


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


@function("text(value)", "The value as text (entities give their name).", min_args=1, max_args=1)
def _text(call: Call) -> str:
    from .template import format_value

    return format_value(call.arg(0))


@function("lower(text)", "Lower-case text.", min_args=1, max_args=1)
def _lower(call: Call) -> str:
    return str(call.arg(0)).lower()


@function("contains(text, part)", "True when `part` occurs in `text` (case-insensitive).",
          min_args=2, max_args=2)
def _contains(call: Call) -> bool:
    return str(call.arg(1)).lower() in str(call.arg(0)).lower()


@function("join(list, separator?)", "Items joined into text (default separator ', ').",
          min_args=1, max_args=2)
def _join(call: Call) -> str:
    from .template import format_value

    return str(call.arg(1, ", ")).join(format_value(item) for item in call.collection(0))
