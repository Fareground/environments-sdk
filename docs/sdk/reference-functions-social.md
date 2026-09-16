# functions / social

## Functions: social

- `$adopters(item)` — How many agents currently hold an item (adopted, not rejected).
- `$channel_log(agent, channel, n?)` — The latest n messages of a channel as the agent reads them (default: the read limit).
- `$channels(agent)` — Rooms and groups an agent may post in (social channels mechanism).
- `$exposures(agent, item)` — How many times an agent was exposed to an item.
- `$feed(viewer, n?)` — The viewer's ranked feed: up to n posts (default feed_size) from the social feed mechanism.
- `$followers(account)` — Ids of the accounts that follow this account.
- `$following(account)` — Ids of the accounts this account follows.
- `$groups(agent)` — Private groups the agent belongs to (social channels mechanism).
- `$heard(agent, mechanism?)` — Items an agent is aware of: [{item, state, exposures}], in the order they started.
- `$homophily(prop)` — Share of follow links joining accounts with the same value of `prop` (null without links).
- `$inbox(agent)` — The agent's channels with unread counts: [{channel, label, title, unread, status}].
- `$inbox_channels(agent)` — Every channel an agent can read: rooms, its groups and its direct-message threads (@id).
- `$influence(account)` — Followers + friends + reposts received: how far an account's voice carries.
- `$insularity(account?)` — Share of an account's connections that are connected to each other (0 diverse – 1 echo chamber); without an account, the mean over accounts with at least two connections.
- `$invites(agent)` — Private groups the agent has been invited to and not joined.
- `$reach(item)` — Distinct agents an item has reached so far: exposed, adopted or rejected (diffusion).
- `$recent_messages(agent, n?)` — Sequence numbers of the latest messages an agent can read and did not write.
- `$spread_state(agent, item)` — unaware | exposed | adopted | rejected.
- `$trending(n?)` — Recent posts with the most engagement per round of age (reposts count toward the original).
- `$unread(agent, channel?)` — Unread messages for an agent, in one channel (a room, group or @id) or in all.
