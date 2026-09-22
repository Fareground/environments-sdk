"""The ``economy`` family's ``ledger`` mode: currencies with credit limits, named sources (UBI,
allowances, subsidies), taxes and fees, and loans with interest, due dates and default."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..registry import MechanismError, family_action, mode
from ..world import Abort
from ._common import ToolsSetting, tools_field
from .econ_assets import balance, move_money
from .econ_base import (INVENTORY, LEDGER, amount, checked_config, props, choice_param, config_of, declared_names, emit_to,
                        entity_of, guarded, money, register_config, run_hook, require_types, type_list, valid_name, whole)
from .econ_inventory import agent_types, baseline

__all__ = ["LedgerConfig"]


class CurrencySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Union[float, str] = Field(0.0, description="Starting balance of every holder (number or expression).")
    credit: Union[float, str, None] = Field(None, description="How far below zero a holder may go (number or expression); none when omitted.")
    unit: str = Field("", description="Unit shown with amounts.")
    value: float = Field(1, description="Worth of one unit in $net_worth.")
    description: str = ""


class SourceSpec(BaseModel):
    """Money created on a schedule: UBI, allowances, subsidies."""

    model_config = ConfigDict(extra="forbid")

    to: str = Field(..., description="Type that receives it.")
    amount: Union[float, str] = Field(..., description="Amount per recipient (number or expression over $it).")
    currency: Optional[str] = Field(None, description="Currency (needed when the ledger has several).")
    where: Optional[str] = Field(None, description="Which recipients ($it).")
    every: int = Field(1, ge=1, description="Rounds between payments.")
    start: int = Field(1, ge=1, description="First round it pays.")
    mode: Literal["add", "top_up", "reset"] = Field("add", description="add | top_up (up to amount) | reset (unspent money expires, then amount).")
    say: str = Field("", description="News headline when it pays.")


class TaxSpec(BaseModel):
    """A levy on payments that name it: {\"economy\": <ledger>, \"action\": \"pay\", ..., \"tax\": name}."""

    model_config = ConfigDict(extra="forbid")

    rate: Union[float, str] = Field(..., description="Share of the payment (number or expression over $payer, $payee, $amount).")
    on: Literal["payee", "payer"] = Field("payee", description="payee: withheld from what is received; payer: added on top.")
    to: Optional[str] = Field(None, description="Entity id collecting it; omitted = the money leaves the economy (a sink).")
    description: str = ""


class LoanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lenders: Union[str, List[str]] = Field(..., description="Type(s) that lend at a posted rate.")
    borrowers: Union[str, List[str]] = Field(..., description="Type(s) that may borrow.")
    currency: Optional[str] = None
    rate_min: float = Field(0, ge=0, description="Lowest interest per round.")
    rate_max: float = Field(0.2, ge=0, description="Highest interest per round.")
    max_amount: Union[float, str] = Field(1000.0, description="Largest loan (number or expression over $actor).")
    max_term: int = Field(12, ge=1, description="Longest term in rounds.")
    grace: int = Field(0, ge=0, description="Rounds after the due date before an unpaid loan defaults.")
    on_default: List[Any] = Field([], description="Effects when a loan defaults ($loan, $lender, $borrower, $unpaid).")


class LedgerConfig(BaseModel):
    """Money held by entities."""

    model_config = ConfigDict(extra="forbid")

    who: Union[str, List[str]] = Field(..., description="Type(s) holding money (subtypes included).")
    currencies: Dict[str, CurrencySpec] = Field(..., min_length=1, description="{currency: {start, credit, unit, value}}; each is a holder property ($actor.cash).")
    sources: Dict[str, SourceSpec] = Field({}, description="Scheduled money creation: {name: {to, amount, every, mode}}.")
    taxes: Dict[str, TaxSpec] = Field({}, description="Levies payments can name: {name: {rate, on, to}}.")
    loans: Optional[LoanSpec] = Field(None, description="Loans at posted rates with interest, due dates and default.")
    actions: List[Literal["pay"]] = Field([], description="Tools generated for agent holders: pay (pay any holder). Loans generate their own.")
    tools: ToolsSetting = tools_field()


register_config(LEDGER, LedgerConfig)


def _currency(config: LedgerConfig, given: Optional[str], field: str) -> str:
    if given is None:
        if len(config.currencies) != 1:
            raise MechanismError("this ledger has several currencies; name one", f"currencies: {', '.join(config.currencies)}", field)
        return next(iter(config.currencies))
    if given not in config.currencies:
        raise MechanismError(f"'{given}' is not a currency of this ledger", f"currencies: {', '.join(config.currencies)}", field)
    return given


def _money_left(currency: str, spec: CurrencySpec) -> str:
    return f"$actor.{currency} + $actor.{currency}_credit" if spec.credit is not None else f"$actor.{currency}"


@mode("economy", "ledger", LedgerConfig,
      "Money: each currency is a number property of every holder (`$actor.cash`) with an optional credit limit. "
      "`pay` moves money (never creating it), `mint`/`burn` name their source or sink, scheduled `sources` pay UBI or "
      "allowances, `taxes` withhold a share of payments that name them, and `loans` add `<name>_borrow`, `<name>_repay` "
      "and `<name>_set_rate` with per-round interest, due dates and default. The invariant `$conserved(<name>)` "
      "proves balances equal $world.<name>_supply; $world.<name>_flows totals every source and sink.",
      example={"who": ["household", "shop"], "currencies": {"cash": {"start": 100, "credit": 20}},
               "sources": {"allowance": {"to": "household", "amount": 300, "every": 30, "mode": "reset"}},
               "taxes": {"sales_tax": {"rate": 0.08, "on": "payer"}}}, was="ledger")
def _expand_ledger(name: str, config: LedgerConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    holders = type_list(config.who)
    require_types(contract, holders, "who")
    taken = {**declared_names(contract, LEDGER, "currencies"), **declared_names(contract, INVENTORY, "items")}
    for currency in config.currencies:
        if taken.get(currency, name) != name:
            raise MechanismError(f"'{currency}' is already declared by '{taken[currency]}'",
                                 "give every currency and item its own name", f"currencies.{currency}")
    holder_props: Dict[str, Any] = {}
    for currency, spec in config.currencies.items():
        if not valid_name(currency):
            raise MechanismError(f"currency '{currency}' is not a valid name", "use letters, digits and _ (not a word expressions use, like in or not)", f"currencies.{currency}")
        holder_props[currency] = {"type": "number", "default": spec.start, "unit": spec.unit,
                                  "description": spec.description or f"Money held ({currency})."}
        if spec.credit is not None:
            holder_props[f"{currency}_credit"] = {"type": "number", "default": spec.credit, "min": 0, "private": True,
                                                  "description": f"How far below zero {currency} may go."}
    fragment: Dict[str, Any] = {
        "types": {t: {"props": holder_props} for t in holders},
        "world": {f"{name}_supply": {"type": "map", "default": baseline(contract, holders, list(config.currencies)),
                                     "description": "Money in existence per currency."},
                  f"{name}_flows": {"type": "map", "default": {}, "description": "Money created (+) and destroyed (−) by each named source and sink."}},
        "invariants": [{"expr": f"$conserved('{name}')", "check": "round",
                        "why": f"Money of {name} changes only by payments or named sources and sinks."}],
        "events": [_source_event(name, config, source, spec, contract) for source, spec in config.sources.items()],
        "actions": {},
    }
    agents = agent_types(contract, holders)
    if "pay" in config.actions and agents:
        fragment["actions"][f"{name}_pay"] = _pay_action(name, config, holders, agents)
    if config.loans is not None:
        _loans(name, config, config.loans, contract, fragment)
    if agents:
        shown = " · ".join(f"{{{c}}} {c}" for c in config.currencies)  # money is not always dollars
        fragment["views"] = {f"{name}_balance": {"for": agents, "title": "Your money", "bullet": False, "show": shown}}
    return fragment


def _source_event(name: str, config: LedgerConfig, source: str, spec: SourceSpec, contract: Mapping[str, Any]) -> Dict[str, Any]:
    require_types(contract, [spec.to], f"sources.{source}.to")
    currency = _currency(config, spec.currency, f"sources.{source}.currency")
    effects: List[Any]
    mint = {"economy": name, "action": "mint", "currency": currency, "to": "$it", "source": source}
    if spec.mode == "add":
        effects = [{**mint, "amount": spec.amount}]
    elif spec.mode == "top_up":
        effects = [{**mint, "amount": f"$max(0, ({spec.amount}) - $it.{currency})"}]
    else:
        effects = [{"if": f"$it.{currency} > 0", "then": [{"economy": name, "action": "burn", "currency": currency, "from": "$it",
                                                             "amount": f"$it.{currency}", "sink": f"{source} expired"}]},
                   {**mint, "amount": spec.amount}]
    event: Dict[str, Any] = {"name": f"{name}: {source}", "phase": "start", "each": spec.to, "do": effects}
    if spec.start > 1:
        event["when"] = f"$round >= {spec.start} and ($round - {spec.start}) % {spec.every} == 0"
    elif spec.every > 1:
        event["every"] = spec.every
    if spec.where:
        event["where"] = spec.where
    if spec.say:
        event["say"] = spec.say
    return event


def _pay_action(name: str, config: LedgerConfig, holders: List[str], agents: List[str]) -> Dict[str, Any]:
    to, ref = choice_param(holders, "$it.id != $actor.id", "Who you pay.")
    params: Dict[str, Any] = {"to": to}
    if len(config.currencies) == 1:
        currency, spec = next(iter(config.currencies.items()))
        params["amount"] = {"type": "number", "min": 0.01, "max": _money_left(currency, spec), "description": f"Amount of {currency}."}
        which: str = currency
    else:
        params["currency"] = {"type": "enum", "values": list(config.currencies)}
        params["amount"] = {"type": "number", "min": 0.01, "description": "Amount."}
        which = "$params.currency"
    return {"by": agents, "description": "Pay money to someone.", "params": params,
            "do": [{"economy": name, "action": "pay", "currency": which, "from": "$actor", "to": ref.format(name="to"),
                    "amount": "$params.amount"}],
            "outcome": f"You paid {{$params.amount|money}} to {{{ref.format(name='to')}}}."}


def _loans(name: str, config: LedgerConfig, loans: LoanSpec, contract: Mapping[str, Any], fragment: Dict[str, Any]) -> None:
    lenders, borrowers = type_list(loans.lenders), type_list(loans.borrowers)
    require_types(contract, lenders, "loans.lenders")
    require_types(contract, borrowers, "loans.borrowers")
    currency = _currency(config, loans.currency, "loans.currency")
    if loans.rate_max < loans.rate_min:
        raise MechanismError("loans.rate_max is below rate_min", None, "loans.rate_max")
    loan_type = f"{name}_loan"
    fragment["types"][loan_type] = {"description": "A loan: owed grows by `rate` each round until repaid or due.", "props": {
        "lender": {"type": "text", "default": ""}, "borrower": {"type": "text", "default": ""},
        "currency": {"type": "text", "default": currency}, "principal": {"type": "number", "default": 0, "min": 0},
        "owed": {"type": "number", "default": 0, "min": 0}, "rate": {"type": "number", "default": 0, "min": 0},
        "signed": {"type": "int", "default": 0}, "due": {"type": "int", "default": 0},
        "status": {"type": "enum", "values": ["active", "repaid", "defaulted"], "default": "active"},
        "paid": {"type": "number", "default": 0, "min": 0}, "written_off": {"type": "number", "default": 0, "min": 0}}}
    for lender_type in lenders:
        fragment["types"].setdefault(lender_type, {"props": {}})["props"] = {
            **fragment["types"].get(lender_type, {}).get("props", {}),
            f"{name}_rate": {"type": "number", "default": loans.rate_min, "min": loans.rate_min, "max": loans.rate_max,
                             "description": "Interest per round you charge on new loans."},
            f"{name}_lending": {"type": "bool", "default": True, "description": "Whether you take new borrowers."}}
    fragment["world"][f"{name}_loans"] = {"type": "map", "default": {}, "description": "Loan totals: made, repaid, defaulted, written_off."}
    fragment["events"].append({"name": f"{name}: loans", "phase": "end", "do": [{"economy": name, "action": "tick"}]})
    if loans.on_default:
        fragment["blocks"] = {f"{name}_on_default": {"args": ["loan", "lender", "borrower", "unpaid"], "do": list(loans.on_default),
                                                  "description": "Runs when a loan defaults."}}
    lender, lender_ref = choice_param(lenders, f"$it.{name}_lending and $it.id != $actor.id", "Lender (see their posted rate).")
    lending_agents, borrowing_agents = agent_types(contract, lenders), agent_types(contract, borrowers)
    if borrowing_agents:
        fragment["actions"][f"{name}_borrow"] = {
            "by": borrowing_agents, "description": f"Borrow {currency} at the lender's posted rate per round; repay before the due round or default.",
            "params": {"lender": lender,
                       "amount": {"type": "number", "min": 1, "max": loans.max_amount, "description": f"Amount of {currency}."},
                       "term": {"type": "int", "min": 1, "max": loans.max_term, "description": "Rounds until due."}},
            "do": [{"economy": name, "action": "lend", "from": lender_ref.format(name="lender"), "to": "$actor",
                    "amount": "$params.amount", "term": "$params.term"}],
            "outcome": "You borrowed {$params.amount|money}, due with interest in round {$round + $params.term}."}
        fragment["actions"][f"{name}_repay"] = {
            "by": borrowing_agents, "description": "Repay part or all of a loan you owe.",
            "params": {"loan": {"type": "entity", "of": loan_type, "where": "$it.borrower == $actor.id and $it.status == active",
                                "description": "A loan you owe."},
                       "amount": {"type": "number", "min": 0.01, "max": guarded("$params.loan.owed", "loan"),
                                  "description": "Amount to repay."}},
            "do": [{"economy": name, "action": "repay", "loan": "$params.loan", "amount": "$params.amount"}],
            "outcome": "You repaid {$params.amount|money}; {$params.loan.owed|money} still owed."}
    if lending_agents:
        fragment["actions"][f"{name}_set_rate"] = {
            "by": lending_agents, "description": "Post the interest rate per round for new loans, and whether you lend at all.",
            "params": {"rate": {"type": "number", "min": loans.rate_min, "max": loans.rate_max},
                       "lending": {"type": "bool", "default": True}},
            "do": [f"$actor.{name}_rate = $params.rate", f"$actor.{name}_lending = $params.lending"],
            "outcome": "Your rate is now {$params.rate|pct} per round."}
    views = fragment.setdefault("views", {})
    parties = sorted(set(lending_agents) | set(borrowing_agents))
    if parties:
        views[f"{name}_loans"] = {"for": parties, "title": "Your loans", "of": loan_type,
                                  "where": "$it.status == active and ($it.borrower == $actor.id or $it.lender == $actor.id)",
                                  "show": "[{id}] {$'you owe' if $it.borrower == $actor.id else 'owed to you'} {owed|money} by round {due} ({rate|pct}/round)"}
    if borrowing_agents:
        views[f"{name}_lenders"] = {"for": borrowing_agents, "title": "Lenders", "look": True, "of": lenders[0] if len(lenders) == 1 else None,
                                    "where": f"$it.{name}_lending", "show": f"[{{id}}] {{name}}: {{{name}_rate|pct}} per round"}
        if len(lenders) != 1:
            views[f"{name}_lenders"]["of"] = " + ".join(f"$filter({t}, true)" for t in lenders)


# ---------------------------------------------------------------------------
# Loan operations
# ---------------------------------------------------------------------------


def _check_loans(checker: Any, effect: Dict[str, Any], path: str) -> list:
    config = checked_config(checker, effect, "economy")
    if config is not None and config.loans is None:
        return [(path, f"ledger {effect['economy']} declares no loans", "add `loans` to the ledger")]
    return []


@family_action("economy", ("ledger",), "lend", keys=("from", "to", "amount", "rate", "term"), required=("from", "to", "amount", "term"),
               check=_check_loans, was=("lend",),
               example='{"economy": "money", "action": "lend", "from": "$params.bank", "to": "$actor", "amount": 100, "term": 6}  '
                       '(a loan: pays the principal now; rate defaults to the lender\'s posted rate)')
def _lend(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: LedgerConfig = config_of(world, name, LEDGER, where)
    if config.loans is None:
        raise RunError(f"ledger '{name}' declares no loans", where)
    lender = entity_of(world, runner.eval(effect["from"], vars), where, "a lender")
    borrower = entity_of(world, runner.eval(effect["to"], vars), where, "a borrower")
    if lender is borrower:
        raise Abort("You cannot borrow from yourself.")
    principal = amount(runner.eval(effect["amount"], vars), where)
    term = whole(runner.eval(effect["term"], vars), where, "term")
    if term < 1 or term > config.loans.max_term:
        raise Abort(f"The term must be 1 to {config.loans.max_term} rounds.")
    rate = runner.eval(effect["rate"], vars) if "rate" in effect else props(lender).get(f"{name}_rate", config.loans.rate_min)
    rate = amount(rate, where, "rate")
    currency = config.loans.currency or next(iter(config.currencies))
    move_money(world, currency, lender, borrower, principal, where, use_credit=False)
    loan = world.create(f"{name}_loan", None, f"Loan {lender.name} → {borrower.name}",
                        {"lender": lender.id, "borrower": borrower.id, "currency": currency, "principal": principal,
                         "owed": principal, "rate": rate, "signed": world.round, "due": world.round + term}, None, world.scope(), where)
    _count(world, name, "made", principal)
    emit_to(world, f"{name}_loan", f"{borrower.name} borrowed {money(principal)} {currency} from {lender.name}, due in round {world.round + term}.",
            [lender.id, borrower.id], {"loan": loan.id})


@family_action("economy", ("ledger",), "repay", keys=("loan", "amount"), required=("loan", "amount"), check=_check_loans,
               was=("repay",),
               example='{"economy": "money", "action": "repay", "loan": "$params.loan", "amount": 50}  '
                       '(pays a loan down; the borrower pays, never on credit)')
def _repay(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    loan = entity_of(world, runner.eval(effect["loan"], vars), where, "a loan")
    if loan.entity_type != f"{name}_loan" or props(loan).get("status") != "active":
        raise Abort("That loan is not open.")
    owed = float(props(loan)["owed"])
    value = min(amount(runner.eval(effect["amount"], vars), where), owed)
    borrower = entity_of(world, props(loan)["borrower"], where)
    lender = entity_of(world, props(loan)["lender"], where)
    move_money(world, props(loan)["currency"], borrower, lender, value, where, use_credit=False)
    _settle(world, name, loan, value)


def _settle(world: Any, name: str, loan: Any, paid: float) -> None:
    owed = round(float(props(loan)["owed"]) - paid, 9)
    world.set_prop(loan, "owed", max(0.0, owed))
    world.set_prop(loan, "paid", float(props(loan)["paid"]) + paid)
    if owed <= 1e-9:
        world.set_prop(loan, "status", "repaid")
        _count(world, name, "repaid", float(props(loan)["principal"]))


def _count(world: Any, name: str, key: str, value: float) -> None:
    totals = dict(world.props.get(f"{name}_loans") or {})
    totals[key] = round(totals.get(key, 0) + value, 9)
    totals[f"{key}_count"] = totals.get(f"{key}_count", 0) + 1
    world.set_world(f"{name}_loans", totals)


@family_action("economy", ("ledger",), "tick", internal=True, was=("ledger_tick",),
               example='{"economy": "money", "action": "tick"}  (accrue loan interest, collect loans that are due, default unpaid ones)')
def _ledger_tick(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: LedgerConfig = config_of(world, name, LEDGER, where)
    if config.loans is None:
        return
    for loan in [e for e in world.entities_of(f"{name}_loan") if props(e).get("status") == "active"]:
        p = props(loan)
        if world.round > p["signed"]:
            world.set_prop(loan, "owed", round(float(p["owed"]) * (1 + float(p["rate"])), 9))
        if world.round < p["due"]:
            continue
        borrower, lender = world.entities.get(p["borrower"]), world.entities.get(p["lender"])
        if borrower is None or lender is None or not borrower.alive or not lender.alive:
            continue
        available = max(0.0, balance(world, borrower, p["currency"], where))
        collected = min(available, float(p["owed"]))
        if collected > 0:
            move_money(world, p["currency"], borrower, lender, collected, where, use_credit=False)
            _settle(world, name, loan, collected)
        if props(loan)["status"] == "repaid":
            emit_to(world, f"{name}_loan", f"{borrower.name}'s loan from {lender.name} is repaid.", [borrower.id, lender.id])
            continue
        unpaid = float(props(loan)["owed"])
        if world.round >= p["due"] + config.loans.grace:
            world.set_prop(loan, "status", "defaulted")
            world.set_prop(loan, "written_off", unpaid)
            world.set_prop(loan, "owed", 0.0)
            _count(world, name, "defaulted", unpaid)
            emit_to(world, f"{name}_default", f"{borrower.name} defaulted on {money(unpaid)} owed to {lender.name}.",
                    [borrower.id, lender.id], {"loan": loan.id, "unpaid": unpaid},
                    why=f"A loan between {borrower.name} and {lender.name} defaulted.")
            if config.loans.on_default:
                run_hook(runner, f"{name}_on_default", {"loan": loan, "lender": lender, "borrower": borrower, "unpaid": unpaid},
                         f"mechanisms.{name}.loans.on_default")
        else:
            emit_to(world, f"{name}_overdue", f"Your loan from {lender.name} is overdue: {money(unpaid)} still owed.",
                    [borrower.id], why="A loan you owe is overdue.")
