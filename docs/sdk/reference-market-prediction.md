# market / prediction

### `market.prediction`
A market on which of several outcomes happens, priced by an automated market maker (lmsr or cpmm). Tools `<name>_buy` and `<name>_sell` trade one outcome by shares or by money; each winning share pays 1 when the market resolves; each trader holds its shares in `<name>_shares` ({outcome: shares}). The market maker's vault starts with seed money (lmsr: liquidity × ln(outcomes), its worst-case loss; cpmm: liquidity), which a ledger counts in its starting supply, not in its flows. It resolves at `resolve_at`, when `resolve_when` holds, or with the op {"market": name, "action": "resolve", "outcome": ...}. Read it with $amm(name), $amm_outcomes(name, viewer) and $amm_cost(name, outcome, shares); metrics <name>_p_<outcome> track prices.

Config:
- `who` (required): Agent type that trades (subtypes included).
- `outcomes` (required): The possible outcomes; exactly one wins.
- `maker` (default "lmsr"): lmsr (logarithmic scoring rule) | cpmm (constant product).
- `liquidity` (default 100): LMSR b (higher = prices move less; the market is seeded with b·ln n) or CPMM starting pool per outcome.
- `currency` (default "cash"): Trader property holding money.
- `fee_pct` (default 0): Fee on each trade's value, to $world.<name>_fees.
- `question` (default ""): What the market is about, shown with prices.
- `resolve_at` (default null): Round at whose end the market resolves (number or expression).
- `resolve_when` (default null): Resolve at the end of the first round this holds.
- `outcome` (default null): Expression giving the winning outcome when the market resolves.
- `stage` (default null): Trade during this declared stage; default: a sequential stage named after the market.
- `max_actions` (default 2): Trades per turn in the generated stage.
- `conserve` (default true): Declare the invariant that the vault covers every share.
- `tools` (default "each"): How the generated tools are offered: each (one tool per action) | one (one tool named after the mechanism, whose `action` argument lists the actions legal now) | auto (one tool only when every action takes the same arguments).

Actions of the `market` op:
- `buy` — takes `who`, `outcome`, `shares`, `spend` (needs `outcome`): {"market": "election", "action": "buy", "outcome": "yes", "spend": 20}  (buy one outcome by `shares` or money (`spend`, the most paid when both are given))
- `sell` — takes `who`, `outcome`, `shares`, `receive` (needs `outcome`): {"market": "election", "action": "sell", "outcome": "yes", "shares": 5}  (sell one outcome by `shares` or money (`receive`, the least accepted when both are given))
- `resolve` — takes `outcome` (needs `outcome`): {"market": "election", "action": "resolve", "outcome": "$world.truth"}  (pay 1 per winning share and close trading)

```json
{"mechanisms": {"my_prediction": {"kind": "market", "mode": "prediction", "who": "forecaster", "outcomes": ["yes", "no"], "maker": "lmsr", "liquidity": 50, "question": "Will the bill pass?", "resolve_at": 5, "outcome": "$world.truth"}}}
```
