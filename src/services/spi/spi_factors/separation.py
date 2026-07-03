from .base import SpiFactor

_SHORT_PERIODS = [5, 13, 21]
_LONG_PERIODS = [89, 144, 233]


class SeparationFactor(SpiFactor):
    name = "separation"

    def __init__(self, weight: float):
        self.weight = weight

    def compute(self, ema_series: dict, last_close: float) -> float:
        short_vals = [ema_series[p][-1] for p in _SHORT_PERIODS if p in ema_series]
        long_vals = [ema_series[p][-1] for p in _LONG_PERIODS if p in ema_series]
        if not short_vals or not long_vals:
            return 0.0
        short_avg = sum(short_vals) / len(short_vals)
        long_avg = sum(long_vals) / len(long_vals)
        if long_avg == 0:
            return 0.0
        raw = (short_avg - long_avg) / abs(long_avg)
        return max(0.0, min(1.0, raw))
