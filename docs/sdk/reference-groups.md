# groups

## Mechanism family `groups`

Who belongs with whom: hidden roles and teams, stable matching.

Named the same in every mode:
- `who`: agent type that belongs to groups
- `views`: generate the mechanism's views
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)

Modes (`"kind": "groups", "mode": ...`; read one with `guide('groups.<mode>')`):
- `roles`: Secret roles: deals a shuffled role deck in round 1 into private `role` and `team` props, lets listed teams know each other, generates role-gated private tools, and reveals a role (public `revealed_role`) when the `eliminate` action takes its player out.
- `matching`: Two-sided stable matching (deferred acceptance, Gale–Shapley): `who` proposes to `to`, each receiver takes up to `seats`.

Functions:
- `$known_role(viewer, player)` — The role `viewer` knows `player` has ('' when unknown): their own, a revealed role, or a teammate's when their team knows each other.
- `$team_alive(team)` — How many players of `team` (or role) are still in the game.
- `$teammates(player)` — The other players `player` knows are on their team (empty when their team is secret).
