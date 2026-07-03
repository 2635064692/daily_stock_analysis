import math
from .base import SpiFactor

_PREV_LOOKBACK = 5


class CompressionFactor(SpiFactor):
    name = "compression"

    def __init__(self, weight: float):
        self.weight = weight

    def compute(self, ema_series: dict, last_close: float) -> float:
        ema_now, ema_prev = [], []
        for series in ema_series.values():
            if len(series) >= _PREV_LOOKBACK + 1:
                ema_now.append(series[-1])
                ema_prev.append(series[-_PREV_LOOKBACK - 1])
        if len(ema_now) < 2:
            return 0.0
        n = len(ema_now)
        mean_now = sum(ema_now) / n
        mean_prev = sum(ema_prev) / n
        std_now = math.sqrt(sum((x - mean_now) ** 2 for x in ema_now) / n)
        std_prev = math.sqrt(sum((x - mean_prev) ** 2 for x in ema_prev) / n)
        if std_prev == 0:
            return 0.0
        raw = (std_now - std_prev) / std_prev
        return max(-1.0, min(1.0, raw))
