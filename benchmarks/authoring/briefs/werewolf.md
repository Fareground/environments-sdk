# Werewolf

Make a werewolf-style social deduction game for seven players, all AI agents. At the start, two players are secretly werewolves, one is the seer and the other four are villagers; the werewolves know each other, but nobody else knows anyone's role. Each night the werewolves agree on one living player to kill, and the seer privately learns the role of one living player of their choice. Each day the survivors discuss and then vote to eliminate one living player; the player with the most votes is eliminated, and on a tie nobody is. When a player dies or is eliminated, their role is not revealed. The village wins when both werewolves are gone; the werewolves win as soon as they are at least as many as everyone else alive. Stop after ten days if neither side has won.

## Deliverables

Report these outputs:
- `winner` — "village", "werewolves", or null if nobody won.
- `roles` — a map from each player's id to "werewolf", "seer" or "villager".
- `eliminated` — a list of the ids of the players removed from the game (killed or voted out), in the order it happened.
- `alive_end` — a list of the ids of the players alive at the end.
