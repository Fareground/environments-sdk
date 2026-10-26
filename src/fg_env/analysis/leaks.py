"""A leak scanner: plays a run and searches everything each agent is shown for other agents' private values.

Per agent per turn it reads the brief, the update, every tool's description and schema (enums and bounds included),
every view by `look`, every entity `inspect` offers and the text of every call it makes, refusals included. The
secrets it looks for are the current private properties of every other agent, rendered as the engine renders them.
Only distinctive values count — long numbers and texts found nowhere in the contract's rules, in public state or in
the reader's own properties — so a match is a leak, not a coincidence. A platform runs it on the environments it
offers (and the ones its people build) to show that no agent is told what is hidden from it::

    from fg_env import analysis

    found = analysis.scan_leaks("shop.json", rounds=3)
    assert found.leaks == [], found.leaks
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..api import Env, load
from ..expr.template import format_value
from ..participants import RandomAgent

__all__ = ["LeakScanner", "scan_leaks"]

#: The shortest rendering that counts as distinctive: shorter numbers and words recur by chance.
DISTINCT = 5


def _renderings(value: Any) -> set[str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):  # as the engine shows numbers: plain (2 decimals at most) and as money
        shown = {format_value(value), f"{value:,.2f}", f"{value:,.2f}".rstrip("0").rstrip(".")}
        return {text for text in shown if sum(ch.isdigit() for ch in text) >= DISTINCT}
    if isinstance(value, str):  # a mask with nothing in it yet ("_ _ _ _") tells nothing but its length
        return {value} if len(value) >= DISTINCT and any(ch.isalnum() for ch in value) else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_renderings(item) for item in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(_renderings(item) for item in value.values())) if value else set()
    return set()


class LeakScanner:
    """Wraps a participant (default: random) for every agent of ``env`` and collects leaks as it plays."""

    concurrent = False  # one turn at a time: the scanner keeps the reader's secrets while its turn is played

    def __init__(self, env: Env, inner: Any = None, source: Any = None):
        self.env = env
        self.inner: Any = inner or RandomAgent(seed=env.seed)
        contract = env.contract
        self.private = {kind: {key for key, spec in contract.props_of(kind).items() if spec.private}
                        for kind in contract.agent_types()}
        rules = dict(source if source is not None else contract.model_dump(mode="json"))
        rules.pop("entities", None)  # where secrets are planted; every other part of the contract is common knowledge
        self.known = json.dumps(rules, default=str)
        self.leaks: list[str] = []
        self.hidden: dict[str, str] = {}
        self.turns = 0
        self.result: Any = None

    def secrets(self, viewer: str) -> dict[str, str]:
        """Distinctive renderings of every other agent's private values, each naming whose it is."""
        entities = self.env.entities(alive=False)
        public: set[str] = set()
        for entity in entities:
            public |= {entity["id"], str(entity["name"])}  # handles every agent may be shown
            hidden = self.private.get(entity["type"], set())
            for key, value in entity["props"].items():
                if entity["id"] == viewer or key not in hidden:
                    public |= _renderings(value)
        public |= _renderings(self.env.props)
        found = {}
        for entity in entities:
            if entity["id"] == viewer:
                continue
            for key in self.private.get(entity["type"], ()):
                for text in _renderings(entity["props"].get(key)) - public:
                    if text not in self.known:
                        found[text] = f"{entity['id']}.{key}"
        return found

    def __call__(self, wake: Any) -> Any:
        self.turns += 1
        self.hidden = self.secrets(wake.entity_id)  # what this reader must not see, as its turn begins
        shown = [wake.brief, wake.update, *self._tools(wake), *self._reads(wake)]
        self._scan(wake, shown, "turn")
        return self.inner(_Watched(wake, self))

    def _tools(self, wake: Any) -> list[str]:
        return [f"{tool.name}: {tool.description} {json.dumps(tool.input_schema)}" for tool in wake.tools]

    def _reads(self, wake: Any) -> list[str]:
        """Every view and every offered entity, rotating by turn, until the turn's free reads run out."""
        calls = []
        for tool in wake.tools:
            prop: dict[str, Any] = next(iter(tool.input_schema.get("properties", {}).values()), {})
            if tool.name in ("look", "inspect"):
                arg = "view" if tool.name == "look" else "id"
                calls += [(tool.name, {arg: choice}) for choice in prop.get("enum", [])]
        texts = []
        start = self.turns % len(calls) if calls else 0
        for name, args in calls[start:] + calls[:start]:
            result = wake.call(name, args)
            if not result.ok:
                break
            texts.append(result.text)
        return texts

    def _scan(self, wake: Any, texts: list[str], where: str) -> None:
        for text in texts:
            for needle, owner in self.hidden.items():
                if re.search(rf"(?<![\w.,]){re.escape(needle)}(?![\w]|[.,]\d)", text):
                    self.leaks.append(f"{wake.entity_id} (round {wake.round}, {where}) was shown {owner} = {needle!r}")


class _Watched:
    """The wake as the wrapped participant sees it: every call's reply and the tools offered after it are scanned."""

    def __init__(self, wake: Any, scanner: LeakScanner):
        self._wake, self._scanner = wake, scanner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wake, name)

    def call(self, name: str, args: Any = None) -> Any:
        result = self._wake.call(name, args)
        self._scanner._scan(self._wake, [result.text, *self._scanner._tools(self._wake)], f"reply to {name}")
        return result


def scan_leaks(contract: Any, seed: int = 1, inner: Any = None, rounds: Any = None, **options: Any) -> LeakScanner:
    """Play ``contract`` (a dict or a path) with every agent scanned — by ``inner`` (default: random agents that read
    everything they are offered) — and return the scanner: its ``leaks`` are what it found (empty: nothing hidden was
    shown), its ``result`` the run. ``options`` go to :func:`fg_env.load` (``inputs``, ``arm`` …)."""
    env = load(contract, seed=seed, **options)
    source = contract if isinstance(contract, dict) else json.loads(Path(contract).read_text())
    scanner = LeakScanner(env, inner, source)
    result = env.run(scanner, rounds=rounds)
    scanner.result = result
    return scanner
