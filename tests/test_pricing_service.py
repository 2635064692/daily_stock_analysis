from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

from src.services.pricing_service import (
    PricingService,
    _BASE_W_CMF,
    _BASE_W_CMF_NO_FLOW,
    _BASE_W_FLOW,
    _BASE_W_SP,
    _BASE_W_SP_NO_FLOW,
)


def _bar(h: float, l: float, c: float, v: float):
    return SimpleNamespace(high=h, low=l, close=c, volume=v)


def _make_bars(close: float = 10.0, n: int = 25) -> list:
    return [_bar(close + 1, close - 1, close, 100.0) for _ in range(n)]


def _make_trend_bars(start: float, end: float, n: int = 25) -> list:
    step = (end - start) / max(n - 1, 1)
    closes = [start + step * idx for idx in range(n)]
    return [_bar(close + 1, close - 1, close, 100.0) for close in closes]


def _quote(total_mv: float | None):
    return None if total_mv is None else SimpleNamespace(total_mv=total_mv)


def _fundamental(
    profit: float | None,
    report_date: str | None = "2026-03-31",
):
    return {
        "earnings": {
            "data": {
                "financial_report": {
                    "net_profit_parent": profit,
                    "report_date": report_date,
                }
            }
        }
    }


def _make_repo(run_id: int = 1) -> MagicMock:
    repo = MagicMock()
    repo.insert_factor_run.return_value = run_id
    repo.save_pricing_batch.return_value = run_id
    return repo


def _make_service(repo=None, constituent_repo=None, fetcher=None) -> PricingService:
    return PricingService(
        repo=repo or MagicMock(),
        constituent_repo=constituent_repo or MagicMock(),
        fetcher=fetcher or MagicMock(),
    )


def _manager_for_codes(
    codes: list[str],
    *,
    flow_values: dict[str, float | None] | None = None,
    total_mvs: dict[str, float | None] | None = None,
    profits: dict[str, float | None] | None = None,
    report_dates: dict[str, str | None] | None = None,
) -> MagicMock:
    flow_values = flow_values or {}
    total_mvs = total_mvs or {}
    profits = profits or {}
    report_dates = report_dates or {}
    mgr = MagicMock()

    def _quote_side_effect(code: str):
        return _quote(total_mvs.get(code, 1_000.0))

    def _fundamental_side_effect(code: str):
        return _fundamental(
            profits.get(code, 100.0),
            report_dates.get(code, "2026-03-31"),
        )

    def _flow_side_effect(code: str):
        value = flow_values.get(code, 100.0)
        if value is None:
            raise Exception("no flow")
        return {"data": {"stock_flow": {"main_net_inflow": value}}}

    mgr.get_realtime_quote.side_effect = _quote_side_effect
    mgr.get_fundamental_context.side_effect = _fundamental_side_effect
    mgr.get_capital_flow_context.side_effect = _flow_side_effect
    return mgr


class TestNoConstituents:
    def test_historical_date_missing_returns_failed(self):
        repo = _make_repo(run_id=99)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = []
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with patch("src.services.pricing_service.time.sleep"):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 1, 2))

        assert result["status"] == "failed"
        assert result["stocks"] == []
        assert result["run_id"] == 99
        repo.insert_factor_run.assert_called_once()
        assert repo.insert_factor_run.call_args.kwargs["error"] == "no_constituents"

    def test_today_no_snapshot_fetches_and_saves(self):
        trade_date = date(2024, 6, 3)
        repo = _make_repo(run_id=1)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = []
        fetcher = MagicMock()
        fetcher.fetch.return_value = ["000001", "000002", "000003"]
        mgr = _manager_for_codes(fetcher.fetch.return_value)
        svc = _make_service(repo=repo, constituent_repo=c_repo, fetcher=fetcher)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.spi_time", return_value=trade_date),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=trade_date)

        assert result["constituent_count"] == 3
        fetcher.fetch.assert_called_once_with(801010)
        c_repo.save_constituents.assert_called_once()
        assert repo.save_pricing_batch.call_args.kwargs["constituent_source"] == "current"


class TestFlowCoverage:
    def _run_with_flow_values(self, flow_values: list[float | None]):
        codes = [f"00000{i}" for i in range(len(flow_values))]
        repo = _make_repo(run_id=42)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(
            codes,
            flow_values={code: flow_values[idx] for idx, code in enumerate(codes)},
        )
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        return result, repo.save_pricing_batch.call_args.kwargs

    def test_flow_enabled_when_coverage_at_threshold(self):
        result, run_kw = self._run_with_flow_values([100.0, None, 200.0, None, 300.0])
        assert result["flow_enabled"] is True
        assert result["flow_coverage"] == pytest.approx(0.6)
        assert run_kw["effective_weights"]["sp"] == pytest.approx(_BASE_W_SP)
        assert run_kw["effective_weights"]["cmf"] == pytest.approx(_BASE_W_CMF)
        assert run_kw["effective_weights"]["flow"] == pytest.approx(_BASE_W_FLOW)

    def test_flow_disabled_when_coverage_below_threshold(self):
        result, run_kw = self._run_with_flow_values([100.0, None, None, None, 200.0])
        assert result["flow_enabled"] is False
        assert result["flow_coverage"] == pytest.approx(0.4)
        assert run_kw["effective_weights"]["sp"] == pytest.approx(_BASE_W_SP_NO_FLOW)
        assert run_kw["effective_weights"]["cmf"] == pytest.approx(_BASE_W_CMF_NO_FLOW)

    def test_single_missing_flow_degrades_only_that_stock(self):
        result, _ = self._run_with_flow_values([100.0, 200.0, 300.0, None, 400.0])
        degraded = [item for item in result["stocks"] if item["status"] == "degraded"]
        assert result["flow_enabled"] is True
        assert len(degraded) == 1


class TestCoreFactorRules:
    def test_cmf_none_gives_missing_core_factor_status(self):
        codes = ["000001", "000002"]
        repo = _make_repo(run_id=7)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        short_bars = [_bar(10, 8, 9, 0)] * 2
        fetch_calls = 0

        def _fetch_bars_side_effect(code, td, window):
            nonlocal fetch_calls
            fetch_calls += 1
            return short_bars if fetch_calls == 1 else _make_bars()

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", side_effect=_fetch_bars_side_effect),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        stocks = {item["stock_code"]: item for item in result["stocks"]}
        assert stocks["000001"]["status"] == "missing_core_factor"
        assert stocks["000001"]["total"] is None
        assert stocks["000002"]["status"] == "ok"

    def test_rs_none_is_diagnostic_only_not_core_factor(self):
        codes = ["000001"]
        repo = _make_repo(run_id=1)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        few_bars = [_bar(10, 8, 9, 100)] * 5
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=few_bars),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        assert result["stocks"][0]["status"] == "ok"
        assert result["stocks"][0]["total"] is not None
        snapshots = repo.save_pricing_batch.call_args.kwargs["snapshots"]
        assert snapshots[0]["rs_score"] is None
        assert snapshots[0]["sp_score"] == pytest.approx(0.5)

    def test_missing_profit_gives_missing_core_factor(self):
        codes = ["000001", "000002"]
        repo = _make_repo(run_id=1)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes, profits={"000001": None, "000002": 100.0})
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        stocks = {item["stock_code"]: item for item in result["stocks"]}
        assert stocks["000001"]["status"] == "missing_core_factor"
        assert stocks["000002"]["status"] == "ok"


class TestBatchStatusAndRanking:
    def test_all_missing_core_factor_batch_status_failed(self):
        codes = ["000001", "000002"]
        repo = _make_repo(run_id=8)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        zero_bars = [_bar(10, 8, 9, 0)] * 2
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=zero_bars),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        assert result["status"] == "failed"
        assert result["priced_count"] == 0
        call_kw = repo.save_pricing_batch.call_args.kwargs
        assert call_kw["status"] == "failed"
        assert call_kw["error"] == "all_constituents_missing_core_factor"

    def test_sp_drives_total_even_when_rs_is_weaker(self):
        codes = ["000001", "000002"]
        repo = _make_repo(run_id=11)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(
            codes,
            total_mvs={"000001": 100.0, "000002": 200.0},
            profits={"000001": 10.0, "000002": 10.0},
            flow_values={"000001": 100.0, "000002": 100.0},
        )
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        def _bars_for_code(code, td, window):
            if code == "000001":
                return _make_trend_bars(10.0, 11.0)
            return _make_trend_bars(10.0, 20.0)

        with (
            patch("src.services.pricing_service._fetch_bars", side_effect=_bars_for_code),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        snapshots = {
            item["stock_code"]: item
            for item in repo.save_pricing_batch.call_args.kwargs["snapshots"]
        }
        assert snapshots["000001"]["rs_score"] < snapshots["000002"]["rs_score"]
        assert snapshots["000001"]["sp_score"] > snapshots["000002"]["sp_score"]
        assert snapshots["000001"]["total"] > snapshots["000002"]["total"]


class TestOperationalDetails:
    def test_sleep_called_once_per_constituent(self):
        codes = ["000001", "000002", "000003"]
        repo = _make_repo(run_id=1)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep") as mock_sleep,
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        assert mock_sleep.call_count == len(codes)
        mock_sleep.assert_called_with(0.5)

    def test_batch_save_receives_sp_fields_and_effective_weights(self):
        codes = ["000001", "000002"]
        repo = _make_repo(run_id=5)
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(
            codes,
            total_mvs={"000001": 100.0, "000002": 300.0},
            profits={"000001": 10.0, "000002": 30.0},
            flow_values={"000001": 50.0, "000002": 150.0},
            report_dates={"000001": "2026-03-31", "000002": "2025-12-31"},
        )
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        call_kw = repo.save_pricing_batch.call_args.kwargs
        assert result["run_id"] == 5
        assert call_kw["effective_weights"]["sp"] == pytest.approx(_BASE_W_SP)
        assert call_kw["effective_weights"]["cmf"] == pytest.approx(_BASE_W_CMF)
        assert call_kw["effective_weights"]["flow"] == pytest.approx(_BASE_W_FLOW)
        assert len(call_kw["snapshots"]) == 2
        for snapshot in call_kw["snapshots"]:
            assert "sp_ratio" in snapshot
            assert "sp_score" in snapshot
            assert snapshot["factor_mask"] in {"sp,cmf,flow", "sp,cmf"}
