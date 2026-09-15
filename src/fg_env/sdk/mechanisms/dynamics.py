"""World dynamics that are not agents: drift, shocks and uncertainty priors.

.. code-block:: json

    "trends": {"kind": "drift", "rules": {
        "fatigue": {"target": "resident.propensity", "model": "mean_reversion", "rate": 0.05, "mean": 0.7, "sd": 0.01}}},
    "events": {"kind": "shocks", "shocks": {
        "superspreader": {"chance": 0.08, "window": [5, 90], "when": "not $world.lockdown", "do": ["..."],
                          "then": [{"shock": "alarm", "after": 2, "chance": 0.5}]}}},
    "uncertainty": {"kind": "priors", "priors": {"transmissibility": {"dist": "beta", "a": 9, "b": 91}}}

Randomness comes from streams derived from the run seed and the rule's name and round, never from
the shared stream: adding a drift rule or a shock never shifts any other draw, and every arm of an
experiment sees the same shock timing and the same prior samples (common random numbers).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, Union

from pydantic import Field, model_validator

from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, is_expr, truthy
from ..registry import MechanismError, effect_op, mechanism
from ..world import prop_type
from . import _common as common
from ._common import Config, Effects, Number

__all__ = ["DriftRule", "DriftConfig", "ShockDef", "ShockConfig", "PriorDef", "PriorsConfig", "sample_prior"]

DRIFT, SHOCKS, PRIORS = "drift", "shocks", "priors"
_PROP_KEYS = frozenset({"type", "default", "min", "max", "values", "private", "description", "unit"})


def _arms(contract: Mapping[str, Any], arms: Optional[List[str]], field: str) -> None:
    declared = contract.get("arms") or {}
    for arm in arms or []:
        if arm not in declared:
            raise MechanismError(f"'{arm}' is not a declared arm", common.suggest(arm, declared), field)


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------

MODELS = ("linear", "mean_reversion", "random_walk", "geometric", "sinusoidal")


class DriftRule(Config):
    """How one numeric property moves by itself."""

    target: str = Field(..., description="'world.<prop>' or '<type>.<prop>'.")
    model: Literal["linear", "mean_reversion", "random_walk", "geometric", "sinusoidal"] = Field(
        "linear", description="linear: + rate | mean_reversion: + rate × (mean − x) + noise | random_walk: + rate + noise | "
                              "geometric: × e^(rate + noise) | sinusoidal: a wave of `amplitude` and `period` around its start.")
    rate: Number = Field(0, description="Step per application (linear, random_walk drift), pull toward the mean (mean_reversion, 0–1) or log growth (geometric).")
    mean: Optional[Number] = Field(None, description="Level it reverts to (mean_reversion).")
    sd: Number = Field(0, description="Standard deviation of the noise added each application.")
    amplitude: Number = Field(0, description="Wave height (sinusoidal).")
    period: Optional[Number] = Field(None, description="Rounds per wave (sinusoidal).")
    offset: Number = Field(0, description="Rounds the wave is shifted by (sinusoidal).")
    min: Optional[Number] = Field(None, description="Lowest value the rule allows (the prop's own min also applies).")
    max: Optional[Number] = Field(None, description="Highest value the rule allows.")
    where: Optional[str] = Field(None, description="Entity targets: which entities drift ($it).")
    when: Optional[str] = Field(None, description="Apply only while true.")
    every: int = Field(1, ge=1, description="Apply every N rounds.")
    arms: Optional[List[str]] = Field(None, description="Only in these experiment arms.")
    description: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "DriftRule":
        if self.model == "mean_reversion" and self.mean is None:
            raise ValueError("mean_reversion needs `mean`")
        if self.model == "sinusoidal" and self.period is None:
            raise ValueError("sinusoidal needs `period`")
        if isinstance(self.period, (int, float)) and self.period <= 0:
            raise ValueError("period must be more than 0")
        if isinstance(self.sd, (int, float)) and self.sd < 0:
            raise ValueError("sd must be ≥ 0")
        if isinstance(self.min, (int, float)) and isinstance(self.max, (int, float)) and self.min > self.max:
            raise ValueError("min is more than max")
        return self


class DriftConfig(Config):
    """Properties that move on their own each round."""

    rules: Dict[str, DriftRule] = Field(
        ..., description="{name: {target, model, rate, mean, sd, amplitude, period, offset, min, max, where, when, every, "
                         "arms}}. Parameters are numbers or expressions ($it for entity targets). An int property moves "
                         "in whole steps (each step's change is rounded).")
    phase: Literal["start", "end"] = Field("start", description="When rules apply: start (before agents act) or end of the round.")


@mechanism(DRIFT, DriftConfig,
           "Property drift: linear trends, mean reversion, random walks, geometric growth and waves on world props or "
           "on every entity of a type, bounded by min/max, gated by when/every/arms. Noise is seeded per rule and round.",
           example={"kind": DRIFT, "rules": {"sentiment": {"target": "voter.mood", "model": "mean_reversion", "rate": 0.1,
                                                          "mean": 0, "sd": 0.02, "min": -1, "max": 1}}})
def _expand_drift(name: str, cfg: DriftConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    for rule_name, rule in cfg.rules.items():
        field = f"rules.{rule_name}"
        _numeric_target(contract, rule.target, f"{field}.target")
        _arms(contract, rule.arms, f"{field}.arms")
        if rule.where is not None and rule.target.startswith("world."):
            raise MechanismError("`where` picks entities; a world target has none", "remove `where`", f"{field}.where")
    return {"events": [{"name": name, "phase": cfg.phase, "do": [{"drift": name}]}]}


def _numeric_target(contract: Mapping[str, Any], target: str, field: str) -> None:
    owner, _, prop = target.partition(".")
    if not prop:
        raise MechanismError(f"a target is 'world.<prop>' or '<type>.<prop>', got '{target}'", None, field)
    if owner == "world":
        declared = contract.get("world") or {}
        if prop not in declared:
            raise MechanismError(f"world has no property '{prop}'", common.suggest(prop, declared), field)
        specs = [declared[prop]]
    else:
        common.types_in(contract, owner, field)
        specs = _raw_props(contract, owner).get(prop, [])
        if not specs:
            raise MechanismError(f"'{owner}' has no property '{prop}'", common.suggest(prop, _raw_props(contract, owner)), field)
    for spec in specs:
        # The PropSpec shorthand rule: an object naming type or default, or holding only spec keys, is a spec.
        is_spec = isinstance(spec, Mapping) and bool(spec) and ("type" in spec or "default" in spec or set(spec) <= _PROP_KEYS)
        kind = spec.get("type") if is_spec else None
        default = spec.get("default") if is_spec else spec
        literal_text = default is not None and not isinstance(default, (int, float)) and not is_expr(default)
        if kind not in (None, "number", "int") or (kind is None and (isinstance(default, bool) or literal_text)):
            raise MechanismError(f"'{target}' is not a number, so it cannot drift", None, field)


def _raw_props(contract: Mapping[str, Any], type_name: str) -> Dict[str, List[Any]]:
    types = contract.get("types") or {}
    props: Dict[str, List[Any]] = {}
    current, seen = type_name, []
    while isinstance(types.get(current), Mapping) and current not in seen:
        seen.append(current)
        for prop, spec in (types[current].get("props") or {}).items():
            props.setdefault(prop, []).append(spec)
        current = types[current].get("extends")
    return props


def _check_drift(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
    name = effect.get("drift")
    raw = checker.c.mechanisms.get(name)
    if not isinstance(raw, Mapping) or raw.get("kind") != DRIFT:
        return [(f"{path}.drift", f"'{name}' is not a declared {DRIFT} mechanism", None)]
    base = set(common.base_roots())
    for rule_name, rule in common.parsed(raw, DriftConfig).rules.items():
        at = f"mechanisms.{name}.rules.{rule_name}"
        owner = rule.target.partition(".")[0]
        roots, types = (base, {}) if owner == "world" else (base | {"it"}, {"it": {owner}})
        for key in ("rate", "mean", "sd", "amplitude", "period", "offset", "min", "max"):
            checker.value(getattr(rule, key), f"{at}.{key}", roots, types)
        checker.expr(rule.where, f"{at}.where", roots, types)
        checker.expr(rule.when, f"{at}.when", base)
    return []


@effect_op("drift", keys=(), literal=("drift",), check=_check_drift,
           example='{"drift": "trends"}  (apply a drift mechanism\'s rules now; generated every round)')
def _drift_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["drift"]
    cfg = common.config(world, mech, DRIFT, DriftConfig, where)
    now = world.round
    for rule_name, rule in cfg.rules.items():
        at = f"mechanisms.{mech}.rules.{rule_name}"
        if rule.arms is not None and world.arm not in rule.arms:
            continue
        if (now - 1) % rule.every:
            continue
        if rule.when is not None and not truthy(common.evaluate(world, rule.when, f"{at}.when")):
            continue
        rng = world.seeds.rng("drift", mech, rule_name, now)
        owner, _, prop = rule.target.partition(".")
        if owner == "world":
            spec = world.contract.world[prop]
            world.set_world(prop, _moved(world, rule, world.props.get(prop), prop_type(spec), rng, at, {}))
            continue
        for entity in world.entities_of(owner):
            if rule.where is not None and not truthy(common.evaluate(world, rule.where, f"{at}.where", it=entity)):
                continue
            kind = prop_type(world.prop_spec(entity, prop))
            world.set_prop(entity, prop, _moved(world, rule, entity.properties.get(prop), kind, rng, at, {"it": entity}))


def _moved(world: Any, rule: DriftRule, value: Any, kind: str, rng: Any, at: str, roots: Dict[str, Any]) -> float:
    x = common.number(value, rule.target, "a number to drift")

    def p(key: str) -> float:
        return common.number(common.evaluate(world, getattr(rule, key), f"{at}.{key}", **roots), f"{at}.{key}")

    sd = p("sd")
    noise = rng.gauss(0.0, sd) if sd > 0 else 0.0
    if rule.model == "linear":
        new = x + p("rate") + noise
    elif rule.model == "random_walk":
        new = x + p("rate") + noise
    elif rule.model == "mean_reversion":
        new = x + p("rate") * (p("mean") - x) + noise
    elif rule.model == "geometric":
        exponent = p("rate") + noise
        if exponent > 700:
            raise RunError(f"geometric drift of {rule.target} overflows (exponent {exponent:.3g})", at)
        new = x * math.exp(exponent)
    else:
        period, offset, t = p("period"), p("offset"), world.round
        if period <= 0:
            raise RunError(f"period must be more than 0, got {period}", f"{at}.period")
        wave = math.sin(2 * math.pi * (t + offset) / period) - math.sin(2 * math.pi * (t - rule.every + offset) / period)
        new = x + p("amplitude") * wave + noise
    if rule.min is not None:
        new = max(p("min"), new)
    if rule.max is not None:
        new = min(p("max"), new)
    if kind == "int":
        new = x + round(new - x)
    if not math.isfinite(new):
        raise RunError(f"drift made {rule.target} {new}", at)
    return new


# ---------------------------------------------------------------------------
# Shocks
# ---------------------------------------------------------------------------


class Cascade(Config):
    """A shock that may follow another."""

    shock: str = Field(..., description="The shock that follows.")
    after: int = Field(0, ge=0, description="Rounds later (0 = at once).")
    chance: Number = Field(1, description="Probability it follows.")
    when: Optional[str] = Field(None, description="It follows only if this holds when it is due.")


class ShockDef(Config):
    """One exogenous event."""

    description: str = ""
    at: Union[int, List[int], str, None] = Field(None, description="Round(s) it fires (number, list or expression).")
    every: Optional[int] = Field(None, ge=1, description="Eligible every N rounds (from the window's first round).")
    chance: Optional[Number] = Field(None, description="Probability per eligible round.")
    window: Optional[List[Optional[int]]] = Field(None, description="[first, last] rounds it can fire in (last may be null).")
    when: Optional[str] = Field(None, description="State gate: it can fire only while true.")
    limit: Optional[int] = Field(None, ge=1, description="Most times it fires in a run.")
    gap: int = Field(0, ge=0, description="Rounds that must pass after it fires before it can fire again.")
    arms: Optional[List[str]] = Field(None, description="Only in these experiment arms.")
    do: Effects = Field(default_factory=list, description="What it does.")
    say: str = Field("", description="News when it fires (template).")
    lasts: Optional[int] = Field(None, ge=1, description="Rounds it lasts; `undo` runs when it is over.")
    undo: Effects = Field(default_factory=list, description="Effects when it is over (needs lasts).")
    end_say: str = Field("", description="News when it is over.")
    then: List[Cascade] = Field(default_factory=list, description="Shocks that may follow: [{shock, after, chance, when}].")

    @model_validator(mode="after")
    def _shape(self) -> "ShockDef":
        if (self.undo or self.end_say) and self.lasts is None:
            raise ValueError("undo and end_say need `lasts`")
        if self.window is not None:
            if not 1 <= len(self.window) <= 2 or self.window[0] is None:
                raise ValueError("window is [first, last] (last may be null)")
            if len(self.window) == 2 and self.window[1] is not None and self.window[1] < self.window[0]:
                raise ValueError("window ends before it starts")
        return self

    @property
    def scheduled(self) -> bool:
        """Fires by itself; otherwise only through cascades or fire_shock."""
        return any(v is not None for v in (self.at, self.every, self.chance, self.window, self.when))


class ShockConfig(Config):
    """Exogenous events."""

    shocks: Dict[str, ShockDef] = Field(
        ..., description="{name: {at, every, chance, window, when, limit, gap, arms, do, say, lasts, undo, end_say, then}}. "
                         "With none of at/every/chance/window/when a shock fires only through a cascade or fire_shock.")
    phase: Literal["start", "end"] = Field("start", description="When shocks are rolled each round.")


@mechanism(SHOCKS, ShockConfig,
           "Exogenous shocks: one-time (at), recurring (chance per round, every, window), state-gated (when), per arm, "
           "limited and spaced (limit, gap), lasting (lasts + undo) and cascading ({shock, after, chance, when}). "
           "$world.<name>.<shock> is {count, rounds}. Fire one now with {\"fire_shock\": shock}. Rolls use their own "
           "seeded stream, so arms share shock timing.",
           example={"kind": SHOCKS, "shocks": {"strike": {"chance": 0.1, "window": [3, 20], "do": ["$world.supply *= 0.5"],
                                                          "say": "Dock workers strike.", "lasts": 2, "undo": ["$world.supply *= 2"]}}})
def _expand_shocks(name: str, cfg: ShockConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    others = {s for other, raw in common.uses(contract, SHOCKS) if other != name for s in (raw.get("shocks") or {})}
    for shock, spec in cfg.shocks.items():
        field = f"shocks.{shock}"
        if not common.NAME.match(shock):
            raise MechanismError(f"shock name '{shock}' must start with a letter and use letters, digits and _", None, field)
        if shock in others:
            raise MechanismError(f"shock '{shock}' is also declared by another shocks mechanism", "rename one", field)
        _arms(contract, spec.arms, f"{field}.arms")
        for index, cascade in enumerate(spec.then):
            if cascade.shock not in cfg.shocks:
                raise MechanismError(f"'{cascade.shock}' is not a shock here", common.suggest(cascade.shock, cfg.shocks),
                                     f"{field}.then[{index}].shock")
    state = {shock: {"count": 0, "rounds": []} for shock in cfg.shocks}
    return {"world": {name: {"type": "map", "default": state, "description": "Shock history: {shock: {count, rounds}}."}},
            "events": [{"name": name, "phase": cfg.phase, "do": [{"shocks_step": name}]}]}


def _shock_index(contract: Any) -> Dict[str, Tuple[str, ShockConfig]]:
    out: Dict[str, Tuple[str, ShockConfig]] = {}
    for mech, raw in common.uses(contract, SHOCKS):
        cfg = common.parsed(raw, ShockConfig)
        for shock in cfg.shocks:
            out.setdefault(shock, (mech, cfg))
    return out


def fire_shock(runner: Any, mech: str, cfg: ShockConfig, shock: str, depth: int = 0) -> None:
    """Fire ``shock`` now: count it, run its effects, schedule its end and roll its cascades."""
    world = runner.world
    at = f"mechanisms.{mech}.shocks.{shock}"
    if depth > 32:
        raise RunError("shock cascades nest more than 32 deep (a cycle with after: 0?)", at)
    spec = cfg.shocks[shock]
    state = dict(world.props.get(mech) or {})
    entry = state.get(shock) or {"count": 0, "rounds": []}
    count = int(entry.get("count", 0)) + 1
    state[shock] = {"count": count, "rounds": [*entry.get("rounds", []), world.round]}
    world.set_world(mech, state)
    runner.run(spec.do, {}, f"{at}.do")
    if spec.say:
        text = runner.text(spec.say, {})
        if text.strip():
            world.emit(mech, text, data={"mechanism": SHOCKS, "shock": shock})
    if spec.lasts is not None:
        ending = list(spec.undo) + ([{"emit": mech, "say": spec.end_say, "data": {"shock": shock, "over": True}}] if spec.end_say else [])
        if ending:
            world.schedule(world.round + spec.lasts, ending, {}, f"{at}.undo")
    for index, cascade in enumerate(spec.then):
        chance = common.number(common.evaluate(world, cascade.chance, f"{at}.then[{index}].chance"), f"{at}.then[{index}].chance")
        roll = world.seeds.rng("cascade", mech, shock, index, world.round, count).random()
        if roll >= chance:
            continue
        if cascade.after == 0:
            if cascade.when is None or truthy(common.evaluate(world, cascade.when, f"{at}.then[{index}].when")):
                fire_shock(runner, mech, cfg, cascade.shock, depth + 1)
            continue
        follow: List[Any] = [{"fire_shock": cascade.shock}]
        if cascade.when is not None:
            follow = [{"if": cascade.when, "then": follow}]
        world.schedule(world.round + cascade.after, follow, {}, f"{at}.then[{index}]")


def _due(world: Any, mech: str, shock: str, spec: ShockDef, at: str) -> bool:
    now = world.round
    if not spec.scheduled or (spec.arms is not None and world.arm not in spec.arms):
        return False
    first = spec.window[0] if spec.window else 1
    last = spec.window[1] if spec.window and len(spec.window) == 2 else None
    if now < (first or 1) or (last is not None and now > last):
        return False
    if spec.at is not None:
        rounds = common.evaluate(world, spec.at, f"{at}.at")
        if now not in (rounds if isinstance(rounds, list) else [rounds]):
            return False
    if spec.every is not None and (now - (first or 1)) % spec.every:
        return False
    entry = (world.props.get(mech) or {}).get(shock) or {}
    if spec.limit is not None and int(entry.get("count", 0)) >= spec.limit:
        return False
    fired = entry.get("rounds") or []
    if fired and now - fired[-1] <= spec.gap:
        return False
    if spec.when is not None and not truthy(common.evaluate(world, spec.when, f"{at}.when")):
        return False
    if spec.chance is None:
        return True
    chance = common.number(common.evaluate(world, spec.chance, f"{at}.chance"), f"{at}.chance")
    return world.seeds.rng("shock", mech, shock, now).random() < chance


def _check_shocks(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
    name = effect.get("shocks_step")
    raw = checker.c.mechanisms.get(name)
    if not isinstance(raw, Mapping) or raw.get("kind") != SHOCKS:
        return [(f"{path}.shocks_step", f"'{name}' is not a declared {SHOCKS} mechanism", None)]
    base = set(common.base_roots())
    for shock, spec in common.parsed(raw, ShockConfig).shocks.items():
        at = f"mechanisms.{name}.shocks.{shock}"
        for key in ("do", "undo"):
            checker.effects(getattr(spec, key), f"{at}.{key}", base, {})
        for key in ("when",):
            checker.expr(spec.when, f"{at}.{key}", base)
        checker.value(spec.at, f"{at}.at", base)
        checker.value(spec.chance, f"{at}.chance", base)
        for key in ("say", "end_say"):
            checker.template(getattr(spec, key) or None, f"{at}.{key}", None, base)
        for index, cascade in enumerate(spec.then):
            checker.value(cascade.chance, f"{at}.then[{index}].chance", base)
            checker.expr(cascade.when, f"{at}.then[{index}].when", base)
    return []


@effect_op("shocks_step", keys=(), literal=("shocks_step",), check=_check_shocks,
           example='{"shocks_step": "events"}  (roll every scheduled shock now; generated every round)')
def _step_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["shocks_step"]
    cfg = common.config(world, mech, SHOCKS, ShockConfig, where)
    for shock, spec in cfg.shocks.items():
        if _due(world, mech, shock, spec, f"mechanisms.{mech}.shocks.{shock}"):
            fire_shock(runner, mech, cfg, shock)


def _check_fire(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
    index = _shock_index(checker.c)
    shock = effect.get("fire_shock")
    return [] if shock in index else [(f"{path}.fire_shock", f"'{shock}' is not a declared shock", common.suggest(str(shock), index))]


@effect_op("fire_shock", keys=(), literal=("fire_shock",), check=_check_fire,
           example='{"fire_shock": "strike"}  (fire a declared shock now, whatever its schedule: effects, news, cascades)')
def _fire_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    index = _shock_index(runner.world.contract)
    shock = effect["fire_shock"]
    if shock not in index:
        raise RunError(f"'{shock}' is not a declared shock", where)
    mech, cfg = index[shock]
    fire_shock(runner, mech, cfg, shock)


# ---------------------------------------------------------------------------
# Priors
# ---------------------------------------------------------------------------

_NEEDS = {"uniform": ("low", "high"), "normal": ("mean", "sd"), "beta": ("a", "b"), "lognormal": ("mu", "sigma"),
          "triangular": ("low", "mode", "high"), "choice": ("values",)}


class PriorDef(Config):
    """An uncertain quantity sampled once per run."""

    dist: Literal["uniform", "normal", "beta", "lognormal", "triangular", "choice"] = Field(..., description="The distribution.")
    low: Optional[Number] = None
    high: Optional[Number] = None
    mean: Optional[Number] = None
    sd: Optional[Number] = None
    a: Optional[Number] = None
    b: Optional[Number] = None
    mu: Optional[Number] = None
    sigma: Optional[Number] = None
    mode: Optional[Number] = None
    values: Optional[List[Any]] = None
    weights: Optional[List[float]] = None
    min: Optional[float] = Field(None, description="Clamp the sample from below.")
    max: Optional[float] = Field(None, description="Clamp the sample from above.")
    integer: bool = Field(False, description="A whole number (uniform draws a whole number between low and high).")
    output: bool = Field(True, description="Record the sample as an output of the same name.")
    description: str = ""

    @model_validator(mode="after")
    def _shape(self) -> "PriorDef":
        for key in _NEEDS[self.dist]:
            if getattr(self, key) is None:
                raise ValueError(f"a {self.dist} prior needs `{key}`")
        literal = {k: getattr(self, k) for k in ("low", "high", "mode", "sd", "a", "b", "sigma") if isinstance(getattr(self, k), (int, float))}
        if "low" in literal and "high" in literal and literal["low"] > literal["high"]:
            raise ValueError("low is more than high")
        if "mode" in literal and "low" in literal and "high" in literal and not literal["low"] <= literal["mode"] <= literal["high"]:
            raise ValueError("mode must lie between low and high")
        for key in ("sd", "sigma"):
            if key in literal and literal[key] < 0:
                raise ValueError(f"{key} must be ≥ 0")
        for key in ("a", "b"):
            if key in literal and literal[key] <= 0:
                raise ValueError(f"{key} must be more than 0")
        if self.dist == "choice":
            if not self.values:
                raise ValueError("a choice prior needs at least one value")
            if self.weights is not None and (len(self.weights) != len(self.values) or any(w < 0 for w in self.weights) or sum(self.weights) <= 0):
                raise ValueError("weights: one number ≥ 0 per value, not all zero")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min is more than max")
        return self


class PriorsConfig(Config):
    """Uncertain inputs sampled per run."""

    priors: Dict[str, PriorDef] = Field(
        ..., description="{name: {dist, low, high, mean, sd, a, b, mu, sigma, mode, values, weights, min, max, integer, "
                         "output}}. Each becomes $world.<name> (sampled at load) and, unless output is false, an output. "
                         "Parameters are numbers or expressions over $inputs.")


@mechanism(PRIORS, PriorsConfig,
           "Uncertainty priors: quantities sampled once per run from uniform, normal, beta, lognormal, triangular or "
           "weighted-choice distributions, stored as world props and recorded as outputs for analysis. Samples come "
           "from a seeded stream per prior, so every arm of an experiment draws the same value for the same run.",
           example={"kind": PRIORS, "priors": {"elasticity": {"dist": "normal", "mean": -1.2, "sd": 0.3, "max": 0}}})
def _expand_priors(name: str, cfg: PriorsConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    world: Dict[str, Any] = {}
    outputs: Dict[str, Any] = {}
    inputs = contract.get("inputs") or {}
    for prior, spec in cfg.priors.items():
        field = f"priors.{prior}"
        if not common.NAME.match(prior):
            raise MechanismError(f"prior name '{prior}' must start with a letter and use letters, digits and _", None, field)
        for key in ("low", "high", "mean", "sd", "a", "b", "mu", "sigma", "mode"):
            raw = getattr(spec, key)
            if not isinstance(raw, str):
                continue
            try:
                compiled = compile_expr(raw)
            except ExprError as exc:
                raise MechanismError(exc.detail, f"expression: {raw}", f"{field}.{key}") from None
            if compiled.roots - {"inputs"} or compiled.functions:
                raise MechanismError("prior parameters are numbers or expressions over $inputs", None, f"{field}.{key}")
            for chain in compiled.paths:
                if len(chain) > 1 and chain[1] not in inputs:
                    raise MechanismError(f"$inputs.{chain[1]}: no such input", common.suggest(chain[1], inputs), f"{field}.{key}")
        kind = "any" if spec.dist == "choice" else ("int" if spec.integer else "number")
        world[prior] = {"type": kind, "default": f"$prior('{name}', '{prior}')",
                        "description": spec.description or f"Sampled from a {spec.dist} prior."}
        if spec.output:
            texts = spec.dist == "choice" and all(isinstance(v, str) for v in spec.values or [])
            outputs[prior] = {"expr": f"$world.{prior}", "type": "text" if texts else ("any" if spec.dist == "choice" else kind),
                              "description": spec.description or f"The value sampled for this run ({spec.dist} prior)."}
    return {"world": world, "outputs": outputs}


def sample_prior(world: Any, mech: str, prior: str, where: str) -> Any:
    """The run's sample of one prior (the same value every time it is asked for)."""
    cfg = common.config(world, mech, PRIORS, PriorsConfig, where)
    spec = cfg.priors.get(prior)
    if spec is None:
        raise RunError(f"'{prior}' is not a prior of '{mech}' ({common.suggest(prior, cfg.priors)})", where)
    rng = world.seeds.rng("prior", mech, prior)

    def p(key: str) -> float:
        return common.number(common.evaluate(world, getattr(spec, key), f"mechanisms.{mech}.priors.{prior}.{key}"),
                             f"mechanisms.{mech}.priors.{prior}.{key}")

    value: Any
    if spec.dist == "choice":
        return rng.choices(list(spec.values or []), weights=spec.weights, k=1)[0]
    if spec.dist == "uniform":
        low, high = p("low"), p("high")
        if low > high:
            raise RunError(f"low ({low}) is more than high ({high})", where)
        value = rng.randint(math.ceil(low), math.floor(high)) if spec.integer else rng.uniform(low, high)
    elif spec.dist == "normal":
        value = rng.gauss(p("mean"), max(0.0, p("sd")))
    elif spec.dist == "beta":
        value = rng.betavariate(p("a"), p("b"))
    elif spec.dist == "lognormal":
        value = rng.lognormvariate(p("mu"), max(0.0, p("sigma")))
    else:
        low, mode, high = p("low"), p("mode"), p("high")
        value = rng.triangular(low, high, mode)
    if spec.min is not None:
        value = max(spec.min, value)
    if spec.max is not None:
        value = min(spec.max, value)
    return int(round(value)) if spec.integer else value


@function("prior(mechanism, name)", "The value this run sampled for a declared prior (the same value every time).",
          min_args=2, max_args=2)
def _prior(call: Call) -> Any:
    try:
        return sample_prior(call.scope.world, str(call.arg(0)), str(call.arg(1)), call.source)
    except RunError as exc:
        raise ExprError(str(exc), call.source) from None
