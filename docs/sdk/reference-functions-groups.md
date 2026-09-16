# functions / groups

## Functions: groups

- `$allies(a, b)` — True when a and b share a faction or belong to allied factions (groups factions mode).
- `$faction_of(agent)` — Ids of the factions the agent belongs to.
- `$factions()` — Every faction: [{id, title, members, allies, open}].
- `$joinable(agent)` — Factions the agent may join now: open ones and those it was invited to.
- `$known_role(viewer, player)` — The role `viewer` knows `player` has ('' when unknown): their own, a revealed role, or a teammate's when their team knows each other.
- `$team_alive(team)` — How many players of `team` (or role) are still in the game.
- `$teammates(player)` — The other players `player` knows are on their team (empty when their team is secret).
