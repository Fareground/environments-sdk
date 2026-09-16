"""``fg_env.conformance``: seeded random playouts that check, at every decision, what search and learning code
assumes of a game.

* legal — a seat that must decide has a legal call; every listed call applies without being refused (checked on
  a copy, for every listed call of every acting seat), and a playout ends within ``max_steps``.
* chance — outcome probabilities add up to 1 and every outcome applies.
* clone — a clone has the same state key and continues identically (state, every seat's observation text and
  information state, returns).
* serialize — a serialized and deserialized state has the same key and history and continues identically.
* returns — a finished playout's returns fit the declared utility class and ``min_return``/``max_return``;
  declared per-step rewards add up to the returns.
* replay — the same steps on the game built again from its contract and seed reach the same state.
* resume — the contract played by ``random`` participants ends exactly the same whether it runs straight, is
  stopped part-way and cloned, or is snapshotted between rounds (JSON) and restored.
* leak — playouts that differ only in what a seat cannot see look the same to it (see :mod:`.leaks`).

Every issue carries the seed and the steps that reproduce it: ``issue.reproduce(game)`` gives the state.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..api import ContractLike, load
from ..chance import PROBABILITY_TOLERANCE
from ..errors import ContractError, RunError, SnapshotError
from ..expr import ExprError
from ..returns import UTILITY_TOLERANCE, utility_issues
from ..runtime import Env
from .game import Game, game
from .leaks import leak_issues
from .state import GameState
from .steps import Step, apply_step, random_step, replay_steps, step_text, steps_to_json

__all__ = ["CHECKS", "ConformanceIssue", "ConformanceReport", "conformance"]

CHECKS = ("legal", "chance", "clone", "serialize", "returns", "replay", "resume", "leak")
_FAILURES = (ValueError, RunError, ExprError, ContractError, SnapshotError)


@dataclass(frozen=True)
class ConformanceIssue:
    """One problem, with the playout that shows it. ``steps`` lead from the initial state to where it shows;
    a leak also has ``other_steps``, the playout it was compared with."""

    check: str
    message: str
    sim: int
    seed: int
    steps: Tuple[Step, ...]
    history: Tuple[str, ...]
    other_steps: Optional[Tuple[Step, ...]] = None

    def reproduce(self, subject: Game) -> GameState:
        """The state the issue shows at (the game must be built with the conformance run's contract and seed)."""
        return replay_steps(subject, self.steps)

    def to_dict(self) -> Dict[str, Any]:
        out = {"check": self.check, "message": self.message, "sim": self.sim, "seed": self.seed,
               "steps": steps_to_json(self.steps), "history": list(self.history)}
        if self.other_steps is not None:
            out["other_steps"] = steps_to_json(self.other_steps)
        return out

    def __str__(self) -> str:
        path = " → ".join(self.history) or "the initial state"
        return f"[{self.check}] {self.message}\n    reproduce: seed {self.seed}, steps: {path}"


@dataclass
class ConformanceReport:
    """What :func:`conformance` checked and found."""

    game: str
    sims: int
    decisions: int = 0
    chance_nodes: int = 0
    checks: Dict[str, int] = field(default_factory=lambda: {name: 0 for name in CHECKS})
    issues: List[ConformanceIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, issue: ConformanceIssue) -> None:
        """Keep one issue per check and message: the one with the shortest reproduction."""
        for position, known in enumerate(self.issues):
            if known.check == issue.check and known.message == issue.message:
                if len(issue.steps) < len(known.steps):
                    self.issues[position] = issue
                return
        self.issues.append(issue)

    def to_dict(self) -> Dict[str, Any]:
        return {"game": self.game, "sims": self.sims, "decisions": self.decisions, "chance_nodes": self.chance_nodes,
                "checks": dict(self.checks), "ok": self.ok, "issues": [issue.to_dict() for issue in self.issues]}

    def summary(self) -> str:
        ran = ", ".join(f"{name} {count}" for name, count in self.checks.items() if count)
        head = (f"{self.game}: {self.sims} playouts, {self.decisions} decisions, {self.chance_nodes} chance nodes; "
                f"checks run: {ran}")
        if self.ok:
            return head + "\nconformant: no issues"
        return head + f"\n{len(self.issues)} issue(s):\n" + "\n".join(f"  {issue}" for issue in self.issues)


def conformance(source: Union[ContractLike, Game], *, sims: int = 20, seed: int = 0,
                inputs: Optional[Mapping[str, Any]] = None, simultaneous: str = "joint", leak_branches: int = 2,
                max_steps: int = 1000, resume: bool = True) -> ConformanceReport:
    """Check a game (a contract, or a :class:`Game` from :func:`fg_env.game`) over ``sims`` seeded random playouts.

    ``simultaneous="turn_based"`` checks the one-seat-at-a-time view of simultaneous stages, where the leak test
    also covers sealed choices. ``leak_branches`` is how many steps of each playout are changed for the leak test;
    ``max_steps`` is the longest a playout may run; ``resume=False`` skips the whole-run resume check.
    """
    if isinstance(sims, bool) or not isinstance(sims, int) or sims < 1:
        raise ValueError(f"sims must be a whole number ≥ 1, got {sims!r}")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError(f"max_steps must be a whole number ≥ 1, got {max_steps!r}")
    subject = source if isinstance(source, Game) else game(source, inputs=inputs, seed=seed,
                                                           simultaneous=simultaneous)
    report = ConformanceReport(subject.id, sims)
    _Checker(subject, report, seed, leak_branches, max_steps, resume).run(sims)
    return report


class _Checker:
    def __init__(self, subject: Game, report: ConformanceReport, seed: int, leak_branches: int, max_steps: int,
                 resume: bool):
        self.game = subject
        self.again = subject.rebuilt()
        self.report = report
        self.seed = seed
        self.leak_branches = leak_branches
        self.max_steps = max_steps
        self.resume = resume
        spec = subject.contract.game
        self.has_returns = spec is not None and spec.returns is not None
        self.declared_rewards = self.has_returns and spec is not None and spec.rewards is not None

    def run(self, sims: int) -> None:
        for sim in range(sims):
            self.sim = sim
            rng = random.Random(f"fg-env-conformance:{self.seed}:{sim}")
            steps = self._playout(rng)
            if steps is not None and self.leak_branches > 0:
                self._leaks(steps, rng)
            if self.resume:
                self._resume(rng)

    # -- recording ----------------------------------------------------------------------------------------

    def issue(self, check: str, message: str, steps: Sequence[Step], other: Optional[Sequence[Step]] = None) -> None:
        self.report.add(ConformanceIssue(check, message, self.sim, self.seed, tuple(steps), self._history(steps),
                                         tuple(other) if other is not None else None))

    def _history(self, steps: Sequence[Step]) -> Tuple[str, ...]:
        try:
            state = self.game.new_initial_state()
        except _FAILURES:
            return tuple(json.dumps(step, sort_keys=True, default=str) for step in steps)
        texts = []
        try:
            for step in steps:
                texts.append(step_text(state, step))
                apply_step(state, step)
        except _FAILURES:
            texts.extend(json.dumps(step, sort_keys=True, default=str) for step in steps[len(texts):])
        finally:
            state.close()
        return tuple(texts)

    # -- one playout --------------------------------------------------------------------------------------

    def _playout(self, rng: random.Random) -> Optional[List[Step]]:
        steps: List[Step] = []
        earned = [0.0] * self.game.num_players()
        try:
            state = self.game.new_initial_state()
        except _FAILURES as exc:
            self.issue("legal", f"the game cannot start: {exc}", steps)
            return None
        try:
            while not state.is_terminal():
                if len(steps) >= self.max_steps:
                    self.issue("legal", f"the playout did not end within {self.max_steps} steps", steps)
                    return None
                self._node(state, steps)
                try:
                    step = random_step(state, rng)
                except ValueError as exc:
                    self.issue("legal", str(exc), steps)
                    return None
                if not self._advance(state, step, steps):
                    return None
                if self.declared_rewards:
                    earned = [total + reward for total, reward in zip(earned, state.rewards())]
            self._finished(state, steps, earned)
            return steps
        except _FAILURES as exc:
            self.issue("legal", f"the playout failed: {exc}", steps)
            return None
        finally:
            state.close()

    def _node(self, state: GameState, steps: List[Step]) -> None:
        if state.is_chance_node():
            self.report.chance_nodes += 1
            self.report.checks["chance"] += 1
            outcomes = state.chance_outcomes()
            total = sum(p for _, p in outcomes)
            if not outcomes or abs(total - 1) > PROBABILITY_TOLERANCE * max(1, len(outcomes)):
                self.issue("chance", f"the outcome probabilities add up to {total:.10g}, not 1", steps)
            for outcome, _ in outcomes:
                self._try(state, {"chance": outcome}, steps, "chance")
            return
        self.report.decisions += 1
        self.report.checks["legal"] += 1
        acting = state.acting_players()
        joint = state.is_simultaneous_node()
        listed = {seat: state.legal_tool_calls(seat) for seat in acting}
        for seat in acting:
            if not listed[seat] and not state.unlisted_actions(seat):
                self.issue("legal", f"seat {seat} ({self.game.players[seat]}) must decide but has no legal action", steps)
        if any(not calls for calls in listed.values()):
            return
        for seat in acting:
            for action in listed[seat]:
                call = {"tool": action.tool, "args": dict(action.args)}
                if joint:
                    others = {other: {"tool": listed[other][0].tool, "args": dict(listed[other][0].args)}
                              for other in acting}
                    self._try(state, {"joint": {**others, seat: call}}, steps, "legal")
                else:
                    self._try(state, {"seat": seat, **call}, steps, "legal")

    def _try(self, state: GameState, step: Step, steps: List[Step], check: str) -> None:
        child = state.clone()
        try:
            apply_step(child, step)
        except _FAILURES as exc:
            self.issue(check, f"{step_text(state, step)} is offered, but applying it fails: {exc}", steps + [step])
        finally:
            child.close()

    def _advance(self, state: GameState, step: Step, steps: List[Step]) -> bool:
        """Apply ``step`` to the state, a clone and a deserialized copy, and compare where they end up."""
        twin = state.clone()
        copies: List[Tuple[str, GameState]] = [("clone", twin)]
        key = state.state_key()
        self.report.checks["clone"] += 1
        if twin.state_key() != key:
            self.issue("clone", "a clone's state key differs from the state's", steps)
        self.report.checks["serialize"] += 1
        try:
            copy = self.game.deserialize_state(state.serialize())
            copies.append(("serialize", copy))
            if copy.state_key() != key or copy.history() != state.history():
                self.issue("serialize", "the deserialized state differs from the state (key or history)", steps)
        except _FAILURES as exc:
            self.issue("serialize", f"the state does not deserialize: {exc}", steps)
        try:
            try:
                apply_step(state, step)
            except _FAILURES as exc:
                self.issue("legal", f"{step_text(twin, step)} was chosen from the legal calls but fails: {exc}",
                           steps + [step])
                return False
            steps.append(step)
            expected = self._signature(state)
            for check, other in copies:
                try:
                    apply_step(other, step)
                except _FAILURES as exc:
                    self.issue(check, f"the {check} copy refuses a step the state accepted: {exc}", steps)
                    continue
                problem = _mismatch(expected, self._signature(other))
                if problem:
                    self.issue(check, f"continuing a {check} copy gives a different {problem}", steps)
            return True
        finally:
            for _, other in copies:
                other.close()

    def _signature(self, state: GameState) -> Dict[str, Any]:
        seats = range(self.game.num_players())
        return {"state key": state.state_key(), "player to move": state.current_player(),
                "observation text": [state.observation_string(s) for s in seats],
                "information state": [state.information_state(s) for s in seats],
                "returns": state.returns() if self.has_returns else None}

    def _finished(self, state: GameState, steps: List[Step], earned: List[float]) -> None:
        if self.has_returns:
            self.report.checks["returns"] += 1
            returns = state.returns()
            for broken in utility_issues(self.game.contract, dict(zip(self.game.players, returns))):
                self.issue("returns", f"{broken.path} {broken.message}", steps)
            slack = UTILITY_TOLERANCE * max(1.0, sum(abs(value) for value in returns))
            if self.declared_rewards and any(abs(a - b) > slack for a, b in zip(earned, returns)):
                self.issue("returns", f"the rewards add up to {earned}, but the returns are {returns}", steps)
        self.report.checks["replay"] += 1
        expected = self._signature(state)
        try:
            again = replay_steps(self.again, steps)
        except _FAILURES as exc:
            self.issue("replay", f"the same steps on the game built again fail: {exc}", steps)
            return
        try:
            problem = _mismatch(expected, self._signature(again))
            if problem:
                self.issue("replay", f"the same steps on the game built again give a different {problem}", steps)
        finally:
            again.close()

    def _leaks(self, steps: List[Step], rng: random.Random) -> None:
        self.report.checks["leak"] += 1
        try:
            for leak in leak_issues(self.game, steps, rng, self.leak_branches):
                self.issue("leak", leak.message, leak.steps, leak.other_steps)
        except _FAILURES as exc:
            self.issue("leak", f"a changed playout could not be replayed: {exc}", steps)

    # -- whole runs ---------------------------------------------------------------------------------------

    def _resume(self, rng: random.Random) -> None:
        """A whole run by random participants: straight, stopped and cloned, and snapshotted and restored."""
        self.report.checks["resume"] += 1
        root = self.game._root
        run_seed = rng.randrange(2 ** 31)

        def fresh() -> Env:
            return load(root.origin.unarmed, inputs=root.inputs, seed=run_seed, arm=root.arm)

        try:
            straight = fresh().run("random").to_dict()
            stop_at = rng.randint(1, 40)
            points = {"passed": 0}

            def stop(_env: Env) -> bool:
                points["passed"] += 1
                return points["passed"] > stop_at

            stopped = fresh()
            stopped.run("random", stop=stop)
            if stopped.status == "stopped":
                twin = stopped.clone()
                self._same_run(straight, twin.run("random").to_dict(), f"stopped at safe point {stop_at} and cloned")
                self._same_run(straight, stopped.run("random").to_dict(), f"stopped at safe point {stop_at} and resumed")
            between = fresh()
            between.run("random", rounds=1)
            if not between.finished:
                snapshot = json.loads(json.dumps(between.snapshot()))
                restored = Env.restore(root.origin.unarmed, snapshot)
                self._same_run(straight, restored.run("random").to_dict(), "snapshotted after round 1 and restored")
        except _FAILURES as exc:
            self.issue("resume", f"a run with seed {run_seed} could not be resumed: {exc}", [])

    def _same_run(self, straight: Dict[str, Any], other: Dict[str, Any], how: str) -> None:
        if other != straight:
            keys = sorted(key for key in set(straight) | set(other) if straight.get(key) != other.get(key))
            self.issue("resume", f"a run {how} ends differently from the straight run (differs in {', '.join(keys)})", [])


def _mismatch(expected: Dict[str, Any], got: Dict[str, Any]) -> Optional[str]:
    for key, value in expected.items():
        if got[key] != value:
            if isinstance(value, list):
                seat = next(index for index, (a, b) in enumerate(zip(value, got[key])) if a != b)
                return f"{key} for seat {seat}"
            return key
    return None
