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
revision that removes parts (an action, a view, an output, an event, an entity ...), or rewrites a rule to do nothing,
is named to the model and kept only once the model saves that removal again. A save that changes nothing is no new
revision, and a contract saved before is not tested again. ``out`` is written each time a new one is kept, so an
interrupted session keeps it; a session where nothing worked writes its latest contract beside ``out`` as
``<name>.not-working.json`` instead. A model that stops before any saved contract works is sent back, with the
problem, a couple of times; one that stops with something unsettled after one worked — its last revision broken, a
removal not confirmed, its reply cut off at the output limit — is sent back once. Rate limits, overload and empty
replies are retried with backoff, within the ``seconds`` budget.

``model`` is ``"anthropic:<model>"`` or ``"openai:<model>"``, on the official client made from ``ANTHROPIC_API_KEY``
or ``OPENAI_API_KEY``. Any OpenAI-compatible server (OpenRouter, a local server) works through ``openai:``: the
client reads ``OPENAI_BASE_URL``, e.g. ``https://openrouter.ai/api/v1`` with the OpenRouter key as ``OPENAI_API_KEY``.
``client`` passes a client of your own instead.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..actions.params import parse_arguments
from ..api import load, parse
from ..engines import list_engines
from ..guides import guide
from ..host.providers import (
    MAX_BACKOFF_SECONDS,
    PROVIDER_CALLS,
    EmptyReply,
    anthropic_blocks,
    field_of,
    openai_calls,
    provider_failure,
    reply_text,
    request_timeout,
    retry_after,
    retryable,
)
from ..host.usage import call_usage, rough_tokens
from ..participants.llm import PROVIDERS, official_client
from ..runtime.budget import CACHED_WEIGHT, is_seconds
from . import testing, workbench
from .testing import Tested
from .workbench import TOOLS, Workbench, describe_changes, removed_parts

__all__ = ["author", "AuthorResult"]

#: What a brief may spend unless ``budget`` says otherwise: model tokens (counted as a run's token budget counts them:
#: input, output and cache writes in full, cache reads at :data:`~fg_env.runtime.budget.CACHED_WEIGHT`), model calls,
#: and wall-clock seconds (checked before each model call, so the session ends at most one step past them).
DEFAULT_BUDGET = {"tokens": 600_000, "calls": 30, "seconds": 1800}
#: How often a rate-limited, overloaded or failing provider call is retried, with backoff.
RETRIES = 4
#: How often a model that stops before any saved contract works is sent back.
MAX_NUDGES = 2
#: The reply cap on Anthropic, which requires one (each request passes a timeout, so the official client does not
#: refuse it as too long for a call that does not stream). OpenAI-compatible servers get none, so a model writes as
#: long a contract as it can.
ANTHROPIC_MAX_TOKENS = 20_000


INSTRUCTION = ("\n\nBuild this environment. Save it with write_contract, revise it with edit_contract, and use the "
               "other tools as you see fit. The guide's steps name fg-env commands; here your tools do them: "
               "write_contract or edit_contract saves the file, `fg-env check` is check, `fg-env preview <file> <id>` "
               "is preview(agent), `fg-env run <file> --seed N` is run(seed) and `fg-env guide <part>` is "
               "guide(part). Reply without calling a tool when you are done.\n\nLimits: {revisions} saved revisions "
               "(each write_contract, edit_contract or start_from call that changes the contract saves one — put "
               "several edits in one edit_contract call; a save that changes nothing or is not valid JSON does not "
               "count), {calls} model calls, {tokens:,} tokens and {seconds:,.0f} seconds in all. Each save is tested "
               "for up to {test:g} seconds.\n\nEngine starters: working environments to adapt. When one is close to "
               "this brief, start from it with start_from and change what the brief needs; otherwise write your "
               "own.\n")
#: What a model that stops after a working revision that removed parts of the kept one is told once.
UNKEPT = ("Revision {latest} works, but it removed {removed}, which revision {kept} has, so revision {kept} "
          "is kept. Put back what the brief asks for; if the brief does not need them, save it again to confirm the "
          "removal. Stopping now keeps revision {kept}.")
NUDGE = "No contract is saved yet. Save it with write_contract (keep replies short; put the contract in the tool call)."
NOT_WORKING = "Nothing you saved works yet: {problem}\nFix it and save it again."
#: What a model that stops on a broken revision, after an earlier one worked, is told once.
REGRESSED = "{latest} does not work: {problem}\nStopping now keeps revision {kept}; or fix it and save it again."
#: What a reply cut off at the output limit, with no tool call, is told.
CUT_REPLY = "Your reply was cut off at the output limit. Keep replies short; put the contract in the tool call."

Message = dict[str, Any]
Ask = Callable[[list[Message]], tuple[Message, dict[str, Any]]]
#: One provider request: the conversation, and the request's ``timeout`` in seconds (the time left in the session).
Request = Callable[[list[Message], float], tuple[Message, dict[str, Any]]]


class SpentEmptyReply(EmptyReply):
    """A provider response with no reply in it (retried like overload) that spent ``spent`` all the same, which the
    session counts."""

    def __init__(self, message: str, spent: Mapping[str, int] | None = None):
        super().__init__(message)
        self.spent = dict(spent or {})


@dataclass
class AuthorResult:
    """What :func:`author` built. ``contract`` is the kept revision, the best that works (``ok``), else the latest one
    saved (``problem`` says what is wrong), else None."""

    contract: dict[str, Any] | None
    ok: bool
    #: What is wrong with the model's last write, or why it was not kept, when that is not the contract kept ("" when
    #: it is).
    problem: str
    #: Why the loop ended: "done"; "gave_up" (the model stopped with nothing working); "revisions" (it saved every
    #: revision it may: ``ok`` says whether one works); "refused" (the provider refused to go on); "tokens", "calls" or
    #: "seconds" (the budget); or "error: <the provider's error>".
    stop: str
    #: Every write, in order: the contract saved, or the text of one that saved nothing (not valid JSON, cut off); a
    #: save of the latest revision as it is is none.
    writes: list[Any]
    #: The whole conversation, in OpenAI chat format.
    messages: list[Message]
    #: input_tokens (fresh ones), cached_tokens (read from the provider's prompt cache), cache_write_tokens (written to
    #: it), output_tokens, calls, truncated (replies cut off at the output limit), unreported (calls whose reply carried
    #: no usage: their tokens are estimated from what was sent), and cost when the provider reports it (OpenRouter
    #: does).
    usage: dict[str, Any]
    seconds: float
    #: Where ``contract`` was written: ``out``, or beside it as ``<name>.not-working.json`` when nothing worked.
    path: str | None = None
    #: The revisions that worked, numbered from 1 in the order they were saved.
    working: list[int] = field(default_factory=list)
    #: The number of the kept revision, ``contract`` (None when none works).
    kept: int | None = None
    #: What testing the kept revision found: how far its runs got when test time ran out (``untested``), its check's
    #: warnings, and the hosts the SDK's stand-in stubs answered (None when none works).
    tested: Tested | None = None
    #: What testing the first revision that worked found, which the summary compares the kept one with (None when
    #: none works).
    first_tested: Tested | None = None

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
            last = ("the last write" if self.writes[-1] is not revisions[-1]
                    else f"revision {len(revisions)} was not kept"
                    if len(revisions) in self.working else f"revision {len(revisions)} was not tested"
                    if self.problem.startswith("not tested") else f"revision {len(revisions)} did not work")
            lines.append(f"  kept revision {kept} of {len(revisions)}; {last}: {self.problem}")
        first = revisions[self.working[0] - 1] if self.ok and self.contract else None
        changed = describe_changes(first, self.contract) if first is not None and self.contract else ""
        if changed:
            lines.append(f"  changed since revision {self.working[0]}, the first that worked: {changed}")
        tests = (self.first_tested, self.tested) if self.first_tested and self.tested else None
        removed = removed_parts(first, self.contract, tests) if first is not None and self.contract else []
        if removed:
            lines.append(f"  REMOVED since revision {self.working[0]}, the first that worked: {', '.join(removed)} — "
                         "check the brief is still met")
        agent = self._describe(lines)
        if self.tested and self.tested.hosts:
            lines.append(f"  hosts: {', '.join(self.tested.hosts)} answered by the SDK's stand-in stubs in testing, "
                         "not a model: bind real hosts to run it for real")
        if self.tested:
            average, largest = self.tested.prompt
            lines.append(f"  prompt: an agent reads ~{average:,} tokens a turn (its brief and update), ~{largest:,} at "
                         "most")
        if self.tested and self.tested.warnings:
            lines += ["  warnings:", *(f"    {warning}" for warning in self.tested.warnings)]
        said = next((m["content"] for m in reversed(self.messages) if m["role"] == "assistant"), "").strip()
        if said:
            lines.append("  the model's last words: " + said.replace("\n", "\n    "))
        cost = f", ${self.usage['cost']:.2f}" if "cost" in self.usage else ""
        cached = f" (+{self.usage['cached_tokens']:,} cached)" if self.usage.get("cached_tokens") else ""
        fresh = self.usage["input_tokens"] + self.usage.get("cache_write_tokens", 0) + self.usage["output_tokens"]
        unreported = self.usage.get("unreported", 0)
        estimated = (f" ({unreported} call(s) came back without usage: their tokens are estimated from what was "
                     "sent)" if unreported else "")
        lines.append(f"  used: {self.usage['calls']} model calls, {fresh:,} tokens{cached}{cost}, "
                     f"{self.seconds:.0f}s{estimated}")
        if self.path:
            hosts = ", ".join(f"{name!r}: ..." for name in self.tested.hosts) if self.tested else ""
            run = (f"run it with its hosts bound: fg_env.host.load({self.path!r}, hosts={{{hosts}}}).run()" if hosts
                   else f"fg-env run {self.path} --seed 1")
            lines.append(f"next: fg-env preview {self.path} {agent} · {run}"
                         + ("" if self.ok else f" · fg-env check {self.path}"))
        return "\n".join(lines)

    def _describe(self, lines: list[str]) -> str:
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


def author(brief: str, model: str, *, client: Any = None, out: str | None = None,
           budget: Mapping[str, float] | None = None, progress: Callable[[str], None] | None = None) -> AuthorResult:
    """Have ``model`` (``"anthropic:<model>"`` or ``"openai:<model>"``) write an environment for ``brief``; returns an
    :class:`AuthorResult` (``result.contract``, ``result.ok``, ``result.summary()``).

    ``out`` is where the contract is written (nothing is written when None): each time a revision is kept, and at the
    end; when none works, the latest is written beside it as ``<name>.not-working.json``. ``budget`` caps ``tokens``
    (input, output and cache writes in full, a cache read counting :data:`CACHED_WEIGHT` of one, as a run's token
    budget counts them), model ``calls`` and wall-clock ``seconds``, by default :data:`DEFAULT_BUDGET`; the model is
    told these limits, the revision limit and the test time of a save up front. ``client`` replaces the official client
    made from the environment; ``progress`` is called with one line per model call. Rate limits, overload, server
    errors and empty replies are retried with backoff, never waiting past the ``seconds`` budget; a provider error that
    persists or that retrying cannot fix does not raise: the loop stops (``result.stop`` says why) and keeps what
    already works."""
    provider, name = _model(model)
    limits = _budget(budget)
    ask = _ANSWERERS[provider](client if client is not None else official_client(provider, name), name)
    if out and not Path(out).parent.is_dir():
        raise ValueError(f"out {out!r}: the folder {str(Path(out).parent)!r} does not exist")
    started = time.time()
    bench = Workbench(started + limits["seconds"])
    # read when the session starts, as the workbench and the tests read them (a setting changed later holds for both)
    instruction = INSTRUCTION.format(revisions=workbench.MAX_REVISIONS, test=testing.TEST_SECONDS, **limits)
    messages: list[Message] = [{"role": "system", "content": guide("authoring")},
                               {"role": "user", "content": brief + instruction + _starters()}]
    usage: dict[str, Any] = {"input_tokens": 0, "cached_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
                             "calls": 0, "truncated": 0, "unreported": 0}
    try:
        deadline = started + limits["seconds"]
        stop = _converse(_retrying(ask, provider, name, progress, deadline), messages, bench, usage, limits, progress,
                         out, deadline)
    finally:
        bench.box.close()
        shutil.rmtree(bench.path.parent, ignore_errors=True)
    contract = bench.best if bench.best is not None else bench.latest
    path = (out if bench.best is not None else _not_working(out)) if out and contract is not None else None
    if path and contract is not None:
        _write(path, contract)
    return AuthorResult(contract, bench.best is not None, bench.problem, stop, bench.writes, messages, usage,
                        round(time.time() - started, 1), path, bench.working, bench.kept,
                        bench.tests.get(bench.kept) if bench.kept else None,
                        bench.tests.get(bench.working[0]) if bench.working else None)


def _starters() -> str:
    """The engine starters, one line each: its id and what it simulates."""
    return "".join(f"- {spec.id}: {spec.description.split('; ')[0]}\n" for spec in list_engines(available=True))


def _not_working(out: str) -> str:
    """Where a contract that does not work is written instead of ``out``: beside it, named so it cannot pass for it."""
    path = Path(out)
    return str(path.with_name(f"{path.stem}.not-working{path.suffix or '.json'}"))


def _converse(ask: Ask, messages: list[Message], bench: Workbench, usage: dict[str, Any], limits: dict[str, float],
              progress: Callable[[str], None] | None, out: str | None, deadline: float) -> str:
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
        except ProviderFailed as exc:  # the provider's error ends the loop; what already works is kept
            for key, value in exc.spent.items():  # what the calls that answered with nothing cost still counts
                usage[key] = usage.get(key, 0) + value
            return f"error: {exc}"[:800]
        except Exception as exc:
            return f"error: {type(exc).__name__}: {exc}"[:800]
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


def _spent(usage: dict[str, Any]) -> float:
    """The tokens ``usage`` counts against the budget, as a run's token budget counts them."""
    return (usage["input_tokens"] + usage["output_tokens"] + usage["cache_write_tokens"]
            + usage["cached_tokens"] * CACHED_WEIGHT)


def _unsettled(bench: Workbench) -> str:
    """What a model that stops is sent back once with: its last write broken or not kept; "" when it is the kept one."""
    if not bench.problem:
        return ""
    latest = len(bench.revisions)
    if bench.writes[-1] is bench.latest and latest in bench.working:
        return UNKEPT.format(latest=latest, removed=", ".join(bench.unconfirmed), kept=bench.kept)
    return REGRESSED.format(latest=f"Revision {latest}" if bench.writes[-1] is bench.latest else "Your last write",
                            problem=bench.problem, kept=bench.kept)


def _write(out: str, contract: dict[str, Any]) -> None:
    """Write ``contract`` to ``out`` whole or not at all: a session stopped mid-write never leaves half a file."""
    path = Path(out)
    scratch = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    scratch.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(scratch, path)


def _answer(calls: list[dict[str, Any]], truncated: bool, bench: Workbench) -> list[Message]:
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


class ProviderFailed(Exception):
    """The provider still failed (or failed in a way retrying cannot fix): the error and how to fix it. ``spent`` is
    what the calls that answered with nothing cost, which the session still counts."""

    def __init__(self, message: str, spent: Mapping[str, int] | None = None):
        super().__init__(message)
        self.spent = dict(spent or {})


def _retrying(ask: Request, provider: str, model: str, progress: Callable[[str], None] | None,
              deadline: float) -> Ask:
    """``ask``, each request given the time left before ``deadline`` (a ``time.time()``) as its timeout, retrying rate
    limits, overload, timeouts, server errors and empty replies with backoff (honouring retry-after), never waiting
    past ``deadline``: a wait that would is not made, and the error stands (:class:`ProviderFailed`, with its fix)."""
    call, client = PROVIDER_CALLS[provider]

    def failed(exc: Exception, attempt: int, spent: Mapping[str, int]) -> ProviderFailed:
        if isinstance(exc, EmptyReply):
            return ProviderFailed(f"{call} still sent no usable reply after {attempt} retr"
                                  f"{'y' if attempt == 1 else 'ies'} ({exc}): try again later, or another provider",
                                  spent)
        return ProviderFailed(provider_failure(exc, call, client, model, attempt), spent)

    def retried(messages: list[Message]) -> tuple[Message, dict[str, Any]]:
        wasted: dict[str, int] = {}  # what calls answered with nothing spent: counted with the reply that follows
        for attempt in range(RETRIES + 1):
            try:
                message, used = ask(messages, request_timeout(max(0.0, deadline - time.time())))
                return message, {key: used.get(key, 0) + wasted.get(key, 0) for key in {*used, *wasted}}
            except Exception as exc:
                if isinstance(exc, SpentEmptyReply):
                    wasted = {key: wasted.get(key, 0) + exc.spent.get(key, 0) for key in {*wasted, *exc.spent}}
                    wasted["calls"] = wasted.get("calls", 0) + 1
                if attempt >= RETRIES or not retryable(exc):
                    raise failed(exc, attempt, wasted) from exc
                delay = retry_after(exc)
                wait = min(MAX_BACKOFF_SECONDS, delay if delay is not None else 2.0 ** attempt)
                if time.time() + wait >= deadline:
                    raise ProviderFailed(f"{call} failed with {type(exc).__name__}: {exc}, and retrying means waiting "
                                         f"{wait:.0f}s, past the session's time budget: give it more seconds "
                                         "(budget={'seconds': ...}), or try again when the provider is less busy",
                                         wasted) from exc
                if progress:
                    progress(f"provider busy ({type(exc).__name__}: {str(exc)[:200]}); retrying in {wait:.0f}s")
                time.sleep(wait)
        raise AssertionError("unreachable")

    return retried


def _model(model: str) -> tuple[str, str]:
    provider, _, name = model.partition(":") if isinstance(model, str) else ("", "", "")
    if provider not in PROVIDERS or not name:
        raise ValueError(f"model {model!r}: use 'anthropic:<model>' or 'openai:<model>' (any OpenAI-compatible "
                         "server, e.g. OpenRouter, through OPENAI_BASE_URL)")
    return provider, name


def _budget(budget: Mapping[str, float] | None) -> dict[str, float]:
    limits: dict[str, float] = {**DEFAULT_BUDGET, **(budget or {})}
    for key, value in limits.items():
        if key not in DEFAULT_BUDGET:
            raise ValueError(f"budget has no {key!r}: use tokens, calls and seconds, e.g. {DEFAULT_BUDGET}")
        if key == "seconds":
            if not is_seconds(value):
                raise ValueError(f"budget seconds must be a number of seconds above 0, got {value!r}")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"budget {key} must be a whole number ≥ 1, got {value!r}")
    return limits


def _openai(client: Any, model: str) -> Request:
    tools = [{"type": "function", "function": tool} for tool in TOOLS]

    def ask(messages: list[Message], timeout: float) -> tuple[Message, dict[str, Any]]:
        response = client.chat.completions.create(model=model, messages=messages, tools=tools, timeout=timeout)
        # read as the participants and hosts read a response: objects or the plain dicts some proxies return
        used, choices = field_of(response, "usage"), field_of(response, "choices")
        reply = field_of(choices[0], "message") if choices else None
        if reply is None:  # OpenRouter does this now and then, with the reason in `error`
            error = field_of(response, "error")
            raise SpentEmptyReply("the provider sent a response with no reply in it" + (f": {error}" if error else ""),
                                  _spent_on(used, "openai", messages))
        choice = choices[0]
        calls = [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
                 for call_id, name, arguments in openai_calls(reply)]
        message: Message = {"role": "assistant", "content": reply_text(field_of(reply, "content"))}
        if calls:
            message["tool_calls"] = calls
        finish = field_of(choice, "finish_reason")
        refused = finish == "content_filter" or bool(field_of(reply, "refusal"))
        counts = {**_spent_on(used, "openai", messages), "truncated": int(finish == "length"), "refused": int(refused)}
        cost = field_of(used, "cost")  # OpenRouter reports it; OpenAI does not
        return message, {**counts, "cost": float(cost)} if isinstance(cost, (int, float)) else counts

    return ask


def _anthropic(client: Any, model: str) -> Request:
    """Each request passes its ``timeout``: without one, the official client refuses a ``max_tokens`` above a model's
    non-streaming cap (8,192 on some) before sending anything, asking for streaming."""
    tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOLS]

    def ask(messages: list[Message], timeout: float) -> tuple[Message, dict[str, Any]]:
        system = [{"type": "text", "text": messages[0]["content"], "cache_control": {"type": "ephemeral"}}]
        conversation = _to_anthropic(messages[1:])
        last = conversation[-1]  # a cache breakpoint on the latest turn: the next call reads all of this from cache
        if isinstance(last["content"], str):
            last["content"] = [{"type": "text", "text": last["content"]}]
        last["content"][-1] = {**last["content"][-1], "cache_control": {"type": "ephemeral"}}
        response = client.messages.create(model=model, system=system, messages=conversation,
                                          tools=tools, max_tokens=ANTHROPIC_MAX_TOKENS, timeout=timeout)
        # read as the participants and hosts read a response: objects or the plain dicts some proxies return
        blocks = anthropic_blocks(field_of(response, "content"))
        text = "".join(b["text"] for b in blocks if b["type"] == "text")
        calls = [{"id": b["id"], "type": "function",
                  "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                 for b in blocks if b["type"] == "tool_use"]
        stop, used = field_of(response, "stop_reason"), field_of(response, "usage")
        if not calls and not text.strip() and stop not in ("max_tokens", "refusal"):
            raise SpentEmptyReply(f"the provider sent a reply with nothing in it (stop_reason {stop!r})",
                                  _spent_on(used, "anthropic", messages))
        message: Message = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = calls
        return message, {**_spent_on(used, "anthropic", messages),
                         "truncated": int(stop == "max_tokens"), "refused": int(stop == "refusal")}

    return ask


def _spent_on(usage: Any, provider: str, messages: list[Message]) -> dict[str, int]:
    """What one call spent, in the session's counts: as the reply's ``usage`` says, or — it says nothing — the size of
    the ``messages`` sent, counted as unreported (see host/usage.py)."""
    spent = call_usage(usage, provider, rough_tokens(len(json.dumps(messages, ensure_ascii=False, default=str))))
    return {"input_tokens": spent.input_tokens, "cached_tokens": spent.cache_read_tokens,
            "cache_write_tokens": spent.cache_write_tokens, "output_tokens": spent.output_tokens,
            "unreported": int(spent.unreported)}


def _to_anthropic(messages: list[Message]) -> list[Message]:
    """The OpenAI-format conversation (after the system message) as Anthropic messages, without whitespace-only text
    (the API refuses it)."""
    out: list[Message] = []
    for m in messages:
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant":
            blocks: list[dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m["content"].strip() else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                        "input": _input(c["function"]["arguments"])} for c in m.get("tool_calls", [])]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
        else:
            out.append({"role": "user", "content": m["content"]})
    return out


_ANSWERERS = {"openai": _openai, "anthropic": _anthropic}


def _input(arguments: str | None) -> Any:
    """A tool call's arguments as the Anthropic API takes them back: the parsed object, or ``{}`` when the model's text
    could not be read (its reply already told the model why)."""
    args, broken = parse_arguments(arguments or "{}")
    return {} if broken or not isinstance(args, dict) else args
