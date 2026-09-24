# functions / math

## Functions: math

- `$abs(x)` — Absolute value.
- `$acos(x)` — Arc cosine in radians, for x in [-1, 1].
- `$asin(x)` — Arc sine in radians, for x in [-1, 1].
- `$atan(x)` — Arc tangent in radians.
- `$atan2(y, x)` — Angle in radians of the point (x, y), in (-pi, pi].
- `$ceil(x)` — Round up to a whole number.
- `$clamp(x, low, high)` — x limited to the range [low, high].
- `$comb(n, k)` — Ways to choose k of n items, ignoring order (0 when k > n).
- `$cos(x)` — Cosine of x (radians).
- `$erf(x)` — Error function; the standard normal CDF is (1 + $erf(x / $sqrt(2))) / 2.
- `$exp(x)` — e to the power x.
- `$factorial(n)` — n! for a whole number n ≥ 0 (limited by the whole-number size limit).
- `$floor(x)` — Round down to a whole number.
- `$gcd(a, b, ...)` — Greatest common divisor of whole numbers (0 when all are 0).
- `$interp(x, xs, ys)` — Piecewise-linear y at x through the points (xs, ys); xs strictly increasing; flat beyond the ends.
- `$lcm(a, b, ...)` — Least common multiple of whole numbers (0 when any is 0).
- `$log(x, base?)` — Logarithm of x: natural by default, or in `base` (e.g. 2 or 10).
- `$logit(p)` — Log-odds ln(p / (1 - p)) of a probability strictly between 0 and 1.
- `$logsumexp(scores)` — ln(Σ e^score), computed without overflow; null for an empty list.
- `$pct(part, whole)` — part / whole, or 0 when whole is 0.
- `$pi()` — The constant pi (3.14159…).
- `$round(x, digits?)` — Round to `digits` decimals (default 0 → whole number); a half rounds away from zero, as money does: 2.5 → 3, 0.125 → 0.13 (a number rounds as it is written).
- `$sigmoid(x)` — Logistic function 1 / (1 + e^-x), in (0, 1).
- `$sign(x)` — -1, 0 or 1 by the sign of x.
- `$sin(x)` — Sine of x (radians).
- `$softmax(scores, temperature?)` — Probabilities proportional to e^(score / temperature) (default temperature 1); sums to 1.
- `$sqrt(x)` — Square root.
- `$tan(x)` — Tangent of x (radians).
- `$tanh(x)` — Hyperbolic tangent, in (-1, 1).
