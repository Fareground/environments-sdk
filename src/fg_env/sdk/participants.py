"""Participants: whoever takes the turns. Anything callable with a :class:`Wake` works.

Built in:

* ``"random"`` — a seeded agent that takes random legal actions with valid arguments.
* ``"idle"`` — ends every turn without acting.
* ``"policy:<name>"`` (or just the policy's name) — a coded policy declared in the contract.
* :func:`anthropic` / :func:`openai` — LLM participants driving the turn with tool calls,
  given your own client object (no SDK dependency is imposed).
"""
from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Union

from .expr import ExprError, compile_expr, resolve, truthy
from .session import Wake

if TYPE_CHECKING:
    from .contract import Contract

__all__ = ["Participant", "RandomAgent", "Idle", "PolicyAgent", "anthropic", "openai", "resolve_participant"]

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
            result = wake.call(tool.name, sample_args(tool.input_schema, rng))
            if result.ended:
                return
        if not wake.done:
            wake.end()

    def __repr__(self) -> str:
        return f"RandomAgent(seed={self.seed})"


def sample_args(schema: Mapping[str, Any], rng: random.Random) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
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
            args[name] = rng.randint(int(low), int(high)) if kind == "integer" else round(rng.uniform(low, high), 2)
        elif kind == "boolean":
            args[name] = rng.random() < 0.5
        elif kind == "string":
            args[name] = "ok"
    return args


class Idle:
    """Never acts."""

    def __call__(self, wake: Wake) -> None:
        wake.end()

    def __repr__(self) -> str:
        return "Idle()"


class PolicyAgent:
    """Runs a coded policy from the contract's ``policies`` section."""

    def __init__(self, contract: "Contract", name: str, seed: int = 0):
        if name not in contract.policies:
            raise ValueError(f"no policy '{name}' in the contract (policies: {', '.join(contract.policies) or 'none'})")
        self.name = name
        self.spec = contract.policies[name]
        self.seed = seed

    def __call__(self, wake: Wake) -> None:
        rng = random.Random(_seed_for(self.seed, wake))
        turn = wake._turn
        while not wake.done:
            acted = False
            for index, rule in enumerate(self.spec.rules):
                path = f"policies.{self.name}.rules[{index}]"
                scope = turn.env.world.scope(actor=turn.actor)
                try:
                    if rule.when is not None and not truthy(compile_expr(rule.when)(scope)):
                        continue
                    if rule.chance is not None:
                        p = compile_expr(rule.chance)(scope) if isinstance(rule.chance, str) else rule.chance
                        if isinstance(p, bool) or not isinstance(p, (int, float)):
                            raise ExprError(f"chance must be a number, got {p!r}", str(rule.chance))
                        if rng.random() >= p:
                            continue
                    if rule.do == "pass":
                        wake.end()
                        return
                    args = resolve(rule.with_, scope)
                except ExprError as exc:
                    from .errors import RunError

                    raise RunError(str(exc), path) from None
                args = {k: (v.id if hasattr(v, "entity_type") else v) for k, v in args.items()}
                if rule.do not in {t.name for t in wake.tools}:
                    continue
                result = wake.call(rule.do, args)
                if result.ok:
                    acted = True
                    break
            if not acted or not self.spec.repeat:
                break
        if not wake.done:
            wake.end()

    def __repr__(self) -> str:
        return f"PolicyAgent({self.name!r})"


def resolve_participant(value: Any, contract: "Contract", seed: int) -> Participant:
    if callable(value):
        return value
    if isinstance(value, str):
        if value == "random":
            return RandomAgent(seed)
        if value == "idle":
            return Idle()
        name = value[len("policy:"):] if value.startswith("policy:") else value
        if name in contract.policies:
            return PolicyAgent(contract, name, seed)
    raise ValueError(
        f"unknown participant {value!r}: use a callable, 'random', 'idle', or 'policy:<name>' "
        f"(policies: {', '.join(contract.policies) or 'none'})"
    )


# ---------------------------------------------------------------------------
# LLM participants
# ---------------------------------------------------------------------------


class _LLMUsage:
    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0

    def to_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


class _Anthropic:
    def __init__(self, client: Any, model: str, max_tokens: int, max_steps: int, system: str):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.max_steps = max_steps
        self.system = system
        self.usage = _LLMUsage()

    def __call__(self, wake: Wake) -> None:
        system = [{"type": "text", "text": (self.system + "\n\n" if self.system else "") + wake.brief,
                   "cache_control": {"type": "ephemeral"}}]
        messages: List[Dict[str, Any]] = [{"role": "user", "content": wake.update}]
        for _ in range(self.max_steps):
            if wake.done:
                return
            tools = wake.tools_for("anthropic")
            response = self.client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                                   system=system, tools=tools, messages=messages)
            self._count(getattr(response, "usage", None))
            calls = [block for block in response.content if getattr(block, "type", "") == "tool_use"]
            messages.append({"role": "assistant", "content": [_block_dict(b) for b in response.content]})
            if not calls:
                break
            results = []
            for block in calls:
                result = wake.call(block.name, dict(block.input or {}))
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": result.text,
                                "is_error": not result.ok})
            messages.append({"role": "user", "content": results})
        if not wake.done:
            wake.end()

    def _count(self, usage: Any) -> None:
        self.usage.calls += 1
        if usage is None:
            return
        self.usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.usage.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.usage.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0


def _block_dict(block: Any) -> Dict[str, Any]:
    kind = getattr(block, "type", "")
    if kind == "text":
        return {"type": "text", "text": block.text}
    if kind == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if hasattr(block, "model_dump"):
        return block.model_dump()
    return dict(block)


def anthropic(client: Any, model: str, *, max_tokens: int = 1024, max_steps: int = 8, system: str = "") -> Participant:
    """An LLM participant using an ``anthropic.Anthropic()`` client. The brief is prompt-cached.

    ``participant.usage`` accumulates token counts across turns.
    """
    return _Anthropic(client, model, max_tokens, max_steps, system)


class _OpenAI:
    def __init__(self, client: Any, model: str, max_steps: int, system: str):
        self.client = client
        self.model = model
        self.max_steps = max_steps
        self.system = system
        self.usage = _LLMUsage()

    def __call__(self, wake: Wake) -> None:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": (self.system + "\n\n" if self.system else "") + wake.brief},
            {"role": "user", "content": wake.update},
        ]
        for _ in range(self.max_steps):
            if wake.done:
                return
            response = self.client.chat.completions.create(model=self.model, messages=messages,
                                                            tools=wake.tools_for("openai"))
            self.usage.calls += 1
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.usage.input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                self.usage.output_tokens += getattr(usage, "completion_tokens", 0) or 0
            message = response.choices[0].message
            calls = list(getattr(message, "tool_calls", None) or [])
            messages.append({"role": "assistant", "content": message.content or "",
                             "tool_calls": [{"id": c.id, "type": "function",
                                             "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                            for c in calls] or None})
            if not calls:
                break
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                result = wake.call(c.function.name, args) if isinstance(args, dict) else None
                text = result.text if result else "arguments were not valid JSON; call again"
                messages.append({"role": "tool", "tool_call_id": c.id, "content": text})
        if not wake.done:
            wake.end()


def openai(client: Any, model: str, *, max_steps: int = 8, system: str = "") -> Participant:
    """An LLM participant using an ``openai.OpenAI()``-compatible client (chat completions + tools)."""
    return _OpenAI(client, model, max_steps, system)


ParticipantsArg = Union[None, Participant, str, Mapping[str, Any]]
