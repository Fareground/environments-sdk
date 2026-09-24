"""Chance nodes: a `chance` effect picks one outcome from a distribution it lists.

Two forms::

    {"chance": [{"p": 0.5, "label": "heads", "do": [...]}, {"p": 0.5, "label": "tails", "do": [...]}], "as": "coin"}
    {"chance": "deal", "outcomes": "$world.deck", "weight": "1", "as": "card", "do": ["$actor.card = $card"]}

The branch form runs the chosen branch's `do` and binds its label to `as`; the named form picks one
item of `outcomes` (each with probability `weight` / total weight, `$it` being the item), binds the
item to `as` and runs `do`. Every pick is logged as a `chance` event (outcome, index, probability).

Outcomes are sampled from the run's random stream, so a seed replays them. Search code can instead
choose them (explicit chance): ``world.chance_picker`` receives the :class:`ChanceNode` and returns
the index of the outcome to take. A run that records exposures notes every chosen outcome
(``exposures["chance"]``), so a replay reproduces it without the chooser.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Set, Tuple

from ..world.entity import Entity
from ..errors import RunError
from ..expr.template import format_value
from .statements import RESERVED_ROOTS

if TYPE_CHECKING:
    from .runner import EffectRunner

__all__ = ["ChanceOutcome", "ChanceNode", "run_chance", "sample", "check_chance", "PROBABILITY_TOLERANCE"]

#: How far listed probabilities may add up from 1 (float noise) and still count as a distribution.
PROBABILITY_TOLERANCE = 1e-9
_BRANCH_KEYS = ("p", "label", "do")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ChanceOutcome:
    """One outcome of a chance node. ``value`` is the item (named form) or the label (branch form)."""

    index: int
    label: str
    p: float
    value: Any


@dataclass(frozen=True)
class ChanceNode:
    """A chance node waiting for its outcome: where it is and what can happen."""

    name: str
    site: str
    outcomes: Tuple[ChanceOutcome, ...]

    @property
    def possible(self) -> List[ChanceOutcome]:
        """The outcomes with a probability above zero, in listed order."""
        return [outcome for outcome in self.outcomes if outcome.p > 0]


def run_chance(runner: "EffectRunner", effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    node = _node(runner, effect, vars, where)
    chosen = node.outcomes[_pick(world, node, where)]
    world.emit("chance", "", to=(), data={"chance": node.name, "outcome": chosen.label, "index": chosen.index,
                                          "p": chosen.p, "site": where})
    name = effect.get("as")
    if name:
        vars[name] = chosen.value
    if isinstance(effect["chance"], list):
        runner.run(effect["chance"][chosen.index].get("do") or [], vars, f"{where}.chance[{chosen.index}].do")
    else:
        runner.run(effect.get("do") or [], vars, f"{where}.do")


def _node(runner: "EffectRunner", effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> ChanceNode:
    raw = effect["chance"]
    if isinstance(raw, list):
        outcomes = []
        for index, branch in enumerate(raw):
            p = _number(runner.eval(branch.get("p"), vars), f"{where}.chance[{index}].p", "a probability")
            if p > 1:
                raise RunError(f"a probability is at most 1, got {format_value(p)}", f"{where}.chance[{index}].p")
            label = runner.text(branch.get("label"), vars) or str(index + 1)
            outcomes.append(ChanceOutcome(index, label, p, label))
        total = sum(outcome.p for outcome in outcomes)
        if abs(total - 1) > PROBABILITY_TOLERANCE:
            raise RunError(f"the probabilities add up to {total:.10g}, not 1", f"{where}.chance")
        return ChanceNode(str(effect.get("as") or "chance"), where, tuple(outcomes))
    items = runner.eval(effect.get("outcomes"), vars)
    if isinstance(items, str) and runner.world.is_type(items):
        items = runner.world.entities_of(items)
    if not isinstance(items, (list, tuple)) or not items:
        raise RunError(f"`outcomes` must give a non-empty list, got {format_value(items)}", f"{where}.outcomes")
    weights = [_number(runner.eval(effect["weight"], {**vars, "it": item, "i": position}), f"{where}.weight",
                       "a weight") if "weight" in effect else 1.0 for position, item in enumerate(items)]
    total = sum(weights)
    if total <= 0:
        raise RunError("every outcome has weight 0, so nothing can happen", f"{where}.weight")
    outcomes = [ChanceOutcome(i, _label(item), weight / total, item) for i, (item, weight) in enumerate(zip(items, weights))]
    return ChanceNode(str(raw), where, tuple(outcomes))


def sample(world: Any, node: ChanceNode) -> int:
    """An outcome drawn from the run's random stream (the default when no picker chooses)."""
    possible = node.possible
    roll, reached = world.rng.random(), 0.0
    for outcome in possible:
        reached += outcome.p
        if roll < reached:
            return outcome.index
    return possible[-1].index  # the roll sat in the float gap below 1


def _pick(world: Any, node: ChanceNode, where: str) -> int:
    possible = node.possible
    picker = world.chance_picker
    if picker is None:
        return sample(world, node)
    index = picker(node)
    if isinstance(index, bool) or not isinstance(index, int) or not any(o.index == index for o in possible):
        choices = ", ".join(f"{o.index} ({o.label})" for o in possible)
        raise RunError(f"the chance picker chose {index!r}, which is not a possible outcome (possible: {choices})", where)
    if world.exposures is not None:  # recorded, so a replay reproduces the pick without the chooser
        world.exposures.picked(node, index, world.round)
    return index


def _number(value: Any, path: str, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RunError(f"{what} must be a number ≥ 0, got {format_value(value)}", path)
    return float(value)


def _label(item: Any) -> str:
    if isinstance(item, Entity):
        return item.name
    return str.__str__(item) if isinstance(item, str) else format_value(item)


# -- static check ---------------------------------------------------------------------------------


def check_chance(checker: Any, effect: Mapping[str, Any], path: str, roots: Set[str], types: Dict[str, Set[str]],
                 params: Optional[Mapping[str, Any]]) -> Set[str]:
    """Check a `chance` effect; returns the names it makes available afterwards."""
    from ..checks.roots import merge_types

    bound: Set[str] = set()
    name = effect.get("as")
    if name is not None:
        if not isinstance(name, str) or not _NAME.match(name):
            checker.error(f"{path}.as", "`as` names a local, like \"card\"", "use a plain name")
        elif name in RESERVED_ROOTS:
            checker.error(f"{path}.as", f"'{name}' is a built-in root", "choose another name")
        else:
            bound.add(name)
    inner = roots | bound
    inner_types = {key: value for key, value in types.items() if key not in bound}
    raw = effect["chance"]
    if isinstance(raw, list):
        for key in ("outcomes", "weight", "do"):
            if key in effect:
                checker.error(f"{path}.{key}", f"a list of branches does not take `{key}`",
                              "give each branch its own `p` and `do`, or name the chance and list `outcomes`")
        if not raw:
            checker.error(f"{path}.chance", "needs at least one branch", 'e.g. [{"p": 0.5, "do": [...]}, {"p": 0.5}]')
        bound |= _check_branches(checker, raw, path, roots, inner, inner_types, params)
    elif isinstance(raw, str) and raw.strip():
        if "outcomes" not in effect:
            checker.error(path, "a named chance needs `outcomes`", "a list, a type name or an expression giving a list")
        checker.value(effect.get("outcomes"), f"{path}.outcomes", roots, types, params)
        checker.expr(effect.get("weight"), f"{path}.weight", roots | {"it", "i"}, types, params)
        bound |= checker.effects(effect.get("do", []), f"{path}.do", inner, inner_types, params) - roots
    else:
        checker.error(f"{path}.chance", "is a list of branches or the name of a chance with `outcomes`",
                      'e.g. {"chance": [{"p": 0.5, "do": [...]}, {"p": 0.5}]}')
    merge_types(types, inner_types)
    return bound


def _check_branches(checker: Any, branches: List[Any], path: str, roots: Set[str], inner: Set[str],
                    types: Dict[str, Set[str]], params: Optional[Mapping[str, Any]]) -> Set[str]:
    bound: Set[str] = set()
    from ..checks.roots import merge_types

    paths: List[Dict[str, Set[str]]] = []
    total, literal = 0.0, True
    labels: Set[str] = set()
    for index, branch in enumerate(branches):
        where = f"{path}.chance[{index}]"
        if not isinstance(branch, dict):
            checker.error(where, "a branch is an object with `p` and `do`", '{"p": 0.5, "label": "heads", "do": [...]}')
            literal = False
            continue
        for key in branch:
            if key not in _BRANCH_KEYS:
                checker.error(f"{where}.{key}", f"'{key}' is not part of a chance branch",
                              f"a branch takes: {', '.join(_BRANCH_KEYS)}")
        p = branch.get("p")
        if p is None:
            checker.error(where, "needs `p` (its probability)")
            literal = False
        elif isinstance(p, str):
            checker.expr(p, f"{where}.p", roots, types, params)
            literal = False
        elif isinstance(p, bool) or not isinstance(p, (int, float)) or not 0 <= p <= 1:
            checker.error(f"{where}.p", f"a probability is a number from 0 to 1, got {p!r}")
            literal = False
        else:
            total += p
        label = branch.get("label")
        if isinstance(label, str):
            checker.template(label, f"{where}.label", None, roots, types, params)
            if label in labels:
                checker.error(f"{where}.label", f"label '{label}' is used by another branch", "give each branch its own label")
            labels.add(label)
        elif label is not None:
            checker.error(f"{where}.label", "a label is text")
        branch_types = dict(types)
        bound |= checker.effects(branch.get("do", []), f"{where}.do", inner, branch_types, params) - roots
        paths.append(branch_types)
    merge_types(types, *paths)
    if literal and branches and abs(total - 1) > PROBABILITY_TOLERANCE:
        checker.error(f"{path}.chance", f"the probabilities add up to {total:.10g}, not 1", "make them add up to 1")
    return bound
