# Climate treaty

Four nations — Norland, Estavia, Suria and Westmark — negotiate a climate treaty, each represented by an AI agent. The treaty fixes one number: the percentage by which every signatory cuts its emissions. Each nation has a secret red line, the largest cut it can accept, a whole number between 10 and 60 that is different every game. No nation may learn another nation's red line. In each round any nation may put forward a proposal for the cut and send messages to the others, and then every nation decides whether to ratify the proposal currently on the table. A nation can never ratify a cut above its own red line. The treaty enters into force as soon as at least three of the four nations ratify the same proposal. If that has not happened after eight rounds, there is no treaty.

## Deliverables

Report these outputs:
- `in_force` — true if the treaty entered into force.
- `ratified_by` — a list of the ids of the nations that ratified the treaty that entered into force (an empty list if none did).
- `agreed_cut` — the agreed percentage cut, or null if there is no treaty.
- `red_lines` — a map from each nation's id to its red line.
