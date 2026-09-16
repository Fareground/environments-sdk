# functions / random

## Functions: random

- `$beta(a, b)` — Beta-distributed number in [0, 1].
- `$binomial(n, p)` — Successes in n independent trials with success probability p.
- `$chance(p)` — True with probability p.
- `$choice(items, weight?)` — One item picked at random; `weight` is a per-item expression ($it), e.g. $choice([a, b], $it == a and 3 or 1).
- `$dice(notation)` — Roll dice written like 3d6+2, d20, 2d8-1 or 4d6kh3 (keep highest 3; kl keeps lowest) and return the total.
- `$dirichlet(alphas)` — Random probabilities (summing to 1) from a Dirichlet with concentration `alphas` (each > 0).
- `$exponential(rate)` — Exponentially distributed number.
- `$gamma(shape, scale)` — Gamma-distributed number (mean shape × scale).
- `$geometric(p)` — Trials up to and including the first success (1, 2, 3, …) with success probability p > 0.
- `$lognormal(mu, sigma)` — Log-normally distributed number.
- `$multinomial(n, weights)` — n draws split across categories in proportion to `weights` (list → list of counts, map → map of counts).
- `$normal(mean, sd)` — Normally distributed number.
- `$poisson(mean)` — Poisson-distributed whole number.
- `$randint(low, high)` — Whole number between low and high inclusive.
- `$random()` — Uniform number in [0, 1).
- `$sample(items, k)` — k distinct items picked at random (all of them when fewer exist).
- `$shuffle(items)` — The items in random order.
- `$triangular(low, high, mode)` — Number between low and high, most likely near `mode`.
- `$truncnormal(mean, sd, low, high)` — Normal number restricted to [low, high] (exact inversion; one draw).
- `$uniform(low, high)` — Uniform number between low and high.
- `$weibull(scale, shape)` — Weibull-distributed number (shape < 1: early failures; > 1: wear-out).
- `$zipf(n, s)` — Whole number 1..n with probability proportional to 1 / k^s (rank 1 most likely).
