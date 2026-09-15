"""Highlights and narrative: the notable moments of one run, found without an LLM.

Every candidate moment gets a ``score`` on one scale: surprise in standard-normal units (a
score of 2 is about as unusual as a two-sigma move). Metric moves are scored against the
run's other moves (median and robust spread); peaks and troughs against the spread a random
walk would reach; streaks by how unlikely a run of same-direction moves is under coin flips;
events and actions by how rarely they happen (in one round out of N). ``significance`` is the
matching tail probability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..measure import RunResult
from .stats import normal_quantile, numeric, sd, two_sided_z

__all__ = ["highlights", "narrative", "Highlight"]

#: Robust spread: the median absolute deviation times this equals the sd for normal data.
_MAD_TO_SD = 1.4826
#: Scores are capped so one extreme item cannot hide the rest of the ranking.
_Z_CAP = 8.0
#: A move counts as reversed when later rounds undo at least this share of it.
_REVERSAL_SHARE = 0.5
#: Shortest same-direction run reported as a streak.
_MIN_STREAK = 3
#: Event kinds that are bookkeeping around actions rather than moments of their own.
_ACTION_KINDS = ("action", "outcome")


@dataclass(frozen=True)
class Highlight:
    kind: str  # move | reversal | peak | trough | streak | first | last | event | end
    round: int
    subject: str
    score: float
    text: str
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def significance(self) -> float:
        """Two-sided tail probability matching the score."""
        return 2.0 * (1.0 - _normal_cdf(self.score))

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "round": self.round, "subject": self.subject, "score": round(self.score, 3),
                "significance": round(self.significance, 4), "text": self.text, "data": self.data}


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _cap(z: float) -> float:
    return max(0.0, min(_Z_CAP, z))


def _one_sided_z(p: float) -> float:
    return _cap(normal_quantile(1.0 - min(1.0 - 1e-12, max(p, 1e-12))))


def _fmt(v: float) -> str:
    return f"{v:.4g}"


def move_score(index: int, changes: Sequence[float]) -> float:
    """Surprise of ``changes[index]`` against the other changes' median and robust spread.

    The spread is 1.4826 × median absolute deviation, or the sd when that is 0; when every other
    change has the same size, the move is scored as a one-in-N event.
    """
    size = abs(changes[index])
    others = [abs(c) for i, c in enumerate(changes) if i != index]
    if not others:
        return 0.0
    centre = median(others)
    spread = _MAD_TO_SD * median([abs(o - centre) for o in others]) or sd(others)
    if spread > 0:
        return _cap((size - centre) / spread)
    return _cap(two_sided_z(1.0 / len(changes))) if size > centre else 0.0


def _series_candidates(name: str, values: List[float], word: str) -> List[Highlight]:
    if len(values) < 3 or max(values) == min(values):
        return []
    changes = [b - a for a, b in zip(values, values[1:])]
    out: List[Highlight] = []
    top = max(range(len(changes)), key=lambda i: (abs(changes[i]), -i))
    if changes[top] != 0:
        out += _move(name, values, changes, top, word)
    out += _extremes(name, values, changes, word)
    streak = _streak(name, values, changes, word)
    if streak:
        out.append(streak)
    return out


def _move(name: str, values: List[float], changes: List[float], i: int, word: str) -> List[Highlight]:
    change, before, after, round_ = changes[i], values[i], values[i + 1], i + 2
    z = move_score(i, changes)
    typical = median([abs(c) for c in changes])
    verb = "rose" if change > 0 else "fell"
    pct = f" ({change / abs(before):+.0%})" if before else ""
    ratio = f", {abs(change) / typical:.1f}× the typical move" if typical > 0 else ""
    text = f"{name} {verb} {_fmt(abs(change))}{pct} to {_fmt(after)} in {word} {round_}: the largest one-{word} move{ratio}"
    back, back_round = 0.0, None
    for k in range(i + 2, len(values)):
        undone = -(values[k] - after) if change > 0 else values[k] - after
        if undone > back:
            back, back_round = undone, k + 1
    share = back / abs(change)
    if back_round is not None and share >= _REVERSAL_SHARE:
        # A reversal is the fuller account of the same moment, so it replaces the move.
        again = "gave back" if change > 0 else "recovered"
        amount = "all of it" if share >= 1.0 else f"{share:.0%} of it"
        return [Highlight("reversal", round_, name, _cap(z * min(1.0, share)),
                          f"{name} {verb} {_fmt(abs(change))}{pct} in {word} {round_}, then {again} {amount} by {word} "
                          f"{back_round}", {"change": change, "undone_by": back_round, "share_undone": share})]
    return [Highlight("move", round_, name, z, text, {"change": change, "from": before, "to": after})]


def _extremes(name: str, values: List[float], changes: List[float], word: str) -> List[Highlight]:
    spread = sd(changes) * math.sqrt(len(changes))
    if spread <= 0:
        return []
    out = []
    for kind, pick in (("peak", max), ("trough", min)):
        i = values.index(pick(values))
        if i in (0, len(values) - 1):
            continue
        before, after = values[:i], values[i + 1:]
        if kind == "peak":
            rise, fall = values[i] - min(before), values[i] - min(after)
            base_before, base_after, verb = min(before), min(after), "peaked"
        else:
            rise, fall = max(before) - values[i], max(after) - values[i]
            base_before, base_after, verb = max(before), max(after), "bottomed out"
        prominence = min(rise, fall)
        if prominence <= 0:
            continue
        out.append(Highlight(kind, i + 1, name, _cap(prominence / spread),
                             f"{name} {verb} at {_fmt(values[i])} in {word} {i + 1} (from {_fmt(base_before)}, then "
                             f"{_fmt(base_after)})", {"value": values[i], "prominence": prominence}))
    return out


def _streak(name: str, values: List[float], changes: List[float], word: str) -> Optional[Highlight]:
    best: Tuple[int, int, int] = (0, 0, 0)  # length, start, sign
    start = 0
    while start < len(changes):
        sign = (changes[start] > 0) - (changes[start] < 0)
        end = start
        while sign and end + 1 < len(changes) and ((changes[end + 1] > 0) - (changes[end + 1] < 0)) == sign:
            end += 1
        if sign and end - start + 1 > best[0]:
            best = (end - start + 1, start, sign)
        start = end + 1
    length, first, sign = best
    whole = length == len(changes)
    if length < _MIN_STREAK or whole:
        return None
    moving = sum(1 for c in changes if c != 0)
    chance = 1.0 - math.exp(-moving * 2.0 ** (-length))  # a run this long anywhere among coin-flip moves
    verb = "rose" if sign > 0 else "fell"
    return Highlight("streak", first + 2, name, _one_sided_z(chance),
                     f"{name} {verb} {length} {word}s in a row ({word}s {first + 2}-{first + length + 1}), from "
                     f"{_fmt(values[first])} to {_fmt(values[first + length])}", {"length": length})


def _event_candidates(result: RunResult) -> List[Highlight]:
    total_rounds = max(1, result.rounds)
    word = result.unit
    title = word[:1].upper() + word[1:]
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for event in result.events:
        kind = str(event.get("kind"))
        data = event.get("data") or {}
        if kind == "outcome" or kind == "end":
            continue
        if kind == "action":
            if not data.get("success", True):
                continue
            key = ("action", str(data.get("action")))
        elif kind == "record":
            key = ("record", str(data.get("record")))
        else:
            key = (kind, str(data.get("event") or event.get("text") or kind))
        groups.setdefault(key, []).append(event)
    out = []
    for (kind, subject), events in groups.items():
        rounds_with = sorted({int(e.get("round", 0)) for e in events})
        first = events[0]
        z = _cap(two_sided_z(len(rounds_with) / total_rounds))
        actor = first.get("actor")
        if kind in ("action", "record"):
            label = f"first {subject}" + (f" by {actor}" if actor else "")
            detail = f": {first['text']}" if first.get("text") else ""
            text = f"{title} {rounds_with[0]}: {label}{detail}"
            out.append(Highlight("first", rounds_with[0], subject, z, text, {"count": len(events), "rounds": len(rounds_with)}))
            last = rounds_with[-1]
            gap = total_rounds - last
            if len(rounds_with) >= 2 and gap > 0:
                rate = len(rounds_with) / last
                silence = _one_sided_z((1.0 - min(rate, 1.0 - 1e-9)) ** gap)
                if silence > 0:
                    out.append(Highlight("last", last, subject, silence,
                                         f"{title} {last}: last {subject}; none in the {gap} {word}(s) after",
                                         {"rounds": len(rounds_with)}))
        else:
            times = f" ({len(events)} times)" if len(events) > 1 else ""
            # A private event's text is written to its recipient ("your order…"): name the kind instead.
            shown = first.get("text") if first.get("text") and not first.get("to") else kind.replace("_", " ")
            text = f"{title} {rounds_with[0]}: {shown}{times}"
            out.append(Highlight("event", rounds_with[0], subject, z, text,
                                 {"kind": kind, "count": len(events), "private": bool(first.get("to"))}))
    if result.status == "ended":
        end = next((e for e in reversed(result.events) if e.get("kind") == "end"), None)
        text = (end or {}).get("text") or f"the run ended ({result.ended_by})"
        winner = f"; winner: {result.winner}" if result.winner is not None else ""
        out.append(Highlight("end", result.rounds, str(result.ended_by), _cap(two_sided_z(1.0 / total_rounds)),
                             f"{title} {result.rounds}: {text}{winner}", {"ended_by": result.ended_by}))
    return out


def _merge_moves(candidates: List[Highlight]) -> List[Highlight]:
    """Moves of several metrics in the same round are one moment: keep the strongest, name the others."""
    by_round: Dict[int, List[Highlight]] = {}
    rest: List[Highlight] = []
    for c in candidates:
        (by_round.setdefault(c.round, []) if c.kind == "move" else rest).append(c)
    merged = []
    for moves in by_round.values():
        moves.sort(key=lambda h: (-h.score, h.subject))
        lead = moves[0]
        if len(moves) > 1:
            also = [m.subject for m in moves[1:]]
            lead = Highlight(lead.kind, lead.round, lead.subject, lead.score,
                             f"{lead.text} (also moving sharply: {', '.join(also)})", {**lead.data, "also": also})
        merged.append(lead)
    return rest + merged


def highlights(result: RunResult, *, top: int = 5, metrics: Optional[Sequence[str]] = None) -> List[Highlight]:
    """The ``top`` most notable moments of a run, most surprising first (ties: earliest first).

    ``metrics`` limits which metric series are scanned (default: all). Event-based moments need
    the run's event log (``RunResult.events``).
    """
    if top < 1:
        raise ValueError("top must be at least 1")
    names = list(metrics) if metrics is not None else list(result.series)
    candidates: List[Highlight] = []
    for name in names:
        if name not in result.series:
            raise ValueError(f"no series '{name}' in this run (series: {', '.join(result.series) or 'none'})")
        values = [numeric(v) for v in result.series[name]]
        if all(v is not None for v in values):
            candidates += _series_candidates(name, [float(v) for v in values if v is not None], result.unit)
    candidates = _merge_moves(candidates) + _event_candidates(result)
    candidates = [c for c in candidates if c.score > 0]
    # Equally rare moments: what everyone saw outranks one agent's private bookkeeping.
    candidates.sort(key=lambda h: (-h.score, bool(h.data.get("private")), h.round, h.subject, h.kind))
    return candidates[:top]


def narrative(result: RunResult, *, limit: int = 10) -> str:
    """A compact factual timeline: how the run went, its notable moments in order, and its results. Rounds are named in
    the clock's unit, with their date on a dated clock (``Week 7 (2026-10-12): …``)."""
    how = f"ended by {result.ended_by}" if result.ended_by else result.status
    title = result.unit[:1].upper() + result.unit[1:]
    lines = [f"{result.status.capitalize()} after {result.rounds} {result.unit}(s), {how}."]
    if result.error:
        lines.append(f"Error: {result.error}")
    moments = highlights(result, top=limit) if result.series or result.events else []
    news = [(int(e.get("round", 0)), str(e["text"])) for e in result.events
            if e.get("kind") not in _ACTION_KINDS + ("record", "end") and e.get("text")]
    entries: List[Tuple[int, int, str]] = [
        (h.round, 0, f"{result.period(h.round, capital=True)}: {h.text.removeprefix(f'{title} {h.round}: ')}")
        for h in moments]
    seen = {text for _, _, text in entries}
    for round_, text in news:
        line = f"{result.period(round_, capital=True)}: {text}"
        if line not in seen and not any(text in t for _, _, t in entries):
            entries.append((round_, 1, line))
            seen.add(line)
    entries.sort(key=lambda e: (e[0], e[1]))
    lines += [text for _, _, text in entries[: max(limit, 1)]]
    if result.winner is not None:
        lines.append(f"Winner: {result.winner}.")
    numbers = [(k, v) for k, v in result.outputs.items() if numeric(v) is not None][:8]
    if numbers:
        lines.append("Results: " + ", ".join(f"{k} {_fmt(float(v)) if not isinstance(v, bool) else v}" for k, v in numbers) + ".")
    return "\n".join(lines)
