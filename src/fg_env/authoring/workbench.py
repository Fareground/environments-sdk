"""The author's workbench: the model's tools on one contract file in a scratch folder, every saved revision tested,
and the revision that works kept. ``check``, ``run`` and ``preview`` run in the sandbox's child process."""
from __future__ import annotations

import copy
import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..api import check, load
from ..engines import get as engine_spec
from ..engines import list_engines
from ..guides import guide
from ..host.hosts import Hosts
from .sandbox import Sandbox, TooSlow
from .testing import TEST_SEEDS, StubHosts, Tested, tested

__all__ = ["TOOLS", "Workbench", "describe_changes", "removed_parts"]

#: Most contract revisions the model may save (a write that saves nothing, such as invalid JSON, is not one).
MAX_REVISIONS = 8
#: Longest one call of the model's ``check``, ``run`` or ``preview`` tool may take.
RUN_SECONDS = 60
#: Longest tool result sent back to the model.
MAX_RESULT = 12000

TOOLS: list[dict[str, Any]] = [
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
    {"name": "guide",
     "description": "Read one part of the SDK guide, e.g. 'actions', 'effects', 'functions.collections'.",
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}, "start": {
         "type": "integer", "description": "Where to start reading, in characters: a part longer than one reply is "
                          "cut, and the cut says where to read on."}}, "required": ["part"]}},
]


#: What a tool call cut off at the output limit is answered.
CUT_CALL = "Your {tool} call was cut off at the output limit, so nothing was {done}."


CUT_WRITE = (" Write the contract shorter, or save a smaller one first and add the rest with edit_contract (which "
             "also revises a saved contract without writing it all again).")


def describe_changes(before: dict[str, Any], after: dict[str, Any]) -> str:
    """How ``after`` differs from ``before`` in what the environment is: its name and the parts :func:`_parts` names."""
    changes = ([f"name {before.get('name')!r} → {after.get('name')!r}"] if before.get("name") != after.get("name")
               else [])
    old_parts, new_parts = _parts(before), _parts(after)
    for key in [k for k in old_parts if k in new_parts]:
        old, new = old_parts[key], new_parts[key]
        if old != new:
            changes.append(f"{key} "
                           + " ".join([f"-{k}" for k in sorted(old - new)] + [f"+{k}" for k in sorted(new - old)]))
    return "; ".join(changes)


def removed_parts(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """The parts of ``before`` that ``after`` no longer has, e.g. ``actions.take`` or ``actions.take.params.count``."""
    old_parts, new_parts = _parts(before), _parts(after)
    gone = [f"{key}.{name}" for key, names in old_parts.items() for name in sorted(names - new_parts.get(key, set()))]
    return [path for path in gone
            if not any(path.startswith(other + ".") for other in gone)]  # an action, not its params


#: The contract sections made of parts: together, what an environment is.
_SECTIONS = ("inputs", "assets", "world", "types", "entities", "population", "relations", "links", "feeds", "patterns",
             "records", "actions", "stages", "views", "events", "triggers", "policies", "metrics", "outputs", "end",
             "arms", "invariants", "defs", "blocks", "mechanisms")


def _parts(contract: dict[str, Any]) -> dict[str, set[str]]:
    """The names of the parts of each of :data:`_SECTIONS` (a list's item by its name, else its position), and of each
    action's params."""
    parts: dict[str, set[str]] = {}
    for key in _SECTIONS:
        value = contract.get(key) or {}
        parts[key] = set(value) if isinstance(value, dict) else {
            str(item.get("name", n)) if isinstance(item, dict) else str(n) for n, item in enumerate(value)}
    for name, action in (contract.get("actions") or {}).items():
        if isinstance(action, dict) and isinstance(action.get("params"), dict):
            parts[f"actions.{name}.params"] = set(action["params"])
    return parts


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
    wrong = _unplayable(participants, list(env.contract.policies))
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

    def __init__(self) -> None:
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

    def tool_edit_contract(self, edits: list[dict[str, Any]]) -> str:
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

    def _save(self, data: dict[str, Any]) -> str:
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
        removed = removed_parts(self.best, data) if self.best is not None else []
        if removed and removed != self.unconfirmed:
            self.unconfirmed = removed
            self.problem = (f"it removed {', '.join(removed)}, which revision {self.kept} has; saving it again keeps "
                            "it instead")
            return (f"{saved}\nBut it removed {', '.join(removed)}, which revision {self.kept} has, so revision "
                    f"{self.kept} stays kept: put back what the brief asks for, or save it again to confirm the "
                    "removal." + _notes(found))
        self.kept, self.unconfirmed = number, []
        return saved + _notes(found)

    def tool_check(self) -> str:
        return self._in_child("check")

    def tool_run(self, seed: int = 1, participants: dict[str, str] | None = None) -> str:
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
            text: str = self.box.call("fg_env.authoring.workbench:_tool",
                                      {"name": name, "path": str(self.path), "args": args}, RUN_SECONDS)
        except TooSlow as exc:
            return f"Too slow: {name} was still going after {RUN_SECONDS:g}s ({exc.step or 'building it'})."
        return text


def _verdict(found: Tested) -> str:
    """What the tests of a working revision showed."""
    checked = "it checks clean" if not found.warnings else "it checks with no errors (warnings below)"
    how = "without a problem" if found.untested else "to the end"
    ran = (f"{checked}, and runs {how} on {found.seeds} seeds with random agents, {len(TEST_SEEDS)} with idle ones, "
           "and once with agents choosing edge values")
    return f"{ran}; {found.untested}" if found.untested else ran


def _notes(found: Tested) -> str:
    """The host stand-ins and check warnings behind a save's verdict, as lines to add to its reply."""
    lines = [f"Its host calls ({', '.join(found.hosts)}) were answered by the SDK's stand-in stubs, not a model: real "
             "answers need a real host bound (fg_env.host.load(..., hosts=...))."] if found.hosts else []
    if found.warnings:
        lines += ["Check warnings:", *found.warnings]
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


def _edit(data: dict[str, Any], one: Any) -> str:
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
