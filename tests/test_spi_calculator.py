import numpy as np
import pandas as pd
import pytest
from src.services.spi.spi_calculator import (
    PERIODS,
    cal_index_spi,
    cal_stock_spi,
    cal_stock_spi_v2,
    ema_for_spi,
    ema_for_spi_v2,
)

np.random.seed(42)

# ── helpers ──────────────────────────────────────────────
def _make_linear_series(n=250):
    """Linear uptrend with noise, matching ema-golden-sample §3.1 style."""
    base = np.linspace(0, 100, n)
    noise = np.random.randn(n) * 5
    close = base + noise
    return pd.Series(close)


def _make_oscillate_series(n=250):
    """Oscillating with jumps, matching ema-golden-sample §3.2 style."""
    rng = np.random.default_rng(42)
    close = np.cumsum(rng.standard_normal(n) * 3) + 50
    close[120:130] += 30
    close[180:200] -= 25
    return pd.Series(close)


def _ewm_last(series, span):
    return series.ewm(span=span, adjust=False).mean().iloc[-1]


# ── ema_for_spi ─────────────────────────────────────────
class TestEmaForSpi:
    """§4.6 hard constraints: short-period zero-diff vs ewm, long-period non-zero-diff."""

    def test_short_period_matches_ewm_linear(self):
        s = _make_linear_series(250)
        ema_map = ema_for_spi(s)
        for p in [5, 13, 21, 34]:
            assert abs(ema_map[p] - _ewm_last(s, p)) < 1e-4

    def test_short_period_matches_ewm_oscillate(self):
        s = _make_oscillate_series(250)
        ema_map = ema_for_spi(s)
        for p in [5, 13, 21, 34]:
            assert abs(ema_map[p] - _ewm_last(s, p)) < 1e-4

    def test_long_period_diverges_from_ewm_linear(self):
        s = _make_linear_series(250)
        ema_map = ema_for_spi(s)
        for p in [144, 233]:
            assert abs(ema_map[p] - _ewm_last(s, p)) > 0.1

    def test_long_period_diverges_from_ewm_oscillate(self):
        s = _make_oscillate_series(250)
        ema_map = ema_for_spi(s)
        for p in [144, 233]:
            assert abs(ema_map[p] - _ewm_last(s, p)) > 0.1

    def test_insufficient_data_skipped(self):
        ema_map = ema_for_spi([10, 11, 12, 13])
        assert 5 not in ema_map  # need 5, only 4
        assert 13 not in ema_map
        assert len(ema_map) == 0

    def test_exactly_period_length_included(self):
        ema_map = ema_for_spi([10, 11, 12, 13, 14])
        assert 5 in ema_map
        seed = sum([10, 11, 12, 13, 14]) / 5
        assert abs(ema_map[5] - seed) < 1e-9

    def test_all_periods_present_with_sufficient_data(self):
        s = list(range(250))
        ema_map = ema_for_spi(s)
        assert set(ema_map.keys()) == set(PERIODS)

    

# ── cal_stock_spi ───────────────────────────────────────
class TestCalStockSpi:

    def test_all_above_close_is_8(self):
        ema_map = {p: 10.0 for p in PERIODS}
        assert cal_stock_spi(11.0, ema_map) == 8

    def test_all_below_close_is_0(self):
        ema_map = {p: 100.0 for p in PERIODS}
        assert cal_stock_spi(10.0, ema_map) == 0

    def test_exact_equal_not_counted(self):
        ema_map = {5: 10.0, 13: 10.0}
        assert cal_stock_spi(10.0, ema_map) == 0

    def test_partial_ema_map(self):
        ema_map = {5: 25.0, 13: 22.0}
        assert cal_stock_spi(23.0, ema_map) == 1  # above 13 only

    def test_range_0_to_8(self):
        ema_map = {p: 50.0 for p in [5, 13, 21, 34]}
        assert cal_stock_spi(100, ema_map) == 4
        assert cal_stock_spi(30, ema_map) == 0

    def test_end_to_end_spi_8(self):
        """§3.5: last_close well above all 8 EMAs → SPI=8."""
        s = _make_linear_series(250)
        ema_map = ema_for_spi(s)
        max_ema = max(ema_map.values())
        spi = cal_stock_spi(max_ema + 1.0, ema_map)
        assert spi == 8

    def test_end_to_end_spi_0(self):
        s = _make_linear_series(250)
        ema_map = ema_for_spi(s)
        min_ema = min(ema_map.values())
        spi = cal_stock_spi(min_ema - 1.0, ema_map)
        assert spi == 0

    def test_end_to_end_spi_varying(self):
        s = _make_linear_series(250)
        ema_map = ema_for_spi(s)
        last = s.iloc[-1]
        spi = cal_stock_spi(last, ema_map)
        assert 0 <= spi <= 8


# ── cal_index_spi ───────────────────────────────────────
class TestCalIndexSpi:

    def test_insufficient_data_returns_neg1(self):
        """close series shorter than min period (5) → -1 sentinel."""
        assert cal_index_spi([10, 11, 12, 13]) == -1

    def test_sufficient_data_returns_valid_spi(self):
        """close series ≥ 233 → integer in [0, 8]."""
        s = _make_linear_series(250)
        result = cal_index_spi(s)
        assert isinstance(result, int)
        assert 0 <= result <= 8

    def test_all_above_all_ema_returns_8(self):
        """Construct close where last value is well above all EMAs → 8."""
        s = _make_linear_series(250)
        ema_map = ema_for_spi(s)
        max_ema = max(ema_map.values())
        # Replace last close with a value above all EMAs
        s.iloc[-1] = max_ema + 10.0
        assert cal_index_spi(s) == 8

    def test_all_below_all_ema_returns_0(self):
        """Construct close where last value is below all EMAs → 0."""
        s = _make_linear_series(250)
        ema_map = ema_for_spi(s)
        min_ema = min(ema_map.values())
        s.iloc[-1] = min_ema - 10.0
        assert cal_index_spi(s) == 0

    def test_returns_neg1_on_empty_series(self):
        assert cal_index_spi([]) == -1


# ── ema_for_spi_v2 ──────────────────────────────────────
class TestEmaForSpiV2:

    def test_returns_dict_of_lists(self):
        s = _make_linear_series(250)
        result = ema_for_spi_v2(s)
        for v in result.values():
            assert isinstance(v, list)

    def test_list_length(self):
        s = _make_linear_series(250)
        result = ema_for_spi_v2(s)
        for period, lst in result.items():
            assert len(lst) == 250 - period + 1

    def test_insufficient_period_excluded(self):
        result = ema_for_spi_v2(list(range(10)))
        assert 5 in result
        assert len(result[5]) == 6
        for p in [13, 21, 34, 55, 89, 144, 233]:
            assert p not in result

    def test_empty_series_returns_empty(self):
        assert ema_for_spi_v2([]) == {}

    def test_values_are_floats(self):
        s = _make_linear_series(250)
        result = ema_for_spi_v2(s)
        for lst in result.values():
            for v in lst:
                assert isinstance(v, float)

    def test_last_value_matches_ema_for_spi(self):
        s = _make_linear_series(250)
        scalar_map = ema_for_spi(s)
        series_map = ema_for_spi_v2(s)
        for period in scalar_map:
            assert abs(series_map[period][-1] - scalar_map[period]) < 1e-9

    def test_exact_period_returns_seed_value(self):
        closes = [10, 11, 12, 13, 14]
        result = ema_for_spi_v2(closes, periods=[5])
        assert result == {5: [sum(closes) / 5]}


# ── cal_stock_spi_v2 ─────────────────────────────────────
class TestCalStockSpiV2:

    def test_score_in_range(self):
        s = _make_linear_series(250)
        score = cal_stock_spi_v2(s)
        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0

    def test_empty_series_returns_zero(self):
        assert cal_stock_spi_v2([]) == 0.0

    def test_exact_period_series_does_not_crash(self):
        score = cal_stock_spi_v2([1, 2, 3, 4, 5], periods=[5])
        assert 0.0 <= score <= 100.0
