"""What each factor of a quantity contributes: ``fg_env.decompose(contract, "demand", key="BRP-TOY-V")``.

For a ``product`` pattern every factor's value is shown with what it adds — the total less the total without that
factor (a winter index of 1.3 on a total of 13 adds 3) — and for a ``sum`` every weighted term. Given a contract,
the pattern is read round by round from a freshly built world; given a run (an :class:`~fg_env.Env`), it is read at
the run's current round, which also covers factors that carry state from the run (promotions, carry-over).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from ..api import ContractLike, load
from ..errors import ContractError, Issue
from ..expr import ExprError
from . import timebase as tb
from .base import KINDS
from .compose import operand_key, operand_names
from .runtime import Ctx, key_text

__all__ = ["decompose", "Decomposition"]


@dataclass
class Decomposition:
    """Each round: the total, every factor's value, and what each factor adds."""

    pattern: str
    key: Optional[str]
    kind: str
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def table(self, digits: int = 3) -> str:
        """The decomposition as a plain-text table, one line per round."""
        if not self.rows:
            return f"{self.pattern}: nothing to show"
        names = list(self.rows[0]["factors"])
        adds = "adds" if self.kind == "product" else "amount"
        header = ["round", "date", "total", *[f"{name} ({adds})" for name in names]]
        lines = [" | ".join(header)]
        for row in self.rows:
            cells = [str(row["round"]), row.get("date") or "", _fmt(row["total"], digits)]
            cells += [f"{_fmt(row['factors'][name], digits)} ({_fmt(row['adds'][name], digits, sign=True)})" for name in names]
            lines.append(" | ".join(cells))
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"pattern": self.pattern, "key": self.key, "kind": self.kind, "rows": self.rows}


def _fmt(value: Any, digits: int, sign: bool = False) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value)
    text = f"{value:.{digits}g}"
    return f"+{text}" if sign and value >= 0 else text


def decompose(source: Union[ContractLike, Any], pattern: str, *, key: Any = None, rounds: Optional[Union[int, Sequence[int]]] = None,
              inputs: Optional[Mapping[str, Any]] = None, seed: int = 0, data_dir: Any = None) -> Decomposition:
    """Decompose a ``product`` or ``sum`` pattern into its factors (see the module).

    ``source`` is a contract (read over ``rounds``: a count from round 1, or a list of rounds; default every round of
    the clock) or a run (read now). ``key`` is required when the pattern has keys."""
    from ..runtime import Env

    live = isinstance(source, Env)
    env: Any = source if live else load(source, inputs=dict(inputs or {}), seed=seed, data_dir=data_dir)
    runtime = env.world.patterns
    cfg = runtime.configs.get(pattern)
    where = f"decompose('{pattern}')"
    if cfg is None:
        raise ContractError([Issue(where, f"'{pattern}' is not a declared pattern", f"patterns: {', '.join(runtime.configs) or 'none'}")])
    if KINDS[cfg.kind].shape != "composite":
        raise ContractError([Issue(where, f"'{pattern}' is a {cfg.kind} pattern; decompose reads products and sums",
                                   "decompose the product or sum that combines it")])
    if cfg.keyed and key is None:
        raise ContractError([Issue(where, f"'{pattern}' has keys: pass key=")])
    text = key_text(key) if key is not None else None
    if live:
        steps = [env.world.round]
    elif rounds is None:
        steps = list(range(1, env.world.rounds + 1))
    else:
        steps = list(range(1, rounds + 1)) if isinstance(rounds, int) else list(rounds)
    result = Decomposition(pattern, text, cfg.kind)
    for number in steps:
        t = tb.now(env.world) if live else float(env.contract.clock.step * max(0, number - 1))
        try:
            result.rows.append(_row(runtime, pattern, text, t, number, env, live))
        except ExprError as exc:
            raise ContractError([Issue(where, exc.detail, "decompose a run (fg_env.decompose(env, …)) when a factor "
                                                          "carries state from it")]) from None
    return result


def _row(runtime: Any, pattern: str, key: Optional[str], t: float, number: int, env: Any, live: bool) -> Dict[str, Any]:
    source = f"decompose('{pattern}')"
    ctx = Ctx(runtime, pattern, key, t, source)
    cfg: Any = ctx.cfg
    total = runtime.evaluate(pattern, key, [], t, source)
    factors: Dict[str, Any] = {}
    adds: Dict[str, Any] = {}
    weights = ctx.numbers("weights") if cfg.kind == "sum" and cfg.weights is not None else None
    for index, name in enumerate(operand_names(cfg)):
        other = runtime.configs[name]
        if KINDS[other.kind].shape == "memory" and not live:
            raise ExprError(f"'{name}' carries state from a run, so it has no value outside one", source)
        value = runtime.evaluate(name, operand_key(ctx, index), [], t, source)
        factors[name] = value
        if cfg.kind == "product":
            adds[name] = total - total / value if value else math.nan
        else:
            adds[name] = (weights[index] if weights is not None else 1.0) * value
    when = tb.moment(tb.calendar_of(env.world), t)
    date = when.date().isoformat() if when is not None else None
    return {"round": number, "date": date, "total": total, "factors": factors, "adds": adds}
