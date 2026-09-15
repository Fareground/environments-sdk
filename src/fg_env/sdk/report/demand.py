"""What moves demand over a report's horizon: every pattern a demand's rate and factors read, told per group as how much
it adds or takes away (see :mod:`.pattern_effects`).

One run of the reported option is replayed round by round, and after each round every item's rate and factors are read
exactly as the demand mode read them — so factors that depend on the run (promotions that carry over, the prices the
policy set, customers switching between tiers) count as much as the calendar. A rate that is itself a product (a base
× a season × a trend) is split into its own factors. The replay reads fitted parameters at their estimates, so what a
pattern adds is the model's estimate rather than one run's draw of it (a trend fitted near zero cannot flip sign).
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..api import load
from ..clock_words import period_label, plural, sub_day, unit_word
from ..measure import RunResult
from ..mechanisms.econ_base import DEMAND, config_of, props
from ..mechanisms.econ_demand_trade import number_of, plan, read_term
from ..patterns.decompose import decompose
from .pattern_effects import Effects, Factor, clauses, factor_of

__all__ = ["demand_lines"]

_GROUP_PROP = re.compile(r"^\s*\$it\.([A-Za-z_][A-Za-z0-9_]*)\s*$")
#: ``(demand mechanism, segment, group)`` → what every factor added there.
Tallies = Dict[Tuple[str, str, str], Effects]


def demand_lines(contract: Any, run: Optional[RunResult], limit: Optional[int]) -> List[str]:
    """For each demand (and segment): its size over the horizon and what each pattern adds, shared by every group or
    per group, largest groups first (at most ``limit`` groups each)."""
    names = [name for name, raw in (contract.mechanisms.items() if contract is not None else ())
             if isinstance(raw, Mapping) and (raw.get("kind"), raw.get("mode")) == ("economy", "demand")]
    if not names or run is None or not run.rounds:
        return []
    tallies, factors, group_words = _replay(contract, run, names)
    unit = unit_word(run.clock)
    horizon = f"{run.rounds} {plural(unit, run.rounds)}"
    out: List[str] = []
    for name in names:
        segments = list(dict.fromkeys(segment for n, segment, _ in tallies if n == name))
        for segment in segments:
            groups = {group: effects for (n, s, group), effects in tallies.items() if (n, s) == (name, segment)}
            head = f"{segment.replace('_', ' ').capitalize()} demand" if len(segments) > 1 else "Demand"
            out += _segment_lines(head, groups, factors, group_words.get(name), unit, horizon, limit)
    return out


def _segment_lines(head: str, groups: Dict[str, Effects], factors: Mapping[str, Factor], group_word: Optional[str],
                   unit: str, horizon: str, limit: Optional[int]) -> List[str]:
    told = {group: clauses(effects, factors, unit, horizon) for group, effects in groups.items()}
    if not any(told.values()):
        return []
    total = sum(effects.amount for effects in groups.values())
    lines = [f"{head} over the {horizon} is about {total:,.0f} units."]
    shared = [pattern for pattern, text in next(iter(told.values())).items()
              if len(told) > 1 and all(t.get(pattern) == text for t in told.values())]
    if shared:
        every = f"every {group_word}" if group_word else "every group"
        lines[0] += f" For {every}, " + "; ".join(next(iter(told.values()))[p] for p in shared) + "."
    elif len(told) == 1 and "" in told:
        lines[0] += " " + _sentence("; ".join(told[""].values()))
        return lines
    ranked = sorted(groups, key=lambda group: -groups[group].amount)
    for group in ranked[:limit]:
        own = [text for pattern, text in told[group].items() if pattern not in shared]
        if own:
            name = _group_name(group, group_word)
            lines.append(f"{name[:1].upper() + name[1:]} ({groups[group].amount:,.0f} units): " + "; ".join(own) + ".")
    return lines


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] + "."


def _group_name(value: str, word: Optional[str]) -> str:
    """``model 17`` for a code, ``brake pads`` for a name."""
    if word and any(ch.isdigit() for ch in value):
        return f"{word} {value}"
    return value.replace("_", " ")


def _replay(contract: Any, run: RunResult, names: List[str]) -> Tuple[Tallies, Dict[str, Factor], Dict[str, str]]:
    env = load(contract, seed=run.seed, arm=run.arm if run.arm in contract.arms else None, inputs=run.inputs)
    env.world.patterns.at_estimates()
    runner, world = env.effects, env.world
    uses = [(name, config_of(world, name, DEMAND)) for name in names]
    group_words = {name: found.group(1).replace("_", " ") for name, config in uses
                   if config.group and (found := _GROUP_PROP.match(str(config.group)))}
    tallies: Tallies = {}
    factors: Dict[str, Factor] = {}
    for round_ in range(1, run.rounds + 1):
        if env.finished:
            break
        env.step()
        date = _date(runner.eval("$clock.date", {}))
        for name, config in uses:
            items = world.entities_of(config.items)
            prices = {item.id: float(props(item)[f"{name}_price"]) for item in items}
            for segment in plan(world, name, config):
                for item in items:
                    scope: Dict[str, Any] = {"it": item, "price": prices[item.id]}
                    if segment.spec.where is not None and not runner.eval(segment.spec.where, scope):
                        continue
                    where = f"mechanisms.{name}"
                    if segment.spec.price is not None:
                        scope["price"] = number_of(runner, segment.spec.price, scope, where, low=0)
                    amount = read_term(runner, segment.rate, item, scope, prices, where)
                    values = _rate_factors(env, runner, segment.rate, item, scope)
                    for term in segment.factors:
                        value = read_term(runner, term, item, scope, prices, where)
                        amount *= value
                        if hasattr(term, "pattern"):
                            values[term.pattern] = value
                    for pattern in values:
                        factors.setdefault(pattern, factor_of(contract, pattern))
                    slots = {p: slot for p in values if factors[p].how == "calendar"
                             for slot in [_slot(contract, p, run.clock, round_, run.rounds, date)] if slot}
                    group = str(runner.eval(config.group, scope)) if config.group else ""
                    tallies.setdefault((name, segment.name, group), Effects()).add(round_, amount, values, slots)
    return tallies, factors, group_words


def _rate_factors(env: Any, runner: Any, term: Any, item: Any, scope: Dict[str, Any]) -> Dict[str, float]:
    """A product rate's own factors (a season, a trend) as the run read them this round."""
    pattern = getattr(term, "pattern", None)
    if pattern is None or env.world.patterns.configs[pattern].kind != "product":
        return {}
    key = runner.eval(term.key, scope) if term.key else item
    row = decompose(env, pattern, key=key).rows[0]
    return {name: float(value) for name, value in row["factors"].items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)}


def _date(value: Any) -> Optional[_dt.date]:
    try:
        return _dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _slot(contract: Any, pattern: str, clock: Mapping[str, Any], round_: int, rounds: int,
          date: Optional[_dt.date]) -> Optional[Tuple[str, str]]:
    """The calendar slot a round falls in for a seasonal pattern, and how a sentence names it."""
    period = (contract.patterns.get(pattern) or {}).get("period")
    if period == "year" and date is not None:
        month = date.strftime("%B")
        return month, f"in {month}"
    if period == "week" and date is not None:
        day = date.strftime("%A")
        return day, f"on {day}s"
    label = period_label(clock, round_, rounds=rounds)
    return label, (f"at {label}" if sub_day(clock) else f"in {label}")
