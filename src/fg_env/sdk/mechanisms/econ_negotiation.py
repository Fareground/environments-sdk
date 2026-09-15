"""The ``agreements`` family's ``negotiation`` mode: offers over several issues, counter-offers,
deadlines, private walk-away values, and binding deals executed as conserved payments and deliveries
over time, with breach detection and penalties. Bilateral by default; coalition offers need every
recipient's acceptance."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import MechanismError, family_action, mode
from ..world import Abort
from ._common import ToolsSetting, tools_field
from .econ_assets import assets, balance, move_items, move_money
from .econ_base import (INVENTORY, LEDGER, NEGOTIATION, bump, choice_param, compiles, config_of, declared_names, emit_to,
                        entity_of, money, props, register_config, require_types, run_hook, to_ids, type_list, valid_name, whole)
from .econ_inventory import agent_types

__all__ = ["NegotiationConfig", "IssueSpec", "ObligationSpec", "BreachSpec"]

#: Most installments one obligation may schedule.
MAX_DUTIES = 1000
STATS = {"offers": 0, "counters": 0, "rejected": 0, "withdrawn": 0, "expired": 0, "deals": 0, "duties_done": 0,
         "breaches": 0, "penalties": 0}
RESERVED_PARAMS = ("to", "recipients", "offer", "note", "duty")


class IssueSpec(BaseModel):
    """One dimension of a deal."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["number", "int", "enum"] = "number"
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[List[Any]] = Field(None, description="Choices (type enum).")
    unit: str = ""
    description: str = ""


class ObligationSpec(BaseModel):
    """What a signed deal makes someone pay or deliver, in installments."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    label: str = ""
    from_: str = Field(..., alias="from", description="Who owes it: expression over $proposer, $acceptor, $parties, $terms.")
    to: str = Field(..., description="Who receives it (same roots).")
    pay: Optional[str] = Field(None, description="Currency paid.")
    give: Optional[str] = Field(None, description="Item delivered.")
    amount: Union[float, str] = Field(..., description="Per installment: number or expression over $terms and $k (1, 2, …).")
    times: Union[int, str] = Field(1, description="Installments (number or expression over $terms).")
    every: int = Field(1, ge=1, description="Rounds between installments.")
    start: int = Field(1, ge=0, description="Rounds after signing until the first is due.")
    manual: bool = Field(False, description="The obligor must fulfil it with a tool by its due round; otherwise it is automatic.")

    @model_validator(mode="after")
    def _one_asset(self) -> "ObligationSpec":
        if (self.pay is None) == (self.give is None):
            raise ValueError("an obligation names exactly one of `pay` (a currency) or `give` (an item)")
        return self


class BreachSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    penalty: Union[float, str] = Field(0.0, description="Owed to the other side on each breach: number or expression over $terms and $duty.")
    currency: Optional[str] = Field(None, description="Currency of the penalty (default: the breached payment's, or the first ledger's).")
    terminate: bool = Field(False, description="A breach ends the deal: remaining installments are cancelled.")
    on_breach: List[Any] = Field([], description="Effects on a breach ($deal, $duty, $breacher, $victim, $terms).")


class NegotiationConfig(BaseModel):
    """Offers, counter-offers and binding deals among parties."""

    model_config = ConfigDict(extra="forbid")

    who: Union[str, List[str]] = Field(..., description="Agent type(s) that negotiate.")
    issues: Dict[str, IssueSpec] = Field(..., min_length=1, description="{issue: {type, min, max, values, unit}}.")
    max_depth: int = Field(6, ge=0, description="Longest chain of counter-offers.")
    expires: Optional[int] = Field(None, ge=1, description="Rounds an offer stays open.")
    deadline: Union[int, str, None] = Field(None, description="Last round offers can be made or accepted (number or expression).")
    coalition: bool = Field(False, description="Offers go to several parties at once; all of them must accept.")
    reservation: Union[float, str, None] = Field(None, description="Private walk-away value of each party (prop `<name>_reservation`).")
    value: Optional[str] = Field(None, description="Worth of terms to a party, shown only to that party: expression over $party and $terms.")
    once: bool = Field(True, description="The first signed deal closes the negotiation.")
    obligations: List[ObligationSpec] = Field([], description="What a signed deal makes parties pay or deliver.")
    breach: BreachSpec = Field(None, validate_default=True,
                               description="What a breach costs: {penalty, currency, terminate, on_breach}; nothing by default.")
    actions: List[Literal["propose", "counter", "accept", "reject", "withdraw", "fulfill"]] = Field(
        ["propose", "counter", "accept", "reject", "withdraw", "fulfill"], description="Tools generated for the parties.")
    tools: ToolsSetting = tools_field()

    @field_validator("breach", mode="before")
    @classmethod
    def _breach_terms(cls, value: Any) -> Any:
        return {} if value is None else value


register_config(NEGOTIATION, NegotiationConfig)


@mode("agreements", "negotiation", NegotiationConfig,
           "Negotiation over several issues: `<name>_propose`, `<name>_counter` (up to `max_depth`), `<name>_accept`, "
           "`<name>_reject` and `<name>_withdraw`, each offered only for offers open to you, with issue bounds as tool "
           "bounds, an optional deadline and expiry. Walk-away values stay private (`<name>_reservation`) and `value` "
           "shows each party what terms are worth to it alone. A signed deal (`<name>_deal`) schedules `obligations` as "
           "duties (`<name>_duty`) executed as conserved payments or deliveries; a duty not met by its due round is a "
           "breach with a penalty, optional termination and `on_breach` effects. Totals in $world.<name>_stats.",
           example={"who": "country", "deadline": 8,
                    "issues": {"tariff": {"min": 0, "max": 30, "unit": "%"}, "quota": {"type": "int", "min": 0, "max": 500}},
                    "reservation": 40, "value": "$party.weight * $terms.quota - $terms.tariff",
                    "obligations": [{"from": "$acceptor", "to": "$proposer", "pay": "credits", "amount": "$terms.quota",
                                     "times": 4}],
                    "breach": {"penalty": 100, "terminate": True}}, was="negotiation")
def _expand_negotiation(name: str, config: NegotiationConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    parties = type_list(config.who)
    require_types(contract, parties, "who")
    agents = agent_types(contract, parties)
    if not agents:
        raise MechanismError("who must be agents", "set \"agent\": true on the party type", "who")
    if config.coalition and len(parties) != 1:
        raise MechanismError("coalition offers need one party type", "give the parties a common parent type", "who")
    for issue, spec in config.issues.items():
        if not valid_name(issue) or issue in RESERVED_PARAMS:
            raise MechanismError(f"issue '{issue}' cannot be used as a name", f"avoid {', '.join(RESERVED_PARAMS)}", f"issues.{issue}")
        if spec.type == "enum" and not spec.values:
            raise MechanismError("an enum issue needs `values`", None, f"issues.{issue}.values")
        if spec.min is not None and spec.max is not None and spec.min > spec.max:
            raise MechanismError("min is above max", None, f"issues.{issue}")
    currencies = declared_names(contract, LEDGER, "currencies")
    items = declared_names(contract, INVENTORY, "items")
    for index, duty in enumerate(config.obligations):
        path = f"obligations[{index}]"
        for field in ("from_", "to", "amount", "times", "pay", "give"):
            compiles(getattr(duty, field), f"{path}.{field.rstrip('_')}")
        if duty.pay is not None and "$" not in duty.pay and duty.pay not in currencies:
            raise MechanismError(f"'{duty.pay}' is not a declared currency", "declare a ledger with it", f"{path}.pay")
        if duty.give is not None and "$" not in duty.give and duty.give not in items:
            raise MechanismError(f"'{duty.give}' is not a declared item", "declare an inventory with it", f"{path}.give")
    for field in ("deadline", "reservation", "value"):
        compiles(getattr(config, field), field)
    compiles(config.breach.penalty, "breach.penalty")
    if config.breach.currency is not None and config.breach.currency not in currencies:
        raise MechanismError(f"'{config.breach.currency}' is not a declared currency", None, "breach.currency")
    return _fragment(name, config, parties, agents)


def _issue_param(spec: IssueSpec) -> Dict[str, Any]:
    param: Dict[str, Any] = {"type": spec.type, "description": (spec.description or "") + (f" ({spec.unit})" if spec.unit else "")}
    for key in ("min", "max", "values"):
        if getattr(spec, key) is not None:
            param[key] = getattr(spec, key)
    return param


def _fragment(name: str, config: NegotiationConfig, parties: List[str], agents: List[str]) -> Dict[str, Any]:
    offer, deal, duty = f"{name}_offer", f"{name}_deal", f"{name}_duty"
    text = {"type": "text", "default": ""}
    fragment: Dict[str, Any] = {
        "types": {
            offer: {"description": "An offer on the table.", "props": {
                "sender": text, "recipients": {"type": "list", "default": []}, "terms": {"type": "map", "default": {}},
                "status": {"type": "enum", "values": ["open", "accepted", "rejected", "countered", "expired", "withdrawn"], "default": "open"},
                "depth": {"type": "int", "default": 0, "min": 0}, "made": {"type": "int", "default": 0},
                "expires": {"type": "int", "default": 0}, "accepted_by": {"type": "list", "default": []}, "note": text}},
            deal: {"description": "A signed, binding deal.", "props": {
                "parties": {"type": "list", "default": []}, "proposer": text, "acceptors": {"type": "list", "default": []},
                "terms": {"type": "map", "default": {}}, "signed": {"type": "int", "default": 0},
                "status": {"type": "enum", "values": ["active", "completed", "terminated"], "default": "active"},
                "breaches": {"type": "int", "default": 0, "min": 0}, "offer": text}},
            duty: {"description": "One installment a deal makes someone pay or deliver.", "props": {
                "deal": text, "label": text, "by": text, "to": text,
                "kind": {"type": "enum", "values": ["pay", "give"], "default": "pay"}, "asset": text,
                "amount": {"type": "number", "default": 0, "min": 0}, "due": {"type": "int", "default": 0},
                "manual": {"type": "bool", "default": False},
                "status": {"type": "enum", "values": ["open", "done", "breached", "cancelled"], "default": "open"},
                "penalty": {"type": "number", "default": 0, "min": 0}}}},
        "world": {f"{name}_closed": {"type": "bool", "default": False, "description": "A deal closed the negotiation."},
                  f"{name}_stats": {"type": "map", "default": dict(STATS), "description": "Negotiation totals."}},
        "events": [{"name": f"{name}: deadlines and duties", "phase": "end", "do": [{"agreements": name, "action": "tick"}]}],
        "actions": {}, "views": {}, "defs": {},
    }
    if config.reservation is not None:
        for party in parties:
            fragment["types"][party] = {"props": {f"{name}_reservation": {
                "type": "number", "default": config.reservation, "private": True, "description": "Your walk-away value: never accept less."}}}
    worth = ""
    if config.value:
        fragment["defs"][f"{name}_value"] = {"args": ["party", "terms"], "expr": config.value,
                                             "description": "What terms are worth to a party."}
        worth = f"{{$' · worth ' + $text($round(${name}_value($actor, $it.terms), 1)) + ' to you'}}"
    if config.breach.on_breach:
        fragment["blocks"] = {f"{name}_on_breach": {"args": ["deal", "duty", "breacher", "victim", "terms"],
                                                    "do": list(config.breach.on_breach), "description": "Runs when a duty is breached."}}
    _actions(name, config, parties, agents, fragment)
    table = "$it.status == open and ($it.sender == $actor.id or $actor.id in $it.recipients)"
    fragment["views"].update({
        f"{name}_table": {"for": agents, "title": "Offers on the table", "of": offer, "where": table, "empty": "No open offers.",
                          "show": f"[{{id}}] from {{$entity($it.sender).name}} to {{$join($map($it.recipients, $entity($it).name))}}: "
                                  f"{{$terms_text($it.terms, '{name}')}}{worth}{{$' · ' + $text($it.note) if $it.note != '' else ''}}"},
        f"{name}_deals": {"for": agents, "title": "Your deals", "of": deal, "where": "$actor.id in $it.parties",
                          "show": f"[{{id}}] signed round {{signed}}: {{$terms_text($it.terms, '{name}')}} · {{status}}, {{breaches}} breach(es)"},
        f"{name}_duties": {"for": agents, "title": "Duties under your deals", "of": duty, "sort": "$it.due", "limit": 12,
                           "where": "$it.status == open and ($it.by == $actor.id or $it.to == $actor.id)",
                           "show": "[{id}] {$entity($it.by).name} {$'pays' if $it.kind == pay else 'delivers'} {amount} {asset} "
                                   "to {$entity($it.to).name} by round {due}{$' — yours to fulfil' if $it.manual and $it.by == $actor.id else ''}"}})
    if config.reservation is not None:
        fragment["views"][f"{name}_walk_away"] = {"for": agents, "title": "Private", "bullet": False,
                                                  "when": f"not $world.{name}_closed",
                                                  "show": f"Your walk-away value: {{{name}_reservation}}"}
    return fragment


def _actions(name: str, config: NegotiationConfig, parties: List[str], agents: List[str], fragment: Dict[str, Any]) -> None:
    offer, closed = f"{name}_offer", f"not $world.{name}_closed"
    issues = {issue: _issue_param(spec) for issue, spec in config.issues.items()}
    terms = {issue: f"$params.{issue}" for issue in config.issues}
    note = {"type": "text", "max_len": 280, "default": "", "description": "Optional short message."}
    open_to_me = "$it.status == open and $actor.id in $it.recipients"
    actions = fragment["actions"]
    if "propose" in config.actions:
        if config.coalition:
            target: Dict[str, Any] = {"recipients": {"type": "list", "of": parties[0], "where": "$it.id != $actor.id", "min_items": 1,
                                               "description": "Parties the offer goes to; all must accept."}}
            recipients = "$params.recipients"
        else:
            param, ref = choice_param(parties, "$it.id != $actor.id", "Party the offer goes to.")
            target, recipients = {"to": param}, ref.format(name="to")
        actions[f"{name}_propose"] = {
            "by": agents, "description": "Put a full offer on the table.", "when": [{"expr": closed, "why": "A deal has been signed."}],
            "params": {**target, **issues, "note": note},
            "do": [{"agreements": name, "action": "propose", "who": "$actor", "to": recipients, "terms": terms, "note": "$params.note"}],
            "outcome": f"Offer made: {{$terms_text({_terms_literal(config)}, '{name}')}}."}
    if "counter" in config.actions:
        actions[f"{name}_counter"] = {
            "by": agents, "description": "Answer an offer made to you with different terms (it replaces that offer).",
            "params": {"offer": {"type": "entity", "of": offer, "where": f"{open_to_me} and $it.depth < {config.max_depth}"},
                       **issues, "note": note},
            "do": [{"agreements": name, "action": "counter", "who": "$actor", "offer": "$params.offer", "terms": terms,
                    "note": "$params.note"}],
            "outcome": f"Counter-offer made: {{$terms_text({_terms_literal(config)}, '{name}')}}."}
    for tool, where, description in (
            ("accept", f"{open_to_me} and not ($actor.id in $it.accepted_by)", "Accept an offer made to you; it binds you once everyone it went to accepts."),
            ("reject", open_to_me, "Turn down an offer made to you."),
            ("withdraw", "$it.status == open and $it.sender == $actor.id", "Take back an offer you made.")):
        if tool in config.actions:
            actions[f"{name}_{tool}"] = {
                "by": agents, "description": description,
                "params": {"offer": {"type": "entity", "of": offer, "where": where}},
                "do": [{"agreements": name, "action": tool, "who": "$actor", "offer": "$params.offer"}]}
    if "fulfill" in config.actions and any(o.manual for o in config.obligations):
        actions[f"{name}_fulfill"] = {
            "by": agents, "description": "Pay or deliver an installment you owe under a deal, before its due round ends.",
            "params": {"duty": {"type": "entity", "of": f"{name}_duty",
                                "where": "$it.status == open and $it.manual and $it.by == $actor.id"}},
            "do": [{"agreements": name, "action": "fulfill", "who": "$actor", "duty": "$params.duty"}],
            "outcome": "Done: {$params.duty.amount} {$params.duty.asset} to {$entity($params.duty.to).name}."}


def _terms_literal(config: NegotiationConfig) -> str:
    return "{" + ", ".join(f"'{issue}': $params.{issue}" for issue in config.issues) + "}"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def terms_text(config: NegotiationConfig, terms: Mapping[str, Any]) -> str:
    parts = []
    for issue, spec in config.issues.items():
        value = terms.get(issue)
        shown = money(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
        parts.append(f"{issue} {shown}{spec.unit if spec.unit in ('%',) else (' ' + spec.unit if spec.unit else '')}")
    return ", ".join(parts)


@function("terms_text(terms, negotiation)", "Terms of an offer or deal as plain words, with units.", min_args=2, max_args=2)
def _terms_text(call: Call) -> str:
    world: Any = call.scope.world
    terms = call.arg(0)
    if not isinstance(terms, Mapping):
        raise ExprError(f"$terms_text: terms must be a map, got {terms!r}", call.source)
    try:
        return terms_text(config_of(world, str(call.arg(1)), NEGOTIATION, call.source), terms)
    except RunError as exc:
        raise ExprError(f"$terms_text: {exc.args[0]}", call.source) from None


def _stat(world: Any, name: str, key: str, delta: float) -> None:
    bump(world, f"{name}_stats", key, delta)


def _eval(runner: Any, value: Any, vars: Dict[str, Any], where: str) -> Any:
    try:
        return runner.eval(value, vars)
    except ExprError as exc:
        raise RunError(str(exc), where) from None


def _deadline(runner: Any, config: NegotiationConfig, name: str) -> Optional[int]:
    if config.deadline is None:
        return None
    return whole(_eval(runner, config.deadline, {}, f"mechanisms.{name}.deadline"), f"mechanisms.{name}.deadline", "deadline")


def _check_terms(config: NegotiationConfig, terms: Any) -> Dict[str, Any]:
    if not isinstance(terms, Mapping) or set(terms) != set(config.issues):
        raise Abort(f"An offer sets every issue: {', '.join(config.issues)}.")
    clean: Dict[str, Any] = {}
    for issue, spec in config.issues.items():
        value = terms[issue]
        if spec.type == "enum":
            if value not in (spec.values or []):
                raise Abort(f"{issue} must be one of {', '.join(map(str, spec.values or []))}.")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise Abort(f"{issue} must be a number.")
            if spec.type == "int" and float(value) != int(value):
                raise Abort(f"{issue} must be a whole number.")
            if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
                raise Abort(f"{issue} must be between {money(spec.min)} and {money(spec.max)}.")
            value = int(value) if spec.type == "int" else value
        clean[issue] = value
    return clean


@family_action("agreements", ("negotiation",), "counter", keys=("who", "offer", "terms", "note"),
               required=("who", "offer", "terms"), was=("propose_terms",),
               example='{"agreements": "trade", "action": "counter", "who": "$actor", "offer": "$params.offer", '
                       '"terms": {"tariff": 12, "quota": 150}}  (answer an offer made to you with other terms; it replaces that offer)')
@family_action("agreements", ("negotiation",), "propose", keys=("who", "to", "terms", "note"), required=("who", "to", "terms"),
               was=("propose_terms",),
               example='{"agreements": "trade", "action": "propose", "who": "$actor", "to": "$params.to", '
                       '"terms": {"tariff": 10, "quota": 200}}  (an offer to a party; a list of parties for a coalition)')
def _offer(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    """A new offer (`propose`) or one answering an offer made to the sender (`counter`)."""
    world = runner.world
    name = effect["agreements"]
    config: NegotiationConfig = config_of(world, name, NEGOTIATION, where)
    sender = entity_of(world, runner.eval(effect["who"], vars), where, "a sender")
    if world.props.get(f"{name}_closed"):
        raise Abort("The negotiation is closed: a deal has been signed.")
    deadline = _deadline(runner, config, name)
    if deadline is not None and world.round > deadline:
        raise Abort(f"The deadline (round {deadline}) has passed.")
    terms = _check_terms(config, runner.eval(effect["terms"], vars))
    depth = 0
    if effect["action"] == "counter":
        answered = entity_of(world, runner.eval(effect["offer"], vars), where, "an offer")
        p = props(answered)
        if answered.entity_type != f"{name}_offer" or p["status"] != "open" or sender.id not in p["recipients"]:
            raise Abort("You can only counter an open offer made to you.")
        if int(p["depth"]) >= config.max_depth:
            raise Abort(f"Counter-offers can go {config.max_depth} deep; accept or reject this one.")
        recipients = [p["sender"]] + [r for r in p["recipients"] if r != sender.id]
        depth = int(p["depth"]) + 1
        world.set_prop(answered, "status", "countered")
        _stat(world, name, "counters", 1)
    else:
        recipients = to_ids(runner.eval(effect.get("to"), vars))
    if not recipients or sender.id in recipients or len(set(recipients)) != len(recipients):
        raise Abort("An offer goes to other parties, each once.")
    if not config.coalition and len(recipients) != 1:
        raise Abort("Offers here go to exactly one party.")
    for rid in recipients:
        other = entity_of(world, rid, where, "a party")
        if not any(world.is_a(other.entity_type, t) for t in type_list(config.who)):
            raise Abort(f"{other.name} is not a party to this negotiation.")
    duplicate = [o for o in world.entities_of(f"{name}_offer") if props(o)["status"] == "open" and props(o)["sender"] == sender.id
                 and sorted(props(o)["recipients"]) == sorted(recipients)]
    if duplicate:
        raise Abort(f"Your offer {duplicate[0].id} to them is still open; withdraw it or wait for an answer.")
    note = runner.eval(effect.get("note", ""), vars) or ""
    made = world.create(f"{name}_offer", None, f"Offer from {sender.name}",
                        {"sender": sender.id, "recipients": recipients, "terms": terms, "depth": depth, "made": world.round,
                         "expires": world.round + config.expires if config.expires else 0, "note": note}, None, world.scope(), where)
    _stat(world, name, "offers", 1)
    emit_to(world, f"{name}_offer", f"{sender.name} offers {terms_text(config, terms)} [{made.id}].", recipients,
            {"offer": made.id}, why=f"{sender.name} made you an offer.")


def _answer_offer(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    """`accept`, `reject` or `withdraw` an open offer."""
    world = runner.world
    name = effect["agreements"]
    config: NegotiationConfig = config_of(world, name, NEGOTIATION, where)
    offer = entity_of(world, runner.eval(effect["offer"], vars), where, "an offer")
    who = entity_of(world, runner.eval(effect["who"], vars), where, "a party")
    answer = effect["action"]
    p = props(offer)
    if offer.entity_type != f"{name}_offer" or p["status"] != "open":
        raise Abort("That offer is no longer open.")
    if answer == "withdraw":
        if p["sender"] != who.id:
            raise Abort("Only its sender can withdraw an offer.")
        world.set_prop(offer, "status", "withdrawn")
        _stat(world, name, "withdrawn", 1)
        emit_to(world, f"{name}_withdrawn", f"{who.name} withdrew offer {offer.id}.", p["recipients"])
        return
    if who.id not in p["recipients"]:
        raise Abort("That offer was not made to you.")
    if answer == "reject":
        world.set_prop(offer, "status", "rejected")
        _stat(world, name, "rejected", 1)
        emit_to(world, f"{name}_rejected", f"{who.name} rejected your offer {offer.id}.", [p["sender"]], why=f"{who.name} rejected your offer.")
        return
    deadline = _deadline(runner, config, name)
    if deadline is not None and world.round > deadline:
        raise Abort(f"The deadline (round {deadline}) has passed.")
    if who.id in p["accepted_by"]:
        raise Abort("You already accepted it.")
    accepted = list(p["accepted_by"]) + [who.id]
    world.set_prop(offer, "accepted_by", accepted)
    if set(accepted) != set(p["recipients"]):
        emit_to(world, f"{name}_accepted", f"{who.name} accepted offer {offer.id}; waiting for the others.", [p["sender"], *p["recipients"]])
        return
    world.set_prop(offer, "status", "accepted")
    _sign(runner, name, config, offer, where)


def _register_answers() -> None:
    for answer, doc in (("accept", "accept an offer made to you; it binds once everyone it went to accepts"),
                        ("reject", "turn down an offer made to you"), ("withdraw", "take back an offer you made")):
        family_action("agreements", ("negotiation",), answer, keys=("who", "offer"), required=("who", "offer"),
                      was=("answer_offer",),
                      example=f'{{"agreements": "trade", "action": "{answer}", "who": "$actor", "offer": "$params.offer"}}  '
                              f'({doc})')(_answer_offer)


_register_answers()


def _sign(runner: Any, name: str, config: NegotiationConfig, offer: Any, where: str) -> None:
    world = runner.world
    p = props(offer)
    parties = [p["sender"], *p["recipients"]]
    deal = world.create(f"{name}_deal", None, f"Deal {offer.id}",
                        {"parties": parties, "proposer": p["sender"], "acceptors": list(p["recipients"]), "terms": dict(p["terms"]),
                         "signed": world.round, "offer": offer.id}, None, world.scope(), where)
    roles = {"proposer": world.entities[p["sender"]], "acceptor": world.entities[p["recipients"][0]],
             "parties": [world.entities[i] for i in parties], "terms": dict(p["terms"]), "deal": deal}
    count = 0
    for index, spec in enumerate(config.obligations):
        path = f"mechanisms.{name}.obligations[{index}]"
        debtor = entity_of(world, _eval(runner, spec.from_, roles, f"{path}.from"), f"{path}.from", "an obligor")
        creditor = entity_of(world, _eval(runner, spec.to, roles, f"{path}.to"), f"{path}.to", "a recipient")
        times = min(MAX_DUTIES, whole(_eval(runner, spec.times, roles, f"{path}.times"), f"{path}.times", "times"))
        kind, asset = ("pay", spec.pay) if spec.pay is not None else ("give", spec.give)
        asset = str(_eval(runner, asset, roles, f"{path}.{kind}"))
        for k in range(1, times + 1):
            value = _eval(runner, spec.amount, {**roles, "k": k}, f"{path}.amount")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise RunError(f"amount must be a number ≥ 0, got {value!r}", f"{path}.amount")
            if value == 0:
                continue
            world.create(f"{name}_duty", None, spec.label or f"{kind} {asset}",
                         {"deal": deal.id, "label": spec.label, "by": debtor.id, "to": creditor.id, "kind": kind, "asset": asset,
                          "amount": value, "due": world.round + spec.start + (k - 1) * spec.every, "manual": spec.manual},
                         None, world.scope(), where)
            count += 1
    if not count:
        world.set_prop(deal, "status", "completed")
    _stat(world, name, "deals", 1)
    if config.once:
        world.set_world(f"{name}_closed", True)
        for other in world.entities_of(f"{name}_offer"):
            if props(other)["status"] == "open":
                world.set_prop(other, "status", "expired")
    emit_to(world, f"{name}_deal", f"Deal signed ({deal.id}): {terms_text(config, p['terms'])}.", parties, {"deal": deal.id},
            why="A deal was signed.")


def _perform(world: Any, duty: Any, where: str) -> None:
    p = props(duty)
    debtor, creditor = entity_of(world, p["by"], where, "the obligor"), entity_of(world, p["to"], where, "the recipient")
    if p["kind"] == "pay":
        move_money(world, p["asset"], debtor, creditor, float(p["amount"]), where, use_credit=False)
    else:
        move_items(world, p["asset"], debtor, creditor, whole(p["amount"], where, "a delivery"), where)
    world.set_prop(duty, "status", "done")


@family_action("agreements", ("negotiation",), "fulfill", keys=("who", "duty"), required=("who", "duty"), was=("fulfill_duty",),
               example='{"agreements": "trade", "action": "fulfill", "who": "$actor", "duty": "$params.duty"}  '
                       '(pay or deliver an installment you owe)')
def _fulfill_duty(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    duty = entity_of(world, runner.eval(effect["duty"], vars), where, "a duty")
    who = entity_of(world, runner.eval(effect["who"], vars), where, "a party")
    if duty.entity_type != f"{name}_duty" or props(duty)["status"] != "open":
        raise Abort("That duty is not open.")
    if props(duty)["by"] != who.id:
        raise Abort("That duty is not yours to fulfil.")
    _perform(world, duty, where)
    _stat(world, name, "duties_done", 1)
    emit_to(world, f"{name}_fulfilled", f"{who.name} fulfilled {duty.id}: {props(duty)['amount']} {props(duty)['asset']}.", [props(duty)["to"]])


def _breach(runner: Any, name: str, config: NegotiationConfig, duty: Any, reason: str, where: str) -> None:
    world = runner.world
    p = props(duty)
    deal = world.entities[p["deal"]]
    breacher, victim = world.entities.get(p["by"]), world.entities.get(p["to"])
    world.set_prop(duty, "status", "breached")
    world.set_prop(deal, "breaches", int(props(deal)["breaches"]) + 1)
    _stat(world, name, "breaches", 1)
    if breacher is None or victim is None or not breacher.alive or not victim.alive:
        return
    vars = {"deal": deal, "duty": duty, "breacher": breacher, "victim": victim, "terms": dict(props(deal)["terms"])}
    penalty = _eval(runner, config.breach.penalty, vars, f"mechanisms.{name}.breach.penalty")
    paid = 0.0
    if isinstance(penalty, (int, float)) and not isinstance(penalty, bool) and penalty > 0:
        currency = config.breach.currency or (p["asset"] if p["kind"] == "pay" else next(iter(_currencies(world)), None))
        if currency is None:
            raise RunError("a breach penalty needs a currency: declare a ledger or set breach.currency", f"mechanisms.{name}.breach")
        paid = min(float(penalty), max(0.0, balance(world, breacher, currency, where)))
        move_money(world, currency, breacher, victim, paid, where, use_credit=False)
        world.set_prop(duty, "penalty", paid)
        _stat(world, name, "penalties", paid)
    text = f"{breacher.name} breached {duty.id} ({reason})" + (f" and paid a penalty of {money(paid)}." if paid else ".")
    if config.breach.terminate:
        world.set_prop(deal, "status", "terminated")
        for other in world.entities_of(f"{name}_duty"):
            if props(other)["deal"] == deal.id and props(other)["status"] == "open":
                world.set_prop(other, "status", "cancelled")
        text += f" Deal {deal.id} is terminated."
    emit_to(world, f"{name}_breach", text, list(props(deal)["parties"]), {"duty": duty.id, "penalty": paid},
            why=f"A deal was breached by {breacher.name}.")
    if config.breach.on_breach:
        run_hook(runner, f"{name}_on_breach", vars, f"mechanisms.{name}.breach.on_breach")


def _currencies(world: Any) -> List[str]:
    return list(assets(world).currencies)


@family_action("agreements", ("negotiation",), "tick", internal=True, was=("negotiation_tick",),
               example='{"agreements": "trade", "action": "tick"}  (expire offers, carry out due installments, detect breaches)')
def _negotiation_tick(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: NegotiationConfig = config_of(world, name, NEGOTIATION, where)
    deadline = _deadline(runner, config, name)
    for offer in world.entities_of(f"{name}_offer"):
        p = props(offer)
        if p["status"] == "open" and ((p["expires"] and p["expires"] <= world.round) or (deadline is not None and world.round >= deadline)):
            world.set_prop(offer, "status", "expired")
            _stat(world, name, "expired", 1)
            emit_to(world, f"{name}_expired", f"Offer {offer.id} expired unanswered.", [p["sender"], *p["recipients"]])
    for duty in sorted(world.entities_of(f"{name}_duty"), key=lambda d: int(props(d)["due"])):
        p = props(duty)
        if p["status"] != "open" or int(p["due"]) > world.round:
            continue
        if p["manual"]:
            _breach(runner, name, config, duty, "not fulfilled in time", where)
            continue
        mark = world.journal.mark()
        try:
            _perform(world, duty, where)
            _stat(world, name, "duties_done", 1)
        except Abort as exc:
            world.journal.rollback(mark)
            _breach(runner, name, config, duty, exc.reason.rstrip("."), where)
    for deal in world.entities_of(f"{name}_deal"):
        if props(deal)["status"] == "active" and not any(
                props(d)["deal"] == deal.id and props(d)["status"] == "open" for d in world.entities_of(f"{name}_duty")):
            world.set_prop(deal, "status", "completed")
            emit_to(world, f"{name}_completed", f"Deal {deal.id} is complete.", list(props(deal)["parties"]))
