# -*- coding: utf-8 -*-
import pytest
from datetime import date
from unittest.mock import MagicMock

from src.services.spi.plate_spi_service import PlateSpiService


def _make_klines(n, base=10.0, step=0.0):
    return [
        {"date": "2025-01-01", "close": base + i * step,
         "open": base + i * step, "high": base + i * step,
         "low": base + i * step}
        for i in range(n)
    ]


@pytest.fixture
def mock_adapter():
    return MagicMock()


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_adapter, mock_repo):
    return PlateSpiService(adapter=mock_adapter, repo=mock_repo, max_workers=2)


class TestComputeBoardSpi:

    def test_sufficient_klines_normal_spi(self, service, mock_adapter):
        mock_adapter.get_index_kline.return_value = _make_klines(300, base=1.0, step=1.0)
        bid_int, spi, name, coverage = service.compute_board_spi(
            "801010", date(2025, 1, 1), "农林牧渔")
        assert isinstance(bid_int, int)
        assert bid_int == 801010
        assert 0 <= spi <= 8
        assert name == "农林牧渔"
        assert coverage == 1.0

    def test_insufficient_klines_partial_coverage(self, service, mock_adapter):
        mock_adapter.get_index_kline.return_value = _make_klines(100, base=1.0, step=0.1)
        bid_int, spi, name, coverage = service.compute_board_spi(
            "801010", date(2025, 1, 1), "农林牧渔")
        assert bid_int == 801010
        assert coverage < 1.0
        assert coverage == pytest.approx(100 / 233.0)

    def test_empty_klines_sentinel(self, service, mock_adapter):
        mock_adapter.get_index_kline.return_value = []
        bid_int, spi, name, coverage = service.compute_board_spi(
            "801010", date(2025, 1, 1), "农林牧渔")
        assert bid_int == 801010
        assert spi == -1
        assert coverage == 0.0

    def test_board_id_str_to_int_conversion(self, service, mock_adapter):
        mock_adapter.get_index_kline.return_value = _make_klines(300, base=10.0)
        bid_int, spi, _name, _cov = service.compute_board_spi(
            "801020", date(2025, 1, 1), "采掘")
        assert bid_int == 801020
        assert isinstance(bid_int, int)

    def test_compute_board_spi_passes_anchor_date(self, service, mock_adapter):
        anchor_date = date(2025, 1, 7)
        mock_adapter.get_index_kline.return_value = _make_klines(300, base=10.0)
        service.compute_board_spi("801010", anchor_date, "农林牧渔")
        mock_adapter.get_index_kline.assert_called_once_with(
            "801010",
            back_count=300,
            end_date=anchor_date,
        )


class TestRefreshAll:

    def _setup_31_levels(self, mock_adapter):
        levels = [
            {"board_id": f"8010{i:02d}", "board_name": f"行业_{i}", "stock_count": 100}
            for i in range(1, 32)
        ]
        mock_adapter.get_sw_first_levels.return_value = levels
        mock_adapter.get_index_kline.return_value = _make_klines(300, base=10.0)
        return levels

    def test_parallel_and_upsert_31_boards(self, service, mock_adapter, mock_repo):
        self._setup_31_levels(mock_adapter)
        result = service.refresh_all(anchor_date=date(2025, 1, 1))
        assert mock_repo.upsert_snapshot.call_count == 31
        assert result["total"] == 31
        assert result["success"] == 31
        assert result["low_confidence"] == 0
        assert "anchor_date" in result

    def test_failure_isolation(self, service, mock_adapter, mock_repo):
        levels = [
            {"board_id": "801001", "board_name": "行业1", "stock_count": 100},
            {"board_id": "801002", "board_name": "行业2", "stock_count": 100},
            {"board_id": "801003", "board_name": "行业3", "stock_count": 100},
        ]
        mock_adapter.get_sw_first_levels.return_value = levels
        call_count = 0

        def side_effect(board_id, back_count=300, end_date=None):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("boom")
            return _make_klines(300, base=10.0)

        mock_adapter.get_index_kline.side_effect = side_effect
        result = service.refresh_all(anchor_date=date(2025, 1, 1))
        assert result["success"] == 2
        assert result["total"] == 3

    def test_low_confidence_on_insufficient_klines(self, service, mock_adapter, mock_repo):
        levels = [
            {"board_id": "801001", "board_name": "行业1", "stock_count": 100},
            {"board_id": "801002", "board_name": "行业2", "stock_count": 100},
        ]
        mock_adapter.get_sw_first_levels.return_value = levels
        kline_map = {
            "801001": _make_klines(300, base=10.0),
            "801002": _make_klines(50, base=10.0),
        }
        mock_adapter.get_index_kline.side_effect = (
            lambda board_id, back_count=300, end_date=None: kline_map[board_id])
        result = service.refresh_all(anchor_date=date(2025, 1, 1))
        assert result["low_confidence"] == 1
        assert result["success"] == 2

    def test_empty_levels(self, service, mock_adapter, mock_repo):
        mock_adapter.get_sw_first_levels.return_value = []
        result = service.refresh_all(anchor_date=date(2025, 1, 1))
        assert result["total"] == 0
        assert result["success"] == 0
        mock_repo.upsert_snapshot.assert_not_called()

    def test_sentinel_not_upserted(self, service, mock_adapter, mock_repo):
        levels = [
            {"board_id": "801001", "board_name": "行业1", "stock_count": 100},
        ]
        mock_adapter.get_sw_first_levels.return_value = levels
        mock_adapter.get_index_kline.return_value = []
        result = service.refresh_all(anchor_date=date(2025, 1, 1))
        assert result["total"] == 1
        assert result["success"] == 0
        mock_repo.upsert_snapshot.assert_not_called()
