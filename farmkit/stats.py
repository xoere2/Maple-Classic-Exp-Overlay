"""Confidence intervals for small samples (no SciPy needed).

Rare drops are the typical case: 1 Mokbi set in 2,000 Jr. Wraith kills says the rate is
"somewhere around 1 in 2,000", but a 95% interval runs from about 1 in 79,000 to 1 in 360.
"""
import math


def _binom_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p), summed in log space so big n is fine."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 0.0 if k < n else 1.0
    lp, lq, total = math.log(p), math.log1p(-p), 0.0
    for i in range(0, k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * lp + (n - i) * lq)
    return min(1.0, total)


def _bisect(f, lo, hi, iters=80):
    for _ in range(iters):
        mid = (lo + hi) / 2
        if f(mid):
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def clopper_pearson(k, n, conf=0.95):
    """Exact (conservative) interval for a proportion: k successes in n tries."""
    if n <= 0:
        return None
    a = (1 - conf) / 2
    lo = 0.0 if k == 0 else _bisect(lambda p: 1 - _binom_cdf(k - 1, n, p) >= a, 0.0, 1.0)
    hi = 1.0 if k == n else _bisect(lambda p: _binom_cdf(k, n, p) <= a, 0.0, 1.0)
    return lo, hi


def wilson(k, n, conf=0.95):
    if n <= 0:
        return None
    z = _z(conf)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _z(conf):
    return {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}.get(round(conf, 2), 1.96)


def poisson_rate_interval(count, seconds, conf=0.95):
    """Interval for events per hour, from `count` events in `seconds` (exact, via the
    chi-square/gamma link computed by bisection on the Poisson CDF)."""
    if seconds <= 0:
        return None
    a = (1 - conf) / 2

    def cdf(k, mu):
        if k < 0:
            return 0.0
        return sum(math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1)) for i in range(k + 1)) if mu > 0 else 1.0
    top = max(10.0, count * 3 + 20)
    lo = 0.0 if count == 0 else _bisect(lambda mu: 1 - cdf(count - 1, mu) >= a, 0.0, top)
    hi = _bisect(lambda mu: cdf(count, mu) <= a, 0.0, top)
    h = seconds / 3600
    return lo / h, hi / h


def drop_rate_summary(drops, kills, conf=0.95):
    """Plain-language observed rate for a drop: '1 in 2,000 observed (95%: 1 in 79,000 to 1 in 360)'."""
    if kills <= 0:
        return {"rate": None, "interval": None, "text": "no kills observed yet"}
    lo, hi = clopper_pearson(drops, kills, conf)
    rate = drops / kills

    def one_in(p):
        return "never" if p <= 0 else f"1 in {1 / p:,.0f}"
    text = (f"{drops} in {kills:,} kills observed ({one_in(rate)}); {conf:.0%} interval "
            f"{one_in(lo)} to {one_in(hi)}")
    if drops < 5:
        text += ". Too few drops to pin down the real rate."
    return {"rate": rate, "interval": (lo, hi), "text": text}
