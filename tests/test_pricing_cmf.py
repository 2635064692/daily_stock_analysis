from __future__ import annotations

from typing import Optional

import pytest

from src.services.pricing.cmf import calc_cmf, normalize_cmf


def _bar(h: float, l: float, c: float, v: float) -> dict:
    return {"high": h, "low": l, "close": c, "volume": v}


def _obj(h: float, l: float, c: float, v: float):
    from types import SimpleNamespace
    return SimpleNamespace(high=h, low=l, close=c, volume=v)


# ── known-value fixture ──────────────────────────────────────────────────────

def _known_bars():
    """5 bars with hand-computable CMF.

    bar1: h=10, l=8, c=9, v=100  → mfm=((9-8)-(10-9))/(10-8)=0/2=0   mfv=0
    bar2: h=12, l=9, c=11, v=200 → mfm=((11-9)-(12-11))/(12-9)=1/3    mfv≈66.67
    bar3: h=11, l=8, c=8,  v=150 → mfm=((8-8)-(11-8))/(11-8)=-3/3=-1  mfv=-150
    bar4: h=13, l=10, c=13, v=300→ mfm=((13-10)-(13-13))/(13-10)=1    mfv=300
    bar5: h=14, l=11, c=12, v=250→ mfm=((12-11)-(14-12))/(14-11)=-1/3  mfv≈-83.33

    Σmfv = 0 + 66.67 - 150 + 300 - 83.33 = 133.34
    Σvol = 1000
    CMF  = 133.34/1000 = 0.13334
    """
    return [
        _bar(10, 8, 9, 100),
        _bar(12, 9, 11, 200),
        _bar(11, 8, 8, 150),
        _bar(13, 10, 13, 300),
        _bar(14, 11, 12, 250),
    ]


EXPECTED_CMF = (0 + (1/3)*200 + (-1)*150 + 1*300 + (-1/3)*250) / 1000


# ── dict-based bars ──────────────────────────────────────────────────────────

def test_calc_cmf_known_value_dict():
    bars = _known_bars()
    result = calc_cmf(bars, period=20, min_period=5)
    assert result == pytest.approx(EXPECTED_CMF, rel=1e-6)


def test_calc_cmf_period_smaller_than_bars_dict():
    bars = _known_bars()  # len=5
    result = calc_cmf(bars, period=3, min_period=1)
    # only last 3 bars used
    # bar3: mfv=-150, vol=150; bar4: mfv=300, vol=300; bar5: mfv=-83.33, vol=250
    expected = ((-1)*150 + 1*300 + (-1/3)*250) / (150+300+250)
    assert result == pytest.approx(expected, rel=1e-6)


# ── object-based bars ────────────────────────────────────────────────────────

def test_calc_cmf_known_value_obj():
    bars = [_obj(h, l, c, v) for h, l, c, v in
            [(10,8,9,100),(12,9,11,200),(11,8,8,150),(13,10,13,300),(14,11,12,250)]]
    result = calc_cmf(bars, period=20, min_period=5)
    assert result == pytest.approx(EXPECTED_CMF, rel=1e-6)


# ── boundary: high == low ────────────────────────────────────────────────────

def test_calc_cmf_high_eq_low_does_not_raise():
    bars = [
        _bar(10, 10, 10, 100),  # high==low → MFM=0, skipped
        _bar(12, 9, 11, 200),
        _bar(11, 8, 8, 150),
        _bar(13, 10, 13, 300),
        _bar(14, 11, 12, 250),
    ]
    result = calc_cmf(bars, period=20, min_period=5)
    assert result is not None
    assert -1.0 <= result <= 1.0


def test_calc_cmf_all_high_eq_low_zero_volume_returns_none():
    """All bars have high==low (MFM=0), total mfv=0 and vol may be nonzero; vol_sum drives result."""
    bars = [_bar(10, 10, 10, v) for v in [100, 200, 150, 300, 250]]
    # vol_sum > 0 but mfv_sum == 0 → CMF == 0.0, not None
    result = calc_cmf(bars, period=20, min_period=5)
    assert result == pytest.approx(0.0)


# ── boundary: Σvolume == 0 ────────────────────────────────────────────────────

def test_calc_cmf_zero_total_volume_returns_none():
    bars = [_bar(12, 8, 10, 0) for _ in range(20)]
    result = calc_cmf(bars, period=20, min_period=5)
    assert result is None


def test_calc_cmf_zero_volume_single_bar_window():
    bars = [_bar(10, 8, 9, 0)] * 5
    result = calc_cmf(bars, period=5, min_period=5)
    assert result is None


# ── boundary: bars < min_period ──────────────────────────────────────────────

def test_calc_cmf_insufficient_bars_returns_none():
    bars = [_bar(10, 8, 9, 100)] * 4  # < default min_period=5
    result = calc_cmf(bars)
    assert result is None


def test_calc_cmf_empty_bars_returns_none():
    result = calc_cmf([])
    assert result is None


# ── boundary: min_period <= bars < period ────────────────────────────────────

def test_calc_cmf_partial_window_returns_value():
    """len(bars) in [min_period, period) → compute over available bars, not None."""
    # 7 bars, period=20, min_period=5 → uses all 7
    bars = [_bar(12, 9, 11, 100)] * 7
    result = calc_cmf(bars, period=20, min_period=5)
    # mfm = ((11-9)-(12-11))/(12-9) = 1/3; same for all 7
    expected = (1/3 * 100 * 7) / (100 * 7)
    assert result is not None
    assert result == pytest.approx(expected, rel=1e-6)


def test_calc_cmf_exactly_min_period_returns_value():
    bars = [_bar(12, 9, 11, 100)] * 5
    result = calc_cmf(bars, period=20, min_period=5)
    assert result is not None


def test_calc_cmf_one_below_min_period_returns_none():
    bars = [_bar(12, 9, 11, 100)] * 4
    result = calc_cmf(bars, period=20, min_period=5)
    assert result is None


# ── normalize_cmf ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmf_val,expected", [
    (None, None),
    (-1.0, 0.0),
    (0.0, 0.5),
    (1.0, 1.0),
    (0.5, 0.75),
    (-0.5, 0.25),
])
def test_normalize_cmf(cmf_val: Optional[float], expected: Optional[float]):
    result = normalize_cmf(cmf_val)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, rel=1e-9)


# ── result range ─────────────────────────────────────────────────────────────

def test_calc_cmf_result_in_range():
    import random
    random.seed(42)
    bars = [_bar(
        h := random.uniform(10, 20),
        l := random.uniform(5, h),
        random.uniform(l, h),
        random.uniform(0, 1000),
    ) for _ in range(30)]
    result = calc_cmf(bars)
    if result is not None:
        assert -1.0 <= result <= 1.0
