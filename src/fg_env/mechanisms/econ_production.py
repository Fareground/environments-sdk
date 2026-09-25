"""The ``economy`` family's ``production`` mode: recipes turning goods into goods, with skills,
tools, places, money costs, production time and a limited number of jobs at once."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, is_expr
from ..expr.objects import Entity
from ..registry import MechanismError, family_action, mode
from ..world.abort import Abort
from ._common import condition, entity_of
from .econ_assets import balance, burn_money, credit_of, destroy_items, held, is_holder, make_items
from .econ_base import (
    INVENTORY,
    LEDGER,
    PRODUCTION,
    checked_config,
    config_of,
    declared_names,
    declared_use,
    emit_to,
    guarded,
    maybe_entity,
    money,
    props,
    register_config,
    require_types,
    type_list,
    uses_of,
    valid_name,
    whole,
)
from .econ_inventory import agent_types
from .expressions import Expr

__all__ = ["ProductionConfig", "RecipeSpec", "SkillSpec", "skill_level"]

#: Most batches one start may ask for.
MAX_BATCHES = 1000


class RecipeSpec(BaseModel):
    """Goods in, goods out."""

    model_config = ConfigDict(extra="forbid")

    inputs: dict[str, int] = Field({}, description="Goods used up per batch {item: qty}; none for gathering.")
    outputs: dict[str, int | str] = Field(..., min_length=1,
                                          description="Goods made per batch {item: qty or expression over $actor}.")
    rounds: int = Field(0, ge=0, description="Rounds until a batch is ready (0 = at once).")
    skill: str | None = Field(None, description="Skill used; gains `xp` per batch.")
    level: int = Field(0, ge=0, description="Skill level needed.")
    xp: float = Field(0, ge=0, description="Experience per batch.")
    tools: dict[str, int] = Field({}, description="Goods that must be held but are not used up {item: qty}.")
    at: str | list[str] | None = Field(None, description="Place(s) where it can be made.")
    when: Expr | None = Field(None, description="Extra requirement over $actor.")
    cost: dict[str, float | str] = Field({},
                                         description="Money per batch {currency: amount}, leaving to the recipe's "
                                                     "sink.")
    description: str = ""


class SkillSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(0, ge=0, description="Level everyone starts at.")
    max_level: int = Field(10, ge=1)
    xp_per_level: float = Field(10, gt=0,
                                description="Experience for the next level (times the next level when growth is "
                                            "linear).")
    growth: Literal["flat", "linear"] = "linear"


class ProductionConfig(BaseModel):
    """Recipes worked by producers."""

    model_config = ConfigDict(extra="forbid")

    who: str | list[str] = Field(..., description="Type(s) that make goods; they must hold the inventory's goods.")
    inventory: str = Field(..., description="The inventory mechanism whose goods are used and made.")
    recipes: dict[str, RecipeSpec] = Field(..., min_length=1,
                                           description="{recipe: {inputs, outputs, rounds, skill, level, xp, tools, "
                                                       "at, when, cost}}.")
    skills: dict[str, SkillSpec] = Field({}, description="{skill: {start, max_level, xp_per_level, growth}}.")
    slots: int | str = Field(1, description="Jobs a producer can have running at once (number or expression).")


register_config(PRODUCTION, ProductionConfig)


@mode("economy", "production", ProductionConfig,
      "Recipes that turn goods into goods: inputs used up, outputs made, optional skill level, tools held, place, "
      "money cost and production time. Generates `<name>_start` listing only recipes you can make now, with the "
      "batch count bounded by your inputs and money; jobs taking rounds occupy a slot and finish at the start of "
      "their due round (waiting while there is no room for the output). Skills gain experience and level up "
      "($skill(agent, skill)). Inputs leave through the recipe as a sink and outputs arrive from it as a source.",
      example={"who": "villager", "inventory": "goods",
               "skills": {"baking": {"xp_per_level": 5}, "foraging": {}},
               "recipes": {"bake": {"inputs": {"flour": 2}, "outputs": {"bread": 3}, "rounds": 1, "skill": "baking",
                                    "xp": 2, "at": "bakery"},
                           "forage": {"outputs": {"berries": "1 + $skill($actor, foraging)"}, "at": "forest",
                                      "skill": "foraging", "xp": 1}}},
      context={"types": {"villager": {"agent": True}, "place": {}},
               "entities": {"bakery": {"type": "place"},
                            "forest": {"type": "place"},
                            "villager": {"type": "villager", "count": 2, "at": "forest"}},
               "space": {"graph": {"nodes": ["bakery", "forest"], "edges": [["bakery", "forest"]]}},
               "mechanisms": {"goods": {"kind": "economy",
                                        "mode": "inventory",
                                        "who": "villager",
                                        "items": {"flour": {}, "bread": {}, "berries": {}}}}})
def _expand_production(name: str, config: ProductionConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    producers = type_list(config.who)
    require_types(contract, producers, "who")
    inventory = declared_use(contract, config.inventory, INVENTORY, "inventory")
    items = inventory.get("items") or {}
    currencies = declared_names(contract, LEDGER, "currencies")
    for recipe, spec in config.recipes.items():
        path = f"recipes.{recipe}"
        if not valid_name(recipe):
            raise MechanismError(f"recipe '{recipe}' is not a valid name",
                                 "use letters, digits and _ (not a word expressions use, like in or not)", path)
        for field, goods in (("inputs", spec.inputs), ("outputs", spec.outputs), ("tools", spec.tools)):
            for item in goods:
                if item not in items:
                    raise MechanismError(f"'{item}' is not an item of inventory '{config.inventory}'",
                                         f"items: {', '.join(items)}", f"{path}.{field}.{item}")
        if spec.skill is not None and spec.skill not in config.skills:
            raise MechanismError(f"skill '{spec.skill}' is not declared", "declare it under `skills`", f"{path}.skill")
        for currency in spec.cost:
            if currency not in currencies:
                raise MechanismError(f"'{currency}' is not a declared currency", "declare a ledger with it",
                                     f"{path}.cost.{currency}")
        if spec.at is not None and not contract.get("space"):
            raise MechanismError("`at` needs a declared space", "declare `space`, or remove `at`", f"{path}.at")
    job = f"{name}_job"
    props_: dict[str, Any] = {f"{name}_slots": {"type": "int", "default": config.slots, "min": 0,
                                                "description": "Jobs you can have running at once."}}
    if config.skills:
        props_["skills"] = {"type": "map", "default": {}, "description": "Skill levels {skill: level}."}
        props_["skill_xp"] = {"type": "map", "default": {}, "private": True,
                              "description": "Experience toward the next level."}
    etas = {r: ("done at once" if s.rounds == 0 else f"ready in {s.rounds} round{'s' if s.rounds != 1 else ''}")
            for r, s in config.recipes.items()}
    fragment: dict[str, Any] = {
        "types": {**{t: {"props": props_} for t in producers},
                  job: {"description": "Goods being made.", "props": {
                      "owner": {"type": "text", "default": ""}, "recipe": {"type": "text", "default": ""},
                      "qty": {"type": "int", "default": 1, "min": 1}, "started": {"type": "int", "default": 0},
                      "due": {"type": "int", "default": 0},
                      "status": {"type": "enum", "values": ["working", "waiting"], "default": "working"}}}},
        "world": {f"{name}_made": {"type": "map", "default": {}, "description": "Batches finished per recipe."}},
        "defs": {f"{name}_eta": {"description": "When each recipe's batches are ready.",
                                 "expr": "{" + ", ".join(f"'{r}': '{t}'" for r, t in etas.items()) + "}"}},
        "events": [{"name": f"{name}: jobs", "phase": "start", "do": [{"economy": name, "action": "tick"}]}],
    }
    agents = agent_types(contract, producers)
    if agents:
        recipes = f"$recipes($actor, '{name}')"
        fragment["actions"] = {f"{name}_start": {
            "by": agents,
            "description": "Make goods from a recipe you can make now (inputs are used up when you start).",
            "when": [{"expr": f"$len({recipes}) > 0",
                      "why": "You cannot make anything now: check inputs, tools, skill, place, money and free job "
               "slots."}],
            "params": {"recipe": {"type": "enum", "values": recipes, "description": "Recipe."},
                       "qty": {"type": "int", "min": 1, "default": 1, "description": "Batches.",
                 "max": guarded(f"$max_batches($actor, '{name}', $params.recipe)", "recipe")}},
            "do": [{"economy": name, "action": "start", "who": "$actor", "recipe": "$params.recipe",
                    "qty": "$params.qty"}],
            "outcome": f"{{$params.qty}} × {{$params.recipe}}: {{$get(${name}_eta, $params.recipe)}}."}}
        fragment["views"] = {
            f"{name}_recipes": {"for": agents, "title": "Recipes", "look": True, "bullet": False,
                                "show": f"{{$recipes_text($actor, '{name}')}}"},
            f"{name}_jobs": {"for": agents, "title": "Your jobs", "of": job, "where": "$it.owner == $actor.id",
                             "show": "{qty} × {recipe}: {$'ready in round ' + $text($it.due) if $it.status == "
                                     "working else 'waiting for room'}"}}
    return fragment


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def skill_level(world: Any, config: ProductionConfig | None, agent: Entity, skill: str) -> int:
    levels = props(agent).get("skills") if is_holder(world, agent, "skills") else None
    if isinstance(levels, dict) and skill in levels:
        return int(levels[skill])
    spec = config.skills.get(skill) if config is not None else None
    return spec.start if spec is not None else 0


def _eval(world: Any, value: Any, agent: Entity, where: str) -> Any:
    if isinstance(value, str) and "$" in value:
        try:
            return compile_expr(value)(world.evaluation.scope(actor=agent))
        except ExprError as exc:
            raise RunError(str(exc), where) from None
    return value


def _busy(world: Any, name: str, agent: Entity) -> int:
    return sum(1 for e in world.entities_of(f"{name}_job") if props(e).get("owner") == agent.id)


def _blocked(world: Any, name: str, config: ProductionConfig, agent: Entity, recipe: str, times: int) -> str | None:
    """Why ``agent`` cannot start ``times`` batches of ``recipe`` now, or None."""
    spec = config.recipes[recipe]
    where = f"mechanisms.{name}.recipes.{recipe}"
    if spec.skill is not None and skill_level(world, config, agent, spec.skill) < spec.level:
        return (f"{recipe} needs {spec.skill} level {spec.level} (you have "
                f"{skill_level(world, config, agent, spec.skill)})")
    for item, qty in spec.tools.items():
        if held(world, agent, item, where) < qty:
            return f"{recipe} needs {qty} {item} in hand"
    if spec.at is not None and agent.location_id not in type_list(spec.at):
        return f"{recipe} is made at {' or '.join(type_list(spec.at))}"
    if spec.when is not None and not condition(world, spec.when, f"{where}.when", actor=agent):
        return f"{recipe}: its requirements are not met"
    for item, qty in spec.inputs.items():
        have = held(world, agent, item, where)
        if have < qty * times:
            return f"{times} × {recipe} needs {qty * times} {item} (you have {have})"
    for currency, cost in spec.cost.items():
        price = float(_eval(world, cost, agent, f"{where}.cost.{currency}")) * times
        if balance(world, agent, currency, where) + credit_of(world, agent, currency) < price - 1e-9:
            return f"{times} × {recipe} costs {money(price)} {currency}"
    if spec.rounds > 0:
        slots = int(props(agent).get(f"{name}_slots") or 0)
        if _busy(world, name, agent) >= slots:
            return f"all your job slots are busy ({slots})"
    return None


def _max_batches(world: Any, name: str, config: ProductionConfig, agent: Entity, recipe: str) -> int:
    if _blocked(world, name, config, agent, recipe, 1) is not None:
        return 0
    spec = config.recipes[recipe]
    where = f"mechanisms.{name}.recipes.{recipe}"
    most = MAX_BATCHES
    for item, qty in spec.inputs.items():
        if qty:
            most = min(most, held(world, agent, item, where) // qty)
    for currency, cost in spec.cost.items():
        price = float(_eval(world, cost, agent, f"{where}.cost.{currency}"))
        if price > 0:
            affordable = balance(world, agent, currency, where) + credit_of(world, agent, currency) + 1e-9
            most = min(most, int(affordable // price))
    return max(0, most)


def _award(world: Any, config: ProductionConfig, agent: Entity, skill: str, xp: float) -> int | None:
    """Add experience; returns the new level when it rose."""
    spec = config.skills[skill]
    if not is_holder(world, agent, "skills") or xp <= 0:
        return None
    level = skill_level(world, config, agent, skill)
    total = float((props(agent).get("skill_xp") or {}).get(skill, 0)) + xp
    start = level
    while level < spec.max_level:
        need = spec.xp_per_level * (level + 1 if spec.growth == "linear" else 1)
        if total < need:
            break
        total -= need
        level += 1
    if level >= spec.max_level:
        total = 0.0
    world.set_prop(agent, "skills", {**(props(agent).get("skills") or {}), skill: level})
    world.set_prop(agent, "skill_xp", {**(props(agent).get("skill_xp") or {}), skill: round(total, 9)})
    return level if level > start else None


def _finish(runner: Any, name: str, config: ProductionConfig, agent: Entity, recipe: str, times: int,
            where: str) -> str:
    """Make the outputs and award experience; Abort (nothing changes) when the output does not fit."""
    world = runner.world
    spec = config.recipes[recipe]
    made: list[str] = []
    for item, qty in spec.outputs.items():
        per_batch = whole(runner.eval(qty, {"actor": agent, "times": times}), f"{where}.outputs.{item}", "an output")
        make_items(world, item, agent, per_batch * times, recipe, where)
        if per_batch:
            made.append(f"{per_batch * times} {item}")
    tallies = dict(world.props.get(f"{name}_made") or {})
    tallies[recipe] = tallies.get(recipe, 0) + times
    world.set_world(f"{name}_made", tallies)
    text = f"{times} × {recipe} done: " + (", ".join(made) or "nothing came of it") + "."
    if spec.skill is not None:
        risen = _award(world, config, agent, spec.skill, spec.xp * times)
        if risen is not None:
            text += f" Your {spec.skill} is now level {risen}."
    return text


# ---------------------------------------------------------------------------
# Functions and operations
# ---------------------------------------------------------------------------


def _config(call: Call, index: int) -> tuple[Any, str, ProductionConfig]:
    world = call.scope.world
    name = str(call.arg(index))
    try:
        return world, name, config_of(world, name, PRODUCTION, call.source)
    except RunError as exc:
        raise ExprError(f"${call.name}: {exc.args[0]}", call.source) from None


def _agent(call: Call) -> Entity:
    found = maybe_entity(call.scope.world, call.arg(0))
    if found is None:
        raise ExprError(f"${call.name}: expected an entity or id, got {call.arg(0)!r}", call.source)
    return found


@function("skill(agent, skill)", "The agent's level in a skill (the skill's start level until it gains one).",
          min_args=2, max_args=2, family="economy")
def _skill(call: Call) -> int:
    world = call.scope.world
    skill = str(call.arg(1))
    config = next((c for c in _productions(world) if skill in c.skills), None)
    if config is None:
        raise ExprError(f"$skill: '{skill}' is not a declared skill", call.source)
    return skill_level(world, config, _agent(call), skill)


def _productions(world: Any) -> list[ProductionConfig]:
    return list(uses_of(world, PRODUCTION).values())


@function("recipes(agent, production)", "Recipes of a production the agent can start now.", min_args=2, max_args=2,
          family="economy")
def _recipes(call: Call) -> list[str]:
    world, name, config = _config(call, 1)
    agent = _agent(call)
    return [r for r in config.recipes if _blocked(world, name, config, agent, r, 1) is None]


@function("max_batches(agent, production, recipe)",
          "Most batches of a recipe the agent can start now (0 when it cannot).", min_args=3, max_args=3,
          family="economy")
def _max_batches_fn(call: Call) -> int:
    world, name, config = _config(call, 1)
    recipe = str(call.arg(2))
    if recipe not in config.recipes:
        raise ExprError(f"$max_batches: '{recipe}' is not a recipe of {name}", call.source)
    return _max_batches(world, name, config, _agent(call), recipe)


@function("recipes_text(agent, production)",
          "Every recipe as plain lines: what it takes, what it makes, and what stops the agent now.",
          min_args=2, max_args=2, family="economy")
def _recipes_text(call: Call) -> str:
    world, name, config = _config(call, 1)
    agent = _agent(call)
    lines = []
    for recipe, spec in config.recipes.items():
        takes = ", ".join(f"{q} {i}" for i, q in spec.inputs.items()) or "nothing"
        makes = ", ".join(f"{q if isinstance(q, int) else 'some'} {i}" for i, q in spec.outputs.items())
        extra = []
        if spec.rounds:
            extra.append(f"{spec.rounds} round(s)")
        if spec.skill:
            extra.append(f"{spec.skill} {spec.level}+")
        if spec.at:
            extra.append("at " + "/".join(type_list(spec.at)))
        if spec.tools:
            extra.append("needs " + ", ".join(f"{q} {i}" for i, q in spec.tools.items()))
        if spec.cost:
            extra.append("costs " + ", ".join(f"{c} {v}" for c, v in spec.cost.items()))
        why = _blocked(world, name, config, agent, recipe, 1)
        status = "ready" if why is None else f"not now: {why}"
        lines.append(f"- {recipe}: {takes} → {makes}" + (f" ({'; '.join(extra)})" if extra else "") + f" — {status}")
    if config.skills and is_holder(world, agent, "skills"):
        lines.append("Skills: " + ", ".join(f"{s} {skill_level(world, config, agent, s)}" for s in config.skills))
    return "\n".join(lines)


def _check_recipe(checker: Any, effect: dict[str, Any], path: str) -> list:
    config = checked_config(checker, effect, "economy")
    recipe = effect.get("recipe")
    if config is None or not isinstance(recipe, str) or is_expr(recipe) or recipe in config.recipes:
        return []
    return [(f"{path}.recipe", f"'{recipe}' is not a recipe of {effect['economy']}",
             f"recipes: {', '.join(config.recipes)}")]


@family_action("economy", ("production",), "start", keys=("who", "recipe", "qty"), required=("who", "recipe"),
               check=_check_recipe,
               example='{"economy": "craft", "action": "start", "who": "$actor", "recipe": "$params.recipe", "qty": '
                       '2}  (use up the inputs and start `qty` batches; done at once when the recipe takes no rounds)')
def _start_job(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: ProductionConfig = config_of(world, name, PRODUCTION, where)
    agent = entity_of(world, runner.eval(effect["who"], vars), where, "a producer")
    recipe = str(runner.eval(effect["recipe"], vars))
    if recipe not in config.recipes:
        raise RunError(f"'{recipe}' is not a recipe of {name} (recipes: {', '.join(config.recipes)})", where)
    times = whole(runner.eval(effect.get("qty", 1), vars), where, "qty")
    if not 1 <= times <= MAX_BATCHES:
        raise Abort(f"Batches must be 1 to {MAX_BATCHES}.")
    why = _blocked(world, name, config, agent, recipe, times)
    if why is not None:
        raise Abort(why[0].upper() + why[1:] + ".")
    spec = config.recipes[recipe]
    for item, qty in spec.inputs.items():
        destroy_items(world, item, agent, qty * times, recipe, where)
    for currency, cost in spec.cost.items():
        burn_money(world, currency, agent, float(_eval(world, cost, agent, where)) * times, recipe, where)
    if spec.rounds == 0:
        text = _finish(runner, name, config, agent, recipe, times, where)
        emit_to(world, f"{name}_done", text, [agent.id])
        return
    world.evaluation.create(f"{name}_job", None, f"{recipe} for {agent.name}",
                            {"owner": agent.id, "recipe": recipe, "qty": times, "started": world.round,
                             "due": world.round + spec.rounds}, None, world.evaluation.scope(), where)


@family_action("economy", ("production",), "tick", internal=True,
               example='{"economy": "craft", "action": "tick"}  (finish jobs that are due; a job whose output does not '
                       'fit waits)')
def _production_tick(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: ProductionConfig = config_of(world, name, PRODUCTION, where)
    for job in [j for j in world.entities_of(f"{name}_job") if int(props(j)["due"]) <= world.round]:
        owner = world.entities.get(props(job)["owner"])
        if owner is None or not owner.alive:
            world.remove(job)
            continue
        recipe, times = props(job)["recipe"], int(props(job)["qty"])
        mark = world.mark()
        try:
            text = _finish(runner, name, config, owner, recipe, times, where)
        except Abort as exc:
            world.rollback(mark)
            if props(job)["status"] != "waiting":
                world.set_prop(job, "status", "waiting")
                emit_to(world, f"{name}_waiting", f"{times} × {recipe} is ready but waits: {exc.reason}", [owner.id])
            continue
        world.remove(job)
        emit_to(world, f"{name}_done", text, [owner.id], why=text)
