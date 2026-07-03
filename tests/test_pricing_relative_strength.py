import pytest
from src.services.pricing.relative_strength import calc_period_return, calc_rs_scores_nullable


# ── calc_period_return ───────────────────────────────────────────────────────

class TestCalcPeriodReturn:
    def test_exact_value(self):
        # 21 closes: base is closes[0]=100, last is closes[20]=110
        closes = [100.0] + [0.0] * 19 + [110.0]
        result = calc_period_return(closes, period=20)
        assert result == pytest.approx(0.10)

    def test_default_period_20(self):
        closes = [50.0] * 20 + [60.0]
        assert calc_period_return(closes) == pytest.approx(0.20)

    def test_insufficient_length_returns_none(self):
        # need period+1=21, only 20
        closes = [100.0] * 20
        assert calc_period_return(closes, period=20) is None

    def test_exactly_period_plus_one_is_valid(self):
        closes = [80.0] + [0.0] * 19 + [100.0]
        result = calc_period_return(closes, period=20)
        assert result == pytest.approx(0.25)

    def test_base_zero_returns_none(self):
        closes = [0.0] + [0.0] * 19 + [100.0]
        assert calc_period_return(closes, period=20) is None

    def test_negative_return(self):
        closes = [100.0] + [0.0] * 19 + [80.0]
        result = calc_period_return(closes, period=20)
        assert result == pytest.approx(-0.20)

    def test_empty_list_returns_none(self):
        assert calc_period_return([], period=20) is None

    def test_period_one(self):
        closes = [100.0, 120.0]
        assert calc_period_return(closes, period=1) == pytest.approx(0.20)


# ── calc_rs_scores_nullable ──────────────────────────────────────────────────

class TestCalcRsScoresNullable:
    def test_basic_percentile_ordering(self):
        # 3 values: lowest→0.0, middle→0.5, highest→1.0
        result = calc_rs_scores_nullable([0.1, 0.3, 0.5])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(1.0)

    def test_output_length_matches_input(self):
        inp = [0.1, 0.2, 0.3, 0.4, 0.5]
        assert len(calc_rs_scores_nullable(inp)) == len(inp)

    def test_single_valid_value_returns_half(self):
        result = calc_rs_scores_nullable([0.15])
        assert result == [0.5]

    def test_empty_list_returns_empty(self):
        assert calc_rs_scores_nullable([]) == []

    def test_all_none_returns_all_none(self):
        result = calc_rs_scores_nullable([None, None, None])
        assert result == [None, None, None]

    def test_none_positions_preserved(self):
        # indices 0 and 2 are valid, index 1 is None
        result = calc_rs_scores_nullable([0.1, None, 0.3])
        assert result[1] is None
        assert result[0] == pytest.approx(0.0)
        assert result[2] == pytest.approx(1.0)

    def test_none_with_single_valid_returns_half(self):
        result = calc_rs_scores_nullable([None, 0.5, None])
        assert result[0] is None
        assert result[1] == pytest.approx(0.5)
        assert result[2] is None

    def test_tied_values_use_average_rank(self):
        # Two equal values: ranks 1 and 2 → avg rank 1.5 → percentile (1.5-1)/(2-1)=0.5
        result = calc_rs_scores_nullable([0.2, 0.2])
        assert result[0] == pytest.approx(0.5)
        assert result[1] == pytest.approx(0.5)

    def test_three_values_middle_two_tied(self):
        # lowest→rank1=0.0, two tied middle→avg rank 2.5→(1.5/2)=0.75, highest→rank3=... wait
        # values: [0.1, 0.3, 0.3, 0.5]  n=4
        # sorted: 0.1(rank1), 0.3(rank2,3→avg2.5), 0.3(rank2,3→avg2.5), 0.5(rank4)
        # percentile: (rank-1)/(n-1)
        # 0.1 → (1-1)/3=0.0, 0.3 → (2.5-1)/3=0.5, 0.5 → (4-1)/3=1.0
        result = calc_rs_scores_nullable([0.1, 0.3, 0.3, 0.5])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(0.5)
        assert result[3] == pytest.approx(1.0)

    def test_all_values_tied(self):
        # All tied → all get percentile (avg_rank-1)/(n-1)
        # For n=3 all tied: avg_rank = (1+2+3)/3 = 2.0 → (2.0-1)/2 = 0.5
        result = calc_rs_scores_nullable([0.3, 0.3, 0.3])
        for v in result:
            assert v == pytest.approx(0.5)

    def test_scores_in_zero_one_range(self):
        import random
        random.seed(0)
        inp = [random.uniform(-0.5, 0.5) for _ in range(20)]
        result = calc_rs_scores_nullable(inp)
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_none_interspersed_non_none_percentiles_correct(self):
        # [None, 0.1, None, 0.5, None, 0.9] → valid: [0.1, 0.5, 0.9] at idx 1,3,5
        result = calc_rs_scores_nullable([None, 0.1, None, 0.5, None, 0.9])
        assert result[0] is None
        assert result[2] is None
        assert result[4] is None
        assert result[1] == pytest.approx(0.0)
        assert result[3] == pytest.approx(0.5)
        assert result[5] == pytest.approx(1.0)
