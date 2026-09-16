"""The pattern registry: every kind's strict config model, what it takes, how it evaluates, how it reads in words.

A pattern is a named value of the world that follows a known shape — a trend, a season, a price response, a
random walk — declared under ``patterns`` and read anywhere as ``$pattern.<name>`` (or called with arguments
and a key: ``$pattern.lift($it.price, $it.sku)``). Kinds register here from their group modules.

Shapes (how a kind is read):

* ``signal`` — a value of time (and key): trend, seasonal, calendar, cycle, lifecycle, step, series, diffusion.
* ``process`` — a random path over time, drawn from the pattern's own seeded stream: random walks, mean
  reversion, autoregression, volatility clustering, regimes, shocks, noise, weather.
* ``draw`` — sampled once per run (and key): priors, heterogeneity, segments.
* ``response`` — a value of its arguments: elasticity, saturation, thresholds, hazards, observation noise.
* ``memory`` — carries what happened in earlier rounds: carryover, promotions, reference prices, habit.
* ``composite`` — combines other patterns: product, sum.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Type, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Number", "PatternConfig", "FitSpec", "KindSpec", "KINDS", "GROUPS", "SHAPES", "kind", "MEMORY_STATE"]

#: A number, or an expression over ``$inputs`` (and ``$key``/``$row`` for keyed patterns) giving one.
Number = Union[float, str]

#: The world property holding memory patterns' state between rounds (managed by the engine).
MEMORY_STATE = "patterns_memory"

SHAPES = ("signal", "process", "draw", "response", "memory", "composite")

#: Groups in the order the guide teaches them: (name, what the group is for).
GROUPS: Dict[str, str] = {
    "time": "values that follow time: trends, seasons, calendars, cycles, lifecycles, steps and data series",
    "random": "random paths drawn from seeded streams: walks, mean reversion, autoregression, volatility, regimes, shocks, noise, weather",
    "response": "how a quantity answers a driver: price elasticity, substitution, promotions, saturation, thresholds, "
                "reference prices, learning curves, network effects, hazards",
    "population": "differences between entities and how things spread: draws, segments, diffusion, habit and fatigue",
    "observation": "what gets recorded: counts with over-dispersion, measurement error, censoring, missing values",
    "memory": "effects that carry over from earlier rounds: adstock and lags",
    "composition": "patterns built from other patterns: products and sums",
}


class FitFactor(BaseModel):
    """A response fitted together with a product, and the column its driver is in."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(..., description="Column holding the driver (price, promotion depth).")
    key: Optional[str] = Field(None, description="Its key, as an expression over $key and $row (the product's table row): "
                                                 "$row.category.")


class FitSpec(BaseModel):
    """Where a pattern's parameters are estimated from: rows of a data input and the columns to read."""

    model_config = ConfigDict(extra="forbid")

    data: str = Field(..., description="Expression over $inputs giving the rows (a table input, e.g. $inputs.history).")
    value: str = Field(..., description="Column holding the observed quantity.")
    time: Optional[str] = Field(None, description="Column holding when each row happened: an ISO date, or clock units "
                                                  "from round 1 (0, 1, 2 …). Needed by time patterns.")
    key: Optional[str] = Field(None, description="Column holding each row's key (keyed patterns fit one set of parameters per key).")
    x: Union[str, Dict[str, Union[str, "FitFactor"]], None] = Field(
        None, description="Column holding the driver a response answers (price, spend, exposure); for a product, "
                          "{pattern: column or {column, key}} for every response that multiplies it in the data "
                          "(its price response, its promotion), fitted together with it.")
    noise: Optional[str] = Field(None, description="A product's counts pattern: its dispersion is estimated from the "
                                                   "same rows, around the fitted means.")
    mean: Optional[str] = Field(None, description="Column holding the expected value of each row (counts: the spread "
                                                  "around it is what is estimated).")
    censored: Optional[str] = Field(None, description="Column that is 1 (or true) where demand went unmet — sales capped "
                                                      "by a stockout, so the true value was more than the one recorded. "
                                                      "Those rows are fitted as censored (expectation–maximisation), not "
                                                      "dropped.")
    where: Optional[str] = Field(None, description="Keep only rows where this holds ($row), e.g. $row.returns == 0.")
    adjust: List[str] = Field(default_factory=list, description="Patterns already fitted that the value is divided by "
                                                                "first (a season before a price response); for counts, "
                                                                "their product is the expected value.")


class PatternConfig(BaseModel):
    """Fields every pattern takes; each kind adds its own."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: str
    description: str = Field("", description="What it stands for, in plain words.")
    unit: str = ""
    keys: Union[str, List[Any], None] = Field(
        None, description="One instance per key: an entity type (keys are its ids), a list, or an expression over "
                          "$inputs giving the keys. Read with the key as the last argument: $pattern.season($it.sku).")
    table: Optional[str] = Field(None, description="Expression over $inputs giving one row per key; parameters read "
                                                   "the row as $row (per-SKU profiles). Keys default to its `column`.")
    column: Optional[str] = Field(None, description="The table column holding each row's key.")
    min: Optional[Number] = Field(None, description="Lowest value it gives.")
    max: Optional[Number] = Field(None, description="Highest value it gives.")
    record: bool = Field(False, description="Record it every round as a metric of the same name ($series.<name>).")
    fit: Optional[FitSpec] = Field(None, description="Estimate its parameters from data with fg_env.fit_patterns.")
    uncertainty: Dict[str, Union[Number, List[Number]]] = Field(
        default_factory=dict, description="{parameter: standard error} (written by fit): each run draws the parameter "
                                          "once from a normal around its value, so forecasts carry estimation uncertainty. "
                                          "An error of 0 uses the value as it is.")

    @property
    def keyed(self) -> bool:
        return self.keys is not None or self.table is not None


Evaluate = Callable[..., Any]


@dataclass(frozen=True)
class KindSpec:
    """One registered kind."""

    name: str
    group: str
    shape: str
    model: Type[PatternConfig]
    doc: str
    example: Dict[str, Any]
    evaluate: Evaluate
    #: Names of the arguments it is called with (a function of the config when they depend on it).
    args: Union[Tuple[str, ...], Callable[[Any], Tuple[str, ...]]] = ()
    #: Draws from the pattern's own stream (an unkeyed random pattern may be given a key to separate streams).
    random: bool = False
    #: One sentence saying what a config does, for describe.
    words: Callable[[Any], str] = lambda cfg: ""
    #: Fields evaluated once per run (and key) — the rest are read raw.
    params: Tuple[str, ...] = ()
    #: For memory kinds: the next state after a round, given the context, the input now and the state before.
    commit: Optional[Callable[..., Dict[str, Any]]] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def arg_names(self, cfg: Any) -> Tuple[str, ...]:
        return self.args(cfg) if callable(self.args) else self.args


KINDS: Dict[str, KindSpec] = {}


def kind(name: str, group: str, shape: Literal["signal", "process", "draw", "response", "memory", "composite"],
         model: Type[PatternConfig], doc: str, *, example: Dict[str, Any],
         args: Union[Tuple[str, ...], Callable[[Any], Tuple[str, ...]]] = (), random: bool = False,
         words: Callable[[Any], str] = lambda cfg: "", params: Tuple[str, ...] = (),
         commit: Optional[Callable[..., Dict[str, Any]]] = None) -> Callable[[Evaluate], Evaluate]:
    """Register a pattern kind. ``params`` are the fields that may be expressions (evaluated once per run and key)."""
    if group not in GROUPS:
        raise ValueError(f"unknown pattern group '{group}'")
    if (shape == "memory") != (commit is not None):
        raise ValueError(f"pattern kind '{name}': memory kinds (and only they) commit state")

    def register(evaluate: Evaluate) -> Evaluate:
        if name in KINDS:
            raise ValueError(f"pattern kind '{name}' is registered twice")
        KINDS[name] = KindSpec(name, group, shape, model, doc, example, evaluate, args, random, words, params, commit)
        return evaluate

    return register
