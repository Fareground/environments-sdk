# Union contract negotiation

A union and a company negotiate a new contract, with the union's negotiator and the company's negotiator both played by AI agents. The only open issue is the wage increase, in percent. They take turns: each round one side proposes a wage increase and the other side accepts it or rejects it, with the union proposing in odd rounds and the company in even rounds. From round 4 on, the union may call a strike, and once on strike it can call it off at any time. Every round spent on strike costs the company $200,000 in lost revenue and costs the workers $50,000 in lost wages. When a negotiator accepts a proposal it becomes a tentative agreement, and the union's five members, also AI agents, then vote to ratify it; a majority yes ratifies it, ends any strike and ends the negotiation. A rejected tentative agreement means talks continue. If there is no ratified deal after 12 rounds, the talks fail.

## Deliverables

Report these outputs:
- `agreed` — true if a deal was ratified.
- `wage_increase` — the ratified wage increase in percent, or null.
- `strike_rounds` — how many rounds were spent on strike.
- `company_losses` — the company's total lost revenue from strikes.
- `worker_lost_wages` — the workers' total lost wages from strikes.
- `ratification_votes` — a map from each union member's id to their vote on the ratified deal, "yes" or "no" (null for everyone if there was none).
