"""The ``agreements`` family's ``labor`` mode: job postings, applications, hiring by choice or by rule,
wages paid on schedule, quitting and firing, and optional firms that turn workers into output at a posted price."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import ExprError, compile_expr
from ..registry import MechanismError, family_action, mode
from ..world.live import Abort
from ._common import ToolsSetting, tools_field
from .econ_assets import assets, destroy_items, held, make_items
from .econ_base import (
    INVENTORY,
    LABOR,
    bump,
    compiles,
    config_of,
    declared_use,
    emit_to,
    entity_of,
    money,
    props,
    register_config,
    require_currency,
    require_types,
    type_list,
    whole,
)
from .econ_inventory import agent_types

__all__ = ["LaborConfig", "FirmSpec"]

STATS = {"hires": 0, "quits": 0, "fires": 0, "wages": 0, "unpaid": 0, "produced": 0}


class FirmSpec(BaseModel):
    """Employers as firms: workers make output, sold at a posted price."""

    model_config = ConfigDict(extra="forbid")

    output: str = Field(..., description="Item made.")
    per_worker: float | str = Field(1.0,
                                    description="Units per worker per round (number or expression over $firm and "
                                                "$workers).")
    inputs: dict[str, int] = Field({}, description="Goods used up per unit {item: qty}.")
    price: float | str = Field(1.0, description="Starting posted price (prop `<name>_price`).")
    price_min: float = Field(0, ge=0, description="Lowest price a firm may post.")
    price_max: float | None = Field(None, ge=0, description="Highest price a firm may post.")


class LaborConfig(BaseModel):
    """Workers, employers and wages."""

    model_config = ConfigDict(extra="forbid")

    who: str | list[str] = Field(..., description="Type(s) that work.")
    employers: str | list[str] = Field(..., description="Type(s) that hire.")
    currency: str = Field(..., description="Ledger currency wages are paid in.")
    hiring: Literal["choice", "rule"] = Field("choice",
                                              description="choice: employers hire applicants with a tool; rule: "
                                                          "applicants are hired automatically each round.")
    rank: str | None = Field(None,
                             description="Rule hiring order: expression over $it (the worker), higher first; default "
                                         "first come.")
    wage_min: float = Field(0, ge=0, description="Lowest wage a posting may offer.")
    wage_max: float | None = Field(None, ge=0, description="Highest wage a posting may offer.")
    pay_every: int = Field(1, ge=1,
                           description="Rounds between paydays; each pays the per-round wage for every round since the "
                                       "last.")
    tax: str | None = Field(None, description="A ledger tax withheld from wages.")
    max_jobs: int = Field(1, ge=1, description="Jobs one worker may hold.")
    max_openings: int = Field(10, ge=1, description="Most openings one posting may have.")
    on_unpaid: Literal["quit", "owe"] = Field("quit",
                                              description="An unpaid wage ends the job (quit) or is owed and paid "
                                                          "first next payday (owe).")
    inventory: str | None = Field(None, description="Inventory of a firm's goods (needed with `firm`).")
    firm: FirmSpec | None = Field(None,
                                  description="Employers are firms: "
                                              "{output, per_worker, inputs, price, price_min, price_max}.")
    actions: list[Literal["post", "close", "hire", "reject", "fire", "apply", "withdraw", "quit", "set_price"]] = Field(
        ["post", "close", "hire", "reject", "fire", "apply", "withdraw", "quit", "set_price"],
        description="Tools generated for agents.")
    tools: ToolsSetting = tools_field()


register_config(LABOR, LaborConfig)


@mode("agreements", "labor", LaborConfig,
           "A labor market: employers `<name>_post` jobs (title, wage, openings) and `<name>_hire`, `<name>_reject` "
           "or `<name>_fire`; workers `<name>_apply` to open postings, `<name>_withdraw` and `<name>_quit`. With "
           "`hiring: rule` pending applicants are hired at the start of each round by `rank`. Wages are paid from "
           "employer to worker every `pay_every` rounds (a named ledger tax withheld); an unpaid wage ends the job or "
           "is owed. With `firm`, each employer makes output from its workers (and inputs) at the end of each round "
           "and posts a price. Entities: `<name>_posting`, `<name>_application`, `<name>_job`; totals in "
           "$world.<name>_stats.",
           example={"who": "person", "employers": "bakery", "currency": "cash", "wage_min": 5, "wage_max": 30,
                    "inventory": "goods",
                    "firm": {"output": "bread", "per_worker": 4, "inputs": {"flour": 1}, "price": 3}})
def _expand_labor(name: str, config: LaborConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    workers, employers = type_list(config.who), type_list(config.employers)
    require_types(contract, workers, "who")
    require_types(contract, employers, "employers")
    require_currency(contract, config.currency)
    if config.wage_max is not None and config.wage_max < config.wage_min:
        raise MechanismError("wage_max is below wage_min", None, "wage_max")
    if config.hiring == "rule" and config.rank is not None:
        compiles(config.rank, "rank")
    if config.firm is not None:
        items = declared_use(contract, config.inventory, INVENTORY, "inventory").get("items") or {}
        for item in [config.firm.output, *config.firm.inputs]:
            if item not in items:
                raise MechanismError(f"'{item}' is not an item of inventory '{config.inventory}'",
                                     f"items: {', '.join(items)}", "firm")
        compiles(config.firm.per_worker, "firm.per_worker")
    posting, application, job = f"{name}_posting", f"{name}_application", f"{name}_job"
    text, count = {"type": "text", "default": ""}, {"type": "int", "default": 0, "min": 0}
    fragment: dict[str, Any] = {
        "types": {
            posting: {"description": "A job posting.", "props": {
                "employer": text, "title": text, "wage": {"type": "number", "default": 0, "min": 0},
                "openings": count, "status": {"type": "enum", "values": ["open", "closed"], "default": "open"},
                "posted": count}},
            application: {"description": "A worker's application to a posting.", "props": {
                "worker": text, "posting": text, "applied": count,
                "status": {"type": "enum", "values": ["pending", "hired", "rejected", "withdrawn"],
                           "default": "pending"}}},
            job: {"description": "Employment: a wage per round from an employer to a worker.", "props": {
                "worker": text, "employer": text, "wage": {"type": "number", "default": 0, "min": 0}, "since": count,
                "status": {"type": "enum", "values": ["active", "ended"], "default": "active"},
                "owed": {"type": "number", "default": 0, "min": 0}, "reason": text}}},
        "world": {f"{name}_stats": {"type": "map", "default": dict(STATS), "description": "Labor totals."}},
        "events": [{"name": f"{name}: hiring", "phase": "start", "do": [{"agreements": name, "action": "tick"}]},
                   {"name": f"{name}: payday", "phase": "end", "do": [{"agreements": name, "action": "payday"}]}],
        "actions": {}, "views": {},
    }
    if config.firm is not None:
        price: dict[str, Any] = {"type": "number", "default": config.firm.price, "min": config.firm.price_min,
                                 "description": f"Posted price of {config.firm.output}."}
        if config.firm.price_max is not None:
            price["max"] = config.firm.price_max
        fragment["types"].update({t: {"props": {f"{name}_price": price, f"{name}_output": {
            **count, "description": f"{config.firm.output} made last round."}}} for t in employers})
    _worker_tools(name, config, agent_types(contract, workers), fragment)
    _employer_tools(name, config, agent_types(contract, employers), fragment)
    return fragment


def _worker_tools(name: str, config: LaborConfig, agents: list[str], fragment: dict[str, Any]) -> None:
    if not agents:
        return
    posting, application, job = f"{name}_posting", f"{name}_application", f"{name}_job"
    employed = f"$count({job}, $it.worker == $actor.id and $it.status == active)"
    actions, views = fragment["actions"], fragment["views"]
    if "apply" in config.actions:
        actions[f"{name}_apply"] = {
            "by": agents, "description": "Apply to an open job posting.",
            "when": [{"expr": f"{employed} < {config.max_jobs}", "why": "You already hold as many jobs as you can."}],
            "params": {"posting": {"type": "entity", "of": posting, "description": "Posting.",
                                   "where": "$it.status == open and $it.employer != $actor.id and not "
                                            f"$any({application}, $it.worker == $actor.id and $it.posting == $outer.id "
                                            "and $it.status == pending)"}},
            "do": [{"create": application,
                    "props": {"worker": "$actor.id", "posting": "$params.posting.id", "applied": "$round"}},
                   {"wake": "$params.posting.employer", "why": "{$actor.name} applied for {$params.posting.title}."}],
            "outcome": "You applied for {$params.posting.title}.", "private": True}
        views[f"{name}_postings"] = {"for": agents, "title": "Jobs open", "of": posting, "where": "$it.status == open",
                                     "sort": "$it.wage", "desc": True, "limit": 10, "empty": "No open jobs.",
                                     "show": "[{id}] {title} at {$entity($it.employer).name}: {wage} "
                                             f"{config.currency} a round, {{openings}} opening(s)"}
    mine = "$it.worker == $actor.id and $it.status == pending"
    if "withdraw" in config.actions:
        actions[f"{name}_withdraw"] = {"by": agents, "description": "Withdraw an application.", "private": True,
                                       "params": {"application": {"type": "entity", "of": application, "where": mine}},
                                       "do": ["$params.application.status = withdrawn"]}
    if "quit" in config.actions:
        actions[f"{name}_quit"] = {"by": agents, "description": "Quit a job (no more wages from it).",
                                   "params": {"job": {"type": "entity", "of": job,
                                                      "where": "$it.worker == $actor.id and $it.status == active"}},
                                   "do": [{"agreements": name, "action": "quit", "job": "$params.job"}]}
    views[f"{name}_my_jobs"] = {"for": agents, "title": "Your work", "of": job,
                                "where": "$it.worker == $actor.id and $it.status == active",
                                "empty": "You have no job.",
                                "show": f"[{{id}}] {{$entity($it.employer).name}}: {{wage}} {config.currency} a round "
                                        "since round "
                                        "{since}{$' · owed ' + $text($it.owed) if $it.owed > 0 else ''}"}


def _employer_tools(name: str, config: LaborConfig, agents: list[str], fragment: dict[str, Any]) -> None:
    if not agents:
        return
    posting, application, job = f"{name}_posting", f"{name}_application", f"{name}_job"
    actions, views = fragment["actions"], fragment["views"]
    wage: dict[str, Any] = {"type": "number", "min": config.wage_min,
                            "description": f"Wage per round ({config.currency})."}
    if config.wage_max is not None:
        wage["max"] = config.wage_max
    if "post" in config.actions:
        actions[f"{name}_post"] = {
            "by": agents, "description": "Post a job: title, wage per round and openings.",
            "params": {"title": {"type": "text", "max_len": 60, "default": "job"}, "wage": wage,
                       "openings": {"type": "int", "min": 1, "max": config.max_openings, "default": 1}},
            "do": [{"create": posting,
                    "props": {"employer": "$actor.id", "title": "$params.title", "wage": "$params.wage",
                              "openings": "$params.openings", "posted": "$round"}}],
            "outcome": "Posted: {$params.title} at {$params.wage} " + config.currency + " a round."}
    if "close" in config.actions:
        actions[f"{name}_close"] = {"by": agents, "description": "Close one of your postings.",
                                    "params": {"posting": {"type": "entity", "of": posting,
                                                           "where": "$it.employer == $actor.id and $it.status == "
                                                                    "open"}},
                                    "do": ["$params.posting.status = closed"]}
    pending = "$it.status == pending and $entity($it.posting).employer == $actor.id"
    if "hire" in config.actions and config.hiring == "choice":
        actions[f"{name}_hire"] = {"by": agents, "description": "Hire an applicant to your posting.",
                                   "params": {"application": {"type": "entity", "of": application, "where": pending}},
                                   "do": [{"agreements": name, "action": "hire", "application": "$params.application"}],
                                   "outcome": "Hired {$entity($params.application.worker).name}."}
    if "reject" in config.actions and config.hiring == "choice":
        actions[f"{name}_reject"] = {"by": agents, "description": "Turn down an applicant.", "private": True,
                                     "params": {"application": {"type": "entity", "of": application, "where": pending}},
                                     "do": ["$params.application.status = rejected"]}
    if "fire" in config.actions:
        actions[f"{name}_fire"] = {"by": agents, "description": "Let a worker go.",
                                   "params": {"job": {"type": "entity", "of": job,
                                                      "where": "$it.employer == $actor.id and $it.status == active"}},
                                   "do": [{"agreements": name, "action": "fire", "job": "$params.job"}]}
    if config.firm is not None and "set_price" in config.actions:
        price: dict[str, Any] = {"type": "number", "min": config.firm.price_min}
        if config.firm.price_max is not None:
            price["max"] = config.firm.price_max
        actions[f"{name}_set_price"] = {"by": agents, "description": f"Post your price for {config.firm.output}.",
                                        "params": {"price": price}, "do": [f"$actor.{name}_price = $params.price"]}
    views[f"{name}_staff"] = {"for": agents, "title": "Your workers", "of": job,
                              "where": "$it.employer == $actor.id and $it.status == active", "empty": "No workers.",
                              "show": f"[{{id}}] {{$entity($it.worker).name}}: {{wage}} {config.currency} a round"}
    views[f"{name}_applicants"] = {"for": agents, "title": "Applicants", "of": application, "where": pending,
                                   "show": "[{id}] {$entity($it.worker).name} for {$entity($it.posting).title}"}


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _stat(world: Any, name: str, key: str, delta: float) -> None:
    bump(world, f"{name}_stats", key, delta)


def _active_jobs(world: Any, name: str, **match: str) -> list[Any]:
    return [j for j in world.entities_of(f"{name}_job") if props(j)["status"] == "active"
            and all(props(j)[k] == v for k, v in match.items())]


def _hire(world: Any, name: str, config: LaborConfig, application: Any, where: str) -> None:
    p = props(application)
    posting = entity_of(world, p["posting"], where, "a posting")
    worker = entity_of(world, p["worker"], where, "a worker")
    if p["status"] != "pending" or props(posting)["status"] != "open" or int(props(posting)["openings"]) < 1:
        raise Abort("That application or posting is no longer open.")
    if len(_active_jobs(world, name, worker=worker.id)) >= config.max_jobs:
        raise Abort(f"{worker.name} already holds as many jobs as allowed.")
    employer = entity_of(world, props(posting)["employer"], where, "an employer")
    world.create(f"{name}_job", None, f"{worker.name} at {employer.name}",
                 {"worker": worker.id, "employer": employer.id, "wage": props(posting)["wage"], "since": world.round},
                 None, world.scope(), where)
    world.set_prop(application, "status", "hired")
    openings = int(props(posting)["openings"]) - 1
    world.set_prop(posting, "openings", openings)
    if openings == 0:
        world.set_prop(posting, "status", "closed")
    _stat(world, name, "hires", 1)
    emit_to(world, f"{name}_hired", f"{employer.name} hired you as {props(posting)['title']} at "
            f"{money(props(posting)['wage'])} {config.currency} a round.", [worker.id],
            why=f"{employer.name} hired you.")


def _end(world: Any, name: str, job: Any, reason: str) -> None:
    world.set_prop(job, "status", "ended")
    world.set_prop(job, "reason", reason)
    worker, employer = props(job)["worker"], props(job)["employer"]
    if reason in ("quit", "fired"):
        _stat(world, name, "quits" if reason == "quit" else "fires", 1)
    who = world.entities.get(worker)
    text = {"quit": f"{who.name if who else worker} quit.", "fired": "You were let go.", "unpaid": "The job ended: "
                                                                                                   "wages went "
                                                                                                   "unpaid."}[reason]
    emit_to(world, f"{name}_ended", text, [employer if reason == "quit" else worker], why=text)


@family_action("agreements", ("labor",), "hire", keys=("application",), required=("application",),
               example='{"agreements": "jobs", "action": "hire", "application": "$params.application"}  '
                       '(turn a pending application into a job)')
def _hire_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    application = entity_of(world, runner.eval(effect["application"], vars), where, "an application")
    if application.entity_type != f"{name}_application":
        raise RunError(f"{application.id} is not an application of {name}", where)
    _hire(world, name, config_of(world, name, LABOR, where), application, where)


@family_action("agreements", ("labor",), "quit", keys=("job",), required=("job",),
               example='{"agreements": "jobs", "action": "quit", "job": "$params.job"}  (the worker leaves: wages '
                       'stop)')
def _quit(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    _end_job(runner, effect, vars, where, "quit")


@family_action("agreements", ("labor",), "fire", keys=("job",), required=("job",),
               example='{"agreements": "jobs", "action": "fire", "job": "$params.job"}  (the employer lets the worker '
                       'go: wages stop)')
def _fire(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    _end_job(runner, effect, vars, where, "fired")


def _end_job(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str, reason: str) -> None:
    world = runner.world
    name = effect["agreements"]
    job = entity_of(world, runner.eval(effect["job"], vars), where, "a job")
    if job.entity_type != f"{name}_job" or props(job)["status"] != "active":
        raise Abort("That job has already ended.")
    _end(world, name, job, reason)


@family_action("agreements", ("labor",), "tick", internal=True,
               example='{"agreements": "jobs", "action": "tick"}  (rule hiring: fill open postings from pending '
                       'applicants by rank)')
def _labor_tick(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: LaborConfig = config_of(world, name, LABOR, where)
    if config.hiring != "rule":
        return
    for posting in world.entities_of(f"{name}_posting"):
        applicants = [a for a in world.entities_of(f"{name}_application")
                      if props(a)["posting"] == posting.id and props(a)["status"] == "pending"]
        applicants.sort(key=lambda a: (-_rank(world, config, a, where), int(props(a)["applied"])))
        for application in applicants:
            if props(posting)["status"] != "open":
                break
            mark = world.journal.mark()
            try:
                _hire(world, name, config, application, where)
            except Abort:
                world.journal.rollback(mark)


def _rank(world: Any, config: LaborConfig, application: Any, where: str) -> float:
    if config.rank is None:
        return 0.0
    worker = world.entities.get(props(application)["worker"])
    try:
        value = compile_expr(config.rank)(world.scope(it=worker))
    except ExprError as exc:
        raise RunError(str(exc), f"{where}.rank") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunError(f"rank must give a number, got {value!r}", f"{where}.rank")
    return float(value)


@family_action("agreements", ("labor",), "payday", internal=True,
               example='{"agreements": "jobs", "action": "payday"}  (firms produce with their workers, then wages are '
                       'paid)')
def _labor_payday(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: LaborConfig = config_of(world, name, LABOR, where)
    if config.firm is not None:
        for firm in [e for e in world.entities.values() if e.alive and f"{name}_price" in props(e)]:
            _produce(runner, name, config, firm, where)
    if (world.round - 1) % config.pay_every != config.pay_every - 1:
        return
    for job in _active_jobs(world, name):
        p = props(job)
        worker, employer = world.entities.get(p["worker"]), world.entities.get(p["employer"])
        if worker is None or employer is None or not worker.alive or not employer.alive:
            _end(world, name, job, "unpaid")
            continue
        rounds = min(config.pay_every, world.round - int(p["since"]) + 1)
        due = round(float(p["wage"]) * rounds + float(p["owed"]), 9)
        payment: dict[str, Any] = {"economy": assets(world).currencies[config.currency], "action": "pay",
                                   "currency": config.currency, "from": "$employer", "to": "$worker", "amount": due}
        if config.tax:
            payment["tax"] = config.tax
        mark = world.journal.mark()
        try:
            runner.run([payment], {"employer": employer, "worker": worker}, f"mechanisms.{name}.wages")
        except Abort:
            world.journal.rollback(mark)
            _stat(world, name, "unpaid", 1)
            if config.on_unpaid == "quit":
                _end(world, name, job, "unpaid")
                emit_to(world, f"{name}_unpaid", f"{employer.name} could not pay {worker.name}; the job ended.",
                        [employer.id])
            else:
                world.set_prop(job, "owed", due)
                emit_to(world, f"{name}_unpaid", f"{employer.name} owes you {money(due)} {config.currency} in wages.",
                        [worker.id], why="Your wages went unpaid.")
            continue
        world.set_prop(job, "owed", 0)
        _stat(world, name, "wages", due)


def _produce(runner: Any, name: str, config: LaborConfig, firm: Any, where: str) -> None:
    world = runner.world
    spec = config.firm
    assert spec is not None
    workers = len(_active_jobs(world, name, employer=firm.id))
    value = runner.eval(spec.per_worker, {"firm": firm, "workers": workers})
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RunError(f"per_worker must give a number ≥ 0, got {value!r}", f"mechanisms.{name}.firm.per_worker")
    units = math.floor(value * workers + 1e-9)
    for item, qty in spec.inputs.items():
        if qty:
            units = min(units, held(world, firm, item, where) // qty)
    made = 0
    if units > 0:
        mark = world.journal.mark()
        try:
            for item, qty in spec.inputs.items():
                destroy_items(world, item, firm, qty * units, f"{name} production", where)
            make_items(world, spec.output, firm, units, f"{name} production", where)
            made = units
        except Abort as exc:
            world.journal.rollback(mark)
            emit_to(world, f"{name}_idle", f"{firm.name} could not store its output: {exc.reason}", [firm.id])
    world.set_prop(firm, f"{name}_output", whole(made, where))
    _stat(world, name, "produced", made)
