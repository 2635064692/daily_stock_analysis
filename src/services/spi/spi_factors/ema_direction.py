from .base import SpiFactor


class EmaDirectionFactor(SpiFactor):
    name = "ema_direction"
    _LOOKBACK = 5

    def __init__(self, weight: float):
        self.weight = weight

    def compute(self, ema_series: dict, last_close: float) -> float:
        if not ema_series:
            return 0.0
        rising = 0
        total = len(ema_series)
        for series in ema_series.values():
            if len(series) >= self._LOOKBACK + 1 and series[-1] > series[-self._LOOKBACK - 1]:
                rising += 1
        return rising / total
