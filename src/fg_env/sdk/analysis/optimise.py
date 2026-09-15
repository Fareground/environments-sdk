"""Optimisation: search the decisions that maximise or minimise an objective subject to constraints.

``optimise(contract, decisions, objective, constraints)`` searches decision values (:mod:`.decisions`) for the best
objective under constraints (:mod:`.constraints`) with a search method (:mod:`.search`). Honesty is built in:

* common random numbers — every decision runs on the same seeds, so a difference comes from the decision;
* a stated confidence — a constraint is judged by a one-sided bound at ``confidence`` (or its own "with 95%
  confidence"), so a decision is *feasible with confidence*, *borderline* or *infeasible*, not merely met on its seeds;
* a margin while searching — the decisions a search keeps sit where their seeds flattered them, so the search asks each
  constraint to clear its confidence bound by :data:`SEARCH_MARGIN` times that bound's distance (see there);
* confirmation — the search's best decisions run again on new seeds, batch by batch down its ranking until one meets
  the constraints there with confidence; one that is borderline gets more seeds, doubling up to
  :data:`CONFIRMATION_GROWTH` times ``runs``, until it is confident or clearly misses. The choice is made and reported
  on those seeds alone: the seeds that picked a decision favour it, so they cannot also vouch for it;
* a held-out check — the chosen decision and the runner-up run on fresh seeds no search ever saw: the objective, the
  constraints' verdicts and the paired difference there, with a plain flag when the choice was seed luck;
* intervals on every objective and constraint value, per key for per-key constraints, and the closest decision (and by
  how much it misses) when no decision meets the constraints;
* ``uncertainty=`` draws parameters per run (:mod:`.draws`), so the decision is good across what is not known.

Two or three objectives trace a Pareto frontier instead of choosing one decision.
Seeds: runs ``0…runs-1`` search, ``runs…2·runs-1`` confirm, then ``holdout_seeds`` fresh ones, then the confirmation's
extra seeds.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike
from ..measure import RunResult
from ..seeds import SeedTree
from . import runner, search
from .assessment import Assessment, Key, assess, rank
from .constraints import Constraint, Standard, parse_constraints
from .decisions import DecisionSpace, Point, parse_decisions
from .draws import parameter_draws, with_draws
from .goals import Objective, dominates, paired, parse_objectives
from .optimise_result import OptimisationResult
from .optimize import BudgetExhausted

__all__ = ["optimise", "OptimisationResult", "SEARCH_MARGIN", "CONFIRMATION_GROWTH"]

#: How far past its bound a search asks a constraint's confidence bound to reach, in multiples of that bound's own
#: distance from the estimate. A decision kept at exactly its confidence bound on the search seeds is confident on fresh
#: seeds only about half the time: fresh seeds' estimate differs from the search's by √2 standard errors. Clearing the
#: bound by 1 + √2 times the confidence distance leaves that room, so the confirmation agrees with the same confidence.
SEARCH_MARGIN = 1 + math.sqrt(2)
#: The confirmation seeds of a borderline decision double until it is confident, clearly misses, or has this many
#: times ``runs``.
CONFIRMATION_GROWTH = 4
#: The search's best decisions are confirmed on new seeds in batches of this many …
_FINALISTS = 5
#: … for at most this many batches, while none meets the constraints there with confidence.
_CONFIRMATION_BATCHES = 4
#: A share-of-runs constraint that tolerates fewer failing runs than this per decision is judged mostly by luck.
_FEW_FAILURES = 2
#: How much a missed constraint outweighs the objective in the penalised loss the simplex searches follow.
_PENALTY = 100.0
_SIMPLEX_METHODS = ("local", "race", "nelder_mead", "cross_entropy")
_PARETO_METHODS = ("grid", "random", "lhs", "frontier")
_INPUT = re.compile(r"\$inputs\.([A-Za-z_][A-Za-z0-9_]*)")

Seeds = Tuple[int, ...]


def _inputs_read(goals: Sequence[Any]) -> Optional[FrozenSet[str]]:
    """The inputs the objectives' and constraints' expressions read (``None``: an expression reads them otherwise, so a
    run keeps all). A contract's data tables live in its inputs, and a search keeps every run it makes."""
    texts = [goal.measure.text for goal in goals]
    if any(text.count("$inputs") != len(_INPUT.findall(text)) for text in texts):
        return None
    return frozenset(name for text in texts for name in _INPUT.findall(text))


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
    #: The inputs a kept run holds on to (``None``: all of them); see :func:`_inputs_read`.
    read: Optional[FrozenSet[str]]
    count: int = 0
    _runs: Dict[Point, Dict[int, RunResult]] = field(default_factory=dict)

    def ensure(self, points: Sequence[Point], indices: Seeds) -> None:
        """Run whatever is missing of ``points`` × the seeds at ``indices``, all in one batch."""
        jobs = []
        for point in dict.fromkeys(points):
            have = self._runs.setdefault(point, {})
            inputs = {**self.fixed, **self.space.decode(point)}
            jobs += [runner.Job(inputs, self.arm, self.seeds[i], {"point": point, "run": i})
                     for i in indices if i not in have]
        if not jobs:
            return
        if self.draws is not None:
            jobs = with_draws(jobs, self.draws)
        results = runner.run_jobs(self.contract, jobs, participants=self.participants, rounds=self.rounds,
                                  workers=self.workers, pool=self.pool, hosts=self.hosts)
        for job, result in zip(jobs, results):
            kept = result if self.read is None else replace(
                result, inputs={k: v for k, v in result.inputs.items() if k in self.read})
            self._runs[job.tags["point"]][job.tags["run"]] = kept
        self.count += len(jobs)

    def results(self, point: Point, indices: Seeds) -> List[RunResult]:
        self.ensure([point], indices)
        return [self._runs[point][i] for i in indices]

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
    #: What the search asks of a constraint: the confidence, with the search's margin.
    searching: Standard
    #: Distinct decisions scored, in order, and the most search seeds each was scored on.
    order: List[Point] = field(default_factory=list)
    depth: Dict[Point, int] = field(default_factory=dict)
    exhausted: bool = False
    _assessed: Dict[Tuple[Point, Seeds, Standard], Assessment] = field(default_factory=dict)
    _scale: Optional[float] = None

    def assessment(self, point: Point, indices: Seeds, standard: Standard) -> Assessment:
        key = (point, indices, standard)
        if key not in self._assessed:
            runs = self.trials.results(point, indices)
            self._assessed[key] = assess(self.objectives, self.constraints, runs, standard,
                                         self.tree.rng("optimise-intervals", repr(point), indices[0], len(indices)))
        return self._assessed[key]

    def searched(self, point: Point, count: int) -> Assessment:
        return self.assessment(point, tuple(range(count)), self.searching)

    def score(self, points: Sequence[Point], count: int) -> List[Key]:
        distinct = list(dict.fromkeys(points))
        allowed, new = [], 0
        for point in distinct:
            if point in self.depth or len(self.depth) + new < self.budget:
                allowed.append(point)
                new += point not in self.depth
        self.trials.ensure(allowed, tuple(range(count)))
        for point in allowed:
            if point not in self.depth:
                self.order.append(point)
            self.depth[point] = max(self.depth.get(point, 0), count)
        keys = {point: rank(self.searched(point, count)) for point in allowed}
        if len(allowed) < len(distinct):
            self.exhausted = True
            raise BudgetExhausted()
        return [keys[point] for point in points]

    def signed(self, points: Sequence[Point], count: int) -> List[Optional[List[float]]]:
        """Each decision's objectives signed so larger is better, or ``None`` when it does not pass the constraints."""
        self.score(points, count)
        out: List[Optional[List[float]]] = []
        for point in points:
            a = self.searched(point, count)
            out.append(_signed(a) if a.feasible else None)
        return out

    def loss(self, point: Point) -> float:
        """A penalised objective for the simplex searches: lower is better, a missed constraint far worse."""
        tier = self.score([point], self.runs)[0][0]
        a = self.searched(point, self.runs)
        if tier == 3:
            return math.inf
        value = -a.senses[0] * a.objectives[0]["value"]
        if self._scale is None:
            self._scale = abs(value) or 1.0
        return value if tier == 0 else value + _PENALTY * self._scale * (1.0 + a.violation)


def optimise(contract: ContractLike, decisions: Mapping[str, Any], objective: Any, constraints: Any = (), *,
             runs: int = 10, seed: int = 0, method: str = "auto", budget: int = 50, workers: int = 1,
             confidence: float = 0.9, uncertainty: Any = None, holdout_seeds: Optional[int] = None,
             inputs: Optional[Mapping[str, Any]] = None, arm: Optional[str] = None, participants: Any = None,
             rounds: Optional[int] = None, data_dir: Any = None, hosts: Any = None) -> OptimisationResult:
    """Search ``decisions`` for the best ``objective`` subject to ``constraints`` (see the module notes).

    ``decisions``: ``{input: {low, high, step?} | [values] | {length|keys, low, high, step?, monotone?, sum?}}``.
    ``objective``: ``"maximise margin"``, ``"minimise p90 of cost"``, or a list of two or three for a Pareto frontier.
    ``constraints``: ``["fill_rate >= 0.95", "sl >= 0.8 in 90% of runs", "each sl_by_interval >= 0.8"]``, each held
    with ``confidence`` unless it says "with 95% confidence". ``runs`` seeds judge each decision; ``budget`` caps the
    distinct decisions searched; ``method``: auto, grid, random, lhs, local, race, nelder_mead, cross_entropy or (for a
    frontier) frontier. ``holdout_seeds`` (default ``runs``; 0 skips it) fresh seeds check the choice. ``inputs`` and
    ``arm`` fix everything else; ``uncertainty`` draws parameters per run.
    """
    runner.check_positive_int("runs", runs)
    runner.check_positive_int("budget", budget, 2)
    runner.check_positive_int("workers", workers)
    held = runs if holdout_seeds is None else runner.check_positive_int("holdout_seeds", holdout_seeds, 0)
    if rounds is not None:
        runner.check_positive_int("rounds", rounds)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.5 < confidence < 1:
        raise ValueError(f"confidence must be above 0.5 and below 1 (0.9 is 90%), got {confidence!r}")
    parsed = runner.as_contract(contract, data_dir)
    space = parse_decisions(parsed, decisions)
    objectives = parse_objectives(parsed, objective)
    checks = parse_constraints(parsed, constraints)
    fixed = dict(inputs or {})
    clash = sorted(set(fixed) & set(space.names))
    if clash:
        raise ValueError(f"{', '.join(clash)} is fixed in inputs and also a decision; leave it to one of them")
    chosen = _method(method, space, objectives, budget)
    total = (1 + CONFIRMATION_GROWTH) * runs + held
    draws = parameter_draws(parsed, uncertainty, total, seed) if uncertainty is not None else None
    tree = SeedTree(seed)
    with runner.worker_pool(workers, participants, hosts) as pool:
        trials = _Trials(parsed, space, fixed, arm, participants, rounds, workers, pool, hosts,
                         runner.run_seeds(seed, total), draws, _inputs_read([*objectives, *checks]))
        scorer = _Scorer(trials, objectives, checks, budget, runs, tree, Standard(float(confidence), SEARCH_MARGIN))
        _search(chosen, space, scorer, budget, runs, tree)
        study = _Study(parsed.name, space, objectives, checks, trials, scorer, runs, held, seed, chosen, tree,
                       Standard(float(confidence)))
        return study.pareto() if len(objectives) > 1 else study.single()


def _method(method: str, space: DecisionSpace, objectives: Sequence[Objective], budget: int) -> str:
    if method != "auto" and method not in search.METHODS:
        raise ValueError(f"method must be auto, {', '.join(search.METHODS)}; got {method!r}")
    stepped = all(axis.step is not None for axis in space.axes)
    small = stepped and space.grid_size() <= budget
    if len(objectives) > 1:
        if method not in ("auto", *_PARETO_METHODS):
            raise ValueError(f"method '{method}' follows one objective; a Pareto frontier uses "
                             f"{', '.join(_PARETO_METHODS)}")
        return method if method != "auto" else ("grid" if small else "frontier")
    if method == "frontier":
        raise ValueError("method 'frontier' traces a Pareto frontier of two or three objectives; one objective uses "
                         "grid, lhs, local, race, nelder_mead or cross_entropy")
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
    elif method == "frontier":
        search.frontier(space, scorer.signed, budget, runs, rng)
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
    #: What the choice must show: every constraint at its confidence, no margin.
    judging: Standard
    #: How many confirmation seeds each finalist has run on.
    confirmed: Dict[Point, int] = field(default_factory=dict)

    @property
    def search_seeds(self) -> Seeds:
        return tuple(range(self.runs))

    @property
    def held_seeds(self) -> Seeds:
        return tuple(range(2 * self.runs, 2 * self.runs + self.held))

    def _confirmation_seeds(self, count: int) -> Seeds:
        """The first ``count`` confirmation seeds: right after the search's, then after the held-out ones."""
        extra = 2 * self.runs + self.held
        return tuple(range(self.runs, 2 * self.runs)) + tuple(range(extra, extra + count - self.runs))

    def _complete(self) -> List[Point]:
        """Decisions scored on every search seed (a race scores most of them on fewer)."""
        return [p for p in self.scorer.order if self.scorer.depth[p] >= self.runs]

    def _base(self, **fields: Any) -> OptimisationResult:
        return OptimisationResult(self.contract, self.method, self.space.names, [o.text for o in self.objectives],
                                  [c.text for c in self.constraints], self.runs, self.seed, len(self.scorer.order),
                                  0, confidence=self.judging.confidence, history=self._history(), **fields)

    def _confirming(self, point: Point) -> Assessment:
        """A decision on its confirmation seeds alone, which played no part in the search."""
        return self.scorer.assessment(point, self._confirmation_seeds(self.confirmed[point]), self.judging)

    def _confirm(self, batch: Sequence[Point]) -> None:
        """Run a batch on the confirmation seeds, doubling them for its borderline decisions until each is confident,
        clearly misses, or has :data:`CONFIRMATION_GROWTH` times ``runs``."""
        count, most = self.runs, CONFIRMATION_GROWTH * self.runs
        unsettled = [p for p in batch if p not in self.confirmed]
        while unsettled:
            self.trials.ensure(unsettled, self._confirmation_seeds(count))
            self.confirmed.update(dict.fromkeys(unsettled, count))
            if count >= most:
                return
            unsettled = [p for p in unsettled if self._confirming(p).verdict == "borderline"]
            count = min(most, 2 * count)

    def _finalists(self) -> List[Point]:
        """The search's ranking confirmed batch by batch until a batch holds a decision that meets the constraints
        there with confidence (a borderline decision that only passed on the search seeds rarely does)."""
        ranked = sorted(self._complete(), key=lambda p: rank(self.scorer.searched(p, self.runs)))
        confirmed: List[Point] = []
        for first in range(0, min(len(ranked), _FINALISTS * _CONFIRMATION_BATCHES), _FINALISTS):
            batch = ranked[first:first + _FINALISTS]
            self._confirm(batch)
            confirmed += batch
            if any(self._confirming(p).verdict == "feasible" for p in batch):
                break
        return sorted(confirmed, key=lambda p: rank(self._confirming(p)))

    def single(self) -> OptimisationResult:
        final = self._finalists()
        best, second = final[0], (final[1] if len(final) > 1 else None)
        chosen = self._confirming(best)
        runner_up = None if second is None else {"decision": self.space.decode(second),
                                                 **self._confirming(second).to_dict()}
        result = self._base(best=self.space.decode(best), feasible=chosen.verdict == "feasible",
                            verdict=chosen.verdict, estimates=chosen.to_dict(), runner_up=runner_up,
                            holdout=self._holdout(best, second, chosen), sensitivity=self._sensitivity(best),
                            notes=self._notes(best, chosen))
        result.total_runs = self.trials.count
        return result

    def _holdout(self, best: Point, second: Optional[Point], chosen: Assessment) -> Optional[Dict[str, Any]]:
        if not self.held:
            return None
        seeds = self.held_seeds
        self.trials.ensure([best] + ([second] if second is not None else []), seeds)
        fresh = self.scorer.assessment(best, seeds, self.judging)
        out: Dict[str, Any] = {"seeds": self.held, "best": fresh.to_dict(), "verdict": fresh.verdict}
        reasons = []
        clearly = [c["constraint"] for c in fresh.constraints if c["clearly_missed"]]
        if clearly and chosen.verdict != "infeasible":
            reasons.append(f"on fresh seeds {', '.join(clearly)} clearly no longer holds")
        out["short_within_noise"] = [c["constraint"] for c in fresh.constraints
                                     if not c["met"] and not c["clearly_missed"]]
        objective, now, was = self.objectives[0], fresh.objectives[0], chosen.objectives[0]
        if now["value"] is not None and was["low"] is not None and (
                objective.sense > 0 and now["value"] < was["low"] or objective.sense < 0 and now["value"] > was["high"]):
            reasons.append(f"the objective on fresh seeds ({now['value']:.4g}) falls outside the confirmation's 95% "
                           f"interval ({was['low']:.4g} to {was['high']:.4g})")
        if second is not None:
            rival = self.scorer.assessment(second, seeds, self.judging)
            diff = paired(objective, self.trials.results(best, seeds), self.trials.results(second, seeds),
                          self.tree.rng("optimise-paired"))
            wins = rank(fresh) <= rank(rival)
            out.update(runner_up={"decision": self.space.decode(second), **rival.to_dict()}, difference=diff,
                       still_wins=wins)
            if not wins and (fresh.verdict != rival.verdict or (diff is not None and diff["clear"])):
                reasons.append("the runner-up does better on fresh seeds")
        out.update(seed_luck=bool(reasons), reasons=reasons)
        return out

    def _sensitivity(self, best: Point) -> List[Dict[str, Any]]:
        seeds = self.search_seeds
        base = self.trials.results(best, seeds)
        rows = []
        for name in self.space.names:
            for direction in (-1, 1):
                moved = self.space.shifted(best, name, direction)
                if moved is None:
                    continue
                a = self.scorer.assessment(moved, seeds, self.judging)
                change = paired(self.objectives[0], self.trials.results(moved, seeds), base,
                                self.tree.rng("optimise-sensitivity", name, direction), signed=False)
                rows.append({"decision": name, "direction": "down" if direction < 0 else "up",
                             "value": self.space.decode(moved)[name], "objective": a.objectives[0]["value"],
                             "change": change, "feasible": a.verdict == "feasible", "verdict": a.verdict,
                             "constraints": [{"constraint": c["constraint"], "value": c["value"], "met": c["met"],
                                              "verdict": c["verdict"]} for c in a.constraints]})
        return rows

    def _history(self) -> List[Dict[str, Any]]:
        rows = []
        for point in self.scorer.order:
            a = self.scorer.searched(point, self.scorer.depth[point])
            rows.append({"decision": self.space.decode(point), "runs": self.scorer.depth[point],
                         "objectives": [o["value"] for o in a.objectives],
                         "constraints": [c["value"] for c in a.constraints], "feasible": a.feasible,
                         "verdict": a.verdict, "rank": rank(a)[1]})
        return rows

    def _notes(self, best: Point, chosen: Assessment) -> List[str]:
        notes = []
        for name, where in self.space.at_edges(best).items():
            place = f" at {', '.join(where)}" if where else ""
            notes.append(f"{name} sits on a bound of its range{place}: the best may lie beyond it")
        if self.method == "local" and self.scorer.exhausted:
            notes.append(f"the search used its whole budget of {self.scorer.budget} decisions before it settled; a "
                         "larger budget may find a better one")
        if chosen.verdict == "infeasible":
            notes.append("no decision tried meets every constraint: widen the decisions' ranges, raise the budget or "
                         "relax a constraint")
        for c in chosen.constraints:
            if chosen.verdict == "borderline" and not c["confident"] and not c["clearly_missed"]:
                notes.append(f"{c['constraint']} is not settled with {c['confidence']:.0%} confidence on "
                             f"{chosen.runs} confirmation seeds: the estimate is within noise of the bound; more runs "
                             "or a decision with more room would settle it")
        for constraint in self.constraints:
            if constraint.share is None:
                continue
            tolerated = math.floor(self.runs * (1 - constraint.share) + 1e-9)
            if constraint.share >= 1:
                notes.append(f"'{constraint.text}' asks for every run, which no number of runs shows with confidence; "
                             "ask for a share below 100%")
            elif tolerated < _FEW_FAILURES:
                notes.append(f"with {self.runs} runs, '{constraint.text}' lets a decision fail in only {tolerated} "
                             "run(s), so luck decides between borderline decisions; more runs judge it better")
        failed = self.trials.failed()
        if failed:
            notes.append(f"{failed} run(s) failed and were left out")
        return notes

    def pareto(self) -> OptimisationResult:
        complete = self._complete()
        assessed = {p: self.scorer.searched(p, self.runs) for p in complete}
        feasible = [p for p in complete if assessed[p].feasible]
        frontier = _front(feasible, {p: _signed(assessed[p]) for p in feasible})
        frontier.sort(key=lambda p: -_signed(assessed[p])[0])
        notes = []
        fresh: Dict[Point, Assessment] = {}
        if self.held and frontier:
            self.trials.ensure(frontier, self.held_seeds)
            fresh = {p: self.scorer.assessment(p, self.held_seeds, self.judging) for p in frontier}
            standing = [p for p in frontier if fresh[p].verdict != "infeasible"]
            kept = set(_front(standing, {p: _signed(fresh[p]) for p in standing}))
            dropped = len(frontier) - len(kept)
            if dropped:
                notes.append(f"{dropped} frontier decision(s) are dominated or clearly miss a constraint on "
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


def _signed(a: Assessment) -> List[float]:
    return [s * o["value"] for s, o in zip(a.senses, a.objectives)]


def _front(points: Sequence[Point], values: Mapping[Point, List[float]]) -> List[Point]:
    return [p for p in points if not any(dominates(values[q], values[p]) for q in points if q != p)]
