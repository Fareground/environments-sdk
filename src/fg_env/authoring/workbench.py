"""The author's workbench: the model's tools on one contract file in a scratch folder, every saved revision tested,
and the revision that works kept. ``check``, ``run`` and ``preview`` run in the sandbox's child process."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..actions.params import parse_arguments
from ..api import check, load
from ..contract.normalize import normalize
from ..engines import get as engine_spec
from ..engines import list_engines
from ..errors import ContractError
from ..guides import guide
from ..host.hosts import Hosts
from ..participants.builtin import policy_names
from .behaviour import collapsed
from .sandbox import Sandbox, TooBig, TooSlow
from .testing import TEST_SEEDS, StubHosts, Tested, tested

__all__ = ["TOOLS", "Workbench", "describe_changes", "removed_parts"]

#: Most contract revisions the model may save (a write that saves nothing, such as invalid JSON, is not one).
MAX_REVISIONS = 8
#: Longest one call of the model's ``check``, ``run`` or ``preview`` tool may take.
RUN_SECONDS = 60
#: Longest tool result sent back to the model.
MAX_RESULT = 12000

TOOLS: list[dict[str, Any]] = [
    {"name": "write_contract", "description": "Save the environment contract: the whole JSON object. Replaces the "
     "previous version.", "parameters": {"type": "object", "properties": {
         "contract": {"type": "object", "additionalProperties": True, "description": "The contract."}},
         "required": ["contract"]}},
    {"name": "edit_contract", "description": "Change parts of the saved contract without writing it all again. Each "
     "edit sets the value at a path such as 'outputs.score' or 'actions.take.do[0]' (on a list, the index one past "
     "the end adds an item); an edit without a value removes what is there. All the edits of one call are saved "
     "together as one new revision.",
     "parameters": {"type": "object", "properties": {"edits": {"type": "array", "items": {
         "type": "object", "properties": {"path": {"type": "string"},
                                          "value": {"description": "The new value: any JSON value (an object, a list, "
                                                                   "a string, a number, true, false or null)."}},
         "required": ["path"]}}}, "required": ["edits"]}},
    {"name": "start_from", "description": "Save an engine starter as your contract (a new revision, tested like any "
     "other), to adapt to the brief with edit_contract. The reply shows the contract; one longer than a reply is cut, "
     "and the cut says where to read on (with `start`, which saves nothing again).",
     "parameters": {"type": "object", "properties": {"engine": {
         "type": "string", "enum": [spec.id for spec in list_engines(available=True)]}, "start": {
         "type": "integer", "minimum": 0, "description": "Read the starter on from here, in characters, without "
                                                         "saving it again."}}, "required": ["engine"]}},
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
    {"name": "guide",
     "description": "Read one part of the SDK guide, e.g. 'actions', 'effects', 'functions.collections'.",
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}, "start": {
         "type": "integer", "minimum": 0,
         "description": "Where to start reading, in characters: a part longer than one reply is cut, and the cut says "
                        "where to read on."}}, "required": ["part"]}},
]


#: What a tool call cut off at the output limit is answered.
CUT_CALL = "Your {tool} call was cut off at the output limit, so nothing was {done}."


CUT_WRITE = (" Write the contract shorter, or save a smaller one first and add the rest with edit_contract (which "
             "also revises a saved contract without writing it all again).")


def describe_changes(before: dict[str, Any], after: dict[str, Any]) -> str:
    """How ``after`` differs from ``before`` in what the environment is: its name, the parts :func:`_parts` names that
    were added or removed, and those whose content changed (an action's `do`, an event's effects ...)."""
    changes = ([f"name {before.get('name')!r} → {after.get('name')!r}"] if before.get("name") != after.get("name")
               else [])
    old_parts, new_parts = _parts(before), _parts(after)
    for key in [*old_parts, *(k for k in new_parts if k not in old_parts)]:
        old, new = old_parts.get(key, {}), new_parts.get(key, {})
        changed = sorted(name for name in old.keys() & new.keys() if old[name] != new[name])
        words = [f"-{k}" for k in sorted(old.keys() - new.keys())] + [f"+{k}" for k in sorted(new.keys() - old.keys())]
        words += [f"~{k}" for k in changed if not key.endswith(".params")]  # an action's changed params: it changed
        if words:
            changes.append(f"{key} " + " ".join(words))
    changes += [change for key in _settings(before, after) for change in _setting(key, before.get(key), after.get(key))]
    return "; ".join(changes)


def _settings(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """The top-level fields outside the sections of parts (`clock`, `space` …) that ``after`` changed."""
    return sorted(key for key in before.keys() | after.keys()
                  if key not in (*_SECTIONS, *_NAMING, "brief") and before.get(key) != after.get(key))


def _setting(key: str, before: Any, after: Any) -> list[str]:
    """A changed setting as a change summary shows it, in full: each of its fields that changed, with both values."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [f"{key}.{name} {_shown(before.get(name))} → {_shown(after.get(name))}"
                for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    return [f"{key} {_shown(before)} → {_shown(after)}"]


def _shown(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if value is not None else "none"


def removed_parts(before: dict[str, Any], after: dict[str, Any],
                  tests: tuple[Tested, Tested] | None = None) -> list[str]:
    """The parts of ``before`` that ``after`` no longer has, e.g. ``actions.take``, ``actions.take.params.count`` or
    ``brief.rules``, and brief texts it cut to almost nothing (the rules replaced by "Play."). With ``tests`` — what
    testing ``before`` and ``after`` found — also every way ``after``'s test runs behaved less than ``before``'s,
    however the revision was written (see :mod:`fg_env.authoring.behaviour`): a rule whose effects no longer change
    anything, an action no agent takes any more, fewer rounds played, an output or the scores constant, a view no
    longer shown, emptied or no longer filled with values, a type's brief gone or emptied."""
    old_parts, new_parts = _parts(before), _parts(after)
    gone = [f"{key}.{name}" for key, names in old_parts.items()
            for name in sorted(names.keys() - new_parts.get(key, {}).keys())]
    outermost = [path for path in gone if not any(path.startswith(other + ".") for other in gone)]
    kept = {f"{key}.{name}" for key, names in new_parts.items() for name in names}
    lost = collapsed(tests[0].profile, tests[1].profile, kept) if tests is not None else []
    return outermost + _cut(before, after) + lost


def _cut(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """The brief's texts — its situation, rules and roles — that ``after`` cut to almost nothing."""
    old, new = _brief_texts(before), _brief_texts(after)
    return [f"{path} (cut from {len(old[path])} to {len(new[path])} characters)" for path in old.keys() & new.keys()
            if len(old[path]) >= _SUBSTANTIAL and len(new[path]) * _CUT_TO < len(old[path])]


def _brief_texts(contract: dict[str, Any]) -> dict[str, str]:
    brief = contract.get("brief")
    if not isinstance(brief, dict):
        return {}
    found = {f"brief.{key}": brief.get(key) for key in ("situation", "rules")}
    roles = brief.get("roles")
    found.update({f"brief.roles.{kind}": text for kind, text in roles.items()} if isinstance(roles, dict) else {})
    return {path: text for path, text in found.items() if isinstance(text, str)}


#: The contract sections made of parts: together, what an environment is.
_SECTIONS = ("inputs", "world", "types", "entities", "relations", "records", "actions", "stages", "views", "events",
             "outputs", "end", "arms", "invariants", "defs", "mechanisms")
#: The top-level fields that name or describe the environment, which a change summary leaves out.
_NAMING = ("name", "description", "fg_env")
#: A brief text this long or longer that a revision cuts to less than a :data:`_CUT_TO`-th of it is gutted.
_SUBSTANTIAL = 40
_CUT_TO = 8
#: What an action or a type holds that a revision taking it away removes: who hears of an action and when it may be
#: taken; what a type's agents are after.
_ACTION_PARTS = ("announce", "when")
_TYPE_PARTS = ("score",)


def _parts(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The parts of each of :data:`_SECTIONS` by name (a list's item by its name, else its position), each action's
    params and :data:`_ACTION_PARTS`, and each type's props and :data:`_TYPE_PARTS`, read in the current form (a
    revision may be written in an earlier one)."""
    try:
        contract = normalize(contract)[0]
    except ContractError:
        pass  # an earlier form that cannot be rewritten: its parts as written (check reports why)
    parts: dict[str, dict[str, Any]] = {}
    for key in _SECTIONS:
        value = contract.get(key) or {}
        parts[key] = dict(value) if isinstance(value, dict) else {
            str(item.get("name", n)) if isinstance(item, dict) else str(n): item for n, item in enumerate(value)}
    for name, action in (contract.get("actions") or {}).items():
        if isinstance(action, dict) and isinstance(action.get("params"), dict):
            parts[f"actions.{name}.params"] = dict(action["params"])
        if isinstance(action, dict):
            parts[f"actions.{name}"] = {key: action[key] for key in _ACTION_PARTS if key in action}
    brief = contract.get("brief")
    if isinstance(brief, dict):  # what agents are told of the world and their role
        parts["brief"] = {key: brief[key] for key in ("situation", "rules", "roles") if brief.get(key)}
        if isinstance(brief.get("roles"), dict):
            parts["brief.roles"] = {kind: text for kind, text in brief["roles"].items() if text}
    for name, spec in (contract.get("types") or {}).items():
        if isinstance(spec, dict):
            parts[f"types.{name}"] = {key: spec[key] for key in _TYPE_PARTS if key in spec}
            if isinstance(spec.get("props"), dict):
                parts[f"types.{name}.props"] = dict(spec["props"])
    return parts


#: Rows of a long data input a starter's contract shows when the whole would not fit in one tool result.
_SHOWN_ROWS = 3


def _abridged(source: dict[str, Any]) -> str:
    """A starter's contract as the model reads it: whole when it fits in one tool result, else with its longest data
    inputs cut to their first rows, each cut marked where the rows were (so writing it back as shown is refused, never
    saved with the data lost) and named after it. The saved contract keeps every row."""
    text = json.dumps(source, ensure_ascii=False)
    shown, cuts = copy.deepcopy(source), []
    inputs: dict[str, Any] = shown["inputs"] if isinstance(shown.get("inputs"), dict) else {}
    tables = [name for name, spec in inputs.items() if isinstance(spec, dict)
              and isinstance(spec.get("default"), list) and len(spec["default"]) > _SHOWN_ROWS]
    for name in sorted(tables, key=lambda n: -len(json.dumps(inputs[n]["default"], ensure_ascii=False))):
        if len(text) <= MAX_RESULT:
            break
        rows = inputs[name]["default"]
        inputs[name]["default"] = [*rows[:_SHOWN_ROWS], f"... {len(rows) - _SHOWN_ROWS:,} more rows, not shown"]
        cuts.append(f"inputs.{name} ({len(rows):,} rows)")
        text = json.dumps(shown, ensure_ascii=False)
    if cuts:
        text += (f"\n[shown with only the first {_SHOWN_ROWS} rows of {', '.join(cuts)}: the saved contract holds "
                 "them all; change it with edit_contract, which keeps them, rather than writing it all again]")
    return text


def _content(contract: dict[str, Any]) -> str:
    """What testing a contract depends on, as a hash: everything but its name and description, so a revision that only
    renames the environment reuses the last test."""
    tested_part = {key: value for key, value in contract.items() if key not in ("name", "description")}
    return hashlib.sha256(json.dumps(tested_part, sort_keys=True).encode()).hexdigest()


def _tool(name: str, path: str, args: dict[str, Any]) -> str:
    """The model's ``check``, ``run`` or ``preview`` tool on the saved contract at ``path``, in the child process."""
    hosts = StubHosts(path)
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
              participants: dict[str, str] | None = None) -> str:
    env = load(path, seed=seed, hosts=hosts)
    wrong = _unplayable(participants, policy_names(env.contract))
    if wrong:
        return f"Bad tool call: run: {wrong}. Nothing was run."
    result = env.run(participants, budget={"seconds": seconds})
    return result.summary() + "\noutputs: " + json.dumps(result.outputs, default=str)


def _preview_tool(path: str, hosts: Hosts, agent: str) -> str:
    shown = load(path, seed=1, hosts=hosts).preview(agent)
    tools = "\n".join(f"- {t['name']}: {t['description']} {json.dumps(t['input_schema'])}" for t in shown["tools"])
    return f"BRIEF:\n{shown['brief']}\n\nUPDATE:\n{shown['update']}\n\nTOOLS:\n{tools}"


_CHILD_TOOLS: dict[str, Callable[..., str]] = {"check": _check_tool, "run": _run_tool, "preview": _preview_tool}


class Workbench:
    """The author's tools, backed by one contract file in a scratch folder. Every saved revision is tested with
    :func:`tested`; ``best`` is the kept one — the latest that works and removes nothing the kept one had, unless it
    was saved again to confirm the removal — ``latest`` the latest saved and ``problem`` what is wrong with the last
    write, or why it was not kept ("" when it is kept)."""

    def __init__(self, deadline: float = math.inf) -> None:
        #: When the session's time is up (a ``time.time()``): no tool call starts after it, and each child process
        #: step is given at most the time left.
        self.deadline = deadline
        self.path = Path(tempfile.mkdtemp(prefix="fg-author-")) / "contract.json"
        #: The child process the saved contract is tested, checked, run and previewed in.
        self.box = Sandbox()
        self.box.start()  # it imports the SDK while the model writes
        self.writes: list[Any] = []
        self.revisions: list[dict[str, Any]] = []
        self.working: list[int] = []
        #: The number of the kept revision.
        self.kept: int | None = None
        #: What testing found, per working revision.
        self.tests: dict[int, Tested] = {}
        #: What the latest working revision not kept removed from the kept one: saving that removal again confirms it.
        self.unconfirmed: list[str] = []
        self.problem = ""
        #: What testing found, by the hash of the contract tested.
        self._tested: dict[str, Tested] = {}

    @property
    def best(self) -> dict[str, Any] | None:
        return self.revisions[self.kept - 1] if self.kept else None

    @property
    def latest(self) -> dict[str, Any] | None:
        return self.revisions[-1] if self.revisions else None

    @property
    def out_of_revisions(self) -> bool:
        return len(self.revisions) >= MAX_REVISIONS

    def call(self, name: str, arguments: str) -> str:
        if self._left() <= 0:
            return f"Not done: the session's time is up, so {name} was not called."
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            return f"Bad tool call: there is no tool {name!r}; the tools are {', '.join(t['name'] for t in TOOLS)}."
        args, wrong = _arguments(tool["parameters"], arguments)
        if wrong:
            return (f"Bad tool call: {name}: {wrong}. It takes: "
                    f"{', '.join(tool['parameters']['properties']) or 'nothing'}.")
        try:
            text = str(getattr(self, "tool_" + name)(**args))
        except Exception as exc:  # the SDK's own errors are what the author reads
            text = f"{type(exc).__name__}: {exc}"
        if len(text) <= MAX_RESULT or name == "start_from":  # a starter cuts its own contract (see tool_start_from)
            return text
        more = (f": read on with guide({args['part']!r}, start={int(args.get('start') or 0) + MAX_RESULT})"
                if name == "guide" else "")
        return text[:MAX_RESULT] + f"\n[cut at {MAX_RESULT:,} of {len(text):,} characters{more}]"

    def cut_off(self, name: str, arguments: str) -> str:
        """Answer a call the provider cut off at the output limit; a cut-off write is recorded as a write."""
        if name not in ("write_contract", "edit_contract"):
            return CUT_CALL.format(tool=name, done="done") + " Call it again, keeping your reply short."
        self.writes.append(arguments)
        self.problem = "it was cut off at the output limit, so it saved nothing"
        return CUT_CALL.format(tool=name, done="saved") + CUT_WRITE

    def tool_write_contract(self, contract: Any) -> str:
        if isinstance(contract, dict):
            return self._save(contract)
        data, broken = parse_arguments(contract) if isinstance(contract, str) else (contract, None)  # JSON text
        if broken:
            self.writes.append(contract)
            self.problem = f"it was {broken}, so it saved nothing"
            return f"{broken[0].upper()}{broken[1:]}. Nothing saved."
        if not isinstance(data, dict):
            self.writes.append(contract)
            self.problem = "it was not a JSON object, so it saved nothing"
            return "Not a JSON object: a contract is one object {...}. Nothing saved."
        return self._save(data)

    def tool_edit_contract(self, edits: list[dict[str, Any]]) -> str:
        if self.latest is None:
            return "No contract saved yet: save one with write_contract first."
        if isinstance(edits, str):  # the edits written as JSON text, as models often do: read as the JSON they are
            parsed, broken = parse_arguments(edits)
            if broken:
                return f"`edits` is text that is {broken}: give the list of edits itself. Nothing saved."
            edits = parsed
        data = copy.deepcopy(self.latest)
        for n, one in enumerate(edits if isinstance(edits, list) else [edits]):
            wrong = _edit(data, one)
            if wrong:
                return f"edits[{n}]: {wrong}. Nothing saved."
        return self._save(data)

    def tool_start_from(self, engine: str, start: int = 0) -> str:
        """Save the starter (unless reading on from ``start``) and show its contract as far as one reply holds, the
        cut saying where to read on."""
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            return "Bad tool call: start_from: `start` is a number of characters ≥ 0. Nothing saved."
        source = engine_spec(engine).materialized_source()
        head = "" if start else self._save(source) + "\n\nThe contract:\n"
        body = _abridged(source)
        end = start + max(MAX_RESULT - len(head), MAX_RESULT // 2)
        if end >= len(body):
            return head + body[start:]
        return (head + body[start:end] + f"\n[cut at {end:,} of {len(body):,} characters of the contract: read on "
                f"with start_from({engine!r}, start={end}), which saves nothing again]")

    def _save(self, data: dict[str, Any]) -> str:
        if data == self.latest:
            return self._unchanged()
        if self.out_of_revisions:
            kept = f"revision {self.kept}, the best that works, is kept" if self.kept else "none works"
            return f"Revision limit reached ({MAX_REVISIONS}): nothing more is saved; {kept}."
        self.writes.append(data)
        self.revisions.append(data)
        self.path.write_text(json.dumps(data, indent=2))
        content = _content(data)
        if content not in self._tested:  # a revision saved before (or renamed), back again, is not tested again
            self._tested[content] = tested(str(self.path), self.box, self._left())
        found = self._tested[content]
        self.problem, number = found.problem[:MAX_RESULT], len(self.revisions)
        if self.problem:
            return f"Saved revision {number}, but it does not work yet: {self.problem}" + _notes(found)
        self.working.append(number)
        self.tests[number] = found
        saved = f"Saved revision {number}: it works — {_verdict(found)}."
        removed = (removed_parts(self.best, data, (self.tests[self.kept], found))
                   if self.best is not None and self.kept is not None else [])
        if removed and removed != self.unconfirmed:
            self.unconfirmed = removed
            self.problem = (f"it removed {', '.join(removed)}, which revision {self.kept} has; saving it again keeps "
                            "it instead")
            return (f"{saved}\nBut it removed {', '.join(removed)}, which revision {self.kept} has, so revision "
                    f"{self.kept} stays kept: put back what the brief asks for, or save it again to confirm the "
                    "removal." + _notes(found))
        self.kept, self.unconfirmed = number, []
        return saved + _notes(found)

    def _unchanged(self) -> str:
        """Answer a save of the latest revision as it is: no new revision; a removal it made is confirmed by it."""
        number = len(self.revisions)
        if self.unconfirmed and number in self.working and self.kept != number:
            self.kept, self.unconfirmed, self.problem = number, [], ""
            return f"Revision {number} saved again unchanged: its removals are confirmed, and it is kept."
        state = "it is kept" if self.kept == number else f"it is not kept: {self.problem}"
        return f"Nothing changed: this is revision {number} as saved, not a new revision; {state}"

    def tool_check(self) -> str:
        """The saved contract's check, with what testing it when it was saved showed besides (the same warnings the
        save's reply gave), so the two never disagree."""
        text = self._in_child("check")
        found = self._tested.get(_content(self.latest)) if self.latest is not None else None
        more = [warning for warning in (found.warnings if found is not None else ()) if warning not in text]
        if not more:
            return text
        head = "" if text.strip() == "No issues." else text + "\n"
        return head + "Testing this revision when it was saved also showed:\n" + "\n".join(more)

    def tool_run(self, seed: int = 1, participants: dict[str, str] | None = None) -> str:
        return self._in_child("run", seconds=RUN_SECONDS, seed=seed, participants=participants)

    def tool_preview(self, agent: str) -> str:
        return self._in_child("preview", agent=agent)

    def tool_guide(self, part: str, start: int = 0) -> str:
        return guide(part)[max(0, int(start or 0)):]

    def _in_child(self, name: str, **args: Any) -> str:
        """The ``name`` tool on the saved contract, in a child process of at most :data:`RUN_SECONDS` (or the session's
        time left)."""
        if not self.path.exists():
            return "No contract saved yet."
        seconds = min(RUN_SECONDS, self._left())
        try:
            text: str = self.box.call("fg_env.authoring.workbench:_tool",
                                      {"name": name, "path": str(self.path), "args": args}, seconds)
        except TooSlow as exc:
            if seconds < RUN_SECONDS:
                return f"Not done: the session's time ran out while {name} was going ({exc.step or 'building it'})."
            return f"Too slow: {name} was still going after {RUN_SECONDS:g}s ({exc.step or 'building it'})."
        except TooBig as exc:
            return f"Too big: {name} held more than {exc.megabytes:,} MB ({exc.step or 'building it'})."
        return text

    def _left(self) -> float:
        """Seconds left in the session."""
        return self.deadline - time.time()


def _verdict(found: Tested) -> str:
    """What the tests of a working revision showed."""
    checked = "it checks clean" if not found.warnings else "it checks with no errors (warnings below)"
    how = "without a problem" if found.untested else "to the end"
    ran = (f"{checked}, and runs {how} on {found.seeds} seeds with random agents, {len(TEST_SEEDS)} with idle ones, "
           "and once with agents choosing edge values")
    ran += f"; {found.untested}" if found.untested else ""
    average, largest = found.prompt
    return f"{ran}; an agent reads ~{average:,} tokens a turn (its brief and update), ~{largest:,} at most"


def _notes(found: Tested) -> str:
    """The host stand-ins and warnings behind a save's verdict, as lines to add to its reply."""
    lines = [f"Its host calls ({', '.join(found.hosts)}) were answered by the SDK's stand-in stubs, not a model: real "
             "answers need a real host bound (fg_env.host.load(..., hosts=...))."] if found.hosts else []
    if found.warnings:
        lines += ["Warnings:", *found.warnings]
    return "".join("\n" + line for line in lines)


def _unplayable(participants: Any, policies: list[str]) -> str:
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


def _arguments(params: dict[str, Any], arguments: str) -> tuple[dict[str, Any], str]:
    """A tool call's arguments, and what is wrong with them for ``params`` ("" when nothing is)."""
    args, broken = parse_arguments(arguments)
    if broken:
        return {}, f"its arguments are {broken}"
    if not isinstance(args, dict):
        return {}, "its arguments must be a JSON object"
    missing = [p for p in params.get("required", []) if p not in args]
    unknown = [p for p in args if p not in params["properties"]]
    wrong = [f"it needs {', '.join(missing)}"] if missing else []
    wrong += [f"it has no {', '.join(unknown)}"] if unknown else []  # a misnamed argument says which, beside the need
    return args, " and ".join(wrong)


def _edit(data: dict[str, Any], one: Any) -> str:
    """Apply one ``{"path": ..., "value": <any JSON value>}`` edit to ``data`` in place; returns what is wrong, or
    ""."""
    if not isinstance(one, dict) or not isinstance(one.get("path"), str) or not one["path"]:
        return 'an edit is {"path": "outputs.score", "value": <any JSON value>} (no value removes what is there)'
    path = one["path"]
    keys = [k for k in path.replace("[", ".").replace("]", "").split(".") if k]
    if not keys:  # "." or "[]": no part of the contract
        return (f"{path!r} names no part of the contract: give a path such as \"outputs.score\" (to replace the "
                "whole contract, call write_contract)")
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
    value = one["value"]
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
