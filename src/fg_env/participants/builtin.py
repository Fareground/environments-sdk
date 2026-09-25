"""The built-in participants — ``"random"``, ``"idle"``, ``"policy:<name>"`` and :func:`replay` — and what a
participant named in a run's ``participants`` resolves to."""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

from ..effects.runner import each_items
from ..errors import ContractError, Issue, RunError
from ..expr import ExprError, compile_expr, resolve, truthy
from ..runtime.facts import PolicyRule
from ..runtime.session import Wake
from ..sampling.probability import is_probability
from ..sampling.seeds import LazyStream
from .llm import PROVIDERS, official_participant

if TYPE_CHECKING:
    from ..contract import Contract, PolicySpec

__all__ = ["Participant", "RandomAgent", "Idle", "PolicyAgent", "policy_names", "replay", "resolve_participant"]

Participant = Callable[[Wake], Any]


def _seed_for(base: int, wake: Wake) -> int:
    import hashlib

    digest = hashlib.sha256(repr((base, wake.entity_id, wake.round, wake.stage)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class RandomAgent:
    """Takes up to ``actions`` random legal actions per turn with valid random arguments."""

    def __init__(self, seed: int = 0, actions: int = 1, pass_rate: float = 0.0):
        self.seed = seed
        self.actions = actions
        self.pass_rate = pass_rate

    def __call__(self, wake: Wake) -> None:
        rng = random.Random(_seed_for(self.seed, wake))
        for _ in range(self.actions):
            acts = [t for t in wake.tools if t.kind == "act"]
            if not acts or wake.done or rng.random() < self.pass_rate:
                break
            tool = rng.choice(acts)
            result = wake.call(tool.name, _fill_dependent(wake, tool.name, sample_args(tool.input_schema, rng), rng))
            if result.ended:
                return
        if not wake.done:
            wake.end()

    def __repr__(self) -> str:
        return f"RandomAgent(seed={self.seed})"


#: What random agents write for free text.
SAMPLE_TEXT = "I would like to try this and see what happens next."


def sample_args(schema: Mapping[str, Any], rng: random.Random) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for name, prop in (schema.get("properties") or {}).items():
        if "enum" in prop:
            if prop["enum"]:
                args[name] = rng.choice(prop["enum"])
            continue
        kind = prop.get("type")
        if kind in ("integer", "number"):
            low = prop.get("minimum", 0)
            high = prop.get("maximum", max(low, 0) + 10)
            if high < low:
                continue
            step = prop.get("multipleOf")
            if step:
                first, last = math.ceil(low / step), math.floor(high / step)
                if last >= first:
                    args[name] = rng.randint(first, last) * step
                continue
            args[name] = rng.randint(int(low), int(high)) if kind == "integer" else round(rng.uniform(low, high), 2)
        elif kind == "boolean":
            args[name] = rng.random() < 0.5
        elif kind == "string":  # a real sentence, so a rule that reads the text can pass
            args[name] = SAMPLE_TEXT[:prop.get("maxLength", len(SAMPLE_TEXT))]
        elif kind == "array":
            items = _sample_list(prop, rng)
            if items is not None:
                args[name] = items
    return args


def _fill_dependent(wake: Wake, tool: str, args: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """``args`` with each choice that depends on earlier arguments (``where: $it.id != $params.a.id``, ``values:
    $params.army.exits``) drawn from the choices that qualify given them: a schema can only list every candidate."""
    from ..errors import RunError

    turn = wake._turn
    actions = turn.env.actions
    if tool not in turn.env.contract.actions:
        return args
    with turn.gate:
        try:
            return actions.fill_dependent(turn.actor, tool, args, rng.choice)
        except RunError:
            return args  # the call reports the broken rule at its path


def _sample_list(prop: Mapping[str, Any], rng: random.Random) -> list[Any] | None:
    item = prop.get("items") or {}
    low = int(prop.get("minItems", 0))
    high = max(low, min(int(prop.get("maxItems", low + 3)), low + 3))
    unique = bool(prop.get("uniqueItems"))
    pool = item.get("enum")
    if pool is not None:
        if unique:
            if low > len(pool):
                return None
            return rng.sample(list(pool), rng.randint(low, min(high, len(pool))))
        return [rng.choice(pool) for _ in range(rng.randint(low, high))] if pool else ([] if low == 0 else None)
    out: list[Any] = []
    for _ in range(rng.randint(low, high)):
        value = sample_args({"properties": {"x": item}}, rng).get("x")
        if value is not None and not (unique and value in out):
            out.append(value)
    return out if len(out) >= low else None


class Idle:
    """Never acts."""

    def __call__(self, wake: Wake) -> None:
        return None  # the turn ends as it returns: no `end_turn`, which a stage that must be acted in would refuse

    def __repr__(self) -> str:
        return "Idle()"


def policy_names(contract: Contract) -> list[str]:
    """Every policy any type declares, once each."""
    return list(dict.fromkeys(name for spec in contract.types.values() for name in spec.policies))


class PolicyAgent:
    """Runs a coded policy of the acting agent's type (``types.<type>.policies``, its ancestors' too): the first rule
    whose condition holds, whose action is legal and whose arguments are valid is taken. ``kinds`` are types it
    will play, checked now (others are checked at their first turn)."""

    #: Before a rule acts, also evaluate the later rules whose action is legal, so a broken rule that an earlier one
    #: always beats is still reported. Check's smoke play sets it; the policy acts the same either way.
    _probe_later = False

    def __init__(self, contract: Contract, name: str, seed: int = 0, kinds: tuple[str, ...] = ()):
        if name not in policy_names(contract):
            raise ValueError(f"no policy '{name}' in the contract (policies: "
                             f"{', '.join(policy_names(contract)) or 'none'})")
        self.contract, self.name, self.seed = contract, name, seed
        for kind in kinds:
            self._policy(kind)

    def _policy(self, kind: str) -> tuple[PolicySpec, str]:
        """The spec agents of ``kind`` play under this name, and its path."""
        found = self.contract.policies_of(kind).get(self.name)
        if found is None:
            own = ", ".join(self.contract.policies_of(kind)) or "none"
            raise ValueError(f"'{kind}' agents have no policy '{self.name}' (their policies: {own}): declare it under "
                             f"types.{kind}.policies, or play another participant")
        owner, spec = found
        return spec, f"types.{owner}.policies.{self.name}"

    def __call__(self, wake: Wake) -> None:
        rng: Any = LazyStream(lambda: random.Random(_seed_for(self.seed, wake)))  # only `chance` rules draw
        turn = wake._turn
        spec, base = self._policy(turn.actor.entity_type)
        while not wake.done:
            acted = False
            for index, rule in enumerate(spec.rules):
                path = f"{base}.rules[{index}]"
                scope = turn.env.world.evaluation.scope(actor=turn.actor, viewer=turn.actor)
                if rule.each is None:
                    outcome = self._try(wake, spec, base, index, scope, rng)
                    if outcome == "passed":
                        return
                    if outcome == "acted":
                        acted = True
                        break
                    continue
                for here in self._each(turn, rule.each, scope, path):
                    if wake.done:
                        return
                    outcome = self._try(wake, spec, base, index, here, rng)
                    if outcome == "passed":
                        return
                    acted = acted or outcome == "acted"
                if acted:
                    break
            if not acted or not spec.repeat:
                break
        if not wake.done:
            wake.end()

    @staticmethod
    def _each(turn: Any, each: str, scope: Any, path: str) -> list[Any]:
        """A scope per item of a rule's `each` ($it, $i), read as the agent reads them (see expr/hidden.py)."""
        world, contract = turn.env.world, turn.env.contract
        if each in contract.types:
            return [scope.child(it=item, i=position) for position, item in enumerate(world.entities_of(each))]
        try:
            with turn.gate, turn.after_choices():
                items = each_items(compile_expr(each)(scope), world, f"{path}.each")
        except ExprError as exc:
            raise RunError(str(exc), f"{path}.each") from None
        return [scope.child(it=item, i=position) for position, item in enumerate(items)]

    def _try(self, wake: Wake, spec: PolicySpec, base: str, index: int, scope: Any, rng: Any) -> str:
        """Try one rule: "acted", "passed" (the turn ends), or "skipped"."""
        turn, rule, path = wake._turn, spec.rules[index], f"{base}.rules[{index}]"
        # Read the world as the agent's next choice meets it: in a sealed stage, after the choices it already made.
        with turn.gate, turn.after_choices():
            choice = self._choose(wake, spec, base, index, scope, rng)
        if choice is None:
            return "skipped"
        if isinstance(choice, str):  # "passed"
            wake.end()
            return choice
        args, problem = choice
        if problem is not None:  # arguments the action does not accept: the call is never made, but it was refused
            turn.env.facts.emit(PolicyRule(path, rule.do, problem, sent=False))
            return "skipped"
        result = wake.call(rule.do, args)
        if result.ok:
            turn.env.facts.emit(PolicyRule(path, rule.do))
            return "acted"
        turn.env.facts.emit(PolicyRule(path, rule.do, result.text))
        return "skipped"  # this rule does not fit right now; try the next one

    def _choose(self, wake: Wake, spec: PolicySpec, base: str, index: int, scope: Any,
                rng: Any) -> None | str | tuple[dict[str, Any], str | None]:
        """Whether a rule applies now: None (it does not), "passed", or its arguments and why they are invalid."""
        turn, rule, path = wake._turn, spec.rules[index], f"{base}.rules[{index}]"
        if rule.do != "pass" and not turn.env.contract.can_take(turn.actor.entity_type, rule.do):
            return None  # a rule for another agent type: its `when` may read what this type does not have
        try:
            if rule.when is not None and not truthy(compile_expr(rule.when)(scope)):
                return None
            if rule.chance is not None:
                p = compile_expr(rule.chance)(scope) if isinstance(rule.chance, str) else rule.chance
                if not is_probability(p):
                    raise ExprError(f"chance must be a number from 0 to 1, got {p!r}", str(rule.chance))
                if rng.random() >= p:
                    return None
            if rule.do == "pass":
                if self._probe_later:
                    self._probe(turn, spec, base, index)
                return "passed"
            # legality without building tool schemas (coded crowds never read them), before `with`, whose arguments
            # may only exist while the action is legal
            if wake.done or not turn._allows(rule.do):
                return None
            args = resolve(rule.with_, scope)
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        args = {k: _as_ids(v) for k, v in args.items()}
        _, problem = turn.env.actions.validate(turn.actor, rule.do, args)
        if problem is None and self._probe_later:
            self._probe(turn, spec, base, index)
        return args, problem

    def _probe(self, turn: Any, spec: PolicySpec, base: str, index: int) -> None:
        """Evaluate each rule after ``index`` whose action is legal now, as a turn would reach it — `when`, then
        `chance` and `with` if it holds — for the first of its `each` items. Nothing acts, and draws come from a stream
        of their own."""
        scope = turn.env.world.evaluation.scope(actor=turn.actor, viewer=turn.actor)
        with turn.gate:
            legal = set(turn._legal()) | {"pass"}
        with turn.env.world.luck.using(random.Random(0)):
            for later in range(index + 1, len(spec.rules)):
                rule, path = spec.rules[later], f"{base}.rules[{later}]"
                if rule.do not in legal:
                    continue
                items = [scope] if rule.each is None else self._each(turn, rule.each, scope, path)[:1]
                for here in items:
                    try:
                        if rule.when is None or truthy(compile_expr(rule.when)(here)):
                            if isinstance(rule.chance, str):
                                compile_expr(rule.chance)(here)
                            resolve(rule.with_, here)
                    except ExprError as exc:
                        raise RunError(f"{exc} (evaluated while rules[{index}] acted first; a turn that reaches this "
                                       "rule fails the same way)", path) from None

    def __repr__(self) -> str:
        return f"PolicyAgent({self.name!r})"


def _as_ids(value: Any) -> Any:
    """A policy argument with entities (alone or in a list, e.g. a ranking from $top) given as their ids."""
    if isinstance(value, list):
        return [_as_ids(item) for item in value]
    return value.id if hasattr(value, "entity_type") else value


def resolve_participant(value: Any, contract: Contract, seed: int, path: str = "participants",
                        kinds: tuple[str, ...] = ()) -> Participant:
    """The participant ``value`` names; an unknown name raises :class:`~fg_env.ContractError` at ``path``. ``kinds``
    are the types it will play, so a policy they do not have is reported now."""
    if callable(value):
        return value
    if isinstance(value, str):
        if value == "random":
            return RandomAgent(seed)
        if value == "idle":
            return Idle()
        name = value[len("policy:"):] if value.startswith("policy:") else value
        if name in policy_names(contract):
            try:
                return PolicyAgent(contract, name, seed, kinds)
            except ValueError as exc:
                raise ContractError([Issue(path, str(exc))], title="participants are invalid") from None
        provider, sep, model = value.partition(":")
        if sep and provider in PROVIDERS:
            return official_participant(provider, model)
        from ..game.algorithms.participants import algorithm_participant

        algorithm = algorithm_participant(value, contract, seed)
        if algorithm is not None:
            return algorithm
    named = ["random", "idle", *(f"policy:{name}" for name in policy_names(contract))]
    hint = get_close_matches(str(value), named, n=1)
    raise ContractError([Issue(path, f"unknown participant {value!r}", (f"did you mean '{hint[0]}'? " if hint else "")
                               + "use a callable, 'random', 'idle', 'policy:<name>', 'anthropic:<model>', "
                                 "'openai:<model>', "
                               "or a game algorithm: 'mcts:<simulations>', 'ismcts:<simulations>', "
                               "'minimax[:<depth>]', "
                               "'cfr:<policy.json>' or 'cfr:<iterations>' (policies: "
                               f"{', '.join(policy_names(contract)) or 'none'})")],
                        title="participants are invalid")


def replay(recording: Any, fallback: Any = None) -> Participant:
    """A participant that plays a recorded run's steps again, turn by turn, checking every wake against the recording
    (``recording``: a result with exposures, its dict, a saved file, or a trace); the run it plays in must record
    exposures. On the first difference the run fails with the divergence, or — given ``fallback`` — that participant
    plays on. Usually you want ``fg_env.analysis.trace(recording).replay(contract)``, which also replays the host
    answers and compares the outcome."""
    from ..trace.rerun import Replayer

    return Replayer(recording, fallback)
