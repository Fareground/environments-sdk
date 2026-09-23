"""Score one authored contract: does it run, and do the brief's fidelity checks hold?

``evaluate(contract, required, checks)`` runs the contract on three seeds with random agents (plus each declared policy), then
calls every check for the brief with a :class:`Ctx`. A check is a plain function that raises ``AssertionError`` with
a reason when the environment is unfaithful.
"""
from __future__ import annotations

import json
import random
import re
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

import fg_env
from fg_env.participants import sample_args

SEEDS = (1, 2, 3)
#: Wall-clock cap for one run, so a runaway contract cannot stall the benchmark.
RUN_SECONDS = 60
#: Most look-tool targets the probe reads per wake (reads beyond the free allowance use up calls).
PROBE_LOOKS = 8


class Probe:
    """A participant that reads everything it can (brief, update, every look tool) and acts at random with
    distinctive numbers and text, so checks can see who was shown what."""

    def __init__(self, seed: int):
        self.seed = seed
        self.texts: Dict[str, List[str]] = defaultdict(list)
        self.calls: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        #: What each agent read about each entity through look tools: ``[(target id, text)]``.
        self.looks: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        self.result: Any = None

    def __call__(self, wake: Any) -> None:
        me = wake.entity_id
        rng = random.Random(f"{self.seed}:{me}:{wake.round}:{wake.stage}")
        self.texts[me] += [wake.brief or "", wake.update or ""]
        for tool in [t for t in wake.tools if t.kind == "look"]:
            targets = ((tool.input_schema.get("properties") or {}).get("id") or {}).get("enum") or []
            for target in targets[:PROBE_LOOKS]:
                text = wake.call(tool.name, {"id": target}).text or ""
                self.texts[me].append(text)
                self.looks[me].append((target, text))
        acts = [t for t in wake.tools if t.kind == "act"]
        if acts and not wake.done:
            tool = rng.choice(acts)
            args = _distinctive(tool.input_schema, rng, f"zq{me}{wake.round}")
            result = wake.call(tool.name, args)
            self.calls[me].append({"round": wake.round, "tool": tool.name, "args": args, "ok": result.ok})
            self.texts[me].append(result.text or "")

    def seen(self, viewer: str, pattern: str) -> Optional[str]:
        """The first text ``viewer`` read that matches the regex ``pattern`` (commas removed), else None."""
        for text in self.texts.get(viewer, []):
            match = re.search(pattern, text.replace(",", ""))
            if match:
                return text[max(0, match.start() - 60):match.end() + 60]
        return None


    def looked(self, viewer: str, subject: str, pattern: str) -> Optional[str]:
        """The first look-tool result about ``subject`` that ``viewer`` read and that matches ``pattern``."""
        for target, text in self.looks.get(viewer, []):
            if target == subject and re.search(pattern, text.replace(",", "")):
                return text[:200]
        return None


def _distinctive(schema: Dict[str, Any], rng: random.Random, token: str) -> Dict[str, Any]:
    """Random valid arguments, with numbers that carry cents (unbounded whole numbers get several digits) and
    text that is a unique token."""
    args = sample_args(schema, rng)
    for name, prop in (schema.get("properties") or {}).items():
        if "enum" in prop:
            continue
        if prop.get("type") == "integer" and "maximum" not in prop and "multipleOf" not in prop:
            args[name] = rng.randint(prop.get("minimum", 0) + 1000, prop.get("minimum", 0) + 99999)
        elif prop.get("type") == "number" and "multipleOf" not in prop:
            low, high = prop.get("minimum", 0), prop.get("maximum", max(prop.get("minimum", 0), 0) + 1000)
            value = round(low + (high - low) * rng.uniform(0.15, 0.85), 2)
            args[name] = value if value != int(value) or value + 0.37 > high else value + 0.37
        elif prop.get("type") == "string":
            args[name] = f"{token}{name}"
    return args


def number_pattern(value: Any) -> str:
    """A regex matching ``value`` as a standalone number in text."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    tail = r"0*" if "." in text else r"(?:\.0+)?"  # 12.5 is also shown as 12.50, 9 as 9.00 (but 9 is not 900)
    return rf"(?<![\d.]){re.escape(text)}{tail}(?![\d])"


class Ctx:
    """What a check reads: the contract, three random runs, and helpers for more runs."""

    def __init__(self, contract: Dict[str, Any]):
        self.contract = contract
        self.results = [self.run(seed) for seed in SEEDS]
        self.outs = [r.outputs for r in self.results]
        self._probes: Dict[int, Probe] = {}

    def run(self, seed: int, participants: Any = "random") -> Any:
        return _run(self.contract, seed, participants)

    def env(self, seed: int = 1) -> Any:
        return fg_env.load(self.contract, seed=seed)

    def probe(self, seed: int = 1) -> Probe:
        """A run with every agent played by a :class:`Probe` (cached per seed)."""
        if seed not in self._probes:
            probe = self._probes[seed] = Probe(seed)
            probe.result = fg_env.load(self.contract, seed=seed).run(probe, budget={"seconds": RUN_SECONDS})
        return self._probes[seed]

    def agents(self) -> List[Dict[str, Any]]:
        env = self.env()
        return [e for e in env.entities() if env.contract.is_agent(e["type"])]

    def names(self) -> Dict[str, str]:
        return {e["id"]: e["name"] for e in self.env().entities()}


#: Runs by (contract JSON, seed, participants): checks and the run table share them.
_RUNS: Dict[Tuple[str, int, str], Any] = {}


def _run(contract: Dict[str, Any], seed: int, participants: Any) -> Any:
    key = (json.dumps(contract, sort_keys=True), seed, json.dumps(participants, sort_keys=True))
    if key not in _RUNS:
        _RUNS[key] = fg_env.load(contract, seed=seed).run(participants, budget={"seconds": RUN_SECONDS})
    return _RUNS[key]


def eval_runs(contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The harness runs: three seeds with random agents, then each declared policy on those seeds, played by the
    agent types that can take the actions its rules call (everyone else random)."""
    plans: List[Tuple[Any, int]] = [("random", seed) for seed in SEEDS]
    for name, policy in (contract.get("policies") or {}).items():
        seats = _policy_seats(contract, name, policy)
        plans += [(seats, seed) for seed in SEEDS if seats]
    out = []
    for participants, seed in plans:
        try:
            result = _run(contract, seed, participants)
            ok, error = result.status != "failed", result.error
        except Exception as exc:  # a contract that cannot even load or run is a failed run, not a harness crash
            ok, error = False, f"{type(exc).__name__}: {exc}"
        out.append({"participants": participants, "seed": seed, "ok": ok, "error": error and str(error)[:300]})
    return out


def _policy_seats(contract: Dict[str, Any], name: str, policy: Any) -> Dict[str, str]:
    actions = contract.get("actions") or {}
    called = {rule.get("do") for rule in (policy or {}).get("rules") or [] if isinstance(rule, dict)}
    types = set()
    for action in called:
        by = (actions.get(action) or {}).get("by") if isinstance(action, str) else None
        types |= {by} if isinstance(by, str) else set(by or [])
    return {kind: f"policy:{name}" for kind in sorted(types)}


def evaluate(contract: Dict[str, Any], required: List[str], checks: List[Callable[[Ctx], None]]) -> Dict[str, Any]:
    """Run the contract and every check. Returns the runs, and per check ``passed`` plus why it failed."""
    runs = eval_runs(contract)
    names = ["outputs_present", *(c.__name__ for c in checks)]
    if not all(r["ok"] for r in runs[:len(SEEDS)]):
        return {"runs": runs, "checks": [{"check": n, "passed": False, "why": "random runs fail"} for n in names]}
    ctx = Ctx(contract)

    def outputs_present(ctx: Ctx) -> None:
        for out in ctx.outs:
            missing = [name for name in required if name not in out]
            assert not missing, f"missing required outputs {missing} (has {sorted(out)})"

    results = []
    for check in [outputs_present, *checks]:
        try:
            check(ctx)
            results.append({"check": check.__name__, "passed": True})
        except AssertionError as exc:
            results.append({"check": check.__name__, "passed": False, "why": str(exc)[:300]})
        except Exception as exc:  # a check that trips over the author's shapes is a fidelity failure
            results.append({"check": check.__name__, "passed": False, "why": f"{type(exc).__name__}: {exc}"[:300]})
    return {"runs": runs, "checks": results}
