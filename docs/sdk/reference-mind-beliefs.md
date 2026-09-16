# mind / beliefs

### `mind.beliefs`
A private world model per agent: beliefs {key: {value, confidence, source, told_by, round}} in the private prop `<name>`, changed by the `learn`, `tell` and `forget` actions, decaying every round. Told beliefs arrive at secondhand confidence (scaled by trust). Read with $believes(agent, key, value?), $belief(agent, key), $confidence(agent, key), $beliefs_of(agent).

Config:
- `who` (required): Entity type that holds beliefs (subtypes included).
- `decay` (default 0.1): Confidence lost per round (a share with exponential, an amount with linear).
- `decay_curve` (default "exponential"): How confidence decays: exponential (loses a share of itself) or linear (loses a fixed amount).
- `forget_below` (default 0.05): Beliefs below this confidence are forgotten.
- `secondhand` (default 0.7): Confidence multiplier for something one was told.
- `trust` (default null): Relation from listener to teller scaling told confidence (value clamped to 0–1).
- `share` (default false): Offer a `<name>_tell` tool: pass one of your beliefs to another holder.
- `views` (default true): Show each holder its own beliefs.
- `view_limit` (default 12): Beliefs shown, most confident first.
- `phase` (default "end"): When beliefs decay each round.

Actions of the `mind` op:
- `learn` — takes `key`, `who`, `value`, `confidence`, `source`, `from` (needs `key`): {"mind": "memory", "action": "learn", "key": "wolf", "who": "$actor", "value": "$params.suspect.id", "confidence": 0.9}  (who, by default $actor, comes to believe key = value; source direct unless given; `from` names who it came from)
- `tell` — takes `key`, `to`, `who`, `value`, `confidence`, `say` (needs `key`, `to`): {"mind": "memory", "action": "tell", "key": "wolf", "to": "$params.listener"}  (who, by default $actor, passes a belief on at secondhand confidence; `value` to tell something else, `say` for the listener's notice)
- `forget` — takes `key`, `who` (needs `key`): {"mind": "memory", "action": "forget", "key": "wolf", "who": "$actor"}  (who, by default $actor, drops a belief)

```json
{"mechanisms": {"my_beliefs": {"kind": "mind", "mode": "beliefs", "who": "villager", "decay": 0.1, "secondhand": 0.6, "share": true}}}
```
