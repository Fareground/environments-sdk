# Beer distribution game

Build the classic beer distribution game. Four companies form a supply chain for one product: a retailer, a wholesaler, a distributor and a factory, each run by an AI agent. Every week, customers buy from the retailer; weekly customer demand is random, a whole number of cases between 0 and 8. Each week every company ships what it can to the company below it, then places an order with the company above it (the factory instead starts production). Orders reach the supplier the following week, and shipments take two weeks to arrive; production also takes two weeks. Everyone starts with 12 cases in stock and 4 cases already on the way on each shipping link. Demand a company cannot fill becomes backlog and must be shipped later. Each company pays $0.50 per case in stock and $1.00 per case of backlog at the end of every week. Companies only see their own stock, backlog, incoming orders and deliveries, never the others' positions. The game lasts 20 weeks and the team's goal is the lowest total cost.

## Deliverables

Report these outputs:
- `customer_demand` — a list of the cases customers demanded each week, in week order.
- `factory_orders` — a list of the cases the factory put into production each week, in week order.
- `costs_by_role` — a map from role ("retailer", "wholesaler", "distributor", "factory") to that company's total cost.
- `total_team_cost` — the sum of all four companies' costs.
- `bullwhip_ratio` — the variance of the factory's weekly production orders divided by the variance of weekly customer demand.
