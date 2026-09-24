# functions / social

## Functions: social

- `$adopter_count(item)` — How many agents currently hold an item (adopted, not rejected).
- `$exposures(agent, item)` — How many times an agent was exposed to an item.
- `$feed(viewer, n?, mechanism?)` — The viewer's ranked feed: up to n posts (default feed_size) from the social feed mechanism.
- `$followers(account, mechanism?)` — Ids of the accounts that follow this account.
- `$following(account, mechanism?)` — Ids of the accounts this account follows.
- `$heard(agent, mechanism?)` — Items an agent is aware of: [{item, state, exposures}], in the order they started.
- `$homophily(prop, mechanism?)` — Share of follow links joining accounts with the same value of `prop` (null without links).
- `$influence(account, mechanism?)` — Followers + friends + reposts received: how far an account's voice carries.
- `$insularity(account?, mechanism?)` — Share of an account's connections that are connected to each other (0 diverse – 1 echo chamber); without an account, the mean over accounts with at least two connections.
- `$reach(item)` — Distinct agents an item has reached so far: exposed, adopted or rejected (diffusion).
- `$spread_state(agent, item)` — unaware | exposed | adopted | rejected.
- `$trending(n?, mechanism?)` — Recent posts with the most engagement per round of age (reposts count toward the original).
