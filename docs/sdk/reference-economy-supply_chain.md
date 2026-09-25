# economy / supply_chain

### `economy.supply_chain`
A serial supply chain (the beer game as data): each round every node receives what reached it, gets its order (customers' demand at the first node), ships what it can toward that order plus backlog and pays holding and backlog costs; then nodes order from the node upstream (the producer starts a batch) with `<name>_order`, one order a round. Orders and shipments travel in pipelines ($world.<name>_pipes) with their own delays; goods in a pipeline count toward the inventory's supply, production enters from the named source `production` and customer sales leave through the sink `sold`. Node props: <name>_backlog, _incoming, _received, _shipped, _last_order, _round_cost, _cost, _peak_backlog; $pipeline(agent, chain) lists goods on the way.

Config:
- `inventory` (required): Inventory holding the chain's stock.
- `item` (required): The item that flows down the chain.
- `nodes` (required): Entity ids from the customer-facing node to the producer.
- `demand` (required): Customers' order at the first node each round: a whole number ≥ 0, or an expression giving one.
- `order_delay` (default 1): Rounds for an order to reach the node upstream.
- `lead_time` (default 2): Rounds for a shipment to reach the node downstream.
- `production_delay` (default 2): Rounds for the producer's batch to be ready.
- `initial_flow` (default 0): Steady flow already in every pipeline when the run starts (whole units).
- `holding_cost` (default 0.0): Cost per unit in stock per round.
- `backlog_cost` (default 0.0): Cost per unit of backlog per round.
- `max_order` (default 1000): Largest order in one round.
- `default_order` (default 0): Order placed for a node that placed none this round (expression over $it).
- `actions` (default ["order"]): order: nodes that are agents place their orders with a tool.

Actions of the `economy` op:
- `order` — takes `who`, `qty` (needs `who`, `qty`): {"economy": "beer", "action": "order", "who": "$actor", "qty": 8}  (a node orders from upstream; the producer starts a batch)

```json
{"types": {"stage_node": {"agent": true}}, "entities": {"retailer": {"type": "stage_node"}, "wholesaler": {"type": "stage_node"}, "distributor": {"type": "stage_node"}, "factory": {"type": "stage_node"}}, "mechanisms": {"stock": {"kind": "economy", "mode": "inventory", "who": "stage_node", "items": {"beer": {}}}, "my_supply_chain": {"kind": "economy", "mode": "supply_chain", "inventory": "stock", "item": "beer", "nodes": ["retailer", "wholesaler", "distributor", "factory"], "demand": "4 if $round < 5 else 8", "initial_flow": 4, "holding_cost": 0.5, "backlog_cost": 1}}}
```
