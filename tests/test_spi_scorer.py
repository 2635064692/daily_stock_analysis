import pytest
from src.services.spi.spi_scorer import SpiScorer


class MockFactor:
    def __init__(self, name: str, weight: float, return_val: float):
        self.name = name
        self.weight = weight
        self._val = return_val

    def compute(self, ema_series, last_close):
        return self._val


def _make_uptrend_ema_series():
    """8 EMA periods with ascending values (uptrend)."""
    base = [float(i) * 0.1 + 1.0 for i in range(50)]
    periods = [5, 13, 21, 34, 55, 89, 144, 233]
    return {p: base for p in periods}


def _registry(*factors):
    return {f.name: f for f in factors}


class TestSpiScorerNormalPath:
    def test_score_in_range_uptrend(self):
        scorer = SpiScorer(registry=_registry(
            MockFactor("ema_direction", 0.3, 0.8),
            MockFactor("alignment", 0.3, 0.7),
            MockFactor("separation", 0.25, 0.6),
            MockFactor("compression", 0.15, 0.2),
        ))
        result = scorer.score(_make_uptrend_ema_series(), last_close=10.0)
        assert 0.0 <= result <= 100.0

    def test_all_factors_maximum_returns_100(self):
        scorer = SpiScorer(registry=_registry(
            MockFactor("ema_direction", 0.3, 1.0),
            MockFactor("alignment", 0.3, 1.0),
            MockFactor("separation", 0.25, 1.0),
            MockFactor("compression", 0.15, 1.0),
        ))
        assert scorer.score({}, 0.0) == pytest.approx(100.0)

    def test_all_factors_minimum_returns_0(self):
        scorer = SpiScorer(registry=_registry(
            MockFactor("ema_direction", 0.3, 0.0),
            MockFactor("alignment", 0.3, 0.0),
            MockFactor("separation", 0.25, 0.0),
            MockFactor("compression", 0.15, -1.0),
        ))
        assert scorer.score({}, 0.0) == pytest.approx(0.0)

    def test_compression_max_negative_others_zero_still_non_negative(self):
        scorer = SpiScorer(registry=_registry(
            MockFactor("ema_direction", 0.3, 0.0),
            MockFactor("alignment", 0.3, 0.0),
            MockFactor("separation", 0.25, 0.0),
            MockFactor("compression", 0.15, -1.0),
        ))
        result = scorer.score({}, 0.0)
        assert result >= 0.0

    def test_custom_weights_change_score(self):
        # Higher weight on a factor with value 1.0 should raise the score
        low_weight = SpiScorer(registry=_registry(
            MockFactor("ema_direction", 0.1, 1.0),
            MockFactor("alignment", 0.9, 0.0),
            MockFactor("separation", 0.0, 0.0),
            MockFactor("compression", 0.0, 0.0),
        ))
        high_weight = SpiScorer(registry=_registry(
            MockFactor("ema_direction", 0.9, 1.0),
            MockFactor("alignment", 0.1, 0.0),
            MockFactor("separation", 0.0, 0.0),
            MockFactor("compression", 0.0, 0.0),
        ))
        assert high_weight.score({}, 0.0) > low_weight.score({}, 0.0)

    def test_empty_registry_returns_zero(self):
        scorer = SpiScorer(registry={})
        assert scorer.score({}, 0.0) == 0.0

    def test_empty_ema_series_with_zero_factor_values_returns_zero(self):
        scorer = SpiScorer(registry=_registry(
            MockFactor("ema_direction", 0.3, 0.0),
            MockFactor("alignment", 0.3, 0.0),
            MockFactor("separation", 0.25, 0.0),
            MockFactor("compression", 0.15, -1.0),
        ))
        assert scorer.score({}, 0.0) == pytest.approx(0.0)
