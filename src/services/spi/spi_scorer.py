from __future__ import annotations

from src.services.spi.spi_factors import REGISTRY


class SpiScorer:
    def __init__(self, registry=None):
        self._registry = registry if registry is not None else REGISTRY

    def score(self, ema_series: dict, last_close: float) -> float:
        """Weighted sum of all registered factors, normalized to [0, 100]."""
        if not self._registry:
            return 0.0

        # Factor output ranges: F1/F2/F3 → [0,1], F4 (compression) → [-1,1]
        # Theoretical min/max per factor based on its own output range
        raw = 0.0
        t_min = 0.0
        t_max = 0.0

        for factor in self._registry.values():
            val = factor.compute(ema_series, last_close)
            w = factor.weight
            raw += w * val
            # compression factor has min -1; all others have min 0
            f_min = -1.0 if getattr(factor, "name", "") == "compression" else 0.0
            t_min += w * f_min
            t_max += w * 1.0

        span = t_max - t_min
        if span == 0.0:
            return 0.0

        normalized = (raw - t_min) / span * 100.0
        return max(0.0, min(100.0, normalized))
