# social / channels

### `social.channels`
Rooms, private groups, direct messages and broadcasts: tools `<name>_say` (channel enum of the rooms and groups you are in), `<name>_dm`, `<name>_reply`, `<name>_read`, `<name>_broadcast`, and group tools. Messages are entries of the record `<name>` delivered only to their audience; @mentions wake the mentioned agent. Read state with $channels(agent), $unread(agent, channel?), $inbox(agent), $channel_log(agent, channel), $groups(agent), $invites(agent); with several channels mechanisms, name one as the last argument ($channels($actor, 'chat')).

Config:
- `who` (required): Agent type that chats (subtypes included).
- `rooms` (default ["general"]): Public rooms every member reads and writes.
- `groups` (default {}): Private groups: {id: {members: [ids], title}}. Only members read them.
- `dm` (default true): Members may message one another directly.
- `replies` (default true): Offer a reply tool for recent messages.
- `broadcast` (default null): Agent type that may broadcast to everyone (e.g. a moderator).
- `create_groups` (default false): Members may create private groups and invite others.
- `max_chars` (default 500): Longest message, in characters.
- `per_turn` (default 3): Messages one agent may send per turn (per tool).
- `per_round` (default null): Messages one agent may send per round (per tool).
- `mentions` (default true): @id or @name in a message wakes that agent (if it can read the message).
- `notify` (default true): Deliver messages as news; false keeps them in the inbox (unread counts, read tool).
- `read_limit` (default 10): Messages the read tool shows.
- `recent` (default 15): Recent messages offered to reply to.
- `keep` (default null): Keep only the latest N messages.
- `stage` (default null): Offer the tools during this declared stage; default: a sequential stage named after the mechanism.
- `passes` (default 1): Passes of the generated stage (agents with nothing new are skipped after the first).

Nested config:
**GroupSpec** — A private group declared up front.
- `members`: [text] — Ids of the starting members.
- `title`: text — What the group is for, shown to its members.

Actions of the `social` op:
- `say` — takes `channel`, `text`, `who` (needs `channel`, `text`): {"social": "chat", "action": "say", "channel": "$params.channel", "text": "$params.text"}  (post in a room, or in a private group the sender belongs to)
- `dm` — takes `to`, `text`, `who` (needs `to`, `text`): {"social": "chat", "action": "dm", "to": "$params.to", "text": "$params.text"}  (a direct message only the recipient reads)
- `reply` — takes `message`, `text`, `who` (needs `message`, `text`): {"social": "chat", "action": "reply", "message": "$params.message", "text": "$params.text"}  (reply to a message by its number, in the same channel)
- `broadcast` — takes `text`, `who` (needs `text`): {"social": "chat", "action": "broadcast", "text": "$params.text"}  (a message every member reads (senders of the broadcast type))
- `read` — takes `channel`, `who` (needs `channel`): {"social": "chat", "action": "read", "channel": "$params.channel"}  (mark a channel (a room, a group or @id) read)
- `create_group` — takes `title`, `invite`, `who`: {"social": "chat", "action": "create_group", "title": "$params.title", "invite": "$params.invite"}  (create a private group and invite members to it)
- `invite` — takes `group`, `guest`, `who` (needs `group`, `guest`): {"social": "chat", "action": "invite", "group": "$params.group", "guest": "$params.guest"}  (invite someone to a private group the sender belongs to)
- `join` — takes `group`, `who` (needs `group`): {"social": "chat", "action": "join", "group": "$params.group"}  (join a private group the sender was invited to)
- `leave` — takes `group`, `who` (needs `group`): {"social": "chat", "action": "leave", "group": "$params.group"}  (leave a private group)

```json
{"mechanisms": {"my_channels": {"kind": "social", "mode": "channels", "who": "citizen", "rooms": ["plaza"], "groups": {"council": {"members": ["ana", "ben"], "title": "Budget committee"}}, "per_turn": 2, "max_chars": 400}}}
```
