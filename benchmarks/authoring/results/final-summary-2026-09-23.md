# Authoring benchmark: before and after T-797

All 12 briefs, model `deepseek/deepseek-v4.1-flash`, 600k-token cap per brief. "Before" is `main` at `d91ff26`
(one sample). "Phase 1" is the first integration run (one sample). "Final" is SDK commit `bd69a38` (three samples;
per-sample scorecards are in `final-s{1,2,3}-2026-09-23.md`, transcripts are not committed).

| | before (n=1) | phase 1 (n=1) | final s1 | final s2 | final s3 | final mean |
|---|---|---|---|---|---|---|
| reached a clean `check` | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 |
| all runs ok | 11/12 | 11/12 | 12/12 | 11/12 | 11/12 | 11.3/12 |
| fidelity checks passed | 42/50 (84%) | 39/50 (78%) | 50/50 | 44/50 | 36/50 | 130/150 (87%) |
| sessions that hit the token cap | 9/12 | — | 5/12 | 4/12 | 6/12 | 5/12 |
| input tokens | 6.53M | 5.98M | 4.40M | 4.95M | 4.93M | 4.76M (−27%) |
| cost | $0.57 | $0.64 | $0.46 | $0.50 | $0.48 | $0.48 |

## What the remaining misses are

Almost every final miss comes from a session that ran out of budget and whose **last** write regressed a contract
that had been working:

- The author swapped the brief's required outputs for debugging outputs (`dbg_*`, `w_*`) and never swapped back:
  s3 werewolf, beer_game and town_hall.
- The last write broke a contract that was already clean: s2 ed_triage.
- A policy rule reads a property that doesn't exist, and `check` never reaches that rule: s3 ed_triage.
- One real fidelity miss: s2 treaty showed a rival's red line in a ratification announcement.

Authors reach a correct environment in almost every session. The gap left is how cheaply they get there and how
they debug: they use outputs to look at state because nothing else shows it to them.
