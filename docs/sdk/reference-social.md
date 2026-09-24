# social

## Mechanism family `social`

Talking and spreading: channels (rooms, direct messages), a social feed, diffusion over a network.

Named the same in every mode:
- `who`: agent type that communicates
- `max_chars`: the longest text accepted or kept

Modes (`"kind": "social", "mode": ...`; read one with `guide('social.<mode>')`):
- `channels`: Rooms, private groups, direct messages and broadcasts: tools `<name>_say` (channel enum of the rooms and groups you are in), `<name>_dm`, `<name>_reply`, `<name>_read`, `<name>_broadcast`, and group tools.
- `diffusion`: Items (rumors, ideas, products) spreading over a relation by independent cascade or linear threshold, with per-agent states (unaware, exposed, adopted, rejected) and exposure counts in the world prop `<name>`.
- `feed`: A social network: posts (type `<name>_post`), replies, reposts, reactions, follows, friend requests, blocks and mutes (relations `<name>_follows`, `<name>_friends`, `<name>_blocks` …), a ranked feed view per account, trending, reputation moved by engagement, and moderator labels that downrank posts.

Functions:
- `$adopter_count(item)` — How many agents currently hold an item (adopted, not rejected).
- `$channel_log(agent, channel, n?, mechanism?)` — The latest n messages of a channel as the agent reads them (default: the read limit).
- `$channels(agent, mechanism?)` — Rooms and groups an agent may post in (social channels mechanism).
- `$exposures(agent, item)` — How many times an agent was exposed to an item.
- `$feed(viewer, n?, mechanism?)` — The viewer's ranked feed: up to n posts (default feed_size) from the social feed mechanism.
- `$followers(account, mechanism?)` — Ids of the accounts that follow this account.
- `$following(account, mechanism?)` — Ids of the accounts this account follows.
- `$groups(agent, mechanism?)` — Private groups the agent belongs to (social channels mechanism).
- `$heard(agent, mechanism?)` — Items an agent is aware of: [{item, state, exposures}], in the order they started.
- `$homophily(prop, mechanism?)` — Share of follow links joining accounts with the same value of `prop` (null without links).
- `$inbox(agent, mechanism?)` — The agent's channels with unread counts: [{channel, label, title, unread, status}].
- `$inbox_channels(agent, mechanism?)` — Every channel an agent can read: rooms, its groups and its direct-message threads (@id).
- `$influence(account, mechanism?)` — Followers + friends + reposts received: how far an account's voice carries.
- `$insularity(account?, mechanism?)` — Share of an account's connections that are connected to each other (0 diverse – 1 echo chamber); without an account, the mean over accounts with at least two connections.
- `$invites(agent, mechanism?)` — Private groups the agent has been invited to and not joined.
- `$reach(item)` — Distinct agents an item has reached so far: exposed, adopted or rejected (diffusion).
- `$recent_messages(agent, n?, mechanism?)` — Sequence numbers of the latest messages an agent can read and did not write.
- `$spread_state(agent, item)` — unaware | exposed | adopted | rejected.
- `$trending(n?, mechanism?)` — Recent posts with the most engagement per round of age (reposts count toward the original).
- `$unread(agent, channel?, mechanism?)` — Unread messages for an agent, in one channel (a room, group or @id) or in all.
