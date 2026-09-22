# Sports league season

Run a season of a six-team football league where each team's manager is an AI agent. Every team plays every other team twice, once at home and once away, over ten matchdays with three matches each, and no team plays twice on the same matchday. Before each matchday, every manager picks a tactic for their match: attack, balanced or defend. Each team has a fixed strength, and a match's score is random but depends on the two teams' strengths, their tactics and a small home advantage. A win is worth 3 points, a draw 1 and a loss 0. The table ranks teams by points, then goal difference, then goals scored, then alphabetically by team name. The champion is the team on top of the table after the last matchday.

## Deliverables

Report these outputs, identifying teams the same way (their id or their name) everywhere:
- `matches` — a list of every match played, each a map with `matchday`, `home`, `away`, `home_goals` and `away_goals`.
- `standings` — the final table in rank order: a list of maps with `team`, `played`, `won`, `drawn`, `lost`, `goals_for`, `goals_against` and `points`.
- `champion` — the team at the top of the final table.
