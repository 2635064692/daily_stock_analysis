from __future__ import annotations

from typing import List, Optional


def calc_period_return(closes: List[float], period: int = 20) -> Optional[float]:
    """Return the N-period return (close[-1]/close[-period] - 1).

    Returns None if len(closes) < period + 1.
    """
    if len(closes) < period + 1:
        return None
    base = closes[-(period + 1)]
    if base == 0:
        return None
    return closes[-1] / base - 1


def calc_rs_scores_nullable(
    stock_returns: List[Optional[float]],
) -> List[Optional[float]]:
    """Board-relative percentile scores using average-rank method.

    - None inputs are preserved as None in the output at the same position.
    - n (number of valid values) <= 1: the single valid position gets 0.5.
    - n >= 2: percentile = (average_rank - 1) / (n - 1), where tied values
      share the average of their ordinal ranks (1-based).
    - Result order matches input order.
    """
    indices = [i for i, v in enumerate(stock_returns) if v is not None]
    n = len(indices)

    result: List[Optional[float]] = [None] * len(stock_returns)

    if n == 0:
        return result

    if n == 1:
        result[indices[0]] = 0.5
        return result

    values = [stock_returns[i] for i in indices]  # type: ignore[index]
    sorted_vals = sorted(enumerate(values), key=lambda x: x[1])

    avg_rank = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and sorted_vals[j + 1][1] == sorted_vals[i][1]:
            j += 1
        rank = (i + j) / 2 + 1  # 1-based average rank
        for k in range(i, j + 1):
            avg_rank[sorted_vals[k][0]] = rank
        i = j + 1

    for pos, local_idx in enumerate(indices):
        result[local_idx] = (avg_rank[pos] - 1) / (n - 1)

    return result
