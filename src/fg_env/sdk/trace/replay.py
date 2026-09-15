"""Replaying a recorded run: its tool calls made again, turn by turn, and checked against what the engine does now.

An LLM run recorded with ``exposures=True`` holds everything needed to play it again for free: the seed, inputs
and arm, every call each agent made (and when it read its brief, update and tools), and every host answer. The
:class:`Replayer` participant makes the recorded calls; the host tape answers the hosts. On every wake it checks
that the same agent woke at the same moment, read the same brief and update, was offered the same tools and got
the same result from each call — so after a change to the engine or the contract, the first difference is
reported precisely instead of surfacing later as a different outcome.

A replay needs a run recorded from its start. It cannot reproduce a wall-clock timeout, so a recorded turn that
ran out of time is reported as a divergence. After a divergence the run fails with :class:`ReplayDivergence`, or
— given a ``fallback`` participant — carries on with it for that turn and every later one.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional

from ..errors import RunError
from ..exposure import text_hash, tools_text
from ..measure import RunResult
from ..participants import Participant, resolve_participant
from ..session import Wake

if TYPE_CHECKING:
    from ..turn import Turn
    from .reader import Trace

__all__ = ["Replayer", "ReplayResult", "ReplayDivergence", "replay_run"]

_FINISHED = ("completed", "ended", "failed")
_USAGE = ("llm_calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "llm_retries",
          "forfeits")
#: Longest quoted text in a divergence message.
_QUOTE = 120
#: The run-level fields compared once every turn and event matched.
_ENDING = ("status", "ended_by", "rounds", "winner", "outputs", "metrics", "series", "error")


class ReplayDivergence(RunError):
    """The replay stopped matching its recording; ``divergence`` says where and how."""

    def __init__(self, divergence: Dict[str, Any]):
        self.divergence = divergence
        super().__init__(divergence["message"])


class Replayer:
    """A participant that makes a recorded run's calls again (see the module docs). ``divergence`` is the first
    difference found, or None; :meth:`unplayed` names recorded turns the replay never reached."""

    concurrent = False

    def __init__(self, recording: Any, fallback: Any = None):
        from .reader import Trace

        self.trace = recording if isinstance(recording, Trace) else Trace(recording)
        self.fallback = fallback
        self.divergence: Optional[Dict[str, Any]] = None
        self._recorded = {wake["turn"]: wake for wake in self.trace.wakes}
        self._played: set = set()
        self._live: Optional[Participant] = None
        self._lock = threading.Lock()

    def __call__(self, wake: Wake) -> Any:
        with self._lock:
            diverged = self.divergence is not None
        if not diverged:
            turn = wake._turn
            recorded = self._recorded.get(turn.number)
            found = self._check_wake(turn, recorded)
            if found is None and recorded is not None:
                found = self._play(wake, recorded)
            if found is None:
                return None
            with self._lock:
                if self.divergence is None:
                    self.divergence = found
            if self.fallback is None:
                raise ReplayDivergence(found)
        return self._fallback(wake)

    def unplayed(self) -> Optional[Dict[str, Any]]:
        """The first recorded turn the replay never reached, as a divergence (None when every one was played)."""
        left = [wake for number, wake in sorted(self._recorded.items()) if number not in self._played]
        if not left:
            return None
        first = left[0]
        return {**_at(first), "what": "missing",
                "message": f"{_label(first)}: the replay never reached this turn "
                           f"({len(left)} recorded turn(s) were not played)"}

    # -- checking a wake ---------------------------------------------------------------------

    def _check_wake(self, turn: "Turn", recorded: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        here = {"turn": turn.number, "entity": turn.actor.id, "round": turn.round, "stage": turn.stage.name}
        if recorded is None:
            last = max(self._recorded, default=0)
            return {**here, "wake": None, "what": "turn",
                    "message": f"{_label(here)}: the recording has no such turn (its last turn is {last})"}
        self._played.add(turn.number)
        if [recorded[key] for key in ("entity", "round", "stage")] != [here[key] for key in ("entity", "round", "stage")]:
            return {**_at(recorded), "what": "turn", "expected": _at(recorded), "got": here,
                    "message": f"{_label(here)}: the recording's turn {turn.number} woke {recorded['entity']} in round "
                               f"{recorded['round']}, stage {recorded['stage']}"}
        if recorded["timed_out"]:
            return {**_at(recorded), "what": "timeout",
                    "message": f"{_label(recorded)}: this turn ran out of time when it was recorded, and a wall-clock "
                               "timeout cannot be replayed"}
        return None

    def _play(self, wake: Wake, recorded: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        usage = recorded.get("usage") or {}
        counts = {key: usage[key] for key in _USAGE if usage.get(key)}
        if counts:
            wake.record_usage(**counts)
        calls: List[Mapping[str, Any]] = recorded["calls"]
        shown_tools: Optional[str] = None
        for index in range(len(calls) + 1):
            for key in ("brief", "update"):
                shown = recorded[key]
                if shown is not None and shown.get("after", 0) == index:
                    now = getattr(wake, key)
                    if text_hash(now) != shown["hash"]:
                        return self._text_differs(recorded, key, now)
            if index == len(calls):
                return None
            call = calls[index]
            offered = call.get("offered")
            if offered is not None and offered != shown_tools:
                tools = wake.tools
                if text_hash(tools_text(tools)) != offered:
                    return self._tools_differ(recorded, offered, tools_text(tools))
                shown_tools = offered
            result = wake.call(call["tool"], call["args"])
            if (result.ok, result.ended, text_hash(result.text)) != (call["ok"], call["ended"], call["result"]):
                return self._call_differs(recorded, index, call, result)
        return None

    def _text_differs(self, recorded: Mapping[str, Any], key: str, now: str) -> Dict[str, Any]:
        before = self.trace.texts.get(recorded[key]["hash"], "")
        return {**_at(recorded), "what": key, "expected": before, "got": now,
                "message": f"{_label(recorded)}: the {key} differs from the recording — {_first_difference(before, now)}"}

    def _tools_differ(self, recorded: Mapping[str, Any], offered: str, now_text: str) -> Dict[str, Any]:
        before = json.loads(self.trace.texts[offered]) if offered in self.trace.texts else []
        now = json.loads(now_text)
        names, now_names = [t["name"] for t in before], [t["name"] for t in now]
        if names != now_names:
            what = f"recorded {', '.join(names) or 'none'}; now {', '.join(now_names) or 'none'}"
        else:
            changed = next(a["name"] for a, b in zip(before, now) if a != b)
            what = f"the definition of '{changed}' changed"
        return {**_at(recorded), "what": "tools", "expected": names, "got": now_names,
                "message": f"{_label(recorded)}: the tools offered differ from the recording — {what}"}

    def _call_differs(self, recorded: Mapping[str, Any], index: int, call: Mapping[str, Any], result: Any) -> Dict[str, Any]:
        before = {"ok": call["ok"], "ended": call["ended"], "text": self.trace.texts.get(call["result"], "")}
        now = {"ok": result.ok, "ended": result.ended, "text": str.__str__(result.text)}
        args = json.dumps(call["args"], ensure_ascii=False)
        return {**_at(recorded), "what": "call", "call": index + 1, "tool": call["tool"], "expected": before, "got": now,
                "message": f"{_label(recorded)}: call {index + 1}, {call['tool']} {args}, returned {_outcome(now)}; "
                           f"the recording had {_outcome(before)}"}

    def _fallback(self, wake: Wake) -> Any:
        if self._live is None:
            env = wake._turn.env
            self._live = resolve_participant(self.fallback, env.contract, env.seeds.derive("participant"))
        return self._live(wake)

    def __repr__(self) -> str:
        return f"Replayer({len(self._recorded)} recorded turns{', fallback' if self.fallback is not None else ''})"


@dataclass
class ReplayResult:
    """A replay: ``ok`` when every turn, event and outcome matched the recording; else ``divergence`` (the first
    difference: ``what``, where, ``expected``/``got`` and a ``message``). ``result`` is the replayed run."""

    result: RunResult
    divergence: Optional[Dict[str, Any]]

    @property
    def ok(self) -> bool:
        return self.divergence is None

    @property
    def message(self) -> str:
        if self.divergence is not None:
            return self.divergence["message"]
        wakes = len(self.result.exposures.get("wakes") or [])
        return f"the replay matched its recording: {wakes} turn(s), {len(self.result.events)} event(s), " \
               f"{self.result.status} after {self.result.rounds} round(s)"

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "divergence": self.divergence,
                "result": self.result.to_dict(events=False)}


def replay_run(recording: "Trace", contract: Any, *, fallback: Any = None, hosts: Optional[Mapping[str, Any]] = None,
               data_dir: Any = None) -> ReplayResult:
    """Play ``contract`` again from ``recording``: its seed, inputs, arm and budget, the recorded calls
    (:class:`Replayer`) and the recorded host answers. ``hosts`` (host name → adapter) answers host calls the
    recording does not have — useful with a ``fallback``. The first divergence found is reported: a turn, a text,
    the tools or a call result; else the first differing event; else a recorded turn never reached; else the ending."""
    from ..api import load
    from ..host.hosts import Hosts

    recorded = recording.result
    if hosts is not None and not isinstance(hosts, Mapping):
        raise TypeError(f"hosts must be a mapping of host name to adapter, got {type(hosts).__name__}")
    tape = recorded.host_tape
    bound = Hosts(hosts, replay=tape) if hosts else Hosts.replaying(tape) if tape else None
    env = load(contract, inputs=recorded.inputs, seed=recorded.seed, arm=recorded.arm, hosts=bound, exposures=True,
               data_dir=data_dir)
    player = Replayer(recording, fallback)
    budget = {**recorded.budget["limits"], "on_exhaust": recorded.budget["on_exhaust"]} if recorded.budget else None
    rounds = None if recorded.status in _FINISHED else recorded.rounds
    result = env.run(player, rounds=rounds, budget=budget)
    divergence = player.divergence or _failure(recorded, result) or _events(recorded, result) or player.unplayed() \
        or _ending(recorded, result)
    return ReplayResult(result, divergence)


def _failure(recorded: RunResult, result: RunResult) -> Optional[Dict[str, Any]]:
    if result.status != "failed" or (recorded.status == "failed" and recorded.error == result.error):
        return None
    return {"what": "failed", "round": result.rounds, "expected": recorded.error, "got": result.error,
            "message": f"the replay failed in round {result.rounds}: {result.error}"}


def _events(recorded: RunResult, result: RunResult) -> Optional[Dict[str, Any]]:
    before, now = _plain(recorded.events), _plain(result.events)
    for old, new in zip(before, now):
        if old != new:
            return {"what": "event", "seq": old.get("seq"), "round": old.get("round"), "expected": old, "got": new,
                    "message": f"event {old.get('seq')} (round {old.get('round')}) differs from the recording — "
                               f"was {_event(old)}; now {_event(new)}"}
    if len(before) != len(now):
        longer, who = (before, "recording") if len(before) > len(now) else (now, "replay")
        first = longer[min(len(before), len(now))]
        return {"what": "event", "seq": first.get("seq"), "round": first.get("round"),
                "message": f"the {who} has {abs(len(before) - len(now))} more event(s), from event {first.get('seq')} "
                           f"(round {first.get('round')}): {_event(first)}"}
    return None


def _ending(recorded: RunResult, result: RunResult) -> Optional[Dict[str, Any]]:
    for key in _ENDING:
        old, new = _plain(getattr(recorded, key)), _plain(getattr(result, key))
        if old != new:
            if isinstance(old, dict) and isinstance(new, dict):
                name = next(k for k in sorted(set(old) | set(new), key=str) if old.get(k) != new.get(k))
                key, old, new = f"{key}.{name}", old.get(name), new.get(name)
            return {"what": "ending", "field": key, "expected": old, "got": new,
                    "message": f"{key} differs from the recording: was {_quote(old)}, now {_quote(new)}"}
    return None


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _at(wake: Mapping[str, Any]) -> Dict[str, Any]:
    return {"turn": wake["turn"], "wake": wake.get("wake"), "entity": wake["entity"], "round": wake["round"],
            "stage": wake["stage"]}


def _label(wake: Mapping[str, Any]) -> str:
    return f"turn {wake['turn']} ({wake['entity']}, round {wake['round']}, stage {wake['stage']})"


def _quote(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return json.dumps(text if len(text) <= _QUOTE else text[:_QUOTE - 1] + "…", ensure_ascii=False)


def _outcome(call: Mapping[str, Any]) -> str:
    return f"{'ok' if call['ok'] else 'a refusal'}{' (turn ended)' if call['ended'] else ''} {_quote(call['text'])}"


def _event(event: Mapping[str, Any]) -> str:
    return f"{event.get('kind')} {_quote(event.get('text', ''))}" if event.get("text") else \
        f"{event.get('kind')} {_quote(event.get('data', {}))}"


def _first_difference(before: str, now: str) -> str:
    old, new = before.splitlines(), now.splitlines()
    for number, (a, b) in enumerate(zip(old, new), 1):
        if a != b:
            return f"line {number} was {_quote(a)}, now {_quote(b)}"
    if len(old) > len(new):
        return f"line {len(new) + 1} {_quote(old[len(new)])} is gone"
    if len(new) > len(old):
        return f"line {len(old) + 1} {_quote(new[len(old)])} is new"
    return "they differ only in spacing"
