"""Replaying a recorded run: every turn's steps played again, and each wake checked against its recording.

A run recorded with ``exposures=True`` holds everything needed to play it again for free: its seed, inputs, arm
and budget; every wake's ``steps`` — the engine tape's record of what the participant did, in order (reads,
calls, reported usage, timeouts; see :mod:`fg_env.sdk.replay`); and every host answer. The :class:`Replayer`
participant plays those steps back through the same wake; the host tape answers the hosts. As each step is played it
compares what the agent was shown and got with the recording — the same agent woke at that moment, read the same
brief and update, was offered the same tools, got the same result from every call and ran out of time the same
way — so after a change to the engine or the contract the first difference is reported precisely.

A replay needs a run recorded from its start; the recording's turn time limits apply, without a wall clock. After
a divergence the run fails with :class:`ReplayDivergence`, or — given a ``fallback`` participant — carries on with
it for that turn and every later one. The replaying run must record exposures (the checks read them).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from ..errors import RunError
from ..measure import RunResult
from ..participants import Participant, resolve_participant
from ..replay import apply_step
from ..session import Wake

if TYPE_CHECKING:
    from ..turn import Turn
    from .reader import Trace

__all__ = ["Replayer", "ReplayResult", "ReplayDivergence", "replay_run"]

_FINISHED = ("completed", "ended", "failed")
#: Refusals given once a turn is over: they change nothing and are not steps on the tape.
_AFTER_THE_TURN = ("ended", "timeout")
#: Longest quoted text in a divergence message.
_QUOTE = 120
#: The run-level fields compared once every turn and event matched.
_ENDING = ("status", "ended_by", "rounds", "winner", "outputs", "returns", "metrics", "series", "error")


class ReplayDivergence(RunError):
    """The replay stopped matching its recording; ``divergence`` says where and how."""

    def __init__(self, divergence: Dict[str, Any]):
        self.divergence = divergence
        super().__init__(divergence["message"])


class Replayer:
    """A participant that plays a recorded run's steps again (see the module docs). ``divergence`` is the first
    difference found, or None; :meth:`unplayed` names recorded turns the replay never reached."""

    concurrent = False

    def __init__(self, recording: Any, fallback: Any = None):
        from .reader import Trace

        self.trace = recording if isinstance(recording, Trace) else Trace(recording)
        if any("steps" not in wake for wake in self.trace.wakes):
            raise ValueError("this recording has no steps to replay (it was saved by an older engine); record the run "
                             "again with exposures=True")
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
            if turn.exposure is None:
                raise RunError("a replay checks every turn against its recording, so the replaying run must record "
                               "exposures: load it with exposures=True (fg_env.trace(recording).replay(contract) does)",
                               f"participant:{turn.actor.id}")
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
        return {**_at(left[0]), "what": "missing",
                "message": f"{_label(left[0])}: the replay never reached this turn "
                           f"({len(left)} recorded turn(s) were not played)"}

    # -- checking a turn -------------------------------------------------------------------

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
        return None

    def _play(self, wake: Wake, recorded: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Play the turn's steps, checking each against the recording as soon as it is done."""
        turn = wake._turn
        made = _effective(recorded["calls"])
        checked = 0
        for step in recorded["steps"]:
            if step[0] == "call" and wake.done:
                continue  # the turn is already over here: the call count below reports it
            apply_step(wake, step)
            if step[0] in ("brief", "update", "tools"):
                found = self._read_differs(turn, recorded, step[0])
            elif step[0] == "call":
                found, checked = self._calls_differ(turn, recorded, made, checked)
            else:
                found = None
            if found is not None:
                return found
        return self._ending_differs(turn, recorded, made)

    def _read_differs(self, turn: "Turn", recorded: Mapping[str, Any], kind: str) -> Optional[Dict[str, Any]]:
        now, texts = self._live_record(turn)
        if kind == "tools":
            before, after = recorded["tool_sets"][:1], now["tool_sets"][:1]
            return self._tools_differ(recorded, before, after, texts) if before != after else None
        shown, seen = recorded[kind], now[kind]
        if (shown or {}).get("hash") == (seen or {}).get("hash"):
            return None
        return self._text_differs(recorded, kind, shown, seen, texts)

    def _calls_differ(self, turn: "Turn", recorded: Mapping[str, Any], made: List[Mapping[str, Any]],
                      checked: int) -> Tuple[Optional[Dict[str, Any]], int]:
        now, texts = self._live_record(turn)
        calls = _effective(now["calls"])
        for index in range(checked, len(calls)):
            if index >= len(made):
                return self._count_differs(recorded, len(made), len(calls)), index
            if _outcome_of(made[index]) != _outcome_of(calls[index]):
                return self._call_differs(recorded, index + 1, made[index], calls[index], texts), index
        return None, len(calls)

    def _ending_differs(self, turn: "Turn", recorded: Mapping[str, Any],
                        made: List[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
        played = len(_effective(self._live_record(turn)[0]["calls"]))
        if played != len(made):
            return self._count_differs(recorded, len(made), played)
        if recorded["timed_out"] != turn.timed_out:  # the live record gets its totals when the turn closes
            which = "the recording" if recorded["timed_out"] else "the replay"
            return {**_at(recorded), "what": "timeout", "expected": recorded["timed_out"], "got": turn.timed_out,
                    "message": f"{_label(recorded)}: the turn ran out of time only in {which}"}
        return None

    @staticmethod
    def _live_record(turn: "Turn") -> Tuple[Dict[str, Any], Mapping[str, str]]:
        """The replayed turn's exposure record so far, and the replaying run's texts."""
        assert turn.exposure is not None and turn.env.world.exposures is not None
        return turn.exposure.record, turn.env.world.exposures.texts

    @staticmethod
    def _count_differs(recorded: Mapping[str, Any], before: int, now: int) -> Dict[str, Any]:
        return {**_at(recorded), "what": "call", "call": min(before, now) + 1, "expected": before, "got": now,
                "message": f"{_label(recorded)}: the recording made {before} call(s) in this turn and the replay {now} "
                           f"(the turn went differently after call {min(before, now)})"}

    def _text_differs(self, recorded: Mapping[str, Any], key: str, before: Optional[Mapping[str, Any]],
                      after: Optional[Mapping[str, Any]], texts: Mapping[str, str]) -> Dict[str, Any]:
        old = self.trace.texts.get(before["hash"], "") if before else None
        new = texts.get(after["hash"], "") if after else None
        if old is None or new is None:
            what = f"it was read only in {'the replay' if old is None else 'the recording'}"
        else:
            what = _first_difference(old, new)
        return {**_at(recorded), "what": key, "expected": old, "got": new,
                "message": f"{_label(recorded)}: the {key} differs from the recording — {what}"}

    def _tools_differ(self, recorded: Mapping[str, Any], before: List[str], after: List[str],
                      texts: Mapping[str, str]) -> Dict[str, Any]:
        old = json.loads(self.trace.texts[before[0]]) if before and before[0] in self.trace.texts else []
        new = json.loads(texts[after[0]]) if after else []
        names, now_names = [t["name"] for t in old], [t["name"] for t in new]
        if names != now_names or not old:
            what = f"recorded {', '.join(names) or 'none'}; now {', '.join(now_names) or 'none'}"
        else:
            what = f"the definition of '{next(a['name'] for a, b in zip(old, new) if a != b)}' changed"
        return {**_at(recorded), "what": "tools", "expected": names, "got": now_names,
                "message": f"{_label(recorded)}: the tools offered differ from the recording — {what}"}

    def _call_differs(self, recorded: Mapping[str, Any], index: int, before: Mapping[str, Any],
                      after: Mapping[str, Any], texts: Mapping[str, str]) -> Dict[str, Any]:
        old = {"ok": before["ok"], "ended": before["ended"], "text": self.trace.texts.get(before["result"], "")}
        new = {"ok": after["ok"], "ended": after["ended"], "text": texts.get(after["result"], "")}
        args = json.dumps(before["args"], ensure_ascii=False)
        return {**_at(recorded), "what": "call", "call": index, "tool": before["tool"], "expected": old, "got": new,
                "message": f"{_label(recorded)}: call {index}, {before['tool']} {args}, returned {_outcome(new)}; "
                           f"the recording had {_outcome(old)}"}

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
    """Play ``contract`` again from ``recording``: its seed, inputs, arm, budget and turn time limits, the recorded
    steps (:class:`Replayer`) and the recorded host answers. ``hosts`` (host name → adapter) answers host calls the
    recording does not have — useful with a ``fallback``. The first divergence is reported: a turn, a text, the tools
    or a call; else the first differing event; else a recorded turn never reached; else the ending."""
    from ..api import load
    from ..host.hosts import Hosts

    recorded = recording.result
    if hosts is not None and not isinstance(hosts, Mapping):
        raise TypeError(f"hosts must be a mapping of host name to adapter, got {type(hosts).__name__}")
    tape = recorded.host_tape
    bound = Hosts(hosts, replay=tape) if hosts else Hosts.replaying(tape) if tape else None
    player = Replayer(recording, fallback)
    env = load(contract, inputs=recorded.inputs, seed=recorded.seed, arm=recorded.arm, hosts=bound, exposures=True,
               data_dir=data_dir)
    env.driver.timed = False  # timeouts come from the recorded steps, never from this machine's clock
    budget = {**recorded.budget["limits"], "on_exhaust": recorded.budget["on_exhaust"]} if recorded.budget else None
    rounds = None if recorded.status in _FINISHED else recorded.rounds
    result = env.run(player, rounds=rounds, budget=budget, time_limit=_run_time_limit(recording, env.contract))
    divergence = player.divergence or _failure(recorded, result) or _events(recorded, result) or player.unplayed() \
        or _ending(recorded, result)
    return ReplayResult(result, divergence)


def _run_time_limit(recording: "Trace", contract: Any) -> Optional[float]:
    """The run-wide turn time limit the recording was made with: the one its wakes show in stages that set none."""
    staged = {stage.name for stage in contract.stage_list() if stage.time_limit is not None}
    found = {wake["time_limit"] for wake in recording.wakes if "time_limit" in wake and wake["stage"] not in staged}
    return next(iter(found)) if len(found) == 1 else None


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


def _effective(calls: List[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    """The calls that reached an open turn (refusals after the turn was over change nothing and are not steps)."""
    return [call for call in calls if call.get("error") not in _AFTER_THE_TURN]


def _outcome_of(call: Mapping[str, Any]) -> List[Any]:
    return [call["tool"], call["args"], call["ok"], call["ended"], call["result"]]


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
