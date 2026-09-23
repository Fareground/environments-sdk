# groups / roles

### `groups.roles`
Secret roles: deals a shuffled role deck in round 1 into private `role` and `team` props, lets listed teams know each other, generates role-gated private tools, and reveals a role (public `revealed_role`) when the `eliminate` action takes its player out. Views show only your own role and known teammates. Functions: $known_role, $teammates, $team_alive.

Config:
- `who` (required): Agent type that receives roles.
- `deck` (required): {role: count}; a count may be an expression, and one role may be "rest" (everyone left).
- `teams` (default {}): {team: [roles]}; a role in no team is its own team.
- `know` (default []): Teams (or roles) whose members know each other from the start.
- `alive` (default "living"): Bool player property that says a player is still in the game (not the built-in `alive`, which turns false only when an entity is removed).
- `reveal` (default "elimination"): Reveal a role when its player is eliminated.
- `actions` (default {}): Role-gated tools: {name: {...action, "roles": [roles]}}; private unless said otherwise.
- `views` (default true): Generate views of your role, your known teammates and who is still in.

Actions of the `groups` op:
- `eliminate` — takes `who`, `say` (needs `who`): {"groups": "roles", "action": "eliminate", "who": "$out", "say": "{$out.name} is exiled."}  (out of the game; the role is revealed unless reveal: never)
- `reveal` — takes `who` (needs `who`): {"groups": "roles", "action": "reveal", "who": "$filter(player, true)"}  (make these players' roles public)

```json
{"mechanisms": {"my_roles": {"kind": "groups", "mode": "roles", "who": "player", "deck": {"werewolf": 2, "seer": 1, "villager": "rest"}, "teams": {"wolves": ["werewolf"], "village": ["seer", "villager"]}, "know": ["wolves"], "actions": {"inspect_player": {"roles": ["seer"], "params": {"target": {"type": "entity", "of": "player"}}, "do": ["$seen = $params.target.role"], "outcome": "{$params.target.name} is {$seen}."}}}}}
```
