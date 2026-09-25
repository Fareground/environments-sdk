"""The smoke play of :func:`fg_env.check`: the contract built and played with random agents, then with agents that
choose boundary values (a parameter's least value, zero, its greatest), then with agents that never act, then with an
agent that tries to read hidden numbers out through its actions (:mod:`.probing`), then once per declared policy playing
every agent type (as ``run --agent policy:<name>`` may play it), so problems that only appear with real values — in a
later round, at an edge of what a tool allows, in a policy's own rules, on a missed turn, in a refusal that tells a
hidden number for free — are reported like the static ones."""
from __future__ import annotations

import math
import random
import time
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..contract import MAX_ENTITIES, Contract
from ..errors import Issue
from ..expr import ExprError, compile_expr
from ..participants.builtin import PolicyAgent, RandomAgent, _fill_dependent, _seed_for, sample_args
from ..runtime.diagnostics import MIN_CALLS
from ..runtime.measure import RunResult
from .probing import Prober, hides_numbers
from .reading import Reading
from .rules import scheduled_rounds

if TYPE_CHECKING:
    from ..runtime.env import Env

__all__ = ["SMOKE_ROUNDS", "EdgeAgent", "smoke_issues", "run_issue"]

#: Rounds each play lasts when the caller names none (fewer when the run ends sooner; more to reach the last round a
#: one-off event is scheduled for).
SMOKE_ROUNDS = 12
#: Rounds the boundary-value and idle plays last by default: they look for a rule that fails at an edge value or on a
#: missed turn, which the first rounds show; the random play covers the later rounds and every scheduled event.
_SHORT_ROUNDS = 4
#: Wall-clock seconds the plays of a default check share at most: a guard against a contract too slow to play, not a
#: budget — within it every check plays the same rounds, and a play it cuts short is reported.
_GUARD_SECONDS = 30.0
#: Entities gained per round late in the random play against early, above which the population is taken to compound
#: (agents creating agents) rather than grow by a steady amount (which is assumed when unsure: it projects less).
_COMPOUNDING = 1.5
#: Findings of the boundary play that are worth reporting: a rule that fails for an edge value. (Its other findings
#: say how the edges play, not whether the rules work.)
_EDGE_FINDINGS = ("action_rule_failed", "action_broke_invariant")
_EDGES = "agents choosing boundary values"


def smoke_issues(contract: Contract, build: Callable[[], Env], rounds: int | None,
                 seed: int) -> tuple[list[Issue], list[Issue]]:
    """``(errors, warnings)`` from playing the contract built by ``build``: first with random agents that read
    everything they are shown, then with agents that choose boundary values, then with every agent idle (as when a
    model times out or refuses), then with each policy played by the agents of the type that declares it (its
    subtypes too; its rules for actions a subtype cannot take are skipped for it).
    ``rounds`` None plays :data:`SMOKE_ROUNDS` rounds, or up to the last round a one-off event (`at`, a market's
    resolution) is scheduled for, so each such event is played (the boundary-value and idle plays last only the first
    few rounds); a wall-clock guard stops a play too slow to finish, and
    says so. A number plays exactly that many rounds. An action that was called in these plays and never once succeeded
    is reported too; a stage whose `until` the random play never let hold, only when no policy play let it hold."""
    agents = contract.agent_types()
    policies = [(owner, name, [kind for kind in contract.subtypes(owner) if kind in agents])
                for owner, spec in contract.types.items() for name in spec.policies]
    seconds = _GUARD_SECONDS / (3 + len(policies)) if rounds is None else None
    errors: list[Issue] = []
    warnings: list[Issue] = []
    played: list[Env] = []

    census = _Census()
    random_env = _kept(build(), played)
    default = rounds is None
    rounds = _default_rounds(random_env) if rounds is None else rounds
    random_play = _play(random_env, {"*": Reading(RandomAgent(seed))}, rounds, seconds, census)
    census.take(random_env)
    _failure(random_play, "random agents", errors)
    runaway = census.runaway(random_env)
    if runaway is not None:
        warnings.append(runaway)
    _outputs(random_play, errors, warnings)
    _random_findings(random_play, errors, warnings)
    short = min(rounds, _SHORT_ROUNDS) if default else rounds
    edge_play = _play(_kept(build(), played), {"*": EdgeAgent(seed)}, short, seconds)
    _failure(edge_play, _EDGES, errors,
             "an agent may choose any value its tool allows: bound the parameter (min, max, where) to the values the "
             "rule can handle, or guard the rule for the edge (e.g. `10 / $it.rate if $it.rate > 0 else 0`)")
    reported = {issue.path for issue in errors + warnings}
    warnings.extend(Issue(found["path"], f"{found['message']} (smoke run of {edge_play.rounds} round(s), {_EDGES})",
                          found["fix"], "warning")
                    for found in edge_play.diagnostics if found["code"] in _EDGE_FINDINGS
                    and found["path"] not in reported)
    idle_env = build()
    idle_play = _play(idle_env, {"*": "idle"}, short, seconds)
    _failure(idle_play, "agents that never act", errors,
             "a turn can pass without an action (a timeout, a refusal, a forfeit): give what the action sets a default "
             "the rules allow, or guard the rule for it")
    if hides_numbers(contract):  # one round of probing; how that play ends says nothing about the contract
        prober = Prober(seed)
        _play(build(), {"*": prober}, 1, None)
        errors.extend(prober.found.values())
    policy_plays: list[Env] = []
    for owner, name, players in policies:
        if not players:
            continue  # the static check reports a policy no agent plays
        agent, who = _Probing(contract, name, seed), f"policy '{name}' playing {', '.join(players)}"
        policy_env = _kept(build(), played)
        policy_plays.append(policy_env)
        result = _play(policy_env, {kind: agent for kind in players}, rounds, seconds)
        _failure(result, who, errors)
        prefix = f"types.{owner}.policies.{name}."
        warnings.extend(Issue(found["path"], f"{found['message']} (smoke run of {result.rounds} round(s), {who})",
                              found["fix"], "warning")
                        for found in result.diagnostics
                        if found["code"] == "policy_rule_never_acted" and found["path"].startswith(prefix))
    warnings.extend(_until_capped(random_play, policy_plays))
    reported = {issue.path for issue in errors + warnings}
    warnings.extend(issue for issue in _never_succeeded(contract, played) if issue.path not in reported)
    cut = [env for env in played + [idle_env] if env.status == "stopped"]
    if cut:
        per_action = any(invariant.check == "action" for invariant in contract.invariants)
        warnings.append(Issue("(check)", f"a smoke play was cut short by the time guard ({seconds:.0f} s a play) in "
                                         f"round {min(env.round for env in cut)} of {rounds}, so later rounds went "
                                         "unchecked: the contract plays slowly",
                              "check a smaller population or fewer rounds (`rounds=`), or profile the rules each "
                              "round runs" + ("; invariants checked after every action (`check: action`, an order "
                                              "book's `conserve: true`) go over the world each time: check them each "
                                              "round (`check: round`, `conserve: round`)" if per_action else ""),
                              "warning"))
    return errors, warnings


def _default_rounds(env: Env) -> int:
    """The rounds a default check plays: :data:`SMOKE_ROUNDS`, or up to the last round an event names in its `when`
    (``$round == 30``, which a mechanism's scheduled resolution is too), within the run's own rounds."""
    last = SMOKE_ROUNDS
    for event in env.contract.events:
        for text in scheduled_rounds(event.when):
            try:
                at = compile_expr(text)(env.world.evaluation.scope())
            except ExprError:
                continue  # a bad `when` is the static check's to report
            for moment in at if isinstance(at, list) else [at]:
                if isinstance(moment, int) and not isinstance(moment, bool):
                    last = max(last, moment)
    return min(last, env.world.rounds)


def _kept(env: Env, played: list[Env]) -> Env:
    played.append(env)
    return env


def _outputs(play: RunResult, errors: list[Issue], warnings: list[Issue]) -> None:
    """Outputs the random play could not work out: an error when the run finished (they are final), else a warning."""
    finished = play.status in ("completed", "ended")  # its outputs are final, not provisional
    for problem in play.output_issues:
        message = f"{problem['message']} after {play.rounds} smoke round(s)"
        if finished and problem["path"].startswith("outputs."):
            errors.append(Issue(problem["path"], f"{message}, at the end of the run",
                                problem.get("fix") or "fix the expression (a bare word is text: a property is read "
                                "as `$it.<name>` or `$world.<name>`); if it can be worked out only sometimes, guard "
                                "it: `<value> if <it can be worked out> else null` (null means no value)"))
        else:
            warnings.append(Issue(problem["path"], message,
                                  "fine if it only has a value later in a run; otherwise guard it", "warning"))


def _random_findings(play: RunResult, errors: list[Issue], warnings: list[Issue]) -> None:
    """The random play's diagnostics, one per cause: an action whose rule always failed is not also reported as
    offered when none of its choices could succeed (the failing rule is why). The play's own time running out
    (`budget_cut`) says nothing about the contract, and an action no call of this play got through is judged over
    every play (:func:`_never_succeeded`)."""
    broken = {found["path"] for found in play.diagnostics if found["code"] == "action_always_faulted"}
    for found in play.diagnostics:
        if found["code"] in ("output_failed", "budget_cut", "action_never_succeeded", "stage_until_capped") or (
                found["code"] == "action_offered_but_unusable" and found["path"] in broken):
            continue  # a failing output is reported by _outputs; the failing rule is why the action was unusable
        severity = "error" if found["code"] == "action_always_faulted" else "warning"  # a broken rule, not a hunch
        (errors if severity == "error" else warnings).append(
            Issue(found["path"], f"{found['message']} (smoke run of {play.rounds} round(s), random agents)",
                  found["fix"], severity))


def _until_capped(play: RunResult, policy_plays: list[Env]) -> list[Issue]:
    """The random play's stages whose `until` it never let hold, less those the contract's own policies show it can:
    random agents cannot be expected to agree or get ready, and a stage whose `until` a policy play reached waits for
    something the rules make happen."""
    out = []
    for found in play.diagnostics:
        if found["code"] != "stage_until_capped":
            continue
        stage = found["path"].split(".")[1]
        if any(ran > capped for env in policy_plays
               for _, ran, _, capped in [env.state.diagnosis.stages.get(stage, [0, 0, 0, 0])]):
            continue
        out.append(Issue(found["path"], f"{found['message']} (smoke run of {play.rounds} round(s), random agents)",
                         found["fix"], "warning"))
    return out


def _never_succeeded(contract: Contract, played: list[Env]) -> list[Issue]:
    """Actions called at least :data:`~fg_env.runtime.diagnostics.MIN_CALLS` times across the plays that act and refused
    every time: the rules or arguments the tool offers never let it happen, so what it does was never exercised.
    Actions that take free text are left out: smoke agents write placeholder text, so its refusal says nothing."""
    totals: dict[str, list[Any]] = {}  # action → [calls, applied, {cause: [count, wording]}]
    for env in played:
        for name, entry in env.state.diagnosis.actions.items():
            total = totals.setdefault(name, [0, 0, {}])
            total[0] += entry["calls"]
            total[1] += entry["applied"] + entry["faulted"]  # a rule that failed is reported on its own
            for cause, (count, text) in entry["reasons"].items():
                kept = total[2].setdefault(cause, [0, text])
                kept[0], kept[1] = kept[0] + count, min(kept[1], text)
    out = []
    for name, (calls, applied, reasons) in sorted(totals.items()):
        takes_text = any(p.type == "text" and not p.values for p in contract.actions[name].params.values())
        if calls < MIN_CALLS or applied or not reasons or takes_text:
            continue
        count, text = min(reasons.values(), key=lambda entry: (-entry[0], entry[1]))
        out.append(Issue(f"actions.{name}", f"never succeeded in the smoke plays: all {calls} call(s) were refused; "
                                            f"most often: {text.rstrip('.')} ({count}×)",
                         "make the tool offer only choices that can work: bound or list its parameters (min, max, "
                         "values, where — `values` and `where` may read earlier arguments), and put a requirement "
                         "that depends on the state in `when` with a `why`; then agents can take the action and its "
                         "rules run",
                         "warning"))
    return out


def run_issue(message: str) -> Issue:
    """An error a smoke play ran into, at the path its message starts with when it names one."""
    path = None
    if ": " in message:
        head, _, rest = message.partition(": ")
        if " " not in head:
            path, message = head, rest
    return Issue(path or "(run)", message, "fix the rule at this path (found by a smoke run)")


def _play(env: Env, participants: Any, rounds: int, seconds: float | None,
          census: _Census | None = None) -> RunResult:
    """Play ``rounds`` rounds — when ``seconds`` guards the play, stopping (status "stopped") in the round under way
    when they run out, after the first. ``census`` counts the living entities as the play goes."""
    deadline = time.monotonic() + seconds if seconds is not None else None

    def stop(e: Env) -> bool:
        if census is not None:
            census.take(e)
        return deadline is not None and e.round > 1 and time.monotonic() > deadline

    return env.run(participants, rounds=rounds, stop=stop)


class _Census:
    """The living entities of a play, the latest count kept per round, and whether that growth, kept up, would pass the
    engine's ceiling (:data:`~fg_env.contract.MAX_ENTITIES`) before the run's last round — a run that fails there."""

    def __init__(self) -> None:
        self.counts: dict[int, int] = {}
        self.start: Counter = Counter()

    def take(self, env: Env) -> None:
        if not self.counts:
            self.start = _by_type(env)
        self.counts[env.round] = env.world.types.living

    def runaway(self, env: Env) -> Issue | None:
        rounds = sorted(self.counts)
        if len(rounds) < 3:
            return None
        first, middle, last = rounds[0], rounds[len(rounds) // 2], rounds[-1]
        start, half, end = self.counts[first], self.counts[middle], self.counts[last]
        early, late = (half - start) / (middle - first), (end - half) / (last - middle)
        if late <= 0:
            return None
        if early > 0 and late > _COMPOUNDING * early:
            rate = (end / half) ** (1 / (last - middle))
            reached = last + math.ceil(math.log(MAX_ENTITIES / end) / math.log(rate))
        else:
            reached = last + math.ceil((MAX_ENTITIES - end) / late)
        total = env.world.rounds
        if reached > total:
            return None
        grew = _by_type(env) - self.start
        kind = max(sorted(grew), key=lambda name: grew[name])
        return Issue(f"types.{kind}",
                     f"the population grows from {start:,} to {end:,} living entities in {last - first} smoke "
                     f"round(s), most of them '{kind}'; at that pace it passes the ceiling of {MAX_ENTITIES:,} around "
                     f"round {reached} of {total}, and the run fails there",
                     f"bound the growth: create only while a limit holds, e.g. {{\"if\": \"$count({kind}) < 1000\", "
                     f"\"then\": [{{\"create\": \"{kind}\"}}]}}, or remove entities that are done", "warning")


def _by_type(env: Env) -> Counter:
    return Counter(entity.entity_type for entity in env.world.entities.values() if entity.alive)


def _failure(result: RunResult, who: str, errors: list[Issue], fix: str | None = None) -> None:
    """Add the error a failed play ran into, unless an earlier play already reported it."""
    if result.status != "failed":
        return
    issue = run_issue(result.error or "the run failed")
    if any(e.path == issue.path and e.message.startswith(issue.message) for e in errors):
        return
    errors.append(Issue(issue.path, f"{issue.message} (smoke run of {result.rounds} round(s), {who})",
                        fix or issue.fix))


class _Probing(PolicyAgent):
    """A policy that also evaluates the later rules an earlier one beat to the turn."""

    _probe_later = True


class _Edges(random.Random):
    """Draws on one edge of what a tool allows: a range's least value, zero (when the range holds it, else its least)
    or its greatest; a list's first, middle or last item."""

    def __init__(self, edge: int) -> None:
        super().__init__(0)
        self.edge = edge  # 0 least, 1 zero, 2 greatest

    def randint(self, a: int, b: int) -> int:
        return int(self.uniform(a, b))

    def uniform(self, a: float, b: float) -> float:
        return (a, 0 if a <= 0 <= b else a, b)[self.edge]

    def choice(self, seq: Any) -> Any:
        return seq[(0, len(seq) // 2, -1)[self.edge]]


class EdgeAgent:
    """Takes one random action a turn, with every argument on an edge of what its tool allows — its least value,
    zero, or its greatest (in turn, round by round); the first, middle or last choice: the values random play
    almost never picks, and a rule most often fails on (a division by a rate an agent set to 0)."""

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def __call__(self, wake: Any) -> None:
        acts = [t for t in wake.tools if t.kind == "act"]
        if acts and not wake.done:
            tool, edges = random.Random(_seed_for(self.seed, wake)).choice(acts), _Edges(wake.round % 3)
            wake.call(tool.name, _fill_dependent(wake, tool.name, sample_args(tool.input_schema, edges), edges))
        if not wake.done:
            wake.end()

    def __repr__(self) -> str:
        return f"EdgeAgent(seed={self.seed})"
