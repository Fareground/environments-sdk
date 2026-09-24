# economy / inventory

### `economy.inventory`
Goods held by entities: stackable items in a map property listing each one (`$actor.goods.bread`, 0 when none are held) and unique items as entities with an owner. Generates `<name>_give`, `<name>_consume`, `<name>_drop` and `<name>_pickup` tools listing only goods you hold, capacity limits, recurring needs and spoilage, and the invariant `$conserved(<name>)`: goods change only by moves or by named sources and sinks (the `make` and `use` actions). Totals are in $world.<name>_supply and every named flow in $world.<name>_flows.

Config:
- `who` (required): Type(s) that hold goods (subtypes included).
- `items` (required): {item: {unique, value, size, unit, shelf_life, decay, props, consumable, on_consume}}.
- `prop` (default null): Holder property with the stackable goods {item: qty}; default the mechanism's name.
- `capacity` (default null): Space each holder has (number or expression); unlimited when omitted.
- `start` (default {}): Goods every holder starts with {item: qty or expression} (entity props override).
- `actions` (default ["give", "consume"]): Tools generated for agent holders: give, consume (consumable items), drop and pickup (needs a space).
- `give_to` (default "$it.id != $actor.id"): Which holders an agent may give goods to ($actor, $it).
- `needs` (default {}): Goods used up every `every` rounds per type: {type: {item: qty or expression over $it}}.
- `on_short` (default []): Effects when a need is not met ($it, $item, $short).
- `every` (default 1): Rounds between needs.

Nested config:
**ItemSpec** — One kind of good.
- `unique`: bool = false — Each unit is its own entity (type named after the item) with an owner and props.
- `value`: number = 0 — Worth of one unit, used by $net_worth when no price is given.
- `size`: number = 1 — Capacity one unit takes.
- `unit`: text — Unit word shown with quantities (loaf, kg).
- `description`: text
- `shelf_life`: int — Rounds until a unit spoils (oldest units first).
- `decay`: number — Chance each unit is lost every round (seeded).
- `props`: object — Unique items: properties of each instance.
- `consumable`: bool = false — Holders may consume it (a `<name>_consume` tool).
- `on_consume`: [any] — Effects when consumed ($actor, $qty); implies consumable.

Actions of the `economy` op:
- `give` — takes `item`, `from`, `to`, `qty` (needs `item`, `from`, `to`): {"economy": "goods", "action": "give", "item": "$params.item", "from": "$actor", "to": "$params.to", "qty": 2}  (moves goods; a unique item by kind or instance id; fails if short or the receiver is full)
- `make` — takes `item`, `to`, `qty`, `source`, `props` (needs `item`, `to`, `source`): {"economy": "goods", "action": "make", "item": "bread", "to": "$actor", "qty": 3, "source": "baking"}  (new goods from a named source; unique items take props)
- `use` — takes `item`, `from`, `qty`, `sink` (needs `item`, `from`, `sink`): {"economy": "goods", "action": "use", "item": "flour", "from": "$actor", "qty": 2, "sink": "baking"}  (goods used up into a named sink; fails if short)
- `drop` — takes `item`, `from`, `qty` (needs `item`, `from`): {"economy": "goods", "action": "drop", "item": "$params.item", "from": "$actor", "qty": 1}  (leave goods on the ground where the holder stands)
- `pickup` — takes `item`, `to`, `qty` (needs `item`, `to`): {"economy": "goods", "action": "pickup", "item": "$params.item", "to": "$actor", "qty": 1}  (take goods lying where the holder stands)

```json
{"mechanisms": {"my_inventory": {"kind": "economy", "mode": "inventory", "who": "villager", "capacity": 20, "items": {"bread": {"value": 3, "shelf_life": 4, "on_consume": ["$actor.hunger -= 2 * $qty"]}, "axe": {"unique": true, "value": 20, "props": {"durability": 10}}}}}}
```
