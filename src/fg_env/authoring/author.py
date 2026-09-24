"""``fg_env.author``: a plain-language brief in, a working contract out, written by an LLM with the SDK's own tools.

    result = fg_env.author("A corner shop orders stock every week ...", "anthropic:claude-sonnet-4-5", out="shop.json")
    print(result.summary())

The model starts from ``guide("authoring")`` and the list of engine starters, and works with seven tools —
``write_contract``, ``edit_contract``, ``start_from`` (an engine starter as its contract), ``check``, ``run``,
``preview`` and ``guide`` — until it says it is done or the budget runs out. Every contract it saves is tested
(:func:`tested`: checked, then run with random, idle and edge-value agents that read every view they may look at,
within a time budget — a long run that budget cuts short counts for the rounds it reached, and the result says so), in
a child process killed when that budget is spent (:mod:`fg_env.authoring.sandbox`), so a contract too slow to test is
reported, never hangs the session. Its host calls are answered by the SDK's stand-in stubs (:mod:`fg_env.host.stubs`).
The result keeps the best revision that works: the latest one that removes nothing the kept one has. A working
revision that removes parts (an action, a view, an output, an event, an entity ...) is named to the model and kept
only once the model saves that removal again. ``out`` is written each time a new one is kept, so an interrupted
session keeps it; a session where nothing worked writes its latest contract beside ``out`` as
``<name>.not-working.json`` instead. A model that stops before any saved contract works is sent back, with the
problem, a couple of times; one that stops with something unsettled after one worked — its last revision broken, a
removal not confirmed, its reply cut off at the output limit — is sent back once. Rate limits, overload and empty
replies are retried with backoff.

``model`` is ``"anthropic:<model>"`` or ``"openai:<model>"``, on the official client made from ``ANTHROPIC_API_KEY``
or ``OPENAI_API_KEY``. Any OpenAI-compatible server (OpenRouter, a local server) works through ``openai:``: the
client reads ``OPENAI_BASE_URL``, e.g. ``https://openrouter.ai/api/v1`` with the OpenRouter key as ``OPENAI_API_KEY``.
``client`` passes a client of your own instead.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Set, Tuple

from ..api import ContractLike, check, contract_source, load, parse
from .sandbox import Sandbox, TooSlow, step
from ..runtime.budget import CACHED_WEIGHT, is_seconds
from ..contract import Contract
from ..runtime.diagnostics import DEGRADING
from ..engines import get as engine_spec, list_engines
from ..guides import guide
from ..host.hosts import Hosts
from ..host.stubs import StubDescriber, StubEvaluator, StubFeed, StubGameMaster, StubRanker, StubTools, StubWriter
from ..runtime.measure import RunResult
from ..participants import _MAX_BACKOFF_SECONDS, _PROVIDERS, Idle, RandomAgent, _retry_after, _retryable, official_client
from ..checks.smoke import EdgeAgent
from ..runtime.session import Wake

__all__ = ["author", "AuthorResult"]

#: What a brief may spend unless ``budget`` says otherwise: model tokens (input + output, cache reads weighted by
#: :data:`~fg_env.runtime.budget.CACHED_WEIGHT` and cache writes by :data:`CACHE_WRITE_WEIGHT`), model calls, and wall-clock
#: seconds (checked before each model call, so the session ends at most one step past them).
DEFAULT_BUDGET = {"tokens": 600_000, "calls": 30, "seconds": 1800}
#: What an input token written to the provider's prompt cache counts for in the budget: providers bill it at a quarter
#: more than a fresh one.
CACHE_WRITE_WEIGHT = 1.25
#: Most contract revisions the model may save (a write that saves nothing, such as invalid JSON, is not one).
MAX_REVISIONS = 8
#: Seeds every saved contract is run on, with random agents and with idle ones.
TEST_SEEDS = (1, 2, 3)
#: Most seeds random agents play a saved contract on: when every run on :data:`TEST_SEEDS` finished with test time to
#: spare, random agents play on further seeds while it lasts, so a problem that shows in one run in ten is found too.
MOST_SEEDS = 20
#: Longest testing a saved contract may take: its check, then all its test runs together. A run still going then has
#: passed the rounds it reached: the contract works, with the rest of its rounds untested. A check, or a single turn,
#: still going then makes the contract too slow to test.
TEST_SECONDS = 60
#: Longest one call of the model's ``check``, ``run`` or ``preview`` tool may take.
RUN_SECONDS = 60
#: How often a rate-limited, overloaded or failing provider call is retried, with backoff.
RETRIES = 4
#: Longest tool result sent back to the model.
MAX_RESULT = 12000
#: How often a model that stops before any saved contract works is sent back.
MAX_NUDGES = 2
#: The reply cap on Anthropic, which requires one: the most a non-streaming call may ask for. OpenAI-compatible
#: servers get none, so a model writes as long a contract as it can.
ANTHROPIC_MAX_TOKENS = 20_000

TOOLS: List[Dict[str, Any]] = [
    {"name": "write_contract", "description": "Save the environment contract (the whole JSON object, as text). "
     "Replaces the previous version.", "parameters": {"type": "object", "properties": {
         "contract": {"type": "string", "description": "The contract as JSON text (a JSON object is taken too)."}},
         "required": ["contract"]}},
    {"name": "edit_contract", "description": "Change parts of the saved contract without writing it all again. Each "
     "edit sets the value at a path such as 'outputs.score' or 'actions.take.do[0]' (on a list, the index one past "
     "the end adds an item); an edit without a value removes what is there. Saved as a new revision.",
     "parameters": {"type": "object", "properties": {"edits": {"type": "array", "items": {
         "type": "object", "properties": {"path": {"type": "string"},
                                          "value": {"type": "string", "description": "The new value as JSON text."}},
         "required": ["path"]}}}, "required": ["edits"]}},
    {"name": "start_from", "description": "Save an engine starter as your contract (a new revision, tested like any "
     "other), to adapt to the brief with edit_contract. The reply shows the whole contract.",
     "parameters": {"type": "object", "properties": {"engine": {
         "type": "string", "enum": [spec.id for spec in list_engines(available=True)]}}, "required": ["engine"]}},
    {"name": "check", "description": "Check the saved contract: every problem with its path and a fix.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "run", "description": "Run the saved contract once and return its summary and outputs.",
     "parameters": {"type": "object", "properties": {
         "seed": {"type": "integer"},
         "participants": {"type": "object", "description": "Optional map of type or entity id to 'random', "
                          "'idle' or 'policy:<name>'. Default: random agents."}}}},
    {"name": "preview", "description": "Exactly what one agent reads on its next turn: brief, update and tools.",
     "parameters": {"type": "object", "properties": {"agent": {"type": "string", "description": "Entity id."}},
                    "required": ["agent"]}},
    {"name": "guide", "description": "Read one part of the SDK guide, e.g. 'actions', 'effects', 'functions.collections'.",
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}, "start": {
         "type": "integer", "description": "Where to start reading, in characters: a part longer than one reply is "
                                           "cut, and the cut says where to read on."}}, "required": ["part"]}},
]

INSTRUCTION = ("\n\nBuild this environment. Save it with write_contract, revise it with edit_contract, and use the other "
               "tools as you see fit. The guide's steps name fg-env commands; here your tools do them: write_contract or "
               "edit_contract saves the file, `fg-env check` is check, `fg-env preview <file> <id>` is preview(agent), "
               "`fg-env run <file> --seed N` is run(seed) and `fg-env guide <part>` is guide(part). "
               "Reply without calling a tool when you are done.\n\nEngine starters: working environments to adapt. When "
               "one is close to this brief, start from it with start_from and change what the brief needs; otherwise "
               "write your own.\n")
#: What a model that stops after a working revision that removed parts of the kept one is told once.
UNKEPT = ("Revision {latest} works, but it removed {removed}, which revision {kept} has, so revision {kept} is kept. Put "
          "back what the brief asks for; if the brief does not need them, save it again to confirm the removal. "
          "Stopping now keeps revision {kept}.")
NUDGE = "No contract is saved yet. Save it with write_contract (keep replies short; put the contract in the tool call)."
NOT_WORKING = "Nothing you saved works yet: {problem}\nFix it and save it again."
#: What a model that stops on a broken revision, after an earlier one worked, is told once.
REGRESSED = "{latest} does not work: {problem}\nStopping now keeps revision {kept}; or fix it and save it again."
#: What a reply cut off at the output limit, with no tool call, is told.
CUT_REPLY = "Your reply was cut off at the output limit. Keep replies short; put the contract in the tool call."
#: What a tool call cut off at the output limit is answered.
CUT_CALL = "Your {tool} call was cut off at the output limit, so nothing was {done}."
CUT_WRITE = (" Write the contract shorter, or save a smaller one first and add the rest with edit_contract (which "
             "also revises a saved contract without writing it all again).")

Message = Dict[str, Any]
Ask = Callable[[List[Message]], Tuple[Message, Dict[str, Any]]]


class EmptyReply(Exception):
    """A provider response with no reply in it; retried like overload."""


@dataclass
class AuthorResult:
    """What :func:`author` built. ``contract`` is the kept revision, the best that works (``ok``), else the latest one
    saved (``problem`` says what is wrong), else None."""

    contract: Optional[Dict[str, Any]]
    ok: bool
    #: What is wrong with the model's last write, or why it was not kept, when that is not the contract kept ("" when
    #: it is).
    problem: str
    #: Why the loop ended: "done"; "gave_up" (the model stopped with nothing working); "revisions" (it saved every
    #: revision it may: ``ok`` says whether one works); "refused" (the provider refused to go on); "tokens", "calls" or
    #: "seconds" (the budget); or "error: <the provider's error>".
    stop: str
    #: Every write, in order: the contract saved, or the text of one that saved nothing (not valid JSON, cut off).
    writes: List[Any]
    #: The whole conversation, in OpenAI chat format.
    messages: List[Message]
    #: input_tokens (fresh ones), cached_tokens (read from the provider's prompt cache), cache_write_tokens (written to
    #: it), output_tokens, calls, truncated (replies cut off at the output limit), and cost when the provider reports it
    #: (OpenRouter does).
    usage: Dict[str, Any]
    seconds: float
    #: Where ``contract`` was written: ``out``, or beside it as ``<name>.not-working.json`` when nothing worked.
    path: Optional[str] = None
    #: The revisions that worked, numbered from 1 in the order they were saved.
    working: List[int] = field(default_factory=list)
    #: The number of the kept revision, ``contract`` (None when none works).
    kept: Optional[int] = None
    #: What testing the kept revision found: how far its runs got when test time ran out (``untested``), its check's
    #: warnings, and the hosts the SDK's stand-in stubs answered (None when none works).
    tested: Optional["Tested"] = None

    def summary(self) -> str:
        """What it built — name, agent types, actions, stages, outputs — what changed along the way, what it used, and
        what to do next."""
        revisions = [w for w in self.writes if isinstance(w, dict)]
        kept = self.kept if self.ok and self.kept else len(revisions)
        untested = self.tested.untested if self.tested else ""
        if self.contract is None:
            lines = [f"no contract saved (stopped: {self.stop})"]
        else:
            status = ("built, PARTLY TESTED" if untested else "built") if self.ok else "NOT WORKING"
            lines = [f"{status}: {self.contract.get('name') or '(unnamed)'} — revision {kept} of {len(revisions)}, "
                     f"stopped: {self.stop}"]
        if untested:
            lines.append(f"  PARTLY TESTED: {untested}")
        if self.problem and not self.ok:
            lines.append(f"  problem: {self.problem}")
        elif self.problem:
            last = ("the last write" if self.writes[-1] is not revisions[-1] else f"revision {len(revisions)} was not kept"
                    if len(revisions) in self.working else f"revision {len(revisions)} did not work")
            lines.append(f"  kept revision {kept} of {len(revisions)}; {last}: {self.problem}")
        first = revisions[self.working[0] - 1] if self.ok and self.contract else None
        changed = _changes(first, self.contract) if first is not None and self.contract else ""
        if changed:
            lines.append(f"  changed since revision {self.working[0]}, the first that worked: {changed}")
        removed = _removed(first, self.contract) if first is not None and self.contract else []
        if removed:
            lines.append(f"  REMOVED since revision {self.working[0]}, the first that worked: {', '.join(removed)} — "
                         "check the brief is still met")
        agent = self._describe(lines)
        if self.tested and self.tested.hosts:
            lines.append(f"  hosts: {', '.join(self.tested.hosts)} answered by the SDK's stand-in stubs in testing, not a "
                         "model: bind real hosts to run it for real")
        if self.tested and self.tested.warnings:
            lines += ["  check warnings:", *(f"    {warning}" for warning in self.tested.warnings)]
        said = next((m["content"] for m in reversed(self.messages) if m["role"] == "assistant"), "").strip()
        if said:
            lines.append("  the model's last words: " + said.replace("\n", "\n    "))
        cost = f", ${self.usage['cost']:.2f}" if "cost" in self.usage else ""
        cached = f" (+{self.usage['cached_tokens']:,} cached)" if self.usage.get("cached_tokens") else ""
        fresh = self.usage["input_tokens"] + self.usage.get("cache_write_tokens", 0) + self.usage["output_tokens"]
        lines.append(f"  used: {self.usage['calls']} model calls, {fresh:,} tokens{cached}{cost}, "
                     f"{self.seconds:.0f}s")
        if self.path:
            lines.append(f"next: fg-env preview {self.path} {agent} · fg-env run {self.path} --seed 1"
                         + ("" if self.ok else f" · fg-env check {self.path}"))
        return "\n".join(lines)

    def _describe(self, lines: List[str]) -> str:
        """Add the contract's types, actions, stages and outputs to ``lines``; returns an agent id to preview."""
        agent = "<agent id>"
        try:  # a model's contract can fail to parse or build in any way; the summary then leaves this part out
            built = parse(self.contract) if self.contract is not None else None
            if built is not None and self.ok:  # population-made agents too: the built world's first agent
                agent = next((e["id"] for e in load(built, seed=1).entities() if built.types[e["type"]].agent), agent)
        except Exception:
            built = None
        if built is not None:
            types = [f"{name} (agent)" if spec.agent else name for name, spec in built.types.items()]
            for label, names in (("types", types), ("actions", list(built.actions)),
                                 ("stages", [stage.name for stage in built.stages]), ("outputs", list(built.outputs))):
                lines.append(f"  {label}: {', '.join(names) or '-'}")
        return agent


def _changes(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    """How ``after`` differs from ``before`` in what the environment is: its name and the parts :func:`_parts` names."""
    changes = [f"name {before.get('name')!r} → {after.get('name')!r}"] if before.get("name") != after.get("name") else []
    old_parts, new_parts = _parts(before), _parts(after)
    for key in [k for k in old_parts if k in new_parts]:
        old, new = old_parts[key], new_parts[key]
        if old != new:
            changes.append(f"{key} " + " ".join([f"-{k}" for k in sorted(old - new)] + [f"+{k}" for k in sorted(new - old)]))
    return "; ".join(changes)


def _removed(before: Dict[str, Any], after: Dict[str, Any]) -> List[str]:
    """The parts of ``before`` that ``after`` no longer has, e.g. ``actions.take`` or ``actions.take.params.count``."""
    old_parts, new_parts = _parts(before), _parts(after)
    gone = [f"{key}.{name}" for key, names in old_parts.items() for name in sorted(names - new_parts.get(key, set()))]
    return [path for path in gone if not any(path.startswith(other + ".") for other in gone)]  # an action, not its params


#: The contract sections made of parts: together, what an environment is.
_SECTIONS = ("inputs", "assets", "world", "types", "entities", "population", "relations", "links", "feeds", "patterns",
             "records", "actions", "stages", "views", "events", "triggers", "policies", "metrics", "outputs", "end",
             "arms", "invariants", "defs", "blocks", "mechanisms")


def _parts(contract: Dict[str, Any]) -> Dict[str, Set[str]]:
    """The names of the parts of each of :data:`_SECTIONS` (a list's item by its name, else its position), and of each
    action's params."""
    parts: Dict[str, Set[str]] = {}
    for key in _SECTIONS:
        value = contract.get(key) or {}
        parts[key] = set(value) if isinstance(value, dict) else {
            str(item.get("name", n)) if isinstance(item, dict) else str(n) for n, item in enumerate(value)}
    for name, action in (contract.get("actions") or {}).items():
        if isinstance(action, dict) and isinstance(action.get("params"), dict):
            parts[f"actions.{name}.params"] = set(action["params"])
    return parts


def author(brief: str, model: str, *, client: Any = None, out: Optional[str] = None,
           budget: Optional[Mapping[str, float]] = None, progress: Optional[Callable[[str], None]] = None) -> AuthorResult:
    """Have ``model`` (``"anthropic:<model>"`` or ``"openai:<model>"``) write an environment for ``brief``; returns an
    :class:`AuthorResult` (``result.contract``, ``result.ok``, ``result.summary()``).

    ``out`` is where the contract is written (nothing is written when None): each time a revision is kept, and at the
    end; when none works, the latest is written beside it as ``<name>.not-working.json``. ``budget`` caps ``tokens``
    (input + output, a cache read counting :data:`CACHED_WEIGHT` of one and a cache write :data:`CACHE_WRITE_WEIGHT`),
    model ``calls`` and wall-clock ``seconds``, by default :data:`DEFAULT_BUDGET`. ``client`` replaces the official client made from the environment; ``progress`` is called with one line per model call. Rate limits, overload and server errors are
    retried with backoff; a provider error that persists or that retrying cannot fix does not raise: the loop stops
    (``result.stop`` says why) and keeps what already works."""
    provider, name = _model(model)
    limits = _budget(budget)
    ask = _ANSWERERS[provider](client if client is not None else official_client(provider, name), name)
    if out and not Path(out).parent.is_dir():
        raise ValueError(f"out {out!r}: the folder {str(Path(out).parent)!r} does not exist")
    bench, started = _Workbench(), time.time()
    messages: List[Message] = [{"role": "system", "content": guide("authoring")},
                               {"role": "user", "content": brief + INSTRUCTION + _starters()}]
    usage: Dict[str, Any] = {"input_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
                             "calls": 0, "truncated": 0}
    try:
        stop = _converse(_retrying(ask, progress), messages, bench, usage, limits, progress, out,
                         started + limits["seconds"])
    finally:
        bench.box.close()
        shutil.rmtree(bench.path.parent, ignore_errors=True)
    contract = bench.best if bench.best is not None else bench.latest
    path = (out if bench.best is not None else _not_working(out)) if out and contract is not None else None
    if path and contract is not None:
        _write(path, contract)
    return AuthorResult(contract, bench.best is not None, bench.problem, stop, bench.writes, messages, usage,
                        round(time.time() - started, 1), path, bench.working, bench.kept,
                        bench.tests.get(bench.kept) if bench.kept else None)


def _starters() -> str:
    """The engine starters, one line each: its id and what it simulates."""
    return "".join(f"- {spec.id}: {spec.description.split('; ')[0]}\n" for spec in list_engines(available=True))


def _not_working(out: str) -> str:
    """Where a contract that does not work is written instead of ``out``: beside it, named so it cannot pass for it."""
    path = Path(out)
    return str(path.with_name(f"{path.stem}.not-working{path.suffix or '.json'}"))


def _converse(ask: Ask, messages: List[Message], bench: "_Workbench", usage: Dict[str, Any], limits: Dict[str, float],
              progress: Optional[Callable[[str], None]], out: Optional[str], deadline: float) -> str:
    """The tool loop, until ``deadline`` (a ``time.time()``); returns why it stopped. Each revision kept is written to
    ``out`` at once."""
    nudges, sent_back = 0, False
    while True:
        if usage["calls"] >= limits["calls"]:
            return "calls"
        if _spent(usage) >= limits["tokens"]:
            return "tokens"
        if time.time() >= deadline:
            return "seconds"
        try:
            message, used = ask(messages)
        except Exception as exc:  # the provider's error ends the loop; what already works is kept
            return f"error: {type(exc).__name__}: {exc}"[:500]
        refused = used.pop("refused", 0)
        for key, value in used.items():
            usage[key] = usage.get(key, 0) + value
        usage["calls"] += 1
        messages.append(message)
        calls = message.get("tool_calls") or []
        if progress:
            progress(f"call {usage['calls']}: {', '.join(c['function']['name'] for c in calls) or 'done'}"
                     f"{' (cut off)' if used['truncated'] else ''} "
                     f"({usage['input_tokens']:,}+{usage['output_tokens']:,} tokens)")
        if not calls and refused:
            return "refused"
        if not calls:
            if bench.best is None:
                if nudges >= MAX_NUDGES:
                    return "gave_up"
                nudges += 1  # stopped (or was cut off) before any saved contract works: send it back once more
                messages.append({"role": "user", "content": CUT_REPLY if used["truncated"] else
                                 NOT_WORKING.format(problem=bench.problem) if bench.writes else NUDGE})
                continue
            unsettled = CUT_REPLY if used["truncated"] else _unsettled(bench)
            if unsettled and not sent_back:
                sent_back = True  # stopped with something unsettled after one worked: send it back once
                messages.append({"role": "user", "content": unsettled})
                continue
            return "done"
        best = bench.best
        messages += _answer(calls, bool(used["truncated"]), bench)
        if out and bench.best is not None and bench.best is not best:
            _write(out, bench.best)
        if bench.out_of_revisions:
            return "revisions"


def _spent(usage: Dict[str, Any]) -> float:
    """The tokens ``usage`` counts against the budget."""
    return (usage["input_tokens"] + usage["output_tokens"] + usage["cached_tokens"] * CACHED_WEIGHT
            + usage["cache_write_tokens"] * CACHE_WRITE_WEIGHT)


def _unsettled(bench: "_Workbench") -> str:
    """What a model that stops is sent back once with: its last write broken or not kept; "" when it is the kept one."""
    if not bench.problem:
        return ""
    latest = len(bench.revisions)
    if bench.writes[-1] is bench.latest and latest in bench.working:
        return UNKEPT.format(latest=latest, removed=", ".join(bench.unconfirmed), kept=bench.kept)
    return REGRESSED.format(latest=f"Revision {latest}" if bench.writes[-1] is bench.latest else "Your last write",
                            problem=bench.problem, kept=bench.kept)


def _write(out: str, contract: Dict[str, Any]) -> None:
    """Write ``contract`` to ``out`` whole or not at all: a session stopped mid-write never leaves half a file."""
    path = Path(out)
    scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    scratch.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(scratch, path)


def _answer(calls: List[Dict[str, Any]], truncated: bool, bench: "_Workbench") -> List[Message]:
    """Make a reply's tool calls; returns their results. In a reply cut off at the output limit, only the last call
    is incomplete: it is answered as cut off, and the provider is sent back arguments it can read (the half-written
    ones stay in ``writes``)."""
    results = []
    for n, call in enumerate(calls):
        name, arguments = call["function"]["name"], call["function"].get("arguments") or "{}"
        if truncated and n == len(calls) - 1:
            call["function"]["arguments"] = "{}"
            text = bench.cut_off(name, arguments)
        else:
            text = bench.call(name, arguments)
        results.append({"role": "tool", "tool_call_id": call["id"], "content": text})
    return results


def _retrying(ask: Ask, progress: Optional[Callable[[str], None]]) -> Ask:
    """``ask``, retrying rate limits, overload, timeouts and server errors with backoff (honouring retry-after)."""

    def retried(messages: List[Message]) -> Tuple[Message, Dict[str, Any]]:
        for attempt in range(RETRIES + 1):
            try:
                return ask(messages)
            except Exception as exc:
                if attempt >= RETRIES or not (isinstance(exc, EmptyReply) or _retryable(exc)):
                    raise
                delay = _retry_after(exc)
                wait = min(_MAX_BACKOFF_SECONDS, delay if delay is not None else 2.0 ** attempt)
                if progress:
                    progress(f"provider busy ({type(exc).__name__}: {str(exc)[:200]}); retrying in {wait:.0f}s")
                time.sleep(wait)
        raise AssertionError("unreachable")

    return retried


def _model(model: str) -> Tuple[str, str]:
    provider, _, name = model.partition(":") if isinstance(model, str) else ("", "", "")
    if provider not in _PROVIDERS or not name:
        raise ValueError(f"model {model!r}: use 'anthropic:<model>' or 'openai:<model>' (any OpenAI-compatible "
                         "server, e.g. OpenRouter, through OPENAI_BASE_URL)")
    return provider, name


def _budget(budget: Optional[Mapping[str, float]]) -> Dict[str, float]:
    limits: Dict[str, float] = {**DEFAULT_BUDGET, **(budget or {})}
    for key, value in limits.items():
        if key not in DEFAULT_BUDGET:
            raise ValueError(f"budget has no {key!r}: use tokens, calls and seconds, e.g. {DEFAULT_BUDGET}")
        if key == "seconds":
            if not is_seconds(value):
                raise ValueError(f"budget seconds must be a number of seconds above 0, got {value!r}")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"budget {key} must be a whole number ≥ 1, got {value!r}")
    return limits


def contract_problem(source: ContractLike) -> str:
    """What stops a contract from working, or "" when it works (see :func:`tested`)."""
    return tested(source).problem


class Tested(NamedTuple):
    """What testing a contract found (see :func:`tested`)."""

    #: What stops the contract from working, or "".
    problem: str
    #: How far the test runs got when the time budget ended them ("" when every run finished).
    untested: str = ""
    #: Its check's warnings.
    warnings: Tuple[str, ...] = ()
    #: How many seeds random agents played it on.
    seeds: int = 0
    #: The hosts its runs consulted, answered by the SDK's stand-in stubs.
    hosts: Tuple[str, ...] = ()


def tested(source: ContractLike, box: Optional[Sandbox] = None) -> Tested:
    """What testing the contract finds. Its ``problem`` is what stops it from working, or "": its first check error;
    else that it declares no outputs, or has an agent type with no action; else the first of its test runs — on each
    of :data:`TEST_SEEDS` with random agents and with idle ones (agents that never act), once with agents that choose
    each tool's edge values, then with random agents on more seeds, up to :data:`MOST_SEEDS`, while test time is left —
    that fails, has an output that fails, does not show how the environment plays (``RunResult.degraded``; for idle
    and edge-value agents, beyond their not acting — so random agents, who write a real sentence for free text, must
    get some action through) or in which an agent's choice breaks a rule. Every test agent reads its brief and update
    each turn, and looks at every view and inspects an entity while its free reads last, as a model does, so a view
    that breaks on a state play reaches is found. Anything evaluating the contract raises is its problem too.

    It all runs in a child process within :data:`TEST_SECONDS`: the check first, then the runs, each taking an even
    share of what is left. A run still going when its share ends has passed the rounds it reached, and ``untested``
    then says how far the runs got; a check or a turn still going when the time is up makes the contract too slow to
    test. Hosts the contract consults are answered by the SDK's stand-in stubs. ``box`` is the child process to use
    (by default, one of its own)."""
    request = {"source": _plain(source), "seconds": TEST_SECONDS, "seeds": list(TEST_SEEDS), "most": MOST_SEEDS}
    try:
        if box is None:
            with Sandbox() as own:
                found = own.call("fg_env.authoring.author:_test", request, TEST_SECONDS)
        else:
            found = box.call("fg_env.authoring.author:_test", request, TEST_SECONDS)
    except TooSlow as exc:
        return Tested(f"too slow to test: {exc.step or 'starting'} was still going when the {TEST_SECONDS:g}s test "
                      "budget ran out → make each round cheaper: fewer entities, or views and rules that do not go "
                      "over every entity for every agent (such a view grows with the square of their number)")
    except RuntimeError as exc:  # the child died: a contract can break the engine in any way
        return Tested(str(exc))
    return Tested(found["problem"], found["untested"], tuple(found["warnings"]), found["seeds"], tuple(found["hosts"]))


def _plain(source: ContractLike) -> Any:
    """``source`` as JSON data a child process can read: a path or JSON text as it is, a contract as written."""
    if isinstance(source, Contract):
        return contract_source(source)
    return str(source) if isinstance(source, os.PathLike) else source


def _test(source: Any, seconds: float, seeds: List[int], most: int) -> Dict[str, Any]:
    """:func:`tested`'s work, in the child process: its findings as JSON data."""
    hosts, deadline = _StubHosts(source), time.monotonic() + seconds
    found: Dict[str, Any] = {"problem": "", "untested": "", "warnings": [], "seeds": 0, "hosts": []}
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
                                                                         seeds, most)
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


def _plays(source: Any, contract: Contract, hosts: Hosts, seconds: float, deadline: float, seeds: List[int],
           most: int) -> Tuple[str, str, int]:
    """``(problem, untested, random seeds)`` from :func:`tested`'s runs, within ``deadline`` (the end of the
    ``seconds`` of the test budget)."""
    plays: List[Tuple[Any, str, int, frozenset]] = [
        (_Reading(RandomAgent(seed)) if agents == "random" else _Reading(Idle()), f"{agents} agents", seed,
         frozenset() if agents == "random" else _NOT_ACTING)
        for seed in seeds for agents in ("random", "idle")]
    plays.append((_Reading(EdgeAgent(1)), "agents choosing edge values", 1, _NOT_ACTING))
    reached, total = [], 0
    for n, (participant, who, seed, exempt) in enumerate(plays):
        step(f"the run with {who} (seed {seed})")
        env = load(source, seed=seed, hosts=hosts)
        share = (deadline - time.monotonic()) / (len(plays) - n)
        result = env.run({"*": participant}, budget={"seconds": max(share, 0.001)})  # every run plays a round
        problem = _run_problem(result, f"{who} (seed {seed})", exempt)
        if problem:
            return problem, "", 0
        if result.budget.get("exhausted") == "seconds":
            reached.append(result.rounds)
            total = env.world.rounds
    if reached:
        of = f" of {total:,}" if contract.clock.mode == "rounds" else ""
        return "", (f"tested at least {min(reached):,}{of} rounds in every test run within the {seconds:g}s test "
                    "budget; longer runs untested"), len(seeds)
    return _more_seeds(source, hosts, deadline, seeds, most)


def _more_seeds(source: Any, hosts: Hosts, deadline: float, seeds: List[int], most: int) -> Tuple[str, str, int]:
    """Random agents on further seeds, up to ``most`` in all, while each run is likely to finish before ``deadline``:
    ``(problem, "", seeds played)``."""
    played, seed, took = len(seeds), max(seeds), 0.0
    while played < most and time.monotonic() + took < deadline:
        seed += 1
        step(f"the run with random agents (seed {seed})")
        began = time.monotonic()
        result = load(source, seed=seed, hosts=hosts).run({"*": _Reading(RandomAgent(seed))},
                                                          budget={"seconds": deadline - began})
        problem = _run_problem(result, f"random agents (seed {seed})", frozenset())
        if problem:
            return problem, "", played
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
    """``agent``, reading first each turn, as a model does — its brief and update, then every view it may look at and
    one entity it may inspect, while the turn's free reads last: a view or template that fails on a state play reaches
    fails the run."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def __call__(self, wake: Wake) -> None:
        wake.brief
        wake.update
        for name, args in _reads(wake)[:wake.calls_left]:  # a turn has as many free reads as calls
            wake.call(name, args)
        self.agent(wake)


def _reads(wake: Wake) -> List[Tuple[str, Dict[str, Any]]]:
    """A ``look`` at every view ``wake`` offers, then an ``inspect`` of one entity (a different one each round)."""
    reads: List[Tuple[str, Dict[str, Any]]] = []
    tools = {tool.name: tool.input_schema for tool in wake.tools}
    if "look" in tools:
        reads += [("look", {"view": view}) for view in tools["look"]["properties"]["view"]["enum"]]
    if "inspect" in tools:
        ids = tools["inspect"]["properties"]["id"].get("enum") or [wake.entity_id]
        reads.append(("inspect", {"id": ids[wake.round % len(ids)]}))
    return reads


class _StubHosts(Hosts):
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
        self.asked: List[str] = []
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
    def names(self) -> List[str]:
        return list(self.asked)


def _tool(name: str, path: str, args: Dict[str, Any]) -> str:
    """The model's ``check``, ``run`` or ``preview`` tool on the saved contract at ``path``, in the child process."""
    hosts = _StubHosts(path)
    try:
        text = _CHILD_TOOLS[name](path, hosts, **args)
    except Exception as exc:  # the SDK's own errors are what the author reads
        return f"{type(exc).__name__}: {exc}"
    if hosts.asked:
        text += f"\n(host answers — {', '.join(hosts.asked)} — came from the SDK's stand-in stubs, not a model)"
    return text


def _check_tool(path: str, hosts: Hosts) -> str:
    return "\n".join(map(str, check(path, hosts=hosts))) or "No issues."


def _run_tool(path: str, hosts: Hosts, seconds: float, seed: int = 1,
              participants: Optional[Dict[str, str]] = None) -> str:
    env = load(path, seed=seed, hosts=hosts)
    wrong = _unplayable(participants, list(env.contract.policies))
    if wrong:
        return f"Bad tool call: run: {wrong}. Nothing was run."
    result = env.run(participants, budget={"seconds": seconds})
    return result.summary() + "\noutputs: " + json.dumps(result.outputs, default=str)


def _preview_tool(path: str, hosts: Hosts, agent: str) -> str:
    shown = load(path, seed=1, hosts=hosts).preview(agent)
    tools = "\n".join(f"- {t['name']}: {t['description']} {json.dumps(t['input_schema'])}" for t in shown["tools"])
    return f"BRIEF:\n{shown['brief']}\n\nUPDATE:\n{shown['update']}\n\nTOOLS:\n{tools}"


_CHILD_TOOLS: Dict[str, Callable[..., str]] = {"check": _check_tool, "run": _run_tool, "preview": _preview_tool}


class _Workbench:
    """The author's tools, backed by one contract file in a scratch folder. Every saved revision is tested with
    :func:`tested`; ``best`` is the kept one — the latest that works and removes nothing the kept one had, unless it
    was saved again to confirm the removal — ``latest`` the latest saved and ``problem`` what is wrong with the last
    write, or why it was not kept ("" when it is kept)."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="fg-author-")) / "contract.json"
        #: The child process the saved contract is tested, checked, run and previewed in.
        self.box = Sandbox()
        self.box.start()  # it imports the SDK while the model writes
        self.writes: List[Any] = []
        self.revisions: List[Dict[str, Any]] = []
        self.working: List[int] = []
        #: The number of the kept revision.
        self.kept: Optional[int] = None
        #: What testing found, per working revision.
        self.tests: Dict[int, Tested] = {}
        #: What the latest working revision not kept removed from the kept one: saving that removal again confirms it.
        self.unconfirmed: List[str] = []
        self.problem = ""

    @property
    def best(self) -> Optional[Dict[str, Any]]:
        return self.revisions[self.kept - 1] if self.kept else None

    @property
    def latest(self) -> Optional[Dict[str, Any]]:
        return self.revisions[-1] if self.revisions else None

    @property
    def out_of_revisions(self) -> bool:
        return len(self.revisions) >= MAX_REVISIONS

    def call(self, name: str, arguments: str) -> str:
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            return f"Bad tool call: there is no tool {name!r}; the tools are {', '.join(t['name'] for t in TOOLS)}."
        args, wrong = _arguments(tool["parameters"], arguments)
        if wrong:
            return f"Bad tool call: {name}: {wrong}. It takes: {', '.join(tool['parameters']['properties']) or 'nothing'}."
        try:
            text = str(getattr(self, "tool_" + name)(**args))
        except Exception as exc:  # the SDK's own errors are what the author reads
            text = f"{type(exc).__name__}: {exc}"
        if len(text) <= MAX_RESULT or name == "start_from":  # a starter is adapted from the whole of it
            return text
        more = (f": read on with guide({args['part']!r}, start={int(args.get('start') or 0) + MAX_RESULT})"
                if name == "guide" else "")
        return text[:MAX_RESULT] + f"\n[cut at {MAX_RESULT:,} of {len(text):,} characters{more}]"

    def cut_off(self, name: str, arguments: str) -> str:
        """Answer a call the provider cut off at the output limit; a cut-off write is recorded as a write."""
        if name not in ("write_contract", "edit_contract"):
            return CUT_CALL.format(tool=name, done="done") + " Call it again, keeping your reply short."
        self.writes.append(arguments)
        self.problem = "the last write was cut off at the output limit, so it saved nothing"
        return CUT_CALL.format(tool=name, done="saved") + CUT_WRITE

    def tool_write_contract(self, contract: Any) -> str:
        if isinstance(contract, dict):  # many models send the object itself
            return self._save(contract)
        try:
            data = json.loads(contract) if isinstance(contract, str) else contract
        except json.JSONDecodeError as exc:
            self.writes.append(contract)
            self.problem = f"the last write was not valid JSON ({exc}), so it saved nothing"
            return f"Not valid JSON: {exc}. Nothing saved."
        if not isinstance(data, dict):
            self.writes.append(contract)
            self.problem = "the last write was not a JSON object, so it saved nothing"
            return "Not a JSON object: a contract is one object {...}. Nothing saved."
        return self._save(data)

    def tool_edit_contract(self, edits: List[Dict[str, Any]]) -> str:
        if self.latest is None:
            return "No contract saved yet: save one with write_contract first."
        data = copy.deepcopy(self.latest)
        for n, one in enumerate(edits if isinstance(edits, list) else [edits]):
            wrong = _edit(data, one)
            if wrong:
                return f"edits[{n}]: {wrong}. Nothing saved."
        return self._save(data)

    def tool_start_from(self, engine: str) -> str:
        source = engine_spec(engine).materialized_source()
        return self._save(source) + "\n\nThe contract:\n" + json.dumps(source, ensure_ascii=False)

    def _save(self, data: Dict[str, Any]) -> str:
        if self.out_of_revisions:
            kept = f"revision {self.kept}, the best that works, is kept" if self.kept else "none works"
            return f"Revision limit reached ({MAX_REVISIONS}): nothing more is saved; {kept}."
        self.writes.append(data)
        self.revisions.append(data)
        self.path.write_text(json.dumps(data, indent=2))
        found = tested(str(self.path), self.box)
        self.problem, number = found.problem[:MAX_RESULT], len(self.revisions)
        if self.problem:
            return f"Saved revision {number}, but it does not work yet: {self.problem}" + _notes(found)
        self.working.append(number)
        self.tests[number] = found
        saved = f"Saved revision {number}: it works — {_verdict(found)}."
        removed = _removed(self.best, data) if self.best is not None else []
        if removed and removed != self.unconfirmed:
            self.unconfirmed = removed
            self.problem = (f"it removed {', '.join(removed)}, which revision {self.kept} has; saving it again keeps it "
                            "instead")
            return (f"{saved}\nBut it removed {', '.join(removed)}, which revision {self.kept} has, so revision "
                    f"{self.kept} stays kept: put back what the brief asks for, or save it again to confirm the "
                    "removal." + _notes(found))
        self.kept, self.unconfirmed = number, []
        return saved + _notes(found)

    def tool_check(self) -> str:
        return self._in_child("check")

    def tool_run(self, seed: int = 1, participants: Optional[Dict[str, str]] = None) -> str:
        return self._in_child("run", seconds=RUN_SECONDS, seed=seed, participants=participants)

    def tool_preview(self, agent: str) -> str:
        return self._in_child("preview", agent=agent)

    def tool_guide(self, part: str, start: int = 0) -> str:
        return guide(part)[int(start or 0):]

    def _in_child(self, name: str, **args: Any) -> str:
        """The ``name`` tool on the saved contract, in a child process of at most :data:`RUN_SECONDS`."""
        if not self.path.exists():
            return "No contract saved yet."
        try:
            text: str = self.box.call("fg_env.authoring.author:_tool", {"name": name, "path": str(self.path), "args": args},
                                      RUN_SECONDS)
        except TooSlow as exc:
            return f"Too slow: {name} was still going after {RUN_SECONDS:g}s ({exc.step or 'building it'})."
        return text


def _verdict(found: Tested) -> str:
    """What the tests of a working revision showed."""
    checked = "it checks clean" if not found.warnings else "it checks with no errors (warnings below)"
    how = "without a problem" if found.untested else "to the end"
    ran = (f"{checked}, and runs {how} on {found.seeds} seeds with random agents, {len(TEST_SEEDS)} with idle ones, and "
           "once with agents choosing edge values")
    return f"{ran}; {found.untested}" if found.untested else ran


def _notes(found: Tested) -> str:
    """The host stand-ins and check warnings behind a save's verdict, as lines to add to its reply."""
    lines = [f"Its host calls ({', '.join(found.hosts)}) were answered by the SDK's stand-in stubs, not a model: real "
             "answers need a real host bound (fg_env.host.load(..., hosts=...))."] if found.hosts else []
    if found.warnings:
        lines += ["Check warnings:", *found.warnings]
    return "".join("\n" + line for line in lines)


def _unplayable(participants: Any, policies: List[str]) -> str:
    """What is wrong with the run tool's ``participants``, or "": only the contract's own agents may play — model,
    file and search participants would spend money, read files or run for hours outside the author's budget."""
    if participants is None:
        return ""
    allowed = ["random", "idle", *(f"policy:{name}" for name in policies)]
    if not isinstance(participants, dict):
        return f"participants maps a type or entity id to one of {', '.join(map(repr, allowed))}"
    for key, value in participants.items():
        if value not in allowed:
            return f"participants.{key}: {value!r} cannot play here: use {', '.join(map(repr, allowed))}"
    return ""


def _arguments(params: Dict[str, Any], arguments: str) -> Tuple[Dict[str, Any], str]:
    """A tool call's arguments, and what is wrong with them for ``params`` ("" when nothing is)."""
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError as exc:
        return {}, f"its arguments are not valid JSON ({exc})"
    if not isinstance(args, dict):
        return {}, "its arguments must be a JSON object"
    missing = [p for p in params.get("required", []) if p not in args]
    if missing:
        return args, f"it needs {', '.join(missing)}"
    unknown = [p for p in args if p not in params["properties"]]
    return args, f"it has no {', '.join(unknown)}" if unknown else ""


def _edit(data: Dict[str, Any], one: Any) -> str:
    """Apply one ``{"path": ..., "value": <JSON text>}`` edit to ``data`` in place; returns what is wrong, or ""."""
    if not isinstance(one, dict) or not isinstance(one.get("path"), str) or not one["path"]:
        return 'an edit is {"path": "outputs.score", "value": "<JSON text>"} (no value removes what is there)'
    path = one["path"]
    keys = [k for k in path.replace("[", ".").replace("]", "").split(".") if k]
    parent: Any = data
    for depth, key in enumerate(keys[:-1]):
        parent = _child(parent, key)
        if parent is None:
            return f"{path}: the contract has no {'.'.join(keys[:depth + 1])}"
    key, last = keys[-1], ".".join(keys[:-1]) or "(contract)"
    if isinstance(parent, list):
        size = len(parent)
        if not key.isdigit() or int(key) > size - ("value" not in one):
            adds = f", or {size} to add one" if "value" in one else ""
            return f"{path}: {last} has {size} item(s): use an index below {size}{adds}"
    elif not isinstance(parent, dict):
        return f"{path}: {last} is a {type(parent).__name__}, not an object or a list"
    if "value" not in one:
        if isinstance(parent, list):
            del parent[int(key)]
        elif key in parent:
            del parent[key]
        else:
            return f"{path}: there is nothing there to remove"
        return ""
    try:
        value = json.loads(one["value"]) if isinstance(one["value"], str) else one["value"]
    except json.JSONDecodeError as exc:
        return f"{path}: the value is not valid JSON ({exc}); strings are quoted, e.g. '\"text\"'"
    if isinstance(parent, list) and int(key) == len(parent):
        parent.append(value)
    else:
        parent[int(key) if isinstance(parent, list) else key] = value
    return ""


def _child(parent: Any, key: str) -> Any:
    if isinstance(parent, dict):
        return parent.get(key)
    if isinstance(parent, list) and key.isdigit() and int(key) < len(parent):
        return parent[int(key)]
    return None


def _openai(client: Any, model: str) -> Ask:
    tools = [{"type": "function", "function": tool} for tool in TOOLS]

    def ask(messages: List[Message]) -> Tuple[Message, Dict[str, Any]]:
        response = client.chat.completions.create(model=model, messages=messages, tools=tools)
        if not getattr(response, "choices", None):  # OpenRouter does this now and then, with the reason in `error`
            error = getattr(response, "error", None)
            raise EmptyReply("the provider sent a response with no reply in it" + (f": {error}" if error else ""))
        choice = response.choices[0]
        reply = choice.message
        calls = [{"id": c.id, "type": "function", "function": {"name": c.function.name,
                                                               "arguments": c.function.arguments}}
                 for c in reply.tool_calls or []]
        message: Message = {"role": "assistant", "content": reply.content or ""}
        if calls:
            message["tool_calls"] = calls
        used = response.usage
        cached = getattr(getattr(used, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        counts = {"input_tokens": (getattr(used, "prompt_tokens", 0) or 0) - cached, "cached_tokens": cached,
                  "output_tokens": getattr(used, "completion_tokens", 0) or 0,
                  "truncated": int(getattr(choice, "finish_reason", None) == "length"),
                  "refused": int(getattr(choice, "finish_reason", None) == "content_filter")}
        cost = getattr(used, "cost", None)  # OpenRouter reports it; OpenAI does not
        return message, {**counts, "cost": float(cost)} if isinstance(cost, (int, float)) else counts

    return ask


def _anthropic(client: Any, model: str) -> Ask:
    tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOLS]

    def ask(messages: List[Message]) -> Tuple[Message, Dict[str, Any]]:
        system = [{"type": "text", "text": messages[0]["content"], "cache_control": {"type": "ephemeral"}}]
        conversation = _to_anthropic(messages[1:])
        last = conversation[-1]  # a cache breakpoint on the latest turn: the next call reads all of this from cache
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1] = {**last["content"][-1], "cache_control": {"type": "ephemeral"}}
        response = client.messages.create(model=model, system=system, messages=conversation,
                                          tools=tools, max_tokens=ANTHROPIC_MAX_TOKENS)
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [{"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                 for b in response.content if b.type == "tool_use"]
        message: Message = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = calls
        used = response.usage
        return message, {"input_tokens": used.input_tokens,
                         "cached_tokens": getattr(used, "cache_read_input_tokens", 0) or 0,
                         "cache_write_tokens": getattr(used, "cache_creation_input_tokens", 0) or 0,
                         "output_tokens": used.output_tokens,
                         "truncated": int(getattr(response, "stop_reason", None) == "max_tokens"),
                         "refused": int(getattr(response, "stop_reason", None) == "refusal")}

    return ask


def _to_anthropic(messages: List[Message]) -> List[Message]:
    """The OpenAI-format conversation (after the system message) as Anthropic messages, without whitespace-only text
    (the API refuses it)."""
    out: List[Message] = []
    for m in messages:
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant":
            blocks: List[Dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m["content"].strip() else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"] or "{}")} for c in m.get("tool_calls", [])]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
        else:
            out.append({"role": "user", "content": m["content"]})
    return out


_ANSWERERS = {"openai": _openai, "anthropic": _anthropic}
