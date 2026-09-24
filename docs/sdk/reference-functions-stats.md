# functions / stats

## Functions: stats

- `$abs_error(forecast, outcome)` — Absolute error |forecast - outcome|; for two equal-length lists, the mean absolute error (pairs with a null skipped; null when none).
- `$autocorr(series, lag)` — Autocorrelation of a series with itself `lag` steps later; null when too short or constant.
- `$brier(forecast, outcome)` — Brier score (lower is better): (p - outcome)² for a probability and a true/false outcome; for a list (outcome = position) or map (outcome = key), the sum over classes (0–2).
- `$corr(xs, ys)` — Pearson correlation of paired lists, -1 to 1; null when fewer than two pairs or a list is constant.
- `$cov(xs, ys)` — Sample covariance of paired lists (pairs with a null skipped); null when fewer than two pairs.
- `$crps(samples, outcome)` — Continuous ranked probability score of a sample forecast (list; nulls skipped) or a point forecast, against the outcome (lower is better; in the outcome's units).
- `$drawdown(series)` — Largest fall from a running peak as a fraction of that peak (0–1), e.g. 0.25 for 120 → 90.
- `$elo(rating_a, rating_b, score_a, k?)` — Elo update after a game: score_a is 1 (A won), 0.5 (draw) or 0 (A lost); gives {a, b, expected_a} with K-factor k (default 32).
- `$ema(series, alpha)` — Exponential moving average, same length: e[0] = x[0], e[t] = alpha·x[t] + (1 - alpha)·e[t-1].
- `$entropy(weights, base?)` — Shannon entropy of probabilities or counts (a list or map; normalised), in bits unless `base` is given; null when all are 0.
- `$excess_kurtosis(values)` — Excess kurtosis of a series (0 for a normal; positive = fat tails).
- `$gini(items, value?)` — Gini inequality of non-negative values, 0 (equal) to near 1 (one holds all); null when none.
- `$hhi(items, value?)` — Herfindahl–Hirschman concentration: the sum of squared shares, 1/n (even) to 1 (one holds all); null when none.
- `$histogram(values, bins, low?, high?)` — Counts in `bins` equal-width bins over [low, high] (default the data range): {edges, counts}; outside values and nulls skipped.
- `$linreg(xs, ys)` — Least-squares line through paired lists: {slope, intercept, r2, n}; null when fewer than two pairs or xs are constant.
- `$log_loss(forecast, outcome, epsilon?)` — Log loss (lower is better): -ln of the probability given to what happened; probabilities are floored at `epsilon` (default 1e-15). Forecast forms as in $brier.
- `$market_realism(series, reference)` — Realism score of a simulated tape against a reference: {score, components} on volatility, fat tails, no return memory, volatility clustering, volume and volume-volatility correlation. Each side is a price list, {prices, volumes} or $market_stats(...).
- `$market_stats(prices, volumes?)` — Stylized facts of a price series: {bars, sigma, mean_return, kurtosis, acf1, acf_abs, max_drawdown, total_return, avg_volume, vol_volume_corr}.
- `$percentile_rank(values, x)` — Share of values below x, counting ties as half (0–1); nulls skipped, null when none.
- `$pool(forecasts, method?, exponent?, weights?)` — Combine forecasts (probabilities, probability lists or maps): linear (weighted mean, default), log (normalised weighted geometric mean) or extremized (log pool raised to `exponent`, default 2.5).
- `$returns(series, kind?)` — Step-to-step returns: simple (default) x[t] / x[t-1] - 1, or log ln(x[t] / x[t-1]); one shorter than the series.
- `$sma(series, n)` — Simple moving average over the last n items, same length (null until n items exist).
- `$tally_votes(method, ballots, options?, threshold?, ties?)` — Count ballots with a voting method (plurality, majority, supermajority, approval, ranked, borda, score, condorcet). ballots: {voter: ballot} or a list; returns {winner, decided, passed, counts, ranking, votes, tie, tied, share, rounds}: decided = there is a winner, passed = the first option won (list a motion's yes first). ties: random (seeded) | none | first.
- `$variance(items, value?, where?)` — Sample variance (n - 1) of `value` over matching items, or of a list; null when fewer than two.
- `$zscore(x, values)` — Standard score (x - mean) / sample sd of the values; null when fewer than two values or no spread.
