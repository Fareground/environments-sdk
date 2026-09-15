"""How one game's result becomes a number per seat (higher is better)."""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..contract import Contract
from ..expr import ExprError, Scope, compile_expr, is_expr
from ..measure import RunResult

__all__ = ["ScoreSpec", "SeatScorer"]

#: ``None`` (the winner), an output name, an expression over $outputs/$seat, or ``fn(result, seat) -> number``.
ScoreSpec = Union[None, str, Callable[[RunResult, str], Any]]

#: A scored game: every seat's number, or ``None`` with the reason it could not be scored.
Scored = Tuple[Optional[Dict[str, float]], str]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


class SeatScorer:
    """Turns a run result into one score per seat.

    * ``None`` — the run's winner (``result.winner``, else a ``winner`` output): 1 for a winning seat, 0
      otherwise; no winner is a draw. A winner may be a seat's entity id or name, or a list of them.
    * an output name — a map of seat (id or name) → number, or a winner as above.
    * an expression — evaluated once per seat over ``$outputs``, ``$metrics``, ``$winner``, ``$seat`` (the
      seat's entity id) and ``$seat_name``: ``"$outputs.final_stacks[$seat]"``.
    * a function ``fn(result, seat_id)`` returning a number.
    """

    def __init__(self, contract: Contract, spec: ScoreSpec, seats: Sequence[str], agents: Mapping[str, str]):
        self.seats = list(seats)
        self.names = {seat: agents[seat] for seat in seats}
        self.agents = agents
        counts: Dict[str, int] = {}
        for name in self.names.values():
            counts[name] = counts.get(name, 0) + 1
        self._by_name = {name: seat for seat, name in self.names.items() if counts[name] == 1}
        self.spec = spec
        self._expr: Any = None
        if spec is None or callable(spec):
            return
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError(f"score must be an output name, an expression, or a function, got {spec!r}")
        if is_expr(spec):
            try:
                self._expr = compile_expr(spec)
            except ExprError as exc:
                raise ValueError(f"score: {exc}") from None
        elif spec not in contract.outputs:
            raise ValueError(f"score '{spec}' is not a declared output (outputs: {', '.join(contract.outputs) or 'none'}); "
                             "an expression needs a $, like $outputs.points[$seat]")

    def describe(self) -> str:
        if self.spec is None:
            return "the winner"
        if callable(self.spec):
            return f"function {getattr(self.spec, '__name__', 'score')}"
        return f"expression {self.spec}" if self._expr is not None else f"output {self.spec}"

    def __call__(self, result: RunResult) -> Scored:
        if self.spec is None:
            return self._default(result)
        if callable(self.spec):
            return self._each(lambda seat: self.spec(result, seat), "the score function")  # type: ignore[misc,operator]
        if self._expr is not None:
            def evaluate(seat: str) -> Any:
                scope = Scope({"outputs": result.outputs, "metrics": result.metrics, "winner": result.winner,
                               "seat": seat, "seat_name": self.names[seat]})
                return self._expr(scope)
            return self._each(evaluate, "the score expression")
        return self._output(result.outputs.get(str(self.spec)))

    def _default(self, result: RunResult) -> Scored:
        """How a contract scores its own seats when no ``score`` is given: today, its winner. Per-seat returns a
        contract declares (a ``game`` section) belong here, ahead of the winner."""
        return self._winners(result.winner if result.winner is not None else result.outputs.get("winner"))

    def _seat(self, key: Any) -> Optional[str]:
        if isinstance(key, Mapping):
            key = key.get("id")
        if not isinstance(key, str):
            return None
        return key if key in self.names else self._by_name.get(key)

    def _winners(self, value: Any) -> Scored:
        members: List[Any] = list(value) if isinstance(value, (list, tuple)) else \
            [] if value is None or value == "" else [value]
        winners = set()
        for member in members:
            seat = self._seat(member)
            if seat is not None:
                winners.add(seat)
            elif not (isinstance(member, str) and (member in self.agents or member in self.agents.values())):
                return None, (f"the winner {member!r} is not a seat ({', '.join(self.seats)}); "
                              "pass score= to say how seats are scored")
        return {seat: 1.0 if seat in winners else 0.0 for seat in self.seats}, ""

    def _output(self, value: Any) -> Scored:
        if isinstance(value, Mapping):
            scores: Dict[str, float] = {}
            for seat in self.seats:
                raw = value.get(seat, value.get(self.names[seat]))
                number = _number(raw)
                if number is None:
                    return None, f"output {self.spec} has no number for seat {seat} (got {raw!r})"
                scores[seat] = number
            return scores, ""
        if value is None or isinstance(value, (str, list, tuple)):
            return self._winners(value)
        return None, (f"output {self.spec} is {value!r}; a per-seat score needs a map of seat → number, a winner, "
                      "or an expression over $seat")

    def _each(self, score: Callable[[str], Any], what: str) -> Scored:
        scores: Dict[str, float] = {}
        for seat in self.seats:
            try:
                raw = score(seat)
            except ExprError as exc:
                return None, f"{what} failed for seat {seat}: {exc}"
            number = _number(raw)
            if number is None:
                return None, f"{what} gave {raw!r} for seat {seat}, not a number"
            scores[seat] = number
        return scores, ""
