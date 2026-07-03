from __future__ import annotations

from typing import List, Optional


def extract_flow(stock_flow: dict) -> Optional[float]:
    for key in ("main_net_inflow", "inflow_5d"):
        raw = stock_flow.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def normalize_flow_scores(
    values: List[Optional[float]],
) -> List[Optional[float]]:
    """Board-relative percentile scores using average-rank method.

    Semantics match relative_strength.calc_rs_scores_nullable:
    None preserved; n<=1 valid → 0.5; n>=2 → (avg_rank-1)/(n-1) ∈ [0,1].
    """
    indices = [i for i, v in enumerate(values) if v is not None]
    n = len(indices)

    result: List[Optional[float]] = [None] * len(values)

    if n == 0:
        return result

    if n == 1:
        result[indices[0]] = 0.5
        return result

    vals = [values[i] for i in indices]  # type: ignore[index]
    sorted_vals = sorted(enumerate(vals), key=lambda x: x[1])

    avg_rank = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and sorted_vals[j + 1][1] == sorted_vals[i][1]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            avg_rank[sorted_vals[k][0]] = rank
        i = j + 1

    for pos, local_idx in enumerate(indices):
        result[local_idx] = (avg_rank[pos] - 1) / (n - 1)

    return result
