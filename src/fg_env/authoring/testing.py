"""Testing a contract the model saved: checked, then played with random, idle and edge-value agents in a child
process within a time budget (:func:`tested`). Hosts it consults are answered by the SDK's stand-in stubs."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Any, NamedTuple

from ..api import ContractLike, check, contract_source, load, parse
from ..checks.smoke import EdgeAgent
from ..contract import Contract
from ..describe.metadata import game_metadata
from ..host.hosts import Hosts
from ..host.stubs import StubDescriber, StubEvaluator, StubFeed, StubGameMaster, StubRanker, StubTools, StubWriter
from ..information.reads import inspect_tool, inspectable
from ..participants import Idle, RandomAgent
from ..runtime.diagnostics import DEGRADING
from ..runtime.measure import RunResult
from ..runtime.session import Wake
from ..runtime.turn import Turn
from .findings import Seen
from .sandbox import Sandbox, TooSlow, step

__all__ = ["TEST_SEEDS", "MOST_SEEDS", "TEST_SECONDS", "Tested", "tested", "contract_problem", "StubHosts"]

#: Seeds every saved contract is run on, with random agents and with idle ones.
TEST_SEEDS = (1, 2, 3)
#: Most seeds random agents play a saved contract that draws on chance: when every run on :data:`TEST_SEEDS` finished
#: with test time to spare, random agents play on further seeds while it lasts, so a problem that shows in one run in
#: ten is found too. A contract that draws nothing at random is done after :data:`TEST_SEEDS`.
MOST_SEEDS = 20
#: Longest testing a saved contract may take: its check, then all its test runs together. A run still going then has
#: passed the rounds it reached: the contract works, with the rest of its rounds untested. A check, or a single turn,
#: still going then makes the contract too slow to test.
TEST_SECONDS = 60


class Tested(NamedTuple):
    """What testing a contract found (see :func:`tested`)."""

    #: What stops the contract from working, or "".
    problem: str
    #: How far the test runs got when the time budget ended them ("" when every run finished).
    untested: str = ""
    #: Its check's warnings, then what its runs showed (see :mod:`fg_env.authoring.findings`): an output or metric
    #: that came out the same in every run, a round that sends models a great many prompt tokens, an agent type whose
    #: brief never says what it is after.
    warnings: tuple[str, ...] = ()
    #: How many seeds random agents played it on.
    seeds: int = 0
    #: The hosts its runs consulted, answered by the SDK's stand-in stubs.
    hosts: tuple[str, ...] = ()
    #: ``(average, largest)`` tokens of what an agent read in a turn of its runs: its brief and update.
    prompt: tuple[int, int] = (0, 0)


def tested(source: ContractLike, box: Sandbox | None = None) -> Tested:
    """What testing the contract finds. Its ``problem`` is what stops it from working, or "": its first check error;
    else that it declares no outputs, or has an agent type with no action; else the first of its test runs — on each
    of :data:`TEST_SEEDS` with random agents and with idle ones (agents that never act), once with agents that choose
    each tool's edge values, then — when the contract draws on chance — with random agents on more seeds, up to
    :data:`MOST_SEEDS`, while test time is left — that fails, has an output that fails, does not show how the
    environment plays (``RunResult.degraded``; for idle and edge-value agents, beyond their not acting — so random
    agents, who write a real sentence for free text, must get some action through) or in which an agent's choice breaks
    a rule. Every test agent reads its brief and update each turn, and in every stage of every round each view its type
    may look at and an entity of every type it may inspect are read, however few free reads a turn allows, so a view
    that breaks on a state play reaches is found. Anything evaluating the contract raises is its problem too. Its
    ``warnings`` are its check's, then what the runs showed (:mod:`fg_env.authoring.findings`).

    It all runs in a child process within :data:`TEST_SECONDS`: the check first, then the runs, each taking an even
    share of what is left. A run still going when its share ends has passed the rounds it reached, and ``untested``
    then says how far the runs got; a check or a turn still going when the time is up makes the contract too slow to
    test. Hosts the contract consults are answered by the SDK's stand-in stubs. ``box`` is the child process to use
    (by default, one of its own)."""
    request = {"source": _plain(source), "seconds": TEST_SECONDS, "seeds": list(TEST_SEEDS), "most": MOST_SEEDS}
    try:
        if box is None:
            with Sandbox() as own:
                found = own.call("fg_env.authoring.testing:_test", request, TEST_SECONDS)
        else:
            found = box.call("fg_env.authoring.testing:_test", request, TEST_SECONDS)
    except TooSlow as exc:
        return Tested(f"too slow to test: {exc.step or 'starting'} was still going when the {TEST_SECONDS:g}s test "
                      "budget ran out → make each round cheaper: fewer entities, or views and rules that do not go "
                      "over every entity for every agent (such a view grows with the square of their number)")
    except RuntimeError as exc:  # the child died: a contract can break the engine in any way
        return Tested(str(exc))
    return Tested(found["problem"], found["untested"], tuple(found["warnings"]), found["seeds"], tuple(found["hosts"]),
                  tuple(found["prompt"]))


def contract_problem(source: ContractLike) -> str:
    """What stops a contract from working, or "" when it works (see :func:`tested`)."""
    return tested(source).problem


def _plain(source: ContractLike) -> Any:
    """``source`` as JSON data a child process can read: a path or JSON text as it is, a contract as written."""
    if isinstance(source, Contract):
        return contract_source(source)
    return str(source) if isinstance(source, os.PathLike) else source


def _test(source: Any, seconds: float, seeds: list[int], most: int) -> dict[str, Any]:
    """:func:`tested`'s work, in the child process: its findings as JSON data."""
    hosts, deadline, seen = StubHosts(source), time.monotonic() + seconds, Seen()
    found: dict[str, Any] = {"problem": "", "untested": "", "warnings": [], "seeds": 0, "hosts": [], "prompt": [0, 0]}
    try:
        step("checking it")
        issues = check(source, hosts=hosts)
        errors = [str(i) for i in issues if i.severity == "error"]
        found["warnings"] = [str(i) for i in issues if i.severity != "error"]
        if errors:
            found["problem"] = errors[0] + (f" (and {len(errors) - 1} more: call check)" if len(errors) > 1 else "")
            return found
        contract = parse(source)
        found["problem"] = _pointless(contract)
        if not found["problem"]:
            found["problem"], found["untested"], found["seeds"] = _plays(source, contract, hosts, seconds, deadline,
                                                                         seeds, most, seen)
        if not found["problem"]:
            warned = {issue.path for issue in issues}  # what check already warned about is not said twice
            found["warnings"] += [str(i) for i in seen.warnings(contract) if i.path not in warned]
            found["prompt"] = list(seen.prompt)
    except Exception as exc:  # a model's contract can break the engine in any way: that is its problem to fix
        found["problem"] = f"{type(exc).__name__}: {exc}"
    found["hosts"] = list(hosts.asked)
    return found


def _pointless(contract: Contract) -> str:
    """What makes a contract that plays still no environment — nothing to measure, an agent that can do nothing — or
    ""."""
    if not contract.outputs:
        return ("outputs: it declares no outputs, so a run measures nothing → declare the results this environment "
                "produces (guide('outputs'))")
    acting = {kind for action in contract.actions.values() for kind in ([action.by] if isinstance(action.by, str)
                                                                        else action.by)}
    for name in contract.agent_types():
        parent = any(contract.is_a(other, name) for other in contract.types if other != name)
        if not parent and not any(contract.is_a(name, kind) for kind in acting):
            return (f"types.{name}: this agent type has no action, so its agents can do nothing → add an action with "
                    f"`\"by\": \"{name}\"` (guide('actions')), or make it a plain type")
    return ""


def _plays(source: Any, contract: Contract, hosts: Hosts, seconds: float, deadline: float, seeds: list[int],
           most: int, seen: Seen) -> tuple[str, str, int]:
    """``(problem, untested, random seeds)`` from :func:`tested`'s runs, within ``deadline`` (the end of the
    ``seconds`` of the test budget); ``seen`` gathers what they show besides."""
    plays: list[tuple[Any, str, int, frozenset]] = [
        (_Reading(RandomAgent(seed), seen) if agents == "random" else _Reading(Idle(), seen), f"{agents} agents",
         seed, frozenset() if agents == "random" else _NOT_ACTING)
        for seed in seeds for agents in ("random", "idle")]
    plays.append((_Reading(EdgeAgent(1), seen), "agents choosing edge values", 1, _NOT_ACTING))
    reached, total = [], 0
    for n, (participant, who, seed, exempt) in enumerate(plays):
        step(f"the run with {who} (seed {seed})")
        env = load(source, seed=seed, hosts=hosts)
        share = (deadline - time.monotonic()) / (len(plays) - n)
        result = env.run({"*": participant}, budget={"seconds": max(share, 0.001)})  # every run plays a round
        problem = _run_problem(result, f"{who} (seed {seed})", exempt)
        if problem:
            return problem, "", 0
        seen.runs.append(result)
        if result.budget.get("exhausted") == "seconds":
            reached.append(result.rounds)
            total = env.world.rounds
    if reached:
        return "", (f"tested at least {min(reached):,} of {total:,} rounds in every test run within the {seconds:g}s "
                    "test budget; longer runs untested"), len(seeds)
    if game_metadata(contract)["chance_mode"] == "deterministic":
        return "", "", len(seeds)  # nothing it does is luck: more seeds would only vary what random agents choose
    return _more_seeds(source, hosts, deadline, seeds, most, seen)


def _more_seeds(source: Any, hosts: Hosts, deadline: float, seeds: list[int], most: int,
                seen: Seen) -> tuple[str, str, int]:
    """Random agents on further seeds, up to ``most`` in all, while each run is likely to finish before ``deadline``:
    ``(problem, "", seeds played)``."""
    played, seed, took = len(seeds), max(seeds), 0.0
    while played < most and time.monotonic() + took < deadline:
        seed += 1
        step(f"the run with random agents (seed {seed})")
        began = time.monotonic()
        result = load(source, seed=seed, hosts=hosts).run({"*": _Reading(RandomAgent(seed), seen)},
                                                          budget={"seconds": deadline - began})
        problem = _run_problem(result, f"random agents (seed {seed})", frozenset())
        if problem:
            return problem, "", played
        seen.runs.append(result)
        if result.budget.get("exhausted") == "seconds":
            break  # the time is spent: the rounds this run reached are tested, the seed does not count
        played, took = played + 1, time.monotonic() - began
    return "", "", played


def _run_problem(result: RunResult, who: str, exempt: frozenset) -> str:
    """What a test run with ``who`` shows is wrong — a failure, an output that fails, or a degrading or fault finding
    not among ``exempt`` — or ""."""
    if result.status == "failed":
        return f"a run with {who} failed in round {result.rounds}: {result.error}"
    if result.output_issues:
        issue = result.output_issues[0]
        return f"a run with {who} has an output that fails: {issue['path']}: {issue['message']}"
    found = [f for f in result.diagnostics if f["code"] in (DEGRADING | _FAULTS) - exempt - _TEST_SETUP]
    if found:
        return (f"a run with {who} shows {found[0]['code']}: {found[0]['path']}: {found[0]['message']} → "
                f"{found[0]['fix']}")
    return ""


#: Findings that an agent's choice broke a rule as its action applied (the action was refused and undone).
_FAULTS = frozenset({"action_rule_failed", "action_broke_invariant"})


#: What a test run's own setup causes, not the contract: its share of the test time running out, and the contract's
#: declared fallbacks answering in place of hosts (a feed's fallback is what a test run plays).
_TEST_SETUP = frozenset({"budget_cut", "host_fallback"})


#: What says nothing about a contract when its test agents mean not to act, or act only on the edges: that they never
#: acted, and that agents waiting on them (a chair with no raised hand to recognise) never could.
_NOT_ACTING = frozenset({"agents_never_acted", "agents_never_able_to_act"})


class _Reading:
    """``agent``, reading first each turn as a model does — its brief and update, whose size ``seen`` counts — and, the
    first time an agent of its type is woken in a stage of a round, every view it may look at and an entity of every
    type it may inspect, beyond the turn's free reads (they spend none of them): a view or template that fails on a
    state play reaches fails the run, however many views there are and however few reads a turn allows."""

    def __init__(self, agent: Any, seen: Seen) -> None:
        self.agent, self.seen, self.round = agent, seen, 0
        #: What agents have read this round: ``(stage, agent type, "look" or "inspect", view or entity type)``.
        self.read: set[tuple[str, str, str, str]] = set()

    def __call__(self, wake: Wake) -> None:
        self.seen.read(len(wake.brief) + len(wake.update))
        turn = wake._turn  # read straight from the turn, as its look and inspect tools do, but without their allowance
        with turn.env._lock:
            if turn.round != self.round:
                self.round, self.read = turn.round, set()
            for kind, name, args in _reads(turn):
                key = (turn.stage.name, turn.actor.entity_type, kind, name)
                if key not in self.read:
                    self.read.add(key)
                    turn._look(args) if kind == "look" else turn._inspect(args)
        self.agent(wake)


def _reads(turn: Turn) -> list[tuple[str, str, dict[str, Any]]]:
    """``(kind, what, args)`` of a ``look`` at every view ``turn`` offers, and an ``inspect`` of one entity of every
    type it may inspect (a different one each round)."""
    env, actor = turn.env, turn.actor
    reads = [("look", view, {"view": view}) for view in env.information.look_views(actor)]
    if inspect_tool(env.information, actor, turn.ledger.max_calls) is not None:
        members: dict[str, list[str]] = {}
        for entity in inspectable(env.information, actor):
            members.setdefault(entity.entity_type, []).append(entity.id)
        reads += [("inspect", kind, {"id": ids[turn.round % len(ids)]}) for kind, ids in members.items()]
    return reads


class StubHosts(Hosts):
    """Every host the contract at ``source`` names, answered by the SDK's deterministic stand-ins
    (:mod:`fg_env.host.stubs`), so a judged or game-mastered contract is tested without a model — except a feed's host
    when the feed declares a fallback: the stub's numbers are no data, the contract's own stand-in is. ``asked`` names
    the hosts consulted."""

    def __init__(self, source: Any) -> None:
        super().__init__()
        try:
            feeds = parse(source).feeds
        except Exception:  # check reports what is wrong with it
            feeds = {}
        self.unbound = {spec.host for spec in feeds.values() if spec.fallback is not None}
        self.asked: list[str] = []
        self._stub = SimpleNamespace(judge=StubEvaluator().judge, resolve=StubGameMaster().resolve,
                                     call=StubTools().call, write=StubWriter().write, rank=StubRanker().rank,
                                     fetch=StubFeed().fetch, describe=StubDescriber().describe)

    def adapter(self, name: str) -> Any:
        if name in self.unbound:
            return None
        if name not in self.asked:
            self.asked.append(name)
        return self._stub

    @property
    def names(self) -> list[str]:
        return list(self.asked)
