"""Built-in expression functions. Each is documented in the generated authoring guide."""
from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Any

from ..expr import (
    MAX_LIST_LEN,
    MAX_RANGE,
    Call,
    ExprError,
    PrivateRead,
    Untrusted,
    _describe,
    _entity_id,
    _held,
    _number,
    attr,
    charge,
    check_size,
    derived,
    function,
    map_key,
    truthy,
)
from ..sampling.poisson import sample_poisson
from ..sampling.probability import is_probability

# ---------------------------------------------------------------------------
# Collections — first argument is an entity type name or a list
# ---------------------------------------------------------------------------


def _numbers(call: Call, values: list[Any]) -> list[Any]:
    """Numbers from ``values``; nulls are skipped (like SQL aggregates)."""
    return [_number(v, call.source, f"numbers in ${call.name}") for v in values if v is not None]


def _is_scalar_form(call: Call) -> bool:
    if len(call) < 2:
        return False
    first = call.arg(0)
    return isinstance(first, (int, float)) and not isinstance(first, bool)


@function("count(items, where?)", "How many items (entities of a type, or a list) match `where`.",
          min_args=1, max_args=2, lazy=[1])
def _count(call: Call) -> int:
    if len(call) == 1:
        return len(call.members(0))
    return len(call.filtered(0, 1))


@function("sum(items, value?, where?)",
          "Total of `value` over items matching `where`; $sum(list) adds a list of numbers.",
          min_args=1, max_args=3, lazy=[1, 2])
def _sum(call: Call) -> Any:
    if len(call) == 1:
        return sum(_numbers(call, call.collection(0)))
    items = call.filtered(0, 2)
    return sum(_numbers(call, [call.each(1, it, i) for i, it in enumerate(items)]))


@function("avg(items, value?, where?)",
          "Mean of `value` over matching items (nulls skipped); null when none. $avg(list) averages a list.",
          min_args=1, max_args=3, lazy=[1, 2])
def _avg(call: Call) -> Any:
    if len(call) == 1:
        values = _numbers(call, call.collection(0))
        return sum(values) / len(values) if values else None
    items = call.filtered(0, 2)
    values = _numbers(call, [call.each(1, it, i) for i, it in enumerate(items)])
    return sum(values) / len(values) if values else None


def _extreme(call: Call, pick: Any) -> Any:
    if len(call) == 1:
        values = _numbers(call, call.collection(0))
        return pick(values) if values else None
    if _is_scalar_form(call):
        return pick(_numbers(call, [call.arg(i) for i in range(len(call))]))
    items = call.filtered(0, 2)
    values = _numbers(call, [call.each(1, it, i) for i, it in enumerate(items)])
    return pick(values) if values else None


@function("min(items, value, where?) | min(list) | min(a, b, ...)",
          "Smallest `value` over matching items, of a list, or of the numbers given.",
          min_args=1, lazy=[1, 2])
def _min(call: Call) -> Any:
    return _extreme(call, min)


@function("max(items, value, where?) | max(list) | max(a, b, ...)",
          "Largest `value` over matching items, of a list, or of the numbers given.",
          min_args=1, lazy=[1, 2])
def _max(call: Call) -> Any:
    return _extreme(call, max)


def _sort_key(call: Call, key: Any) -> Any:
    """A number, text, or a list of them (compared in order: `[$it.price, -$it.seq]`)."""
    parts = key if isinstance(key, list) else [key]
    for part in parts:
        if not isinstance(part, (int, float, str)) or isinstance(part, bool):
            raise ExprError(f"${call.name}: sort key must be a number, text, or a list of them, got {_describe(part)}",
                            call.source)
    return tuple(parts) if isinstance(key, list) else key


def _sorted(call: Call, descending: bool) -> list[Any]:
    items = call.filtered(0, 3)
    if len(call) < 2:  # $sort(list): the items are their own keys
        keyed = [(_sort_key(call, it), i, it) for i, it in enumerate(items)]
    else:
        keyed = [(_sort_key(call, call.each(1, it, i)), i, it) for i, it in enumerate(items)]
    try:
        keyed.sort(key=lambda t: t[0], reverse=descending)  # stable: ties keep listing order, highest first or lowest
    except TypeError:  # a number and a text in the same place of two keys: neither comes first
        raise ExprError(f"${call.name}: the sort keys mix numbers and text, which have no order between them; sort "
                        "by one kind (`$text(x)` makes a number text)", call.source) from None
    ordered = [it for _, _, it in keyed]
    if len(call) > 2 and call.arg(2) is not None:
        ordered = ordered[: int(call.number(2))]
    return ordered


def _values(call: Call, value_arg: int, where_arg: int) -> list[Any]:
    items = call.filtered(0, where_arg)
    return _numbers(call, [call.each(value_arg, it, i) for i, it in enumerate(items)])


@function("median(items, value?, where?)",
          "Median of `value` over matching items, or of a list (nulls skipped); null when none.",
          min_args=1, max_args=3, lazy=[1, 2])
def _median(call: Call) -> Any:
    values = sorted(_numbers(call, call.collection(0)) if len(call) == 1 else _values(call, 1, 2))
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


@function("quantile(items, value, q, where?)", "The q-quantile (0–1, linear interpolation) of `value`; null when none.",
          min_args=3, max_args=4, lazy=[1, 3])
def _quantile(call: Call) -> Any:
    q = call.number(2)
    if not 0 <= q <= 1:
        raise ExprError(f"$quantile q must be between 0 and 1, got {q}", call.source)
    values = sorted(_values(call, 1, 3))
    if not values:
        return None
    position = q * (len(values) - 1)
    low = math.floor(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


@function("stdev(items, value?, where?)",
          "Sample standard deviation of `value`, or of a list; null when fewer than two.",
          min_args=1, max_args=3, lazy=[1, 2])
def _stdev(call: Call) -> Any:
    values = _numbers(call, call.collection(0)) if len(call) == 1 else _values(call, 1, 2)
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _keyed(pairs: Iterable[tuple[Any, Any]], combine: Any = None) -> dict:
    """A map from (key, value) pairs in first-seen key order. Equal keys merge (``combine(old, new)``,
    default: the later value wins); a key that was ever participant text stays marked untrusted."""
    index: dict[Any, int] = {}
    keys: list[Any] = []
    values: list[Any] = []
    for key, value in pairs:
        at = index.get(key)
        if at is None:
            index[key] = len(keys)
            keys.append(key)
            values.append(value)
            continue
        if isinstance(key, Untrusted):
            keys[at] = key
        values[at] = combine(values[at], value) if combine else value
    return dict(zip(keys, values))


@function("dict(items, key, value)", "A {key: value} map with one entry per item (later items win).",
          min_args=3, max_args=3, lazy=[1, 2])
def _dict(call: Call) -> dict:
    def pairs() -> Iterable[tuple[Any, Any]]:
        for i, item in enumerate(call.collection(0)):
            key = _entity_id(call.each(1, item, i))
            if not isinstance(key, (str, int, float)) or isinstance(key, bool):
                raise ExprError(f"$dict keys must be text or numbers, got {_describe(key)}", call.source)
            yield map_key(key), call.each(2, item, i)

    return _keyed(pairs())


@function("keys(map)", "The keys of a map.", min_args=1, max_args=1)
def _keys(call: Call) -> list[Any]:
    value = call.arg(0)
    if not isinstance(value, dict):
        raise ExprError(f"$keys needs a map, got {_describe(value)}", call.source)
    return check_size(list(value), call.source)


@function("values(map)", "The values of a map.", min_args=1, max_args=1)
def _map_values(call: Call) -> list[Any]:
    value = call.arg(0)
    if not isinstance(value, dict):
        raise ExprError(f"$values needs a map, got {_describe(value)}", call.source)
    return check_size(list(value.values()), call.source)


@function("top(items, by, n?, where?)", "Items sorted by `by` (a value or a list of values), highest first; the first "
          "`n` when given; ties keep listing order. $sort is the same, lowest first.",
          min_args=2, max_args=4, lazy=[1, 3])
def _top(call: Call) -> list[Any]:
    return _sorted(call, True)


def _rank_key(call: Call, value: Any) -> tuple[Any, ...]:
    parts = value if isinstance(value, list) else [value]
    for part in parts:
        if not isinstance(part, (int, float, str)):  # a bool is an int
            raise ExprError(f"$best: a ranking key is a number, text or a list of them, got {part!r}", call.source)
    return tuple(int(p) if isinstance(p, bool) else p for p in parts)


@function("best(items, by, ties?)",
          "The best of `items` by `by` (a value or list of values, highest first): always one item — a tie is broken "
          "at random (seeded) with ties 'random' (default), or gives null with 'none'; null when empty. ties 'all' "
          "always gives a list: every item tied for best ([] when empty).",
          min_args=2, max_args=3, lazy=[1])
def _best(call: Call) -> Any:
    items = call.collection(0)
    ties = call.arg(2, "random")
    if ties not in ("random", "none", "all"):
        raise ExprError(f"$best: ties is random, none or all, got {ties!r}", call.source)
    if not items:
        return [] if ties == "all" else None
    try:
        keyed = [(_rank_key(call, call.each(1, item, i)), item) for i, item in enumerate(items)]
        best = max(key for key, _ in keyed)
    except TypeError:
        raise ExprError("$best: ranking keys must be comparable (all numbers or all text)", call.source) from None
    top = [item for key, item in keyed if key == best]
    if ties == "all":
        return top
    if len(top) == 1:
        return top[0]
    if ties == "none":
        return None
    world: Any = call.scope.world
    ids = sorted(getattr(item, "id", str(item)) for item in top)
    return top[world.seeds.rng("winner", world.round, *ids).randrange(len(top))]


@function("filter(items, where)", "The items for which `where` holds.", min_args=2, max_args=2, lazy=[1])
def _filter(call: Call) -> list[Any]:
    return call.filtered(0, 1)


@function("map(items, value)", "`value` computed for each item.", min_args=2, max_args=2, lazy=[1])
def _map(call: Call) -> list[Any]:
    return [call.each(1, it, i) for i, it in enumerate(call.collection(0))]


@function("pick(items, where)", "The first item for which `where` holds, or null ($first(items) is the first of all).",
          min_args=2, max_args=2, lazy=[1])
def _pick(call: Call) -> Any:
    items = call.collection(0)
    for i, item in call.candidates(items, 1):
        if truthy(call.each(1, item, i)):
            return item
    return None


@function("any(items, where)", "True when at least one item matches.", min_args=2, max_args=2, lazy=[1])
def _any(call: Call) -> bool:
    return any(truthy(call.each(1, it, i)) for i, it in call.candidates(call.collection(0), 1))


@function("all(items, where)", "True when every item matches (and for no items).",
          min_args=2, max_args=2, lazy=[1])
def _all(call: Call) -> bool:
    return all(truthy(call.each(1, it, i)) for i, it in enumerate(call.collection(0)))


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
def _unique(call: Call) -> list[Any]:
    seen: dict[Any, int] = {}
    out: list[Any] = []
    for item in call.collection(0):
        key = _entity_id(item)
        if isinstance(key, (list, dict)):
            key = repr(key)
        at = seen.get(key)
        if at is None:
            seen[key] = len(out)
            out.append(item)
        elif isinstance(item, Untrusted) and not isinstance(out[at], Untrusted):
            out[at] = item  # equal text: keep the participant-text marker
    return out


@function("tally(list)", "Counts of each distinct value, as a {value: count} map (order of first appearance; keys are "
          "text, as in every map).",
          min_args=1, max_args=1)
def _tally(call: Call) -> dict:
    return _counts(call, map_key)


def _counts(call: Call, key: Any = _entity_id) -> dict:
    """How often each distinct value of the list occurs, keyed by ``key(value)``."""
    return _keyed(((key(item), 1) for item in call.collection(0)), lambda old, new: old + new)


@function("mode(list)", "The most frequent value (first seen wins ties), or null for an empty list.",
          min_args=1, max_args=1)
def _mode(call: Call) -> Any:
    counts = _counts(call)
    if not counts:
        return None
    best = max(counts.values())
    return next(k for k, v in counts.items() if v == best)


@function("get(object, key, default?)",
          "Field `key` of an entity, map or record, or element `key` of a list; `default` when missing.",
          min_args=2, max_args=3)
def _get(call: Call) -> Any:
    obj, key = call.arg(0), call.arg(1)
    if isinstance(obj, dict):
        key = map_key(key)
        return obj[key] if isinstance(key, str) and key in obj else call.arg(2)
    if isinstance(obj, (list, tuple)):
        if isinstance(key, int) and not isinstance(key, bool) and -len(obj) <= key < len(obj):
            return obj[key]
        return call.arg(2)
    if obj is None:
        return call.arg(2)
    try:
        return attr(obj, str(key), call.source, call.scope)
    except PrivateRead:
        raise
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


@function("records(name, where?)", "Entries of a declared record, oldest first. Game logic reads every entry; what an "
          "agent is shown or offered reads only those the record's `visible` rule lets it see.",
          min_args=1, max_args=2, lazy=[1])
def _records(call: Call) -> list[Any]:
    # $viewer is bound only while rendering for, or offering choices to, one agent.
    viewer = call.scope.vars.get("viewer")
    name = str(call.arg(0))
    rows = _held(lambda: call.scope.world.visible_records(name, viewer))  # evaluates `visible` per entry
    charge(len(rows), call.source)
    if len(call) < 2:
        return list(rows)
    return [row for i, row in enumerate(rows) if truthy(call.each(1, row, i))]


@function("events(kind?, where?)", "Events so far (optionally of one kind), oldest first. Game logic reads every "
          "event; what an agent is shown or offered reads only those it may know of (a record event follows its "
          "entry's `visible` rule).", min_args=0, max_args=2, lazy=[1])
def _events(call: Call) -> list[Any]:
    kind = call.arg(0) if len(call) else None
    viewer = call.scope.vars.get("viewer")
    rows = _held(lambda: call.scope.world.events(kind, viewer))
    charge(len(rows), call.source)
    if len(call) < 2:
        return list(rows)
    return [row for i, row in enumerate(rows) if truthy(call.each(1, row, i))]


@function("seen(agent, item)",
          "Whether `agent` was shown `item` on a wake so far — an event (from $events), a record entry "
          "(from $records) or a view by name. Needs the exposure log, which a contract that calls $seen keeps.",
          min_args=2, max_args=2)
def _seen(call: Call) -> bool:
    log = getattr(call.scope.world, "exposures", None)
    if log is None:
        raise ExprError("$seen needs the exposure log, which this run does not keep; load it with exposures=True",
                        call.source)
    item = call.arg(1)
    found = log.seen(_entity_id(call.arg(0)), item)
    if found is None:
        raise ExprError(f"$seen: expected an event, a record entry or a view name, got {_describe(item)}", call.source)
    return bool(found)


@function("relation(a, b, kind)", "Value of the `kind` link from a to b, or null when not linked.",
          min_args=3, max_args=3)
def _relation(call: Call) -> Any:
    return call.scope.world.relation(call.arg(0), call.arg(1), str(call.arg(2)))


@function("linked(a, b, kind)", "True when a has a `kind` link to b.", min_args=3, max_args=3)
def _linked(call: Call) -> bool:
    return call.scope.world.relation(call.arg(0), call.arg(1), str(call.arg(2))) is not None


@function("link(a, b, kind)", "The `kind` link from a to b — its `value`, `source`, `target`, `kind` and the "
          "relation's fields — or null when not linked. Effects assign `.value` or a field: `$link($actor, $it, "
          "trusts).since = $round`.",
          min_args=3, max_args=3)
def _link(call: Call) -> Any:
    return call.scope.world.link_view(call.arg(0), call.arg(1), str(call.arg(2)))


@function("links(entity, kind, where?)", "The `kind` links from `entity` (either direction on a symmetric relation) "
          "to living entities, each with `value`, `source`, `target` and the relation's fields; `where` filters ($it "
          "is a link).",
          min_args=2, max_args=3, lazy=[2])
def _links(call: Call) -> list[Any]:
    rows = call.scope.world.links_of(call.arg(0), str(call.arg(1)))
    charge(len(rows), call.source)
    if len(call) < 3:
        return list(rows)
    return [row for i, row in enumerate(rows) if truthy(call.each(2, row, i))]


@function("neighbors(entity, kind)", "Entities linked to `entity` by `kind` (either direction).",
          min_args=2, max_args=2)
def _neighbors(call: Call) -> list[Any]:
    return check_size(list(call.scope.world.neighbors(call.arg(0), str(call.arg(1)))), call.source)


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


@function("log(x, base?)", "Logarithm of x: natural by default, or in `base` (e.g. 2 or 10).", min_args=1, max_args=2)
def _log(call: Call) -> float:
    x = call.number(0)
    if x <= 0:
        raise ExprError(f"$log: x must be above 0, got {x}", call.source)
    if len(call) < 2:
        return math.log(x)
    base = call.number(1)
    if base <= 0 or base == 1:
        raise ExprError(f"$log: the base must be above 0 and not 1, got {base}", call.source)
    if base == 10:  # exact at powers of the base: log(1000) / log(10) is 2.9999999999999996
        return math.log10(x)
    if base == 2:
        return math.log2(x)
    return math.log(x) / math.log(base)


#: Enough digits for any whole number an expression may hold (MAX_INT_BITS) and any float.
_ROUND_CONTEXT = Context(prec=1_300, rounding=ROUND_HALF_UP)


@function("round(x, digits?)", "Round to `digits` decimals (default 0 → whole number); a half rounds away from zero, "
          "as money does: 2.5 → 3, 0.125 → 0.13 (a number rounds as it is written).", min_args=1, max_args=2)
def _round(call: Call) -> Any:
    return _half_up(call.number(0), int(call.number(1, 0)))


def _half_up(value: Any, digits: int) -> Any:
    """``value`` to ``digits`` decimals, halves away from zero. A float rounds as the decimal it is written as, so
    1.005 → 1.01 even though its binary value is a hair below. Whole numbers come back as ints when digits ≤ 0."""
    if isinstance(value, int) and digits >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and digits >= 0:
        return int(value) if digits == 0 else value
    exact = Decimal(value) if isinstance(value, int) else Decimal(repr(value))
    exponent = exact.as_tuple().exponent  # an int: the value is finite
    if isinstance(exponent, int) and exponent < -digits:  # more decimals than asked for
        exact = exact.quantize(Decimal(1).scaleb(-digits), context=_ROUND_CONTEXT)
    return int(exact) if digits == 0 or isinstance(value, int) else float(exact)


@function("coalesce(value, default)",
          "`value`, or `default` when it is null: the default for a missing value. Unlike `value or default`, it keeps "
          "0, false and ''.", min_args=2, max_args=2)
def _coalesce(call: Call) -> Any:
    value = call.arg(0)
    return call.arg(1) if value is None else value


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


@function("chance(p)", "True with probability p.", min_args=1, max_args=1)
def _chance(call: Call) -> bool:
    # Retain draw order when the argument itself draws randomness.
    roll = call.rng.random()
    probability = call.number(0)
    if not is_probability(probability):
        raise ExprError(f"$chance probability must be a number from 0 to 1, got {probability!r}", call.source)
    return roll < probability


@function("uniform(low, high)", "Uniform number between low and high.", min_args=2, max_args=2)
def _uniform(call: Call) -> float:
    return call.rng.uniform(call.number(0), call.number(1))


@function("randint(low, high)", "Whole number between low and high inclusive.", min_args=2, max_args=2)
def _randint(call: Call) -> int:
    low, high = int(call.number(0)), int(call.number(1))
    if low > high:
        raise ExprError(f"$randint's low ({low}) is above its high ({high}): write $randint({high}, {low})",
                        call.source)
    return call.rng.randint(low, high)


@function("normal(mean, sd)", "Normally distributed number.", min_args=2, max_args=2)
def _normal(call: Call) -> float:
    sd = call.number(1)
    if sd < 0:
        raise ExprError(f"$normal's sd (spread) must be 0 or more, got {sd}", call.source)
    return call.rng.gauss(call.number(0), sd)


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
    try:
        return sample_poisson(call.rng, mean)
    except ValueError as exc:
        raise ExprError(str(exc), call.source) from None


@function("choice(items, weight?)",
          "One item picked at random; `weight` is a per-item expression ($it), e.g. $choice([a, b], $it == a and 3 or "
          "1).",
          min_args=1, max_args=2, lazy=[1])
def _choice(call: Call) -> Any:
    if len(call) < 2:
        members = call.members(0)
        return members[call.rng.randrange(len(members))] if members else None
    items = call.collection(0)
    if not items:
        return None
    weights = _numbers(call, [call.each(1, it, i) for i, it in enumerate(items)])
    if any(w < 0 for w in weights) or sum(weights) <= 0:
        raise ExprError("$choice weights must be ≥ 0 and not all zero", call.source)
    return call.rng.choices(items, weights=weights, k=1)[0]


@function("sample(items, k)", "k distinct items picked at random (all of them when fewer exist).",
          min_args=2, max_args=2)
def _sample(call: Call) -> list[Any]:
    items = call.collection(0)
    k = min(len(items), int(call.number(1)))
    return call.rng.sample(items, k)


@function("shuffle(items)", "The items in random order.", min_args=1, max_args=1)
def _shuffle(call: Call) -> list[Any]:
    items = list(call.collection(0))
    call.rng.shuffle(items)
    return items


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


@function("text(value)", "The value as text (entities give their name).", min_args=1, max_args=1)
def _text(call: Call) -> str:
    from ..expr.template import format_value

    return format_value(call.arg(0))


@function("lower(text)", "Lower-case text.", min_args=1, max_args=1)
def _lower(call: Call) -> str:
    value = call.arg(0)
    return derived(str(value).lower(), value)


@function("contains(text, part)", "True when `part` occurs in `text` (case-insensitive).",
          min_args=2, max_args=2)
def _contains(call: Call) -> bool:
    return str(call.arg(1)).lower() in str(call.arg(0)).lower()


@function("join(list, separator?)", "Items joined into text (default separator ', ').",
          min_args=1, max_args=2)
def _join(call: Call) -> str:
    from ..expr.template import format_value

    separator = call.arg(1, ", ")
    text = str(separator).join(format_value(item) for item in call.collection(0))
    return derived(check_size(text, call.source), separator)


# ---------------------------------------------------------------------------
# Lists and formatting
# ---------------------------------------------------------------------------


@function("sort(items, by?, n?, where?)", "Items sorted by `by` (a value or a list of values; default the items "
          "themselves), lowest first; the first `n` when given; ties keep listing order. $top is the same, highest "
          "first.",
          min_args=1, max_args=4, lazy=[1, 3])
def _sort(call: Call) -> list[Any]:
    return _sorted(call, False)


@function("reverse(list)", "The list in reverse order.", min_args=1, max_args=1)
def _reverse(call: Call) -> list[Any]:
    return list(reversed(call.collection(0)))


@function("slice(list, start, end?)",
          "Elements from `start` up to (not including) `end`; negative counts from the end.", min_args=2, max_args=3)
def _slice(call: Call) -> list[Any]:
    items = call.collection(0)
    start = int(call.number(1))
    end = int(call.number(2)) if len(call) > 2 and call.arg(2) is not None else None
    return items[start:end]


@function("range(n) | range(start, end)", "Whole numbers 0..n-1, or start..end-1.", min_args=1, max_args=2)
def _range(call: Call) -> list[int]:
    start, end = (0, int(call.number(0))) if len(call) == 1 else (int(call.number(0)), int(call.number(1)))
    if end - start > MAX_RANGE:
        raise ExprError(f"$range is limited to {MAX_RANGE:,} numbers", call.source)
    charge(max(0, end - start), call.source)
    return list(range(start, end))


@function("fmt(value, format)", "The value as text in a template format: money, pct, int, 0–4 decimals, upper, …",
          min_args=2, max_args=2)
def _fmt(call: Call) -> str:
    from ..expr.template import _FORMATS, FORMATS

    name = str(call.arg(1))
    if name not in _FORMATS:
        raise ExprError(f"$fmt: unknown format '{name}' (formats: {', '.join(FORMATS)})", call.source)
    return check_size(_FORMATS[name](call.arg(0)), call.source)


@function("is(entity, type)", "True when the entity is of `type` or a type that extends it.", min_args=2, max_args=2)
def _is(call: Call) -> bool:
    entity, type_name = call.arg(0), str(call.arg(1))
    if entity is None:
        return False
    if not hasattr(entity, "entity_type"):
        raise ExprError(f"$is needs an entity, got {_describe(entity)}", call.source)
    return call.scope.world.is_a(entity.entity_type, type_name)


@function("flatten(lists)", "One list from a list of lists (one level).", min_args=1, max_args=1)
def _flatten(call: Call) -> list[Any]:
    out: list[Any] = []
    for item in call.collection(0):
        if isinstance(item, (list, tuple)):
            if len(out) + len(item) > MAX_LIST_LEN:
                raise ExprError(f"$flatten would build more than {MAX_LIST_LEN:,} items", call.source)
            out.extend(item)
        else:
            out.append(item)
    charge(len(out), call.source)
    return out

