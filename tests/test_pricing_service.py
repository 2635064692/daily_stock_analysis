from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Stub out modules unavailable in the test environment before any src import.
for _mod in ("dotenv",):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# dotenv.load_dotenv / dotenv_values must be callable
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

from src.services.pricing_service import (
    PricingService,
    _BASE_W_CMF,
    _BASE_W_CMF_NO_FLOW,
    _BASE_W_FLOW,
    _BASE_W_RS,
    _BASE_W_RS_NO_FLOW,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _bar(h: float, l: float, c: float, v: float):
    return SimpleNamespace(high=h, low=l, close=c, volume=v)


def _make_bars(close: float = 10.0, n: int = 25) -> list:
    """Bars with distinct high/low to yield a non-None CMF."""
    return [_bar(close + 1, close - 1, close, 100.0) for _ in range(n)]


def _make_repo(run_id: int = 1) -> MagicMock:
    repo = MagicMock()
    repo.insert_factor_run.return_value = run_id
    repo.save_pricing_batch.return_value = run_id
    return repo


def _make_service(
    repo=None,
    constituent_repo=None,
    fetcher=None,
) -> PricingService:
    return PricingService(
        repo=repo or MagicMock(),
        constituent_repo=constituent_repo or MagicMock(),
        fetcher=fetcher or MagicMock(),
    )


def _mock_capital_flow(value: float | None):
    """Returns a mock manager whose get_capital_flow_context returns the given value."""
    mgr = MagicMock()
    if value is None:
        mgr.get_capital_flow_context.side_effect = Exception("flow unavailable")
    else:
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": value}}
        }
    return mgr


# ── test: no constituents (historical date missing) ───────────────────────────

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
        call_kw = repo.insert_factor_run.call_args.kwargs
        assert call_kw["status"] == "failed"
        assert call_kw["error"] == "no_constituents"
        assert call_kw["constituent_source"] == "missing"

    def test_today_fetcher_returns_empty_also_failed(self):
        repo = _make_repo(run_id=1)
        trade_date = date(2024, 6, 3)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = []

        fetcher = MagicMock()
        fetcher.fetch.return_value = []

        svc = _make_service(repo=repo, constituent_repo=c_repo, fetcher=fetcher)

        with (
            patch("src.services.pricing_service.spi_time", return_value=trade_date),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=trade_date)

        assert result["status"] == "failed"


# ── test: constituent source paths ────────────────────────────────────────────

class TestConstituentSource:
    def _run(self, trade_date: date, snapshot_codes: list, fetched_codes: list):
        repo = _make_repo(run_id=1)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = snapshot_codes

        fetcher = MagicMock()
        fetcher.fetch.return_value = fetched_codes

        bars = _make_bars()
        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 1000.0}}
        }

        svc = _make_service(repo=repo, constituent_repo=c_repo, fetcher=fetcher)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=bars),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=trade_date)

        return result, c_repo, fetcher

    def test_snapshot_hit_uses_snapshot_source(self):
        result, c_repo, fetcher = self._run(
            trade_date=date(2024, 6, 1),
            snapshot_codes=["000001", "000002"],
            fetched_codes=[],
        )
        fetcher.fetch.assert_not_called()
        assert result["constituent_count"] == 2

    def test_today_no_snapshot_fetches_and_saves(self):
        trade_date = date(2024, 6, 3)
        repo = _make_repo(run_id=1)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = []

        fetcher = MagicMock()
        fetcher.fetch.return_value = ["000001", "000002", "000003"]

        bars = _make_bars()
        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 500.0}}
        }

        svc = _make_service(repo=repo, constituent_repo=c_repo, fetcher=fetcher)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=bars),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.spi_time", return_value=trade_date),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=trade_date)

        fetcher.fetch.assert_called_once_with(801010)
        c_repo.save_constituents.assert_called_once()
        assert result["constituent_count"] == 3

        run_kw = repo.save_pricing_batch.call_args.kwargs
        assert run_kw["constituent_source"] == "current"


# ── test: flow_coverage threshold switching ───────────────────────────────────

class TestFlowCoverage:
    def _run_with_flow_values(self, flow_values: list, trade_date: date | None = None):
        """flow_values: list of float|None, one per constituent."""
        codes = [f"00000{i}" for i in range(len(flow_values))]
        trade_date = trade_date or date(2024, 6, 3)

        repo = _make_repo(run_id=42)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        bars = _make_bars()

        call_idx = 0

        def _side_effect_flow(code):
            nonlocal call_idx
            v = flow_values[call_idx]
            call_idx += 1
            if v is None:
                raise Exception("no flow")
            return {"data": {"stock_flow": {"main_net_inflow": v}}}

        mgr = MagicMock()
        mgr.get_capital_flow_context.side_effect = _side_effect_flow

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=bars),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=trade_date)

        run_kw = repo.save_pricing_batch.call_args.kwargs
        return result, run_kw

    def test_flow_enabled_when_coverage_at_threshold(self):
        # 3 out of 5 valid = 0.6 exactly → flow_enabled
        result, run_kw = self._run_with_flow_values([100.0, None, 200.0, None, 300.0])
        assert result["flow_enabled"] is True
        assert result["flow_coverage"] == pytest.approx(0.6)
        assert run_kw["effective_weights"]["flow"] == pytest.approx(_BASE_W_FLOW)
        assert run_kw["effective_weights"]["rs"] == pytest.approx(_BASE_W_RS)
        assert run_kw["effective_weights"]["cmf"] == pytest.approx(_BASE_W_CMF)

    def test_flow_disabled_when_coverage_below_threshold(self):
        # 2 out of 5 valid = 0.4 → flow_disabled
        result, run_kw = self._run_with_flow_values([100.0, None, None, None, 200.0])
        assert result["flow_enabled"] is False
        assert result["flow_coverage"] == pytest.approx(0.4)
        assert "flow" not in run_kw["effective_weights"]
        assert run_kw["effective_weights"]["rs"] == pytest.approx(_BASE_W_RS_NO_FLOW)
        assert run_kw["effective_weights"]["cmf"] == pytest.approx(_BASE_W_CMF_NO_FLOW)

    def test_flow_disabled_all_stocks_use_rs_cmf_weights(self):
        result, _ = self._run_with_flow_values([None, None, None, None])
        assert result["flow_enabled"] is False
        for s in result["stocks"]:
            if s["status"] == "ok":
                # total must be weighted by RS_NO_FLOW + CMF_NO_FLOW
                assert s["total"] is not None

    def test_flow_enabled_single_missing_flow_is_degraded(self):
        # 4 valid, 1 None → coverage=0.8 → flow_enabled; the None stock is degraded
        result, _ = self._run_with_flow_values([100.0, 200.0, 300.0, None, 400.0])
        assert result["flow_enabled"] is True
        degraded = [s for s in result["stocks"] if s["status"] == "degraded"]
        assert len(degraded) == 1

    def test_all_flow_valid_produces_three_factor_total(self):
        result, run_kw = self._run_with_flow_values([100.0, 200.0, 300.0])
        assert result["flow_enabled"] is True
        for s in result["stocks"]:
            assert s["status"] == "ok"
            assert s["total"] is not None
        assert run_kw["effective_weights"]["flow"] == pytest.approx(_BASE_W_FLOW)


# ── test: single stock missing core factor (CMF=None) ─────────────────────────

class TestMissingCoreFactor:
    def test_cmf_none_gives_missing_core_factor_status(self):
        codes = ["000001", "000002"]

        repo = _make_repo(run_id=7)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 500.0}}
        }

        normal_bars = _make_bars()
        # Bars that cause CMF=None: fewer than min_period=5
        short_bars = [_bar(10, 8, 9, 0)] * 2  # Σvol=0 → None CMF

        call_count = 0

        def _fetch_bars_side(code, td, window):
            nonlocal call_count
            b = short_bars if call_count == 0 else normal_bars
            call_count += 1
            return b

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", side_effect=_fetch_bars_side),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        stocks = {s["stock_code"]: s for s in result["stocks"]}
        assert stocks["000001"]["status"] == "missing_core_factor"
        assert stocks["000001"]["total"] is None
        assert stocks["000002"]["status"] == "ok"
        assert stocks["000002"]["total"] is not None

    def test_rs_none_also_missing_core_factor(self):
        """RS=None (insufficient closes) → missing_core_factor, total=None."""
        codes = ["000001"]

        repo = _make_repo(run_id=1)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 100.0}}
        }

        # Only 5 bars: CMF is fine (min_period=5) but RS needs period+1=21 closes → None
        few_bars = [_bar(10, 8, 9, 100)] * 5

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=few_bars),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        s = result["stocks"][0]
        assert s["status"] == "missing_core_factor"
        assert s["total"] is None


# ── test: batch status rules ──────────────────────────────────────────────────

class TestBatchStatus:
    def _run(self, codes: list, bars_per_code: dict, flow_map: dict, trade_date=date(2024, 6, 3)):
        repo = _make_repo(run_id=1)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        def _fetch(code, td, window):
            return bars_per_code.get(code, _make_bars())

        def _flow(code):
            v = flow_map.get(code)
            if v is None:
                raise Exception("no flow")
            return {"data": {"stock_flow": {"main_net_inflow": v}}}

        mgr = MagicMock()
        mgr.get_capital_flow_context.side_effect = _flow

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", side_effect=_fetch),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            return svc.price_board(board_id=801010, trade_date=trade_date)

    def test_all_ok_batch_status_ok(self):
        codes = ["000001", "000002"]
        bars = {c: _make_bars() for c in codes}
        flow = {c: 100.0 for c in codes}
        result = self._run(codes, bars, flow)
        assert result["status"] == "ok"

    def test_one_missing_core_makes_partial(self):
        codes = ["000001", "000002"]
        short = [_bar(10, 8, 9, 0)] * 2  # CMF=None → missing_core_factor
        bars = {"000001": short, "000002": _make_bars()}
        flow = {c: 100.0 for c in codes}
        result = self._run(codes, bars, flow)
        assert result["status"] == "partial"

    def test_degraded_stock_makes_partial(self):
        codes = ["000001", "000002", "000003", "000004", "000005"]
        bars = {c: _make_bars() for c in codes}
        # 4 out of 5 flow valid = 0.8 → flow_enabled; 000001 has None → degraded
        flow = {c: 100.0 for c in codes}
        flow["000001"] = None
        result = self._run(codes, bars, flow)
        assert result["status"] == "partial"
        degraded_stocks = [s for s in result["stocks"] if s["status"] == "degraded"]
        assert len(degraded_stocks) == 1

    def test_no_constituents_batch_status_failed(self):
        repo = _make_repo(run_id=1)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = []

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with patch("src.services.pricing_service.time.sleep"):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 1, 2))

        assert result["status"] == "failed"

    def test_all_missing_core_factor_batch_status_failed(self):
        # All constituents have RS=None and CMF=None (zero-volume bars < min_period)
        # → priced_count=0 → batch_status must be 'failed', not 'partial'
        codes = ["000001", "000002"]
        zero_bars = [_bar(10, 8, 9, 0)] * 2  # Σvol=0 → CMF=None; len<21 → RS=None

        repo = _make_repo(run_id=8)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 100.0}}
        }

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=zero_bars),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        assert result["status"] == "failed"
        assert result["priced_count"] == 0
        for s in result["stocks"]:
            assert s["status"] == "missing_core_factor"
            assert s["total"] is None

        run_kw = repo.save_pricing_batch.call_args.kwargs
        assert run_kw["status"] == "failed"
        assert run_kw["error"] == "all_constituents_missing_core_factor"


# ── test: time.sleep is called per constituent ────────────────────────────────

class TestSerialExecution:
    def test_sleep_called_once_per_constituent(self):
        codes = ["000001", "000002", "000003"]

        repo = _make_repo(run_id=1)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 100.0}}
        }

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep") as mock_sleep,
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        assert mock_sleep.call_count == len(codes)
        mock_sleep.assert_called_with(0.5)


# ── test: repo persistence calls ─────────────────────────────────────────────

class TestRepoPersistence:
    def test_batch_save_called_once_with_all_snapshots(self):
        codes = ["000001", "000002"]

        repo = _make_repo(run_id=5)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 200.0}}
        }

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        repo.save_pricing_batch.assert_called_once()
        call_kw = repo.save_pricing_batch.call_args.kwargs
        assert len(call_kw["snapshots"]) == len(codes)
        assert {snapshot["stock_code"] for snapshot in call_kw["snapshots"]} == set(codes)
        assert result["run_id"] == 5

    def test_batch_save_receives_effective_weights(self):
        codes = ["000001"]

        repo = _make_repo(run_id=77)

        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        mgr = MagicMock()
        mgr.get_capital_flow_context.return_value = {
            "data": {"stock_flow": {"main_net_inflow": 100.0}}
        }

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch("src.services.pricing_service.time.sleep"),
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        call_kw = repo.save_pricing_batch.call_args.kwargs
        assert call_kw["effective_weights"] == {"rs": _BASE_W_RS, "cmf": _BASE_W_CMF, "flow": _BASE_W_FLOW}
