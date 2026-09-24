# groups / factions

### `groups.factions`
Factions and alliances: membership with invitations (or open factions), founding, and alliances that form when both factions propose them. State in the world prop `<name>`; tools `<name>_join`, `<name>_leave`, `<name>_invite`, `<name>_found`, `<name>_ally`, `<name>_break_alliance`, and the same actions of the `groups` op for effects. Read with $allies(a, b), $faction_of(agent), $factions(), $joinable(agent); with several factions mechanisms, name one as the last argument ($factions('guilds')).

Config:
- `who` (required): Agent type that belongs to factions.
- `factions` (default {}): {id: {title, members, open}}.
- `allies` (default []): Starting alliances as [faction, faction] pairs.
- `one` (default true): An agent belongs to at most one faction.
- `found` (default false): Members may found new factions.
- `alliances` (default true): Offer tools to propose and break alliances between factions.
- `joining` (default true): Offer join, leave and invite tools.

Nested config:
**FactionSpec** — 
- `title`: text
- `members`: [text]
- `open`: bool = false — Anyone may join without an invitation.

Actions of the `groups` op:
- `join` — takes `in`, `who` (needs `in`): {"groups": "blocs", "action": "join", "in": "$params.faction"}  (join a faction that is open or invited you)
- `leave` — takes `in`, `who` (needs `in`): {"groups": "blocs", "action": "leave", "in": "$params.faction"}  (leave a faction)
- `invite` — takes `in`, `guest`, `who` (needs `in`, `guest`): {"groups": "blocs", "action": "invite", "in": "$params.faction", "guest": "$params.guest"}  (invite `guest` into your faction)
- `found` — takes `title`, `who`: {"groups": "blocs", "action": "found", "title": "$params.title"}  (found a new faction with `who` as its first member)
- `ally` — takes `in`, `other`, `who` (needs `in`, `other`): {"groups": "blocs", "action": "ally", "in": "$params.faction", "other": "$params.other"}  (propose an alliance with `other`, or accept the one it proposed)
- `break_alliance` — takes `in`, `other`, `who` (needs `in`, `other`): {"groups": "blocs", "action": "break_alliance", "in": "$params.faction", "other": "$params.other"}  (end the alliance between `in` and `other`)
- `add` — takes `in`, `who` (needs `in`): {"groups": "blocs", "action": "add", "in": "rebels", "who": "$it"}  (put `who` in a faction without consent)
- `remove` — takes `in`, `who` (needs `in`): {"groups": "blocs", "action": "remove", "in": "rebels", "who": "$it"}  (take `who` out of a faction without consent)

```json
{"mechanisms": {"my_factions": {"kind": "groups", "mode": "factions", "who": "nation", "factions": {"entente": {"members": ["fr", "uk"]}, "central": {"members": ["de"]}}}}}
```
