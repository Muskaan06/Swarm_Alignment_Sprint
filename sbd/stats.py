"""Small exact statistics, stdlib only."""
import math


def _binom_tail(k, n, p):
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def clopper_pearson(successes, trials, alpha=0.05):
    """Exact two-sided confidence interval for a binomial proportion."""
    if trials == 0:
        return 0.0, 1.0

    def solve(target_above):
        low, high = 0.0, 1.0
        for _ in range(60):
            middle = (low + high) / 2
            if target_above(middle):
                high = middle
            else:
                low = middle
        return (low + high) / 2

    lower = 0.0 if successes == 0 else solve(lambda p: _binom_tail(successes, trials, p) >= alpha / 2)
    upper = 1.0 if successes == trials else solve(lambda p: _binom_tail(successes + 1, trials, p) >= 1 - alpha / 2)
    return lower, upper


def bit_error_rate(sent_hex, found_hex, bits=32):
    if found_hex is None:
        return None
    return bin(int(sent_hex, 16) ^ int(found_hex, 16)).count("1") / bits


def binary_entropy(p):
    if p <= 0 or p >= 1:
        return 0.0
    return -p * math.log2(p) - (1 - p) * math.log2(1 - p)


def capacity_bits_per_second(ber, slot_seconds):
    """Binary symmetric channel capacity, floored at zero."""
    return max(0.0, 1 - binary_entropy(min(ber, 0.5))) / slot_seconds


def verdict(delivered, trials):
    """OPEN needs at least 80% delivery, CLOSED needs none, PARTIAL is the rest."""
    if trials == 0:
        return "NOT_RUN"
    if delivered == 0:
        return "CLOSED"
    return "OPEN" if delivered / trials >= 0.8 else "PARTIAL"
