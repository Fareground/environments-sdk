"""Run diagnostics: likely logic problems a run revealed, in plain words, each with a fix.

A contract can pass every check and still not do what its author meant: a tool offered when none of its choices can
work, sealed choices that overwrite each other, an agent type that never has anything to do, a stage that can never
run, a measure that stays empty because nothing ever sets what it reads. These are read from what the run counted
(:mod:`fg_env.sdk.run_diagnosis`) and reported on ``RunResult.diagnostics``, in ``result.summary()`` and as warnings
from ``fg_env.check``. Each is reported only on evidence that random play cannot explain away, so a clean contract
raises none.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["diagnose", "MIN_CALLS", "REFUSED_SHARE", "MIN_ROUNDS"]

#: In a run with model participants (which report their usage), an action called this often and mostly refused is
#: reported; random and coded agents choose blindly, so their refusals say nothing about the tools.
MIN_CALLS = 4
REFUSED_SHARE = 0.5
#: Rounds of evidence needed before a metric that never changes, or an agent type that never can act, is reported.
MIN_ROUNDS = 2

_RECORD_READ = re.compile(r"\$records\(\s*([A-Za-z_]\w*)")
_WORLD_READ = re.compile(r"\$world\.([A-Za-z_]\w*)")
_PROP_READ = re.compile(r"(?:\$it|\$actor|\))\.([A-Za-z_]\w*)")
_ASSIGNED = re.compile(r"[.$]([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:[-+*/]=|(?<![<>!=])=(?!=))")
#: Roots whose value moves on its own: a condition reading one can hold later even if nothing else changes.
_MOVING = re.compile(r"\$(round|clock|time|stage|metrics|series|chance|random|randint|choice|shuffle|pending|pattern)\b")
_BUILT_IN_FIELDS = {"id", "name", "type", "alive", "at"}
#: Sections whose effects and settings can write properties, post to records or name a winner.
_RULE_SECTIONS = ("actions", "stages", "events", "triggers", "blocks", "end", "feeds", "physics", "policies")


def diagnose(env: "Env", outputs: Dict[str, Any]) -> List[Dict[str, str]]:
    """Every likely logic problem the run so far shows, as ``{code, path, message, fix}``."""
    rules = _Rules(env)
    return [*_actions(env), *_overwrites(env), *_idle_agents(env), *_stages(env, rules),
            *_stuck_measures(env, outputs, rules)]


def _finding(code: str, path: str, message: str, fix: str) -> Dict[str, str]:
    return {"code": code, "path": path, "message": message, "fix": fix}


def _most_common(reasons: Dict[str, List[Any]]) -> str:
    count, text = max(reasons.values(), key=lambda entry: entry[0])
    return f"{text.rstrip('.')} ({count}×)"


def _actions(env: "Env") -> List[Dict[str, str]]:
    out = []
    for name, entry in env.diagnosis.actions.items():
        if entry["unusable"]:
            out.append(_finding("action_offered_but_unusable", f"actions.{name}",
                                f"was offered {entry['unusable']} time(s) when none of its choices could succeed; "
                                f"refused with: {_most_common(entry['stuck'])}",
                                "hide it while it cannot work: put the requirement in `when` over $actor, or bound its "
                                "parameters (min, max, where) so the tool only offers choices that can succeed"))
        elif (env.stats.llm_calls and entry["calls"] >= MIN_CALLS and entry["refused"] >= REFUSED_SHARE * entry["calls"]
              and entry["reasons"]):
            out.append(_finding("action_mostly_refused", f"actions.{name}",
                                f"refused {entry['refused']} of {entry['calls']} calls; most often: "
                                f"{_most_common(entry['reasons'])}",
                                "make the tool say what is allowed: tighten its parameters (min, max, values, where) and "
                                "describe the rule in its description"))
    return out


def _overwrites(env: "Env") -> List[Dict[str, str]]:
    return [_finding("sealed_choices_overwrite", f"stages.{stage}",
                     f"sealed choices overwrote each other {count} time(s): {example}",
                     "give each agent its own value (a prop on $actor, or a map keyed by $actor.id) and combine them in "
                     "the stage's on_exit, or make the stage sequential")
            for stage, (count, example) in env.diagnosis.overwrites.items()]


def _idle_agents(env: "Env") -> List[Dict[str, str]]:
    out = []
    for kind, entry in env.diagnosis.agents.items():
        if entry["wakes"] and not entry["able"] and entry["rounds"] >= MIN_ROUNDS:
            out.append(_finding("agents_never_able_to_act", f"types.{kind}",
                                f"no {kind} had an action it could take in any of its {entry['wakes']} turn(s) over "
                                f"{entry['rounds']} rounds; most often: {_most_common(entry['reasons'])}",
                                "check the requirements (`when`) and parameter bounds of its actions, and the stage's "
                                "`who`, against the state when it is woken"))
    return out


def _stages(env: "Env", rules: "_Rules") -> List[Dict[str, str]]:
    out = []
    for stage in env.contract.stage_list():
        reached, ran, woke = env.diagnosis.stages.get(stage.name, [0, 0, 0])
        if reached and not ran and stage.when:
            cause = rules.frozen(stage.when)
            if cause:
                out.append(_finding("stage_never_runs", f"stages.{stage.name}",
                                    f"never ran and cannot: its `when` is false and {cause}",
                                    f"set what `when` reads in an action or event, or fix `when`: {stage.when}"))
        elif ran and not woke and stage.who:
            cause = rules.frozen(stage.who)
            if cause:
                out.append(_finding("stage_wakes_nobody", f"stages.{stage.name}",
                                    f"ran {ran} time(s) but woke no agent, and cannot: {cause}",
                                    f"set what `who` reads in an action or event, or fix `who`: {stage.who}"))
    return out


def _stuck_measures(env: "Env", outputs: Dict[str, Any], rules: "_Rules") -> List[Dict[str, str]]:
    out = []
    if env.finished and env.status != "failed":
        for name, spec in env.contract.outputs.items():
            cause = rules.cause(spec.expr) if name in outputs and outputs[name] is None else None
            if cause:
                out.append(_finding("output_empty", f"outputs.{name}", f"is empty (null) at the end of the run: {cause}",
                                    "set what it reads in an action or event, or read what the rules do change"))
    for name, metric in env.contract.metrics.items():
        series = env.world.series.get(name, [])
        if len(series) < MIN_ROUNDS or len({json.dumps(v, sort_keys=True, default=str) for v in series}) != 1:
            continue
        cause = rules.cause(metric.expr)
        if cause:
            shown = "null" if series[0] is None else json.dumps(series[0], default=str)
            out.append(_finding("metric_never_changes", f"metrics.{name}",
                                f"stayed {shown} for all {len(series)} rounds: {cause}",
                                "set what it reads in an action or event, or read what the rules do change"))
    return out


class _Rules:
    """What the contract's rules can write — found by reading them once, on first need."""

    def __init__(self, env: "Env"):
        self.env = env
        self._scanned: Optional[Tuple[Set[str], bool]] = None

    def cause(self, expr: str) -> Optional[str]:
        """Why ``expr`` cannot change, when everything it reads is frozen: records nothing posts to, properties no
        rule writes (and nothing wrote in this run), a winner no ending gives. None when anything it reads can
        change, or it reads nothing these can tell."""
        if "$pattern" in expr:  # a pattern follows time, chance or a memory input on its own
            return None
        env, world, written = self.env, self.env.world, self.env.diagnosis.written
        names, winner = self._scan()
        frozen: List[str] = []
        for record in _RECORD_READ.findall(expr):
            if record not in world.records_store:
                continue
            if world.records_store[record] or record in names:
                return None
            frozen.append(f"record `{record}`, which nothing posts to")
        declared = {prop for kind in env.contract.types for prop in env.contract.props_of(kind)}
        reads = [(f"`$world.{prop}`", prop) for prop in _WORLD_READ.findall(expr) if prop in env.contract.world]
        reads += [(f"`{prop}`", prop) for prop in _PROP_READ.findall(expr) if prop in declared and prop not in _BUILT_IN_FIELDS]
        for shown, prop in reads:
            if prop in written or prop in names:
                return None
            frozen.append(f"{shown}, which no rule changes")
        if "$result.winner" in expr:
            if winner:
                return None
            frozen.append("`$result.winner`, which no `end` condition or effect gives")
        return "it reads only " + "; ".join(dict.fromkeys(frozen)) if frozen else None

    def frozen(self, expr: str) -> Optional[str]:
        """:meth:`cause` for a condition that reads nothing that moves on its own (rounds, time, chance)."""
        return None if _MOVING.search(expr) else self.cause(expr)

    def _scan(self) -> Tuple[Set[str], bool]:
        """Every name the rules could write or post to (plus every name a mechanism owns), and whether any rule
        gives a winner."""
        if self._scanned is None:
            contract = self.env.contract
            names: Set[str] = set()
            winner = [False]
            data = contract.model_dump(by_alias=True)
            for section in _RULE_SECTIONS:
                _walk(data.get(section), names, winner)
            for spec in data.get("types", {}).values():
                _walk({key: spec.get(key) for key in ("on_create", "on_remove")}, names, winner)
            names |= _mechanism_owned(contract)
            self._scanned = (names, winner[0])
        return self._scanned


def _walk(value: Any, names: Set[str], winner: List[bool]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "props" and isinstance(item, dict):
                names.update(item)
            if key == "winner" and item:
                winner[0] = True
            _walk(item, names, winner)
    elif isinstance(value, list):
        for item in value:
            _walk(item, names, winner)
    elif isinstance(value, str):
        names.update(_ASSIGNED.findall(value))
        names.add(value.strip())  # a bare name as a setting: {"transfer": "cash"}, {"post": "chat"}


def _mechanism_owned(contract: Any) -> Set[str]:
    """Properties and records a mechanism declared: its own code writes them."""
    source = contract._source
    if not contract.mechanisms or not isinstance(source, dict):
        return set()
    written_by_author = set(source.get("world") or {}) | set(source.get("records") or {})
    for spec in (source.get("types") or {}).values():
        if isinstance(spec, dict):
            written_by_author |= set(spec.get("props") or {})
    declared = set(contract.world) | set(contract.records)
    declared |= {prop for kind in contract.types for prop in contract.props_of(kind)}
    return declared - written_by_author
