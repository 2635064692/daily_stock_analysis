import pytest
from src.services.pricing.capital_proxy import extract_flow, normalize_flow_scores


# ── extract_flow ─────────────────────────────────────────────────────────────

class TestExtractFlow:
    def test_main_net_inflow_only(self):
        assert extract_flow({"main_net_inflow": 500.0}) == pytest.approx(500.0)

    def test_inflow_5d_fallback(self):
        assert extract_flow({"inflow_5d": 300.0}) == pytest.approx(300.0)

    def test_main_net_inflow_priority_over_inflow_5d(self):
        assert extract_flow({"main_net_inflow": 100.0, "inflow_5d": 999.0}) == pytest.approx(100.0)

    def test_both_none_returns_none(self):
        assert extract_flow({"main_net_inflow": None, "inflow_5d": None}) is None

    def test_both_absent_returns_none(self):
        assert extract_flow({}) is None

    def test_string_number_converts_to_float(self):
        assert extract_flow({"main_net_inflow": "123.4"}) == pytest.approx(123.4)

    def test_main_net_inflow_non_numeric_falls_back_to_inflow_5d(self):
        assert extract_flow({"main_net_inflow": "abc", "inflow_5d": 200.0}) == pytest.approx(200.0)

    def test_both_non_numeric_returns_none(self):
        assert extract_flow({"main_net_inflow": "abc", "inflow_5d": "xyz"}) is None

    def test_main_net_inflow_none_falls_back_to_inflow_5d(self):
        assert extract_flow({"main_net_inflow": None, "inflow_5d": 150.0}) == pytest.approx(150.0)

    def test_negative_value(self):
        assert extract_flow({"main_net_inflow": -800.0}) == pytest.approx(-800.0)

    def test_zero_value(self):
        assert extract_flow({"main_net_inflow": 0.0}) == pytest.approx(0.0)

    def test_irrelevant_fields_ignored(self):
        assert extract_flow({"inflow_10d": 999.0}) is None

    def test_extra_fields_do_not_affect_result(self):
        result = extract_flow({"main_net_inflow": 42.0, "inflow_10d": 999.0, "other": "x"})
        assert result == pytest.approx(42.0)


# ── normalize_flow_scores ─────────────────────────────────────────────────────

class TestNormalizeFlowScores:
    def test_basic_percentile_ordering(self):
        # lowest→0.0, middle→0.5, highest→1.0
        result = normalize_flow_scores([100.0, 300.0, 500.0])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(1.0)

    def test_output_length_matches_input(self):
        inp = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert len(normalize_flow_scores(inp)) == len(inp)

    def test_single_valid_value_returns_half(self):
        assert normalize_flow_scores([42.0]) == [pytest.approx(0.5)]

    def test_empty_list_returns_empty(self):
        assert normalize_flow_scores([]) == []

    def test_all_none_returns_all_none(self):
        result = normalize_flow_scores([None, None, None])
        assert result == [None, None, None]

    def test_none_positions_preserved(self):
        result = normalize_flow_scores([100.0, None, 500.0])
        assert result[1] is None
        assert result[0] == pytest.approx(0.0)
        assert result[2] == pytest.approx(1.0)

    def test_none_with_single_valid_returns_half(self):
        result = normalize_flow_scores([None, 200.0, None])
        assert result[0] is None
        assert result[1] == pytest.approx(0.5)
        assert result[2] is None

    def test_tied_values_use_average_rank(self):
        # ranks 1 and 2 → avg 1.5 → (1.5-1)/(2-1) = 0.5
        result = normalize_flow_scores([300.0, 300.0])
        assert result[0] == pytest.approx(0.5)
        assert result[1] == pytest.approx(0.5)

    def test_three_values_two_tied(self):
        # [100, 300, 300, 500] n=4
        # sorted: 100(rank1), 300(avg2.5), 300(avg2.5), 500(rank4)
        # percentile: (rank-1)/3 → 0.0, 0.5, 0.5, 1.0
        result = normalize_flow_scores([100.0, 300.0, 300.0, 500.0])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(0.5)
        assert result[3] == pytest.approx(1.0)

    def test_all_tied_returns_half(self):
        # n=3 all tied: avg_rank=2.0 → (2.0-1)/2 = 0.5
        result = normalize_flow_scores([200.0, 200.0, 200.0])
        for v in result:
            assert v == pytest.approx(0.5)

    def test_scores_in_zero_one_range(self):
        import random
        random.seed(1)
        inp = [random.uniform(-1e6, 1e6) for _ in range(20)]
        result = normalize_flow_scores(inp)
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_none_interspersed_percentiles_correct(self):
        # valid at idx 1, 3, 5: [100, 300, 500]
        result = normalize_flow_scores([None, 100.0, None, 300.0, None, 500.0])
        assert result[0] is None
        assert result[2] is None
        assert result[4] is None
        assert result[1] == pytest.approx(0.0)
        assert result[3] == pytest.approx(0.5)
        assert result[5] == pytest.approx(1.0)

    def test_negative_values_rank_correctly(self):
        result = normalize_flow_scores([-500.0, 0.0, 500.0])
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.5)
        assert result[2] == pytest.approx(1.0)
