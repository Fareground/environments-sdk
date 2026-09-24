# functions / groups

## Functions: groups

- `$known_role(viewer, player)` — The role `viewer` knows `player` has ('' when unknown): their own, a revealed role, or a teammate's when their team knows each other.
- `$team_alive(team)` — How many players of `team` (or role) are still in the game.
- `$teammates(player)` — The other players `player` knows are on their team (empty when their team is secret).
