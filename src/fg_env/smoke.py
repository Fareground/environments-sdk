"""The smoke play of :func:`fg_env.check`: the contract built and played with random agents, then with agents that
never act, then once per declared policy, so problems that only appear with real values — in a later round, in a
policy's own rules, on a missed turn — are reported like the static ones."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple

from .contract import Contract
from .errors import Issue
from .measure import RunResult
from .participants import PolicyAgent, RandomAgent

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["SMOKE_ROUNDS", "smoke_issues", "run_issue"]

#: Rounds each play lasts when the caller names none (fewer when the run ends sooner).
SMOKE_ROUNDS = 12
#: Wall-clock seconds the plays of a default check share; every play still plays its first round.
_SMOKE_SECONDS = 2.0


def smoke_issues(contract: Contract, build: Callable[[], "Env"], rounds: Optional[int],
                 seed: int) -> Tuple[List[Issue], List[Issue]]:
    """``(errors, warnings)`` from playing the contract built by ``build``: first with random agents that read
    everything they are shown, then with every agent idle (as when a model times out or refuses), then with each policy playing the agent types whose default it is, or else the types that can take every action it
    takes (every other agent plays as in a plain run: its type's policy, or random). ``rounds`` None plays up to :data:`SMOKE_ROUNDS`
    rounds within a few seconds in all; a number plays exactly that many rounds."""
    policies = [(name, _players(contract, name)) for name in contract.policies]
    policies = [(name, players) for name, players in policies if players]
    seconds = _SMOKE_SECONDS / (2 + len(policies)) if rounds is None else None
    errors: List[Issue] = []
    warnings: List[Issue] = []

    random_play = _play(build(), {"*": _reading(RandomAgent(seed))}, rounds, seconds)
    _failure(random_play, "random agents", errors)
    for problem in random_play.output_issues:
        warnings.append(Issue(problem["path"], f"{problem['message']} after {random_play.rounds} smoke round(s)",
                              "fine if it only has a value later in a run; otherwise guard it", "warning"))
    for found in random_play.diagnostics:
        warnings.append(Issue(found["path"], f"{found['message']} (smoke run of {random_play.rounds} round(s), "
                              "random agents)", found["fix"], "warning"))
    idle_play = _play(build(), {"*": "idle"}, rounds, seconds)
    _failure(idle_play, "agents that never act", errors,
             "a turn can pass without an action (a timeout, a refusal, a forfeit): give what the action sets a default "
             "the rules allow, or guard the rule for it")
    for name, players in policies:
        agent, who = _Probing(contract, name, seed), f"policy '{name}' playing {', '.join(players)}"
        played = _play(build(), {kind: agent for kind in players}, rounds, seconds)
        _failure(played, who, errors)
        warnings.extend(Issue(found["path"], f"{found['message']} (smoke run of {played.rounds} round(s), {who})",
                              found["fix"], "warning")
                        for found in played.diagnostics
                        if found["code"] == "policy_rule_never_acted" and found["path"].startswith(f"policies.{name}."))
    return errors, warnings


def run_issue(message: str) -> Issue:
    """An error a smoke play ran into, at the path its message starts with when it names one."""
    path = None
    if ": " in message:
        head, _, rest = message.partition(": ")
        if " " not in head:
            path, message = head, rest
    return Issue(path or "(run)", message, "fix the rule at this path (found by a smoke run)")


def _players(contract: Contract, policy: str) -> List[str]:
    """The agent types a policy plays in the smoke run: those whose default it is, or else those that can take every
    action it takes."""
    agents = contract.agent_types()
    defaults = [kind for kind in agents if contract.types[kind].policy == policy]
    if defaults:
        return defaults
    actions = [contract.actions[rule.do] for rule in contract.policies[policy].rules if rule.do in contract.actions]
    return [kind for kind in agents if actions and all(
        any(contract.is_a(kind, by) for by in ([action.by] if isinstance(action.by, str) else action.by))
        for action in actions)]


def _play(env: "Env", participants: Any, rounds: Optional[int], seconds: Optional[float]) -> RunResult:
    """Play ``rounds`` rounds; or, when ``seconds`` is set, up to :data:`SMOKE_ROUNDS` rounds while time is left —
    the first round always, then stopping in the round that is under way when time runs out."""
    if seconds is None:
        return env.run(participants, rounds=rounds)
    deadline = time.monotonic() + seconds
    return env.run(participants, rounds=SMOKE_ROUNDS, stop=lambda e: e.round > 1 and time.monotonic() > deadline)


def _failure(result: RunResult, who: str, errors: List[Issue], fix: Optional[str] = None) -> None:
    """Add the error a failed play ran into, unless an earlier play already reported it."""
    if result.status != "failed":
        return
    issue = run_issue(result.error or "the run failed")
    if any(e.path == issue.path and e.message.startswith(issue.message) for e in errors):
        return
    errors.append(Issue(issue.path, f"{issue.message} (smoke run of {result.rounds} round(s), {who})", fix or issue.fix))


class _Probing(PolicyAgent):
    """A policy that also evaluates the later rules an earlier one beat to the turn."""

    _probe_later = True


def _reading(agent: Any) -> Any:
    """``agent`` after reading everything it is shown (brief and update), so a play exercises every view and
    template, not just the rules."""

    def participant(wake: Any) -> None:
        wake.brief
        wake.update
        agent(wake)

    return participant
