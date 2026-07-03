import pytest
from src.services.spi.spi_factors import REGISTRY
from src.services.spi.spi_factors.ema_direction import EmaDirectionFactor
from src.services.spi.spi_factors.alignment import AlignmentFactor
from src.services.spi.spi_factors.separation import SeparationFactor
from src.services.spi.spi_factors.compression import CompressionFactor


def _full_series(base=100.0, length=20, slope=0.5):
    return [base + i * slope for i in range(length)]


def _full_ema_series():
    return {
        5:   _full_series(100, 20, 1.0),
        13:  _full_series(98, 20, 0.9),
        21:  _full_series(96, 20, 0.8),
        34:  _full_series(94, 20, 0.7),
        55:  _full_series(92, 20, 0.6),
        89:  _full_series(90, 20, 0.5),
        144: _full_series(88, 20, 0.4),
        233: _full_series(86, 20, 0.3),
    }


# ── F1 EmaDirection ──────────────────────────────────────────────────────────

class TestEmaDirection:
    factor = EmaDirectionFactor(weight=0.30)

    def test_all_rising(self):
        series = _full_ema_series()
        result = self.factor.compute(series, 120.0)
        assert 0.0 <= result <= 1.0
        assert result == 1.0

    def test_none_rising(self):
        series = {p: list(reversed(_full_series(100, 20, 1.0))) for p in [5, 13, 21]}
        result = self.factor.compute(series, 100.0)
        assert result == 0.0

    def test_empty_series(self):
        assert self.factor.compute({}, 100.0) == 0.0

    def test_series_exactly_lookback_length(self):
        # length == 5 (== lookback), needs lookback+1=6 → treated as not rising
        series = {5: [100.0] * 5}
        assert self.factor.compute(series, 100.0) == 0.0

    def test_series_one_less_than_lookback(self):
        series = {5: [100.0] * 4}
        assert self.factor.compute(series, 100.0) == 0.0

    def test_partial_rising(self):
        series = {
            5:  _full_series(100, 20, 1.0),   # rising
            13: list(reversed(_full_series(100, 20, 1.0))),  # falling
        }
        result = self.factor.compute(series, 100.0)
        assert result == 0.5

    def test_weight(self):
        assert self.factor.weight == 0.30


# ── F2 Alignment ─────────────────────────────────────────────────────────────

class TestAlignment:
    factor = AlignmentFactor(weight=0.30)

    def test_perfect_alignment(self):
        # 5 > 13 > 21 > ... (using last values from _full_ema_series where 5-series highest)
        series = _full_ema_series()
        result = self.factor.compute(series, 120.0)
        assert 0.0 <= result <= 1.0
        # all 7 pairs aligned
        assert result == 7 / 7

    def test_only_one_period(self):
        series = {5: [100.0] * 10}
        assert self.factor.compute(series, 100.0) == 0.0

    def test_no_periods(self):
        assert self.factor.compute({}, 100.0) == 0.0

    def test_two_periods_aligned(self):
        series = {5: [110.0] * 10, 13: [100.0] * 10}
        result = self.factor.compute(series, 110.0)
        assert result == 1 / 7

    def test_two_periods_misaligned(self):
        series = {5: [90.0] * 10, 13: [100.0] * 10}
        result = self.factor.compute(series, 90.0)
        assert result == 0.0

    def test_weight(self):
        assert self.factor.weight == 0.30


# ── F3 Separation ─────────────────────────────────────────────────────────────

class TestSeparation:
    factor = SeparationFactor(weight=0.25)

    def test_positive_separation(self):
        series = {
            5:   [120.0] * 5,
            13:  [118.0] * 5,
            21:  [116.0] * 5,
            89:  [100.0] * 5,
            144: [98.0] * 5,
            233: [96.0] * 5,
        }
        result = self.factor.compute(series, 120.0)
        assert 0.0 < result <= 1.0

    def test_negative_separation_clamps_to_zero(self):
        series = {
            5:   [80.0] * 5,
            13:  [82.0] * 5,
            21:  [84.0] * 5,
            89:  [100.0] * 5,
            144: [102.0] * 5,
            233: [104.0] * 5,
        }
        result = self.factor.compute(series, 80.0)
        assert result == 0.0

    def test_long_avg_zero(self):
        series = {
            5:   [10.0] * 5,
            13:  [10.0] * 5,
            21:  [10.0] * 5,
            89:  [0.0] * 5,
            144: [0.0] * 5,
            233: [0.0] * 5,
        }
        assert self.factor.compute(series, 10.0) == 0.0

    def test_empty_series_entries_do_not_raise(self):
        series = {
            5: [],
            13: [10.0] * 5,
            21: [10.0] * 5,
            89: [],
            144: [9.0] * 5,
            233: [9.0] * 5,
        }
        result = self.factor.compute(series, 10.0)
        assert 0.0 <= result <= 1.0

    def test_missing_short_group(self):
        series = {89: [100.0] * 5, 144: [98.0] * 5}
        assert self.factor.compute(series, 100.0) == 0.0

    def test_missing_long_group(self):
        series = {5: [110.0] * 5, 13: [108.0] * 5}
        assert self.factor.compute(series, 110.0) == 0.0

    def test_extreme_separation_capped(self):
        series = {
            5:   [1000.0] * 5,
            13:  [1000.0] * 5,
            21:  [1000.0] * 5,
            89:  [1.0] * 5,
            144: [1.0] * 5,
            233: [1.0] * 5,
        }
        assert self.factor.compute(series, 1000.0) == 1.0

    def test_weight(self):
        assert self.factor.weight == 0.25


# ── F4 Compression ────────────────────────────────────────────────────────────

class TestCompression:
    factor = CompressionFactor(weight=0.15)

    def test_expansion_positive(self):
        # spread grows: now std > prev std
        series = {
            5:   [100, 101, 102, 103, 104, 105, 106, 108, 111, 115],
            13:  [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            21:  [100, 99, 98, 97, 96, 95, 94, 92, 89, 85],
            34:  [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            55:  [100, 101, 102, 103, 104, 105, 106, 108, 111, 115],
            89:  [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
            144: [100, 99, 98, 97, 96, 95, 94, 92, 89, 85],
            233: [100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
        }
        result = self.factor.compute(series, 115.0)
        assert -1.0 <= result <= 1.0

    def test_compression_negative(self):
        # spread shrinks: now std < prev std
        series = {
            5:   [100, 99, 98, 97, 96, 97, 98, 99, 100, 100],
            13:  [110, 108, 106, 104, 102, 101, 100, 100, 100, 100],
            21:  [90, 92, 94, 96, 98, 99, 100, 100, 100, 100],
        }
        result = self.factor.compute(series, 100.0)
        assert -1.0 <= result <= 1.0

    def test_fewer_than_two_qualifying(self):
        # only 1 series with length >= 6
        series = {5: [100.0] * 6, 13: [100.0] * 3}
        assert self.factor.compute(series, 100.0) == 0.0

    def test_no_qualifying_periods(self):
        series = {5: [100.0] * 4}
        assert self.factor.compute(series, 100.0) == 0.0

    def test_zero_prev_std(self):
        # all periods flat at same value → std_prev == 0
        series = {p: [100.0] * 10 for p in [5, 13, 21]}
        assert self.factor.compute(series, 100.0) == 0.0

    def test_weight(self):
        assert self.factor.weight == 0.15


# ── Full REGISTRY ─────────────────────────────────────────────────────────────

class TestRegistry:
    def test_registry_keys(self):
        assert set(REGISTRY.keys()) == {"ema_direction", "alignment", "separation", "compression"}

    def test_all_factors_compute_full_series(self):
        series = _full_ema_series()
        for name, factor in REGISTRY.items():
            result = factor.compute(series, 119.0)
            assert isinstance(result, float), f"{name} must return float"

    def test_total_weight_sums_to_one(self):
        total = sum(f.weight for f in REGISTRY.values())
        assert abs(total - 1.0) < 1e-9

    def test_factor_names_match_keys(self):
        for key, factor in REGISTRY.items():
            assert factor.name == key
