from __future__ import annotations

import pytest

from src.services.pricing.sp_ratio import calc_sp_ratio, is_eligible_for_sp, sp_ratio_to_score


def test_is_eligible_for_sp_requires_positive_market_cap_and_profit():
    assert is_eligible_for_sp(100.0, 10.0) is True
    assert is_eligible_for_sp(None, 10.0) is False
    assert is_eligible_for_sp(100.0, None) is False
    assert is_eligible_for_sp(0.0, 10.0) is False
    assert is_eligible_for_sp(100.0, 0.0) is False


def test_calc_sp_ratio_returns_expected_share_ratio():
    result = calc_sp_ratio(
        stock_mktcap=100.0,
        board_total_mktcap=300.0,
        stock_profit=10.0,
        board_total_profit=20.0,
    )
    assert result == pytest.approx((100.0 / 300.0) / (10.0 / 20.0))


def test_calc_sp_ratio_returns_none_for_invalid_denominator_or_inputs():
    assert calc_sp_ratio(100.0, 0.0, 10.0, 20.0) is None
    assert calc_sp_ratio(100.0, 300.0, 10.0, 0.0) is None
    assert calc_sp_ratio(None, 300.0, 10.0, 20.0) is None
    assert calc_sp_ratio(100.0, 300.0, None, 20.0) is None


def test_sp_ratio_to_score_maps_lower_sp_to_higher_score():
    assert sp_ratio_to_score(0.5) == pytest.approx(1 / 1.5)
    assert sp_ratio_to_score(1.0) == pytest.approx(0.5)
    assert sp_ratio_to_score(2.0) == pytest.approx(1 / 3.0)
    assert sp_ratio_to_score(None) is None
