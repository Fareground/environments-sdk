"""Random valid contracts from a small grammar, one per seed, for property tests.

Agent types carry public bounded numbers, enums and lists, and private values planted as markers that no rule copies
anywhere (a number far from every other value, a text nobody could guess), so finding one in what another agent reads
is a leak, never a coincidence. Actions take typed parameters (number, int, bool, text, enum, an entity filtered by
`where`), have requirements and chances, and change bounded props and lists, transfer, schedule effects, create and
remove entities (the actor too). Stages are sequential or simultaneous, some repeating or checking the whole
turn; events run per entity with `$chance`.
"""
import random
from typing import Any

MOODS = ["calm", "keen", "wary"]


def marker_number(rng: random.Random) -> int:
    """A private number that no bound, count or public value comes near."""
    return rng.randint(7_000_000, 7_999_999)


def marker_text(rng: random.Random) -> str:
    return "zq" + "".join(rng.choice("bcdfghjkmnpqrstvwxz") for _ in range(8))


def _agent_type(rng: random.Random, name: str) -> dict[str, Any]:
    props: dict[str, Any] = {
        "cash": {"default": rng.randint(0, 20), "min": 0, "max": 100},
        "score": 0,
        "mood": {"type": "enum", "values": MOODS, "default": rng.choice(MOODS)},
        "tags": {"type": "list", "default": []},
        "seated": 0,
        "secret": {"type": "int", "default": 0, "private": True},
        "code": {"type": "text", "default": "", "private": True},
    }
    spec: dict[str, Any] = {"agent": True, "props": props}
    inspect = rng.choice([None, True, "$it.score >= $viewer.score"])
    if inspect is not None:
        spec["inspect"] = inspect
    return spec


def _param(rng: random.Random, agents: list[str]) -> dict[str, Any]:
    kind = rng.choice(["number", "int", "enum", "entity", "entity", "bool", "text"])
    if kind in ("bool", "text"):
        return {"type": kind}
    if kind == "number":
        low = rng.choice([0, 0.5, 1])
        return {"type": "number", "min": low, "max": low + rng.choice([1, 5, 30])}
    if kind == "int":
        return {"type": "int", "min": rng.choice([0, 1]), "max": rng.choice([1, 3, 10])}
    if kind == "enum":
        return {"type": "enum", "values": MOODS[: rng.randint(1, 3)]}
    of = rng.choice([*agents, "token"])
    where = rng.choice([None, "$it.id != $actor.id", "$it.cash >= 0"] if of != "token" else [None, "$it.value >= 0"])
    return {"type": "entity", "of": of, **({"where": where} if where else {})}


def _effects(rng: random.Random, params: dict[str, dict[str, Any]]) -> list[Any]:
    """Effects over the action's own parameters: every one a valid rule, some refused by bounds at run time."""
    numbers = [f"$params.{p}" for p, s in params.items() if s["type"] in ("number", "int")] or ["1"]
    targets = [(p, s["of"]) for p, s in params.items() if s["type"] == "entity"]
    enums = [p for p, s in params.items() if s["type"] == "enum"]
    texts = [p for p, s in params.items() if s["type"] == "text"]
    flags = [p for p, s in params.items() if s["type"] == "bool"]
    options = [
        lambda: f"$actor.cash += {rng.choice(numbers)}",
        lambda: f"$actor.cash -= {rng.choice(numbers)}",
        lambda: f"$actor.score += {rng.choice(numbers)}",
        lambda: "$actor.secret += 1",
        lambda: "$world.pot += 1",
        lambda: {"if": f"{rng.choice(numbers)} > 2", "then": ["$actor.score += 1"], "else": ["$actor.score -= 1"]},
        lambda: {"if": "$chance(0.5)", "then": ["$world.pot += 2"]},
        lambda: {"create": "token", "props": {"value": rng.choice(numbers)}},
        lambda: {"post": "log", "text": "'moved ' + $actor.name"},
        lambda: {"after": 1, "do": ["$world.pot += 1"]},
        lambda: {"if": "$chance(0.1)", "then": [{"remove": "$actor"}]},
    ]
    if enums:
        options.append(lambda: f"$actor.mood = $params.{rng.choice(enums)}")
        options.append(lambda: f"$actor.tags += $params.{rng.choice(enums)}")
        options.append(lambda: f"$actor.tags -= $params.{rng.choice(enums)}")
    for name in texts:
        options.append(lambda name=name: {"post": "log", "text": f"$params.{name}"})
    for name in flags:
        options.append(lambda name=name: {"if": f"$params.{name}", "then": ["$actor.secret -= 1"]})
    for name, of in targets:
        if of == "token":
            options.append(lambda name=name: {"remove": f"$params.{name}"})
        else:
            options.append(lambda name=name: {"transfer": "cash", "from": "$actor", "to": f"$params.{name}",
                                              "amount": rng.choice(numbers)})
            options.append(lambda name=name: f"$params.{name}.score += 1")
            options.append(lambda name=name: {"remove": f"$params.{name}"})
    return [rng.choice(options)() for _ in range(rng.randint(1, 3))]


def _action(rng: random.Random, agents: list[str]) -> dict[str, Any]:
    params = {f"p{k}": _param(rng, agents) for k in range(rng.randint(0, 2))}
    action: dict[str, Any] = {"by": rng.choice(agents), "description": "A move.", "params": params,
                              "do": _effects(rng, params)}
    requirements = []
    if rng.random() < 0.4:
        requirements.append("$actor.cash > 0")
    numbers = [p for p, s in params.items() if s["type"] in ("number", "int")]
    if numbers and rng.random() < 0.5:
        requirements.append({"expr": f"$params.{numbers[0]} <= $actor.cash + 5", "why": "You cannot afford that."})
    if requirements:
        action["when"] = requirements
    if rng.random() < 0.3:
        requirements.append("$actor.secret > 0")
    if rng.random() < 0.3:
        action["chance"] = rng.choice([0.25, 0.5, 0.9])
        action["otherwise"] = ["$actor.score -= 1"]
    if rng.random() < 0.5:
        action["outcome"] = "Done: {$actor.score}, secret {$actor.secret}."
    if rng.random() < 0.2:
        action["private"] = True
    elif rng.random() < 0.4:
        action["announce"] = "{$actor.name} moved."
    return action


def _stages(rng: random.Random, agents: list[str]) -> list[dict[str, Any]]:
    kinds = [rng.choice(["sequential", "simultaneous"]) for _ in range(rng.randint(1, 2))]
    stages = []
    for k, turns in enumerate(kinds):
        stage: dict[str, Any] = {"name": f"s{k}", "turns": turns, "max_actions": rng.randint(1, 3),
                                 "on_enter": [{"each": kind, "do": ["$it.seated += 1"]} for kind in agents]}
        order = rng.choice([None, "random", "$it.cash"])
        if order:
            stage["order"] = order
        if rng.random() < 0.3:
            stage["max_calls"] = rng.randint(1, 4)
        if rng.random() < 0.2:
            stage["must_act"] = True
        if rng.random() < 0.3:
            stage["on_idle"] = ["$actor.score -= 1"]
        if turns == "sequential" and rng.random() < 0.2:
            stage.update({"until": "$world.pot >= 3", "passes": 2})
        if rng.random() < 0.15:
            stage["valid"] = [{"expr": "$actor.cash >= 1", "why": "Keep a coin."}]
        if turns == "simultaneous" and rng.random() < 0.5:
            stage["on_exit"] = [{"each": rng.choice(agents), "where": "$it.cash > 0", "do": ["$world.pot += 1"]}]
        stages.append(stage)
    return stages


def contract(seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    agents = [f"a{k}" for k in range(rng.randint(1, 2))]
    types: dict[str, Any] = {name: _agent_type(rng, name) for name in agents}
    types["token"] = {"props": {"value": 0}}
    entities: dict[str, Any] = {}
    for kind in agents:
        for k in range(rng.randint(2, 3)):
            entities[f"{kind}_{k}"] = {"type": kind, "props": {"secret": marker_number(rng), "code": marker_text(rng)}}
    c: dict[str, Any] = {
        "name": f"Fuzz {seed}",
        "brief": {"rules": "Play well."},
        "world": {"pot": 0},
        "types": types,
        "entities": entities,
        "records": {"log": {"fields": {"text": "text"}}},
        "actions": {f"act{k}": _action(rng, agents) for k in range(rng.randint(1, 4))},
        "stages": _stages(rng, agents),
        "events": [{"phase": "end", "each": rng.choice(agents),
                    "do": [{"if": "$chance(0.3)", "then": ["$it.score += 1"]}], "say": "Round {$round} is over."},
                   {"phase": "start", "when": "$chance(0.3)", "do": [{"create": "token", "props": {"value": 2}}]},
                   {"phase": "end", "each": "token", "where": "$chance(0.2)", "do": [{"remove": "$it"}]}],
        "views": {"table": {"of": rng.choice(agents), "show": "{name}: cash {cash}, mood {mood}, tags {tags}",
                            **rng.choice([{}, {"where": "$it.cash > 2"}, {"sort": "$it.score", "desc": True}])},
                  "mine": {"show": "Your secret is {secret} and your code {code}."},
                  "tokens": {"of": "token", "show": "token worth {value}"}},
        "outputs": {"pot": "$world.pot", "tokens": "$count(token, true)",
                    "cash": {"expr": f"$sum({agents[0]}, $it.cash)", "type": "number"}},
        "invariants": [{"expr": "$world.pot >= 0", "why": "The pot only grows."}],
    }
    c["clock"] = {"rounds": rng.randint(2, 5)}
    return c


def secretive(seed: int) -> dict[str, Any]:
    """:func:`contract` of ``seed`` with the ways hidden information travels besides private properties added on top
    (the base contract is exactly ``contract(seed)``'s): whispers — a record whose entries only their author and
    addressee see, posted by a silent action — and tools whose offer, arguments or refusal turn on what their actor
    may not know: a requirement over those entries or over the log, a default worked out from the actor's own secret,
    a guess at another agent's secret."""
    c = contract(seed)
    rng = random.Random(f"secretive-{seed}")
    agents = [kind for kind, spec in c["types"].items() if spec.get("agent")]
    kind = rng.choice(agents)
    c["records"]["dm"] = {"fields": {"text": "text"},
                          "visible": "$it.author == $viewer.id or $viewer.id in ($it.to or [])"}
    c["actions"]["whisper"] = {"by": kind, "description": "Whisper to another.", "announce": False,
                               "params": {"to": {"type": "entity", "of": kind, "where": "$it.id != $actor.id"},
                                          "text": {"type": "text", "max_len": 30}},
                               "do": [{"post": "dm", "text": "$params.text", "to": ["$params.to"]}]}
    rule = rng.choice(["$len($records(dm)) > 0", "$len($events('action')) > 1", "$count($records(dm)) % 2 == 0"])
    c["actions"]["accuse"] = {"by": kind, "description": "Accuse.", "when": [rule], "do": ["$actor.score += 1"]}
    c["actions"]["claim"] = {"by": kind, "description": "Claim a number.",
                             "params": {"n": {"type": "int", "default": "$actor.secret % 7", "min": 0, "max": 9}},
                             "do": ["$actor.seated += 1"], **rng.choice([{}, {"announce": "{$actor.name} claimed."}])}
    c["actions"]["guess"] = {"by": kind, "description": "Guess another's secret (mod 4).",
                             "params": {"g": {"type": "int", "min": 0, "max": 3, "step": 1},
                                        "who": {"type": "entity", "of": kind, "where": "$it.id != $actor.id"}},
                             "when": [{"expr": "$params.g == $params.who.secret % 4", "why": "Wrong."}],
                             "do": ["$actor.score += 2"], "announce": False}
    c["views"]["whispers"] = {"of": "$records(dm)", "show": "#{$it.seq} {$it.author}: {$it.text}",
                              "sort": "$it.seq", "empty": "No whispers."}
    # Numbers every reader sees: an entry's `seq` in a record's `show` and a view must not count the whispers it
    # cannot read (audit 12 H1).
    c["records"]["dm"]["show"] = "#{$it.seq} {$it.author} whispers: {$it.text}"
    c["records"]["board"] = {"fields": {"text": "text"}, "show": "#{$it.seq} {$it.author}: {$it.text}"}
    c["actions"]["note"] = {"by": kind, "description": "Pin a note to the board.", "announce": False,
                            "params": {"text": {"type": "text", "max_len": 30}},
                            "do": [{"post": "board", "text": "$params.text"}]}
    return c
