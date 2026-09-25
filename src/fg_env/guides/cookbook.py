"""The cookbook: ``guide('cookbook')`` shows every recipe, ``guide('cookbook.<name>')`` one. The recipes and their
known answers live with the starting templates (:mod:`fg_env.authoring.scaffold`), so the page shows exactly the file
``fg-env new <name>`` writes, and the answer the tests hold it to."""
from __future__ import annotations

import json
from importlib.resources import files

from ..authoring.scaffold import RECIPES, Recipe, recipe

__all__ = ["cookbook_page", "recipe_page"]

_INTRO = """\
# Cookbook

Each recipe is a small, complete contract for a pattern that comes up again and again. Start from the one nearest
your idea: `fg-env new <recipe> my_env.json` (`fg_env.new`) writes it, and you change the brief, the numbers and the
rules. Every recipe checks clean, plays with random agents, and gives the known answer shown under it with its coded
policy (`--agent policy:<name>` plays it for every agent).

| recipe | pattern |
|---|---|
"""


_NOTES = """## Notes

**A reader above the rest.** A value only its owner and one role may see — reviewers' scores the area chair reads,
countries' case counts the health agency reads — stays `private`, naming that role: `"score": {"type": "int",
"default": 0, "private": ["chair"]}`. The chair's views and tools may show every score; any other reviewer reading one
is refused, as for any private prop. Removing `private` instead leaves hiding the value to the views alone."""


def cookbook_page() -> str:
    """Every recipe, in order, after a table of them, then notes on patterns that are a field, not a recipe."""
    rows = "\n".join(f"| [`{name}`](#{name}) | {spec.about} |" for name, spec in RECIPES.items())
    return _INTRO + rows + "\n\n" + "\n\n".join(recipe_page(name) for name in RECIPES) + "\n\n" + _NOTES


def recipe_page(name: str) -> str:
    """One recipe: what it shows, the whole contract, and how to try it."""
    spec = RECIPES[name]
    contract = recipe(name)
    text = files("fg_env.authoring").joinpath("recipes", f"{name}.json").read_text("utf-8")
    return (f"## {name}\n\n**{contract['name']}.** {contract['description']}\n\n```json\n{text}```\n\n"
            f"{_try_it(name, spec)}")


def _try_it(name: str, spec: Recipe) -> str:
    agent = f" --agent {spec.policy}" if spec.policy else ""
    command = f"`fg-env new {name} && fg-env run {name}.json{agent} --seed 1`"
    if spec.answer is None:
        return f"Try it: {command}."
    answer = ", ".join(f"`{key}` {json.dumps(value)}" for key, value in spec.answer.items())
    return f"Known answer: {command} gives {answer}: {spec.why}"
