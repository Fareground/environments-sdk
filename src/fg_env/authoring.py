"""``fg_env.author``: a plain-language brief in, a working contract out, written by an LLM with the SDK's own tools.

    result = fg_env.author("A corner shop orders stock every week ...", "anthropic:claude-sonnet-4-5", out="shop.json")
    print(result.summary())

The model starts from ``guide("authoring")`` and works with six tools — ``write_contract``, ``edit_contract``,
``check``, ``run``, ``preview`` and ``guide`` — until it says it is done or the budget runs out. Every contract it
saves is tested (:func:`contract_problem`); the result keeps the latest one that works, so a later revision that breaks
it never replaces it, and ``out`` is written each time a new one works, so an interrupted session keeps it. A model
that stops before any saved contract works is sent back, with the problem, a couple of times; one that stops on a
broken revision after an earlier one worked is sent back once. A reply cut off at the output limit is named to the
model as such, and rate limits, overload and empty replies are retried with backoff.

``model`` is ``"anthropic:<model>"`` or ``"openai:<model>"``, on the official client made from ``ANTHROPIC_API_KEY``
or ``OPENAI_API_KEY``. Any OpenAI-compatible server (OpenRouter, a local server) works through ``openai:``: the
client reads ``OPENAI_BASE_URL``, e.g. ``https://openrouter.ai/api/v1`` with the OpenRouter key as ``OPENAI_API_KEY``.
``client`` passes a client of your own instead.
"""
from __future__ import annotations

import copy
import json
import os
import random
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .api import ContractLike, check, load, parse
from .contract import Contract
from .diagnostics import DEGRADING
from .guides import guide
from .measure import RunResult
from .participants import (_MAX_BACKOFF_SECONDS, _PROVIDERS, _fill_dependent, _retry_after, _retryable, _seed_for,
                           official_client, sample_args)
from .session import Wake

__all__ = ["author", "AuthorResult"]

#: What a brief may spend unless ``budget`` says otherwise: model tokens (input + output, cache reads weighted by
#: :data:`CACHED_WEIGHT`) and model calls.
DEFAULT_BUDGET = {"tokens": 600_000, "calls": 30}
#: What an input token read from the provider's prompt cache counts for in the budget: providers bill it at a small
#: fraction of a fresh one (a tenth on Anthropic, a tenth to a half on OpenAI-compatible servers).
CACHED_WEIGHT = 0.1
#: Most contract revisions the model may save (a write that saves nothing, such as invalid JSON, is not one).
MAX_REVISIONS = 8
#: Seeds every saved contract is run on, with random agents and with idle ones.
TEST_SEEDS = (1, 2, 3)
#: Longest all of a saved contract's test runs may take together; a contract slower than that does not work.
TEST_SECONDS = 60
#: Longest one run of the model's ``run`` tool may take.
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
         "contract": {"type": "string", "description": "The contract as JSON text."}}, "required": ["contract"]}},
    {"name": "edit_contract", "description": "Change parts of the saved contract without writing it all again. Each "
     "edit sets the value at a path such as 'outputs.score' or 'actions.take.do[0]' (on a list, the index one past "
     "the end adds an item); an edit without a value removes what is there. Saved as a new revision.",
     "parameters": {"type": "object", "properties": {"edits": {"type": "array", "items": {
         "type": "object", "properties": {"path": {"type": "string"},
                                          "value": {"type": "string", "description": "The new value as JSON text."}},
         "required": ["path"]}}}, "required": ["edits"]}},
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
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}}, "required": ["part"]}},
]

INSTRUCTION = ("\n\nBuild this environment. Save it with write_contract, revise it with edit_contract, and use the other "
               "tools as you see fit. The guide's steps name fg-env commands; here your tools do them: write_contract or "
               "edit_contract saves the file, `fg-env check` is check, `fg-env preview <file> <id>` is preview(agent), "
               "`fg-env run <file> --seed N` is run(seed) and `fg-env guide <part>` is guide(part). "
               "Reply without calling a tool when you are done.")
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
    """What :func:`author` built. ``contract`` is the latest saved contract that works (``ok``), else the latest one
    saved (``problem`` says what is wrong), else None."""

    contract: Optional[Dict[str, Any]]
    ok: bool
    #: What is wrong with the model's last write, when that is not the contract kept ("" when it is).
    problem: str
    #: Why the loop ended: "done"; "gave_up" (the model stopped with nothing working); "revisions" (it used every
    #: revision with nothing working); "tokens" or "calls" (the budget); or "error: <the provider's error>".
    stop: str
    #: Every write, in order: the contract saved, or the text of one that saved nothing (not valid JSON, cut off).
    writes: List[Any]
    #: The whole conversation, in OpenAI chat format.
    messages: List[Message]
    #: input_tokens (fresh ones), cached_tokens (read from the provider's prompt cache), output_tokens, calls,
    #: truncated (replies cut off at the output limit), and cost when the provider reports it (OpenRouter does).
    usage: Dict[str, Any]
    seconds: float
    path: Optional[str] = None
    #: The revisions that worked, numbered from 1 in the order they were saved.
    working: List[int] = field(default_factory=list)

    def summary(self) -> str:
        """What it built — name, agent types, actions, stages, outputs — what changed along the way, what it used, and
        what to do next."""
        revisions = [w for w in self.writes if isinstance(w, dict)]
        kept = self.working[-1] if self.ok else len(revisions)
        if self.contract is None:
            lines = [f"no contract saved (stopped: {self.stop})"]
        else:
            lines = [f"{'built' if self.ok else 'NOT WORKING'}: {self.contract.get('name') or '(unnamed)'} — revision "
                     f"{kept} of {len(revisions)}, stopped: {self.stop}"]
        if self.problem and not self.ok:
            lines.append(f"  problem: {self.problem}")
        elif self.problem:
            last = f"revision {len(revisions)} did not work" if self.writes[-1] is revisions[-1] else "the last write"
            lines.append(f"  kept revision {kept} of {len(revisions)}; {last}: {self.problem}")
        changed = _changes(revisions[self.working[0] - 1], self.contract) if self.ok and self.contract else ""
        if changed:
            lines.append(f"  changed since revision {self.working[0]}, the first that worked: {changed}")
        agent = self._describe(lines)
        said = next((m["content"] for m in reversed(self.messages) if m["role"] == "assistant"), "").strip()
        if said:
            lines.append("  the model's last words: " + said.replace("\n", "\n    "))
        cost = f", ${self.usage['cost']:.2f}" if "cost" in self.usage else ""
        cached = f" (+{self.usage['cached_tokens']:,} cached)" if self.usage.get("cached_tokens") else ""
        lines.append(f"  used: {self.usage['calls']} model calls, "
                     f"{self.usage['input_tokens'] + self.usage['output_tokens']:,} tokens{cached}{cost}, "
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
    """How ``after`` differs from ``before`` in what the environment is: its name, types and outputs."""
    changes = [f"name {before.get('name')!r} → {after.get('name')!r}"] if before.get("name") != after.get("name") else []
    for key in ("types", "outputs"):
        old, new = set(before.get(key) or {}), set(after.get(key) or {})
        if old != new:
            changes.append(f"{key} " + " ".join([f"-{k}" for k in sorted(old - new)] + [f"+{k}" for k in sorted(new - old)]))
    return "; ".join(changes)


def author(brief: str, model: str, *, client: Any = None, out: Optional[str] = None,
           budget: Optional[Mapping[str, int]] = None, progress: Optional[Callable[[str], None]] = None) -> AuthorResult:
    """Have ``model`` (``"anthropic:<model>"`` or ``"openai:<model>"``) write an environment for ``brief``; returns an
    :class:`AuthorResult` (``result.contract``, ``result.ok``, ``result.summary()``).

    ``out`` is where the contract is written (nothing is written when None): each time a revision works, and at the
    end. ``budget`` caps ``tokens`` (input + output, a cache read counting :data:`CACHED_WEIGHT` of one) and model
    ``calls``, by default 600,000 and 30. ``client`` replaces the official client made from the
    environment; ``progress`` is called with one line per model call. Rate limits, overload and server errors are
    retried with backoff; a provider error that persists or that retrying cannot fix does not raise: the loop stops
    (``result.stop`` says why) and keeps what already works."""
    provider, name = _model(model)
    limits = _budget(budget)
    ask = _ANSWERERS[provider](client if client is not None else official_client(provider, name), name)
    if out and not Path(out).parent.is_dir():
        raise ValueError(f"out {out!r}: the folder {str(Path(out).parent)!r} does not exist")
    bench, started = _Workbench(), time.time()
    messages: List[Message] = [{"role": "system", "content": guide("authoring")},
                               {"role": "user", "content": brief + INSTRUCTION}]
    usage: Dict[str, Any] = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "calls": 0, "truncated": 0}
    try:
        stop = _converse(_retrying(ask, progress), messages, bench, usage, limits, progress, out)
    finally:
        shutil.rmtree(bench.path.parent, ignore_errors=True)
    contract = bench.best if bench.best is not None else bench.latest
    if out and contract is not None:
        _write(out, contract)
    return AuthorResult(contract, bench.best is not None, bench.problem, stop, bench.writes, messages, usage,
                        round(time.time() - started, 1), out if out and contract is not None else None, bench.working)


def _converse(ask: Ask, messages: List[Message], bench: "_Workbench", usage: Dict[str, Any], limits: Dict[str, int],
              progress: Optional[Callable[[str], None]], out: Optional[str]) -> str:
    """The tool loop; returns why it stopped. Each revision that works is written to ``out`` at once."""
    nudges, regressed = 0, False
    while True:
        if usage["calls"] >= limits["calls"]:
            return "calls"
        if _spent(usage) >= limits["tokens"]:
            return "tokens"
        try:
            message, used = ask(messages)
        except Exception as exc:  # the provider's error ends the loop; what already works is kept
            return f"error: {type(exc).__name__}: {exc}"[:500]
        for key, value in used.items():
            usage[key] = usage.get(key, 0) + value
        usage["calls"] += 1
        messages.append(message)
        calls = message.get("tool_calls") or []
        if progress:
            progress(f"call {usage['calls']}: {', '.join(c['function']['name'] for c in calls) or 'done'}"
                     f"{' (cut off)' if used['truncated'] else ''} "
                     f"({usage['input_tokens']:,}+{usage['output_tokens']:,} tokens)")
        if not calls:
            if bench.best is not None and bench.problem and not regressed:
                regressed = True  # stopped on a broken revision after an earlier one worked: send it back once
                messages.append({"role": "user", "content": _regressed(bench)})
                continue
            if bench.best is not None:
                return "done"
            if nudges >= MAX_NUDGES:
                return "gave_up"
            nudges += 1  # stopped (or was cut off) before any saved contract works: send it back once more
            messages.append({"role": "user", "content": CUT_REPLY if used["truncated"] else
                             NOT_WORKING.format(problem=bench.problem) if bench.writes else NUDGE})
            continue
        best = bench.best
        messages += _answer(calls, bool(used["truncated"]), bench)
        if out and bench.best is not None and bench.best is not best:
            _write(out, bench.best)
        if bench.best is None and bench.out_of_revisions:
            return "revisions"


def _spent(usage: Dict[str, Any]) -> float:
    """The tokens ``usage`` counts against the budget."""
    return usage["input_tokens"] + usage["output_tokens"] + usage["cached_tokens"] * CACHED_WEIGHT


def _regressed(bench: "_Workbench") -> str:
    latest = f"Revision {len(bench.revisions)}" if bench.writes[-1] is bench.latest else "Your last write"
    return REGRESSED.format(latest=latest, problem=bench.problem, kept=bench.working[-1])


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


def _budget(budget: Optional[Mapping[str, int]]) -> Dict[str, int]:
    limits = {**DEFAULT_BUDGET, **(budget or {})}
    for key, value in limits.items():
        if key not in DEFAULT_BUDGET:
            raise ValueError(f"budget has no {key!r}: use tokens and calls, e.g. {DEFAULT_BUDGET}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"budget {key} must be a whole number ≥ 1, got {value!r}")
    return limits


def contract_problem(source: ContractLike) -> str:
    """What stops a contract from working, or "" when it works: its first check error, else the first of its test
    runs to the end — on each of :data:`TEST_SEEDS` with random agents and with idle ones (agents that never act),
    then once with agents that choose each tool's edge values — that fails, whose random agents could not play (see
    ``RunResult.degraded``), in which an edge value breaks a rule, or that is still going after :data:`TEST_SECONDS`
    for all of them. Anything evaluating the contract raises is its problem too."""
    try:
        errors = [str(i) for i in check(source) if i.severity == "error"]
        if errors:
            return errors[0] + (f" (and {len(errors) - 1} more: call check)" if len(errors) > 1 else "")
        deadline, random_findings = time.monotonic() + TEST_SECONDS, _random_findings(parse(source))
        plays = [(agents, f"{agents} agents", seed, random_findings if agents == "random" else frozenset())
                 for seed in TEST_SEEDS for agents in ("random", "idle")]
        for participant, who, seed, findings in plays + [(_EdgeAgent(1), "agents choosing edge values", 1, _FAULTS)]:
            left = deadline - time.monotonic()
            result = load(source, seed=seed).run({"*": participant}, budget={"seconds": left}) if left > 0 else None
            problem = _run_problem(result, f"{who} (seed {seed})", findings)
            if problem:
                return problem
    except Exception as exc:  # a model's contract can break the engine in any way: that is its problem to fix
        return f"{type(exc).__name__}: {exc}"
    return ""


def _run_problem(result: Optional[RunResult], who: str, findings: frozenset) -> str:
    """What a test run with ``who`` shows is wrong — a failure, or a diagnostic among ``findings`` — or ""; None is a
    run there was no time left for."""
    if result is None or result.budget.get("exhausted") == "seconds":
        reached = f" (a run with {who} reached round {result.rounds})" if result is not None else ""
        return (f"too slow to test: its test runs did not all finish within {TEST_SECONDS}s{reached}; make a round "
                "cheaper (fewer entities, loops or draws) or the run shorter")
    if result.status == "failed":
        return f"a run with {who} failed in round {result.rounds}: {result.error}"
    found = [f for f in result.diagnostics if f["code"] in findings]
    if found:
        return (f"a run with {who} shows {found[0]['code']}: {found[0]['path']}: {found[0]['message']} → "
                f"{found[0]['fix']}")
    return ""


def _random_findings(contract: Contract) -> frozenset:
    """The degrading findings a run with random agents is judged by. Random agents write placeholder text, so when an
    action takes free text, their calls all being refused says nothing about the contract."""
    writes_text = any(param.type == "text" and not param.values
                      for action in contract.actions.values() for param in action.params.values())
    return DEGRADING - {"agents_never_acted"} if writes_text else DEGRADING


#: Findings that an agent's choice broke a rule as its action applied (the action was refused and undone).
_FAULTS = frozenset({"action_rule_failed", "action_broke_invariant"})


class _Edges(random.Random):
    """Draws on the edges: a range's least or greatest value, a list's first or last item."""

    def __init__(self, high: bool) -> None:
        super().__init__(0)
        self.high = high

    def randint(self, a: int, b: int) -> int:
        return b if self.high else a

    def uniform(self, a: float, b: float) -> float:
        return b if self.high else a

    def choice(self, seq: Any) -> Any:
        return seq[-1] if self.high else seq[0]


class _EdgeAgent:
    """Takes one random action a turn, with every argument on an edge of what its tool allows — its minimum or
    maximum, its last or first choice — high in odd rounds, low in even ones: the values random play almost never
    picks."""

    concurrent = False

    def __init__(self, seed: int) -> None:
        self.seed = seed

    def __call__(self, wake: Wake) -> None:
        acts = [t for t in wake.tools if t.kind == "act"]
        if acts and not wake.done:
            tool, edges = random.Random(_seed_for(self.seed, wake)).choice(acts), _Edges(wake.round % 2 == 1)
            wake.call(tool.name, _fill_dependent(wake, tool.name, sample_args(tool.input_schema, edges), edges))
        if not wake.done:
            wake.end()


class _Workbench:
    """The author's tools, backed by one contract file in a scratch folder. Every saved revision is tested with
    :func:`contract_problem`; ``best`` is the latest that works, ``latest`` the latest saved and ``problem`` what is
    wrong with the last write ("" when it works)."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="fg-author-")) / "contract.json"
        self.writes: List[Any] = []
        self.revisions: List[Dict[str, Any]] = []
        self.working: List[int] = []
        self.problem = ""

    @property
    def best(self) -> Optional[Dict[str, Any]]:
        return self.revisions[self.working[-1] - 1] if self.working else None

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
            return str(getattr(self, "tool_" + name)(**args))[:MAX_RESULT]
        except Exception as exc:  # the SDK's own errors are what the author reads
            return f"{type(exc).__name__}: {exc}"[:MAX_RESULT]

    def cut_off(self, name: str, arguments: str) -> str:
        """Answer a call the provider cut off at the output limit; a cut-off write is recorded as a write."""
        if name not in ("write_contract", "edit_contract"):
            return CUT_CALL.format(tool=name, done="done") + " Call it again, keeping your reply short."
        self.writes.append(arguments)
        self.problem = "the last write was cut off at the output limit, so it saved nothing"
        return CUT_CALL.format(tool=name, done="saved") + CUT_WRITE

    def tool_write_contract(self, contract: str) -> str:
        try:
            data = json.loads(contract)
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

    def _save(self, data: Dict[str, Any]) -> str:
        if self.out_of_revisions:
            return f"Revision limit reached ({MAX_REVISIONS}); the last saved contract is final."
        self.writes.append(data)
        self.revisions.append(data)
        self.path.write_text(json.dumps(data, indent=2))
        self.problem = contract_problem(str(self.path))[:MAX_RESULT]
        number = len(self.revisions)
        if self.problem:
            return f"Saved revision {number}, but it does not work yet: {self.problem}"
        self.working.append(number)
        return (f"Saved revision {number}: it works — it checks clean, and runs to the end on {len(TEST_SEEDS)} seeds "
                "with random agents and with idle ones, and with agents choosing edge values.")

    def tool_check(self) -> str:
        if not self.path.exists():
            return "No contract saved yet."
        return "\n".join(map(str, check(str(self.path)))) or "No issues."

    def tool_run(self, seed: int = 1, participants: Optional[Dict[str, str]] = None) -> str:
        env = load(str(self.path), seed=seed)
        wrong = _unplayable(participants, list(env.contract.policies))
        if wrong:
            return f"Bad tool call: run: {wrong}. Nothing was run."
        result = env.run(participants, budget={"seconds": RUN_SECONDS})
        return result.summary() + "\noutputs: " + json.dumps(result.outputs, default=str)

    def tool_preview(self, agent: str) -> str:
        shown = load(str(self.path), seed=1).preview(agent)
        tools = "\n".join(f"- {t['name']}: {t['description']} {json.dumps(t['input_schema'])}" for t in shown["tools"])
        return f"BRIEF:\n{shown['brief']}\n\nUPDATE:\n{shown['update']}\n\nTOOLS:\n{tools}"

    def tool_guide(self, part: str) -> str:
        return guide(part)


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
                  "truncated": int(getattr(choice, "finish_reason", None) == "length")}
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
        return message, {"input_tokens": used.input_tokens + (getattr(used, "cache_creation_input_tokens", 0) or 0),
                         "cached_tokens": getattr(used, "cache_read_input_tokens", 0) or 0,
                         "output_tokens": used.output_tokens,
                         "truncated": int(getattr(response, "stop_reason", None) == "max_tokens")}

    return ask


def _to_anthropic(messages: List[Message]) -> List[Message]:
    """The OpenAI-format conversation (after the system message) as Anthropic messages."""
    out: List[Message] = []
    for m in messages:
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant":
            blocks: List[Dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m["content"] else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"] or "{}")} for c in m.get("tool_calls", [])]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
        else:
            out.append({"role": "user", "content": m["content"]})
    return out


_ANSWERERS = {"openai": _openai, "anthropic": _anthropic}
