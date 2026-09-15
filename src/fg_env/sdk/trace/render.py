"""Trace readings as text for people: aligned tables and one line per wake or call."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Sequence

from ..tournament.result import _columns

__all__ = ["overview", "turns", "timeline", "search", "invalid", "agent", "args_text", "one_line"]

#: Longest argument list or result shown on a single line before it is cut.
_LINE = 100


def one_line(text: str, width: int = _LINE) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= width else flat[:width - 1] + "…"


def args_text(args: Any) -> str:
    if args in ({}, None):
        return ""
    return one_line(json.dumps(args, ensure_ascii=False))


def _call(call: Mapping[str, Any]) -> str:
    outcome = "ok" if call["ok"] else call.get("error", "refused")
    return f"{call['tool']}{(' ' + args_text(call['args'])) if args_text(call['args']) else ''} → {outcome}"


def overview(data: Mapping[str, Any]) -> str:
    run = data["run"]
    how = f"ended by {run['ended_by']}" if run["ended_by"] else run["status"]
    lines = [f"{run['status']} after {run['rounds']} round(s), {how} (seed {run['seed']}"
             f"{', arm ' + run['arm'] if run['arm'] else ''}); {run['wakes']} wake(s)"]
    if run["winner"] is not None:
        lines.append(f"winner: {run['winner']}")
    if run["budget"] and run["budget"].get("exhausted"):
        lines.append(f"budget: {run['budget']['exhausted']} ran out")
    header = ["agent", "type", "turns", "calls", "invalid", "rejected", "timeouts", "undone", "LLM calls", "tokens in",
              "tokens out"]
    table = [header] + [[row["entity"], row["type"], str(row["turns"]), str(row["calls"]),
                         f"{row['invalid']} ({row['invalid_rate']:.0%})", str(row["rejected"]), str(row["timeouts"]),
                         str(row["undone"]), str(row["llm_calls"]), f"{row['input_tokens']:,}",
                         f"{row['output_tokens']:,}"] for row in [*data["agents"], data["totals"]]]
    return "\n".join(lines + _columns(table))


def turns(wakes: Sequence[Mapping[str, Any]]) -> str:
    return "\n\n".join(_turn(wake) for wake in wakes)


def _turn(wake: Mapping[str, Any]) -> str:
    when = f", time {wake['time']:g}" if "time" in wake else ""
    lines = [f"Wake {wake['wake']}: {wake['entity']} ({wake['type']}), round {wake['round']}{when}, stage {wake['stage']}, "
             f"turn {wake['turn']}{' (reaction)' if wake['kind'] == 'reaction' else ''}",
             f"Why: {wake['reason']}"]
    for key in ("brief", "update"):
        text = wake[key]
        lines.append(f"--- {key}: not read ---" if text is None else f"--- {key} ({len(text):,} chars) ---\n{text}")
    lines.append("--- tools offered ---")
    lines.append(", ".join(wake["tools"]) or "none were read")
    lines.append("--- calls ---")
    for index, call in enumerate(wake["calls"], 1):
        ended = ", turn ended" if call["ended"] else ""
        lines.append(f"{index}. {_call(call)}{ended}")
        lines += ["   " + line for line in call["result"].splitlines()]
    if not wake["calls"]:
        lines.append("none")
    facts = [f"invalid calls: {wake['invalid']}"]
    if wake["timed_out"]:
        facts.append("ran out of time")
    if wake["undone"]:
        facts.append(f"undone: {wake['undone']}")
    if wake["usage"]:
        facts.append("usage: " + ", ".join(f"{key} {value:,}" for key, value in wake["usage"].items()))
    lines.append(" · ".join(facts))
    return "\n".join(lines)


def _prefix(row: Mapping[str, Any]) -> str:
    when = f" t{row['time']:g}" if "time" in row else ""
    return f"#{row['wake']} r{row['round']}{when} {row['stage']} {row['entity']}"


def timeline(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = []
    for row in rows:
        calls = "; ".join(_call(call) for call in row["calls"]) or "no calls"
        notes = (" [reaction]" if row["kind"] == "reaction" else "") + (" [out of time]" if row["timed_out"] else "") \
            + (" [undone]" if row["undone"] else "")
        lines.append(f"{_prefix(row)}{notes}: {calls}")
    return "\n".join(lines) or "no wakes"


def search(hits: Sequence[Mapping[str, Any]], text: str) -> str:
    if not hits:
        return f"'{text}' appears nowhere agents read or wrote"
    return "\n".join(f"{_prefix(hit)} {hit['where']}: {hit['snippet']}" for hit in hits)


def invalid(rows: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"{_prefix(row)} call {row['call']} {row['tool']}{(' ' + args_text(row['args'])) if args_text(row['args']) else ''}"
             f" → {row['error']}: {one_line(row['correction'])}" for row in rows]
    return "\n".join(lines) or "every call went through"


def agent(data: Dict[str, Any]) -> str:
    lines = [f"{data['entity']} ({data['type']}): {data['turns']} turn(s), {data['calls']} call(s), "
             f"{data['invalid']} invalid ({data['invalid_rate']:.0%}), {data['rejected']} rejected, "
             f"{data['timeouts']} timeout(s), {data['undone']} undone"]
    if data["llm_calls"] or data["input_tokens"] or data["output_tokens"]:
        lines.append(f"model: {data['llm_calls']} call(s), {data['input_tokens']:,} tokens in, "
                     f"{data['output_tokens']:,} tokens out")
    tools: List[List[str]] = [["tool", "calls", "ok", "refused"]] + [
        [name, str(t["calls"]), str(t["ok"]), str(t["refused"])] for name, t in data["tools"].items()]
    if len(tools) > 1:
        lines += _columns(tools)
    lines.append(timeline(data["timeline"]))
    return "\n".join(lines)
