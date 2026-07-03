from .base import SpiFactor

_ORDERED_PERIODS = [5, 13, 21, 34, 55, 89, 144, 233]
_MAX_PAIRS = 7


class AlignmentFactor(SpiFactor):
    name = "alignment"

    def __init__(self, weight: float):
        self.weight = weight

    def compute(self, ema_series: dict, last_close: float) -> float:
        available = [p for p in _ORDERED_PERIODS if p in ema_series]
        if len(available) < 2:
            return 0.0
        satisfied = sum(
            1 for i in range(len(available) - 1)
            if ema_series[available[i]][-1] > ema_series[available[i + 1]][-1]
        )
        return satisfied / _MAX_PAIRS
