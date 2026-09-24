# groups / matching

### `groups.matching`
Two-sided stable matching (deferred acceptance, Gale–Shapley): `who` proposes to `to`, each receiver takes up to `seats`. Both sides rank the other in the private list prop `<name>_prefs` (ids, best first; unlisted = unacceptable): agent types get a `<name>_rank` tool (`<name>_rank_<type>` when both sides are agents), coded entities carry the prop. `eligible` (over $proposer and $receiver, e.g. applications) limits the rank tools and cuts every ranking to eligible partners. When the stage ends (after the contract's own events on its end) the match is stable and the best stable one for every proposer; read it as $it.<name>_match (a proposer's receiver id, '' when unmatched) and $it.<name>_matches (a receiver's proposer ids). Output `<name>_matched`.

Config:
- `who` (required): Type that proposes and is matched to at most one receiver (students, doctors).
- `to` (required): Type that receives proposals (schools, hospitals).
- `seats` (default 1): Proposers one receiver accepts: a number or an expression over $it (the receiver), e.g. "$it.capacity".
- `eligible` (default null): Which pairs may match: an expression over $proposer and $receiver, e.g. "$receiver.id in $proposer.applied". Rank tools offer only eligible partners, and rankings are cut to them before matching.
- `stage` (default null): Rank during this declared stage and match when it ends, after the contract's own events on its end (every time); default: a stage named after the mechanism, once.

```json
{"mechanisms": {"my_matching": {"kind": "groups", "mode": "matching", "who": "student", "to": "school", "seats": "$it.capacity"}}}
```
