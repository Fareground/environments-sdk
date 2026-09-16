# groups

## Mechanism family `groups`

Who belongs with whom: hidden roles and teams, factions and alliances, relationships.

Named the same in every mode:
- `who`: agent type that belongs to groups
- `views`: generate the mechanism's views
- `phase`: start | end: when in the round the mechanism's own step runs
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)

Modes (`"kind": "groups", "mode": ...`; read one with `guide('groups.<mode>')`):
- `roles`: Secret roles: deals a shuffled role deck in round 1 into private `role` and `team` props, lets listed teams know each other, generates role-gated private tools, and reveals a role (public `revealed_role`) when the `eliminate` action takes its player out.
- `relationships`: Relations (trust, affinity, rivalry) that drift back toward a baseline every round and fire threshold events (news and effects with $from, $to, $value) when crossed.
- `factions`: Factions and alliances: membership with invitations (or open factions), founding, and alliances that form when both factions propose them.

Functions:
- `$allies(a, b)` — True when a and b share a faction or belong to allied factions (groups factions mode).
- `$faction_of(agent)` — Ids of the factions the agent belongs to.
- `$factions()` — Every faction: [{id, title, members, allies, open}].
- `$joinable(agent)` — Factions the agent may join now: open ones and those it was invited to.
- `$known_role(viewer, player)` — The role `viewer` knows `player` has ('' when unknown): their own, a revealed role, or a teammate's when their team knows each other.
- `$team_alive(team)` — How many players of `team` (or role) are still in the game.
- `$teammates(player)` — The other players `player` knows are on their team (empty when their team is secret).
