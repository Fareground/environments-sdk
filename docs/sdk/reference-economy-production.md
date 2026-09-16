# economy / production

### `economy.production`
Recipes that turn goods into goods: inputs used up, outputs made, optional skill level, tools held, place, money cost and production time. Generates `<name>_start` listing only recipes you can make now, with the batch count bounded by your inputs and money; jobs taking rounds occupy a slot and finish at the start of their due round (waiting while there is no room for the output). Skills gain experience and level up ($skill(agent, skill)). Inputs leave through the recipe as a sink and outputs arrive from it as a source.

Config:
- `who` (required): Type(s) that make goods; they must hold the inventory's goods.
- `inventory` (required): The inventory mechanism whose goods are used and made.
- `recipes` (required): {recipe: {inputs, outputs, rounds, skill, level, xp, tools, at, when, cost}}.
- `skills` (default {}): {skill: {start, max_level, xp_per_level, growth}}.
- `slots` (default 1): Jobs a producer can have running at once (number or expression).

Nested config:
**RecipeSpec** — Goods in, goods out.
- `inputs`: object — Goods used up per batch {item: qty}; none for gathering.
- `outputs`: object (required) — Goods made per batch {item: qty or expression over $actor}.
- `rounds`: int = 0 — Rounds until a batch is ready (0 = at once).
- `skill`: text — Skill used; gains `xp` per batch.
- `level`: int = 0 — Skill level needed.
- `xp`: number = 0 — Experience per batch.
- `tools`: object — Goods that must be held but are not used up {item: qty}.
- `at`: text | [text] — Place(s) where it can be made.
- `when`: text — Extra requirement over $actor.
- `cost`: object — Money per batch {currency: amount}, leaving to the recipe's sink.
- `description`: text
**SkillSpec** — 
- `start`: int = 0 — Level everyone starts at.
- `max_level`: int = 10
- `xp_per_level`: number = 10 — Experience for the next level (times the next level when growth is linear).
- `growth`: any = "linear"

Actions of the `economy` op:
- `start` — takes `who`, `recipe`, `qty` (needs `who`, `recipe`): {"economy": "craft", "action": "start", "who": "$actor", "recipe": "$params.recipe", "qty": 2}  (use up the inputs and start `qty` batches; done at once when the recipe takes no rounds)

```json
{"mechanisms": {"my_production": {"kind": "economy", "mode": "production", "who": "villager", "inventory": "goods", "skills": {"baking": {"xp_per_level": 5}, "foraging": {}}, "recipes": {"bake": {"inputs": {"flour": 2}, "outputs": {"bread": 3}, "rounds": 1, "skill": "baking", "xp": 2, "at": "bakery"}, "forage": {"outputs": {"berries": "1 + $skill($actor, foraging)"}, "at": "forest", "skill": "foraging", "xp": 1}}}}}
```
