"""Optimisation: search the decisions that maximise or minimise an objective subject to constraints.

``optimise(contract, decisions, objective, constraints)`` searches decision values (:mod:`.decisions`) for the best
objective under constraints (:mod:`.goals`) with a search method (:mod:`.search`). Honesty is built in:

* common random numbers — every decision runs on the same seeds, so a difference comes from the decision;
* confirmation — the search's best decisions run again on as many new seeds, batch by batch down its ranking until
  one meets the constraints there, and the choice is made and reported on those seeds alone: picking the best of many
  decisions on the same seeds favours the lucky ones, so the seeds that picked them cannot also vouch for them;
* a held-out check — the chosen decision and the runner-up run on fresh seeds no search ever saw: the objective, the
  constraints and the paired difference there, with a plain flag when the choice was seed luck;
* intervals on every objective and constraint value, and the closest decision (and by how much it misses) when no
  decision meets the constraints;
* ``uncertainty=`` draws parameters per run (:mod:`.draws`), so the decision is good across what is not known.

Two or three objectives trace a Pareto frontier instead of choosing one decision.
Seeds: runs ``0…runs-1`` search, ``runs…2·runs-1`` confirm, then ``holdout_seeds`` fresh ones.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike
from ..measure import RunResult
from ..seeds import SeedTree
from . import runner, search
from .decisions import DecisionSpace, Point, parse_decisions
from .draws import parameter_draws, with_draws
from .goals import (Assessment, Constraint, Key, Objective, assess, dominates, paired, parse_constraints,
                    parse_objectives, rank)
from .optimise_result import OptimisationResult
from .optimize import BudgetExhausted

__all__ = ["optimise", "OptimisationResult"]

#: The search's best decisions are confirmed on new seeds in batches of this many …
_FINALISTS = 5
#: … for at most this many batches, while none meets the constraints on those seeds.
_CONFIRMATION_BATCHES = 4
#: A share-of-runs constraint that tolerates fewer failing runs than this per decision is judged mostly by luck.
_FEW_FAILURES = 2
#: How much a missed constraint outweighs the objective in the penalised loss the simplex searches follow.
_PENALTY = 100.0
_SIMPLEX_METHODS = ("local", "race", "nelder_mead", "cross_entropy")


@dataclass
class _Trials:
    """Every decision's runs by seed index: a decision never runs twice on one seed."""

    contract: Any
    space: DecisionSpace
    fixed: Dict[str, Any]
    arm: Optional[str]
    participants: Any
    rounds: Optional[int]
    workers: int
    pool: Any
    hosts: Any
    seeds: List[int]
    draws: Optional[List[Dict[str, Any]]]
    count: int = 0
    _runs: Dict[Point, Dict[int, RunResult]] = field(default_factory=dict)

    def ensure(self, points: Sequence[Point], count: int, start: int = 0) -> None:
        """Run whatever is missing of ``points`` × seeds ``start…start+count-1``, all in one batch."""
        jobs = []
        for point in dict.fromkeys(points):
            have = self._runs.setdefault(point, {})
            inputs = {**self.fixed, **self.space.decode(point)}
            jobs += [runner.Job(inputs, self.arm, self.seeds[i], {"point": point, "run": i})
                     for i in range(start, start + count) if i not in have]
        if not jobs:
            return
        if self.draws is not None:
            jobs = with_draws(jobs, self.draws)
        results = runner.run_jobs(self.contract, jobs, participants=self.participants, rounds=self.rounds,
                                  workers=self.workers, pool=self.pool, hosts=self.hosts)
        for job, result in zip(jobs, results):
            self._runs[job.tags["point"]][job.tags["run"]] = result
        self.count += len(jobs)

    def results(self, point: Point, count: int, start: int = 0) -> List[RunResult]:
        self.ensure([point], count, start)
        return [self._runs[point][i] for i in range(start, start + count)]

    def failed(self) -> int:
        return sum(1 for runs in self._runs.values() for r in runs.values() if r.status == "failed")


@dataclass
class _Scorer:
    """Scores decisions for the search methods on the search seeds, within the budget of distinct decisions."""

    trials: _Trials
    objectives: List[Objective]
    constraints: List[Constraint]
    budget: int
    runs: int
    tree: SeedTree
    #: Distinct decisions scored, in order, and the most search seeds each was scored on.
    order: List[Point] = field(default_factory=list)
    depth: Dict[Point, int] = field(default_factory=dict)
    exhausted: bool = False
    _assessed: Dict[Tuple[Point, int, int], Assessment] = field(default_factory=dict)
    _scale: Optional[float] = None

    def assessment(self, point: Point, count: int, start: int = 0) -> Assessment:
        key = (point, start, count)
        if key not in self._assessed:
            runs = self.trials.results(point, count, start)
            self._assessed[key] = assess(self.objectives, self.constraints, runs,
                                         self.tree.rng("optimise-intervals", repr(point), start, count))
        return self._assessed[key]

    def score(self, points: Sequence[Point], count: int) -> List[Key]:
        distinct = list(dict.fromkeys(points))
        allowed, new = [], 0
        for point in distinct:
            if point in self.depth or len(self.depth) + new < self.budget:
                allowed.append(point)
                new += point not in self.depth
        self.trials.ensure(allowed, count)
        for point in allowed:
            if point not in self.depth:
                self.order.append(point)
            self.depth[point] = max(self.depth.get(point, 0), count)
        keys = {point: rank(self.assessment(point, count)) for point in allowed}
        if len(allowed) < len(distinct):
            self.exhausted = True
            raise BudgetExhausted()
        return [keys[point] for point in points]

    def loss(self, point: Point) -> float:
        """A penalised objective for the simplex searches: lower is better, a missed constraint far worse."""
        tier, _ = self.score([point], self.runs)[0]
        a = self.assessment(point, self.runs)
        if tier == 2:
            return math.inf
        value = -a.senses[0] * a.objectives[0]["value"]
        if self._scale is None:
            self._scale = abs(value) or 1.0
        return value if tier == 0 else value + _PENALTY * self._scale * (1.0 + a.violation)


def optimise(contract: ContractLike, decisions: Mapping[str, Any], objective: Any, constraints: Any = (), *,
             runs: int = 10, seed: int = 0, method: str = "auto", budget: int = 50, workers: int = 1,
             uncertainty: Any = None, holdout_seeds: Optional[int] = None, inputs: Optional[Mapping[str, Any]] = None,
             arm: Optional[str] = None, participants: Any = None, rounds: Optional[int] = None, data_dir: Any = None,
             hosts: Any = None) -> OptimisationResult:
    """Search ``decisions`` for the best ``objective`` subject to ``constraints`` (see the module notes).

    ``decisions``: ``{input: {low, high, step?} | [values] | {length|keys, low, high, step?, monotone?, sum?}}``.
    ``objective``: ``"maximise margin"``, ``"minimise p90 of cost"``, or a list of two or three for a Pareto frontier.
    ``constraints``: ``["fill_rate >= 0.95", "sl >= 0.8 in 90% of runs"]``. ``runs`` seeds judge each decision;
    ``budget`` caps the distinct decisions searched; ``method``: auto, grid, random, lhs, local, race, nelder_mead or
    cross_entropy. ``holdout_seeds`` (default ``runs``; 0 skips it) fresh seeds check the choice. ``inputs`` and
    ``arm`` fix everything else; ``uncertainty`` draws parameters per run.
    """
    runner.check_positive_int("runs", runs)
    runner.check_positive_int("budget", budget, 2)
    runner.check_positive_int("workers", workers)
    held = runs if holdout_seeds is None else runner.check_positive_int("holdout_seeds", holdout_seeds, 0)
    if rounds is not None:
        runner.check_positive_int("rounds", rounds)
    parsed = runner.as_contract(contract, data_dir)
    space = parse_decisions(parsed, decisions)
    objectives = parse_objectives(parsed, objective)
    checks = parse_constraints(parsed, constraints)
    fixed = dict(inputs or {})
    clash = sorted(set(fixed) & set(space.names))
    if clash:
        raise ValueError(f"{', '.join(clash)} is fixed in inputs and also a decision; leave it to one of them")
    chosen = _method(method, space, objectives, budget)
    total = 2 * runs + held
    draws = parameter_draws(parsed, uncertainty, total, seed) if uncertainty is not None else None
    tree = SeedTree(seed)
    with runner.worker_pool(workers, participants, hosts) as pool:
        trials = _Trials(parsed, space, fixed, arm, participants, rounds, workers, pool, hosts,
                         runner.run_seeds(seed, total), draws)
        scorer = _Scorer(trials, objectives, checks, budget, runs, tree)
        _search(chosen, space, scorer, budget, runs, tree)
        study = _Study(parsed.name, space, objectives, checks, trials, scorer, runs, held, seed, chosen, tree)
        return study.pareto() if len(objectives) > 1 else study.single()


def _method(method: str, space: DecisionSpace, objectives: Sequence[Objective], budget: int) -> str:
    if method != "auto" and method not in search.METHODS:
        raise ValueError(f"method must be auto, {', '.join(search.METHODS)}; got {method!r}")
    stepped = all(axis.step is not None for axis in space.axes)
    small = stepped and space.grid_size() <= budget
    if len(objectives) > 1:
        if method in _SIMPLEX_METHODS:
            raise ValueError(f"method '{method}' follows one objective; a Pareto frontier uses grid, random or lhs")
        return method if method != "auto" else ("grid" if small else "lhs")
    if method != "auto":
        return method
    if small:
        return "grid"
    return "nelder_mead" if not any(axis.step is not None for axis in space.axes) else "local"


def _search(method: str, space: DecisionSpace, scorer: _Scorer, budget: int, runs: int, tree: SeedTree) -> None:
    rng = tree.rng("optimise-samples")
    if method == "grid":
        search.grid(space, scorer.score, budget, runs)
    elif method in ("random", "lhs"):
        search.sampled(space, scorer.score, budget, runs, rng, method)
    elif method == "local":
        search.local(space, scorer.score, budget, runs)
    elif method == "race":
        search.race(space, scorer.score, budget, runs, rng)
    elif method == "nelder_mead":
        search.simplex(space, scorer.loss, budget)
    else:
        search.cross_entropy(space, scorer.loss, budget, rng)


@dataclass
class _Study:
    """Turns a finished search into a result: the choice, its confirmation, the held-out check, sensitivity."""

    contract: str
    space: DecisionSpace
    objectives: List[Objective]
    constraints: List[Constraint]
    trials: _Trials
    scorer: _Scorer
    runs: int
    held: int
    seed: int
    method: str
    tree: SeedTree

    def _complete(self) -> List[Point]:
        """Decisions scored on every search seed (a race scores most of them on fewer)."""
        return [p for p in self.scorer.order if self.scorer.depth[p] >= self.runs]

    def _base(self, **fields: Any) -> OptimisationResult:
        return OptimisationResult(self.contract, self.method, self.space.names, [o.text for o in self.objectives],
                                  [c.text for c in self.constraints], self.runs, self.seed, len(self.scorer.order),
                                  0, history=self._history(), **fields)

    def _confirming(self, point: Point) -> Assessment:
        """A decision on the confirmation seeds alone, which played no part in the search."""
        return self.scorer.assessment(point, self.runs, self.runs)

    def _finalists(self) -> List[Point]:
        """The search's ranking confirmed batch by batch on new seeds, until a batch holds a decision that meets the
        constraints there (a borderline decision that only passed on the search seeds rarely does)."""
        ranked = sorted(self._complete(), key=lambda p: rank(self.scorer.assessment(p, self.runs)))
        confirmed: List[Point] = []
        for first in range(0, min(len(ranked), _FINALISTS * _CONFIRMATION_BATCHES), _FINALISTS):
            batch = ranked[first:first + _FINALISTS]
            self.trials.ensure(batch, self.runs, self.runs)
            confirmed += batch
            if any(self._confirming(p).feasible for p in batch):
                break
        return sorted(confirmed, key=lambda p: rank(self._confirming(p)))

    def single(self) -> OptimisationResult:
        final = self._finalists()
        best, second = final[0], (final[1] if len(final) > 1 else None)
        chosen = self._confirming(best)
        runner_up = None if second is None else {"decision": self.space.decode(second),
                                                 **self._confirming(second).to_dict()}
        result = self._base(best=self.space.decode(best), feasible=chosen.feasible, estimates=chosen.to_dict(),
                             runner_up=runner_up, holdout=self._holdout(best, second, chosen),
                             sensitivity=self._sensitivity(best), notes=self._notes(best, chosen))
        result.total_runs = self.trials.count
        return result

    def _holdout(self, best: Point, second: Optional[Point], chosen: Assessment) -> Optional[Dict[str, Any]]:
        if not self.held:
            return None
        start = 2 * self.runs
        self.trials.ensure([best] + ([second] if second is not None else []), self.held, start)
        fresh = self.scorer.assessment(best, self.held, start)
        out: Dict[str, Any] = {"seeds": self.held, "best": fresh.to_dict()}
        reasons = []
        missed = [c for c in fresh.constraints if not c["met"]]
        clearly = [c["constraint"] for c in missed if _clearly_missed(c)]
        if clearly and chosen.feasible:
            reasons.append(f"on fresh seeds {', '.join(clearly)} clearly no longer holds")
        out["short_within_noise"] = [c["constraint"] for c in missed if not _clearly_missed(c)]
        objective, now, was = self.objectives[0], fresh.objectives[0], chosen.objectives[0]
        if now["value"] is not None and was["low"] is not None and (
                objective.sense > 0 and now["value"] < was["low"] or objective.sense < 0 and now["value"] > was["high"]):
            reasons.append(f"the objective on fresh seeds ({now['value']:.4g}) falls outside the search's 95% interval "
                           f"({was['low']:.4g} to {was['high']:.4g})")
        if second is not None:
            rival = self.scorer.assessment(second, self.held, start)
            diff = paired(objective, self.trials.results(best, self.held, start),
                          self.trials.results(second, self.held, start), self.tree.rng("optimise-paired"))
            wins = rank(fresh) <= rank(rival)
            out.update(runner_up={"decision": self.space.decode(second), **rival.to_dict()}, difference=diff,
                       still_wins=wins)
            if not wins and (fresh.feasible != rival.feasible or (diff is not None and diff["clear"])):
                reasons.append("the runner-up does better on fresh seeds")
        out.update(seed_luck=bool(reasons), reasons=reasons)
        return out

    def _sensitivity(self, best: Point) -> List[Dict[str, Any]]:
        base = self.trials.results(best, self.runs)
        rows = []
        for name in self.space.names:
            for direction in (-1, 1):
                moved = self.space.shifted(best, name, direction)
                if moved is None:
                    continue
                a = self.scorer.assessment(moved, self.runs)
                change = paired(self.objectives[0], self.trials.results(moved, self.runs), base,
                                self.tree.rng("optimise-sensitivity", name, direction), signed=False)
                rows.append({"decision": name, "direction": "down" if direction < 0 else "up",
                             "value": self.space.decode(moved)[name], "objective": a.objectives[0]["value"],
                             "change": change, "feasible": a.feasible,
                             "constraints": [{"constraint": c["constraint"], "value": c["value"], "met": c["met"]}
                                             for c in a.constraints]})
        return rows

    def _history(self) -> List[Dict[str, Any]]:
        rows = []
        for point in self.scorer.order:
            a = self.scorer.assessment(point, self.scorer.depth[point])
            rows.append({"decision": self.space.decode(point), "runs": self.scorer.depth[point],
                         "objectives": [o["value"] for o in a.objectives],
                         "constraints": [c["value"] for c in a.constraints], "feasible": a.feasible,
                         "rank": rank(a)[1]})
        return rows

    def _notes(self, best: Point, chosen: Assessment) -> List[str]:
        notes = []
        for name, where in self.space.at_edges(best).items():
            place = f" at {', '.join(where)}" if where else ""
            notes.append(f"{name} sits on a bound of its range{place}: the best may lie beyond it")
        if self.method == "local" and self.scorer.exhausted:
            notes.append(f"the search used its whole budget of {self.scorer.budget} decisions before it settled; a "
                         "larger budget may find a better one")
        if not chosen.feasible:
            notes.append("no decision tried meets every constraint: widen the decisions' ranges, raise the budget or "
                         "relax a constraint")
        for c in chosen.constraints:
            if c["met"] and c["low"] is not None and not _surely_met(c):
                notes.append(f"{c['constraint']} is met on these seeds, but its 95% interval reaches past the bound: "
                             "it may not hold")
        for constraint in self.constraints:
            if constraint.share is None:
                continue
            tolerated = math.floor(self.runs * (1 - constraint.share) + 1e-9)
            if tolerated < _FEW_FAILURES:
                notes.append(f"with {self.runs} runs, '{constraint.text}' lets a decision fail in only {tolerated} "
                             "run(s), so luck decides between borderline decisions; more runs judge it better")
        failed = self.trials.failed()
        if failed:
            notes.append(f"{failed} run(s) failed and were left out")
        return notes

    def pareto(self) -> OptimisationResult:
        complete = self._complete()
        assessed = {p: self.scorer.assessment(p, self.runs) for p in complete}
        feasible = [p for p in complete if assessed[p].feasible]
        frontier = _front(feasible, {p: _signed(assessed[p]) for p in feasible})
        frontier.sort(key=lambda p: -_signed(assessed[p])[0])
        notes = []
        fresh: Dict[Point, Assessment] = {}
        if self.held and frontier:
            self.trials.ensure(frontier, self.held, 2 * self.runs)
            fresh = {p: self.scorer.assessment(p, self.held, 2 * self.runs) for p in frontier}
            kept = set(_front([p for p in frontier if fresh[p].feasible], {p: _signed(fresh[p]) for p in fresh}))
            dropped = len(frontier) - len(kept)
            if dropped:
                notes.append(f"{dropped} frontier decision(s) are dominated or miss a constraint on "
                             f"{self.held} fresh seed(s): the frontier there is thinner than it looks")
        else:
            kept = set(frontier)
        if not feasible and complete:
            closest = min(complete, key=lambda p: assessed[p].violation)
            notes.append(f"no decision tried meets every constraint; the closest is "
                         f"{self.space.describe(closest)}")
        rows = [{"decision": self.space.decode(p), **assessed[p].to_dict(),
                 **({"holdout": {**fresh[p].to_dict(), "on_frontier": p in kept}} if p in fresh else {})}
                for p in frontier]
        result = self._base(frontier=rows, holdout={"seeds": self.held} if fresh else None, notes=notes)
        result.total_runs = self.trials.count
        return result


def _clearly_missed(c: Dict[str, Any]) -> bool:
    """A missed constraint whose whole 95% interval is on the wrong side (without an interval, any miss counts)."""
    if c["value"] is None or c["low"] is None:
        return True
    if "share_needed" in c:
        return bool(c["high"] < c["share_needed"])
    return bool(c["high"] < c["bound"] if c["op"] in (">=", ">") else c["low"] > c["bound"])


def _surely_met(c: Dict[str, Any]) -> bool:
    """A met constraint whose whole 95% interval is on the right side of what it needs."""
    if "share_needed" in c:
        return bool(c["low"] >= c["share_needed"])
    return bool(c["low"] >= c["bound"] if c["op"] in (">=", ">") else c["high"] <= c["bound"])


def _signed(a: Assessment) -> List[float]:
    return [s * o["value"] for s, o in zip(a.senses, a.objectives)]


def _front(points: Sequence[Point], values: Mapping[Point, List[float]]) -> List[Point]:
    return [p for p in points if not any(dominates(values[q], values[p]) for q in points if q != p)]
