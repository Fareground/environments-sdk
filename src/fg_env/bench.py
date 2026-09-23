"""``fg-env bench``: how fast the engine runs contracts — build time, milliseconds per round, rounds per
second, and where each round's time goes (events, stages, physics, triggers, invariants, metrics).

Phase times are exclusive: a trigger checked inside an event counts as trigger time, not event time.
With no contracts, the reference models in the source tree's ``examples/contracts`` are measured.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

from . import run_rounds as _run_rounds
from .api import load, parse

__all__ = ["REFERENCE_MODELS", "PHASES", "BenchResult", "bench", "bench_table"]

#: Reference models measured when no contract is given (files in examples/contracts).
REFERENCE_MODELS = ("boltzmann_wealth", "schelling", "game_of_life", "forest_fire", "wolf_sheep", "sugarscape_lite",
                    "corner_shop_town")
PHASES = ("events", "stages", "physics", "triggers", "invariants", "metrics", "other")

_EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "contracts"


@dataclass
class BenchResult:
    """One contract's timing."""

    name: str
    rounds: int
    status: str
    build_ms: float
    run_ms: float
    #: Milliseconds per round spent in each phase (see :data:`PHASES`).
    phases: Dict[str, float] = field(default_factory=dict)

    @property
    def ms_per_round(self) -> float:
        return self.run_ms / self.rounds if self.rounds else 0.0

    @property
    def rounds_per_second(self) -> float:
        return 1000.0 * self.rounds / self.run_ms if self.run_ms else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "rounds": self.rounds, "status": self.status, "build_ms": round(self.build_ms, 3),
                "ms_per_round": round(self.ms_per_round, 3), "rounds_per_second": round(self.rounds_per_second, 2),
                "phases_ms_per_round": {name: round(value, 3) for name, value in self.phases.items()}}


class _Stopwatch:
    """Exclusive time per phase: entering a phase pauses the one it runs inside."""

    def __init__(self) -> None:
        self.spent: Dict[str, float] = {name: 0.0 for name in PHASES}
        self._stack: List[List[Any]] = []

    def enter(self, phase: str) -> None:
        now = time.perf_counter()
        if self._stack:
            top = self._stack[-1]
            self.spent[top[0]] += now - top[1]
        self._stack.append([phase, now])

    def leave(self) -> None:
        now = time.perf_counter()
        phase, started = self._stack.pop()
        self.spent[phase] += now - started
        if self._stack:
            self._stack[-1][1] = now

    def wrap(self, phase: str, call: Callable[..., Any]) -> Callable[..., Any]:
        def timed(*args: Any, **kwargs: Any) -> Any:
            self.enter(phase)
            try:
                return call(*args, **kwargs)
            finally:
                self.leave()

        return timed

    def wrap_steps(self, phase: str, steps: Callable[..., Iterator[Any]]) -> Callable[..., Iterator[Any]]:
        """Time a generator's work (between its safe points), not the time spent outside it."""
        def timed(*args: Any, **kwargs: Any) -> Iterator[Any]:
            self.enter(phase)
            try:
                running = steps(*args, **kwargs)
            finally:
                self.leave()
            while True:
                self.enter(phase)
                try:
                    point = next(running)
                except StopIteration:
                    return
                finally:
                    self.leave()
                yield point

        return timed


def bench(contracts: Optional[Sequence[Any]] = None, *, rounds: Optional[int] = None, seed: int = 1,
          inputs: Optional[Mapping[str, Any]] = None, participants: Any = None) -> List[BenchResult]:
    """Time each contract (a path, dict or :class:`Contract`; default the reference models) over ``rounds``
    rounds (default: its own length). ``inputs`` apply to every contract that declares them."""
    if rounds is not None and (isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1):
        raise ValueError(f"rounds must be a whole number ≥ 1, got {rounds!r}")
    return [_measure(item, rounds, seed, dict(inputs or {}), participants) for item in _contracts(contracts)]


def _contracts(contracts: Optional[Sequence[Any]]) -> List[Any]:
    if contracts:
        return list(contracts)
    missing = [name for name in REFERENCE_MODELS if not (_EXAMPLES / f"{name}.json").exists()]
    if missing:
        raise ValueError(f"the reference models live in the source tree's examples/contracts ({', '.join(missing)} not "
                         "found); give contract files to measure")
    return [_EXAMPLES / f"{name}.json" for name in REFERENCE_MODELS]


def _measure(contract: Any, rounds: Optional[int], seed: int, inputs: Dict[str, Any], participants: Any) -> BenchResult:
    declared = parse(contract).inputs
    started = time.perf_counter()
    env = load(contract, seed=seed, inputs={key: value for key, value in inputs.items() if key in declared})
    build_ms = (time.perf_counter() - started) * 1000
    watch = _Stopwatch()
    env.happenings.run_events = watch.wrap("events", env.happenings.run_events)  # type: ignore[method-assign]
    env.happenings.run_scheduled = watch.wrap("events", env.happenings.run_scheduled)  # type: ignore[method-assign]
    env.happenings.check_triggers = watch.wrap("triggers", env.happenings.check_triggers)  # type: ignore[method-assign]
    env._check_invariants = watch.wrap("invariants", env._check_invariants)  # type: ignore[method-assign]
    env._run_stage = watch.wrap_steps("stages", env._run_stage)  # type: ignore[method-assign, assignment]
    env.world.step_physics = watch.wrap("physics", env.world.step_physics)  # type: ignore[method-assign]
    sampler = _run_rounds.sample_metrics
    _run_rounds.sample_metrics = watch.wrap("metrics", sampler)  # type: ignore[assignment]
    try:
        watch.enter("other")
        started = time.perf_counter()
        result = env.run(participants, rounds=rounds)
        run_ms = (time.perf_counter() - started) * 1000
        watch.leave()
    finally:
        _run_rounds.sample_metrics = sampler  # type: ignore[assignment]
    played = max(1, result.rounds)
    name = Path(contract).stem if isinstance(contract, (str, Path)) else env.contract.name
    return BenchResult(name, result.rounds, result.status, build_ms, run_ms,
                       {phase: 1000 * seconds / played for phase, seconds in watch.spent.items()})


def bench_table(results: Sequence[BenchResult]) -> str:
    """The results as a plain-text table (phase columns in ms per round)."""
    header = ["model", "rounds", "build ms", "ms/round", "rounds/s", *PHASES]
    rows = [[r.name, str(r.rounds), f"{r.build_ms:.1f}", f"{r.ms_per_round:.2f}", f"{r.rounds_per_second:.1f}",
             *(f"{r.phases.get(phase, 0.0):.2f}" for phase in PHASES)] for r in results]
    widths = [max(len(line[i]) for line in [header, *rows]) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(widths[i]) if i == 0 else cell.rjust(widths[i]) for i, cell in enumerate(line))
             for line in [header, *rows]]
    return "\n".join([lines[0], "-" * len(lines[0]), *lines[1:]])
