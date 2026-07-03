# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.services.spi.rotation_service import RotationService

TRADE_DATE = date(2026, 7, 1)
BOARD_ID = 801010


def _make_bar(close, volume=1_000_000):
    bar = MagicMock()
    bar.close = close
    bar.volume = float(volume)
    return bar


def _make_service(top_boards=None, constituents=None):
    repo = MagicMock()
    constituent_repo = MagicMock()
    repo.find_top_boards_v2.return_value = top_boards or []
    constituent_repo.get_constituents.return_value = constituents or []
    return RotationService(repo=repo, constituent_repo=constituent_repo), repo, constituent_repo


class TestUpdateWatchpool:

    def test_sets_watchpool_from_repo(self):
        svc, repo, _ = _make_service(
            top_boards=[
                {"board_id": 801010, "board_name": "A", "v2_score": 80.0, "spi": 7},
                {"board_id": 801020, "board_name": "B", "v2_score": 75.0, "spi": 6},
                {"board_id": 801030, "board_name": "C", "v2_score": 70.0, "spi": 5},
            ]
        )
        result = svc.update_watchpool(TRADE_DATE, top_n=30)
        assert result == {801010, 801020, 801030}
        assert svc._watchpool == {801010, 801020, 801030}
        repo.find_top_boards_v2.assert_called_once_with(anchor_date=TRADE_DATE, top_n=30)


class TestCheckEntrySignal:

    def _bars_with_pullback(self, ema_period=20):
        """Bars where last_close <= ema*1.02 and volume_ratio >= 1.2."""
        # 25 bars ascending close so EMA ≈ last close; inflate last volume
        closes = [100.0 + i * 0.1 for i in range(ema_period + 5)]
        base_vol = 1_000_000.0
        bars = [_make_bar(c, base_vol) for c in closes]
        # last bar: close at ema level (no gap), volume 2x
        bars[-1].close = closes[-1]
        bars[-1].volume = base_vol * 2.0
        # repo returns descending (latest first)
        return list(reversed(bars))

    def test_entry_buy_signal_triggered(self):
        svc, repo, constituent_repo = _make_service(constituents=["600519"])
        constituent_repo.get_constituents.return_value = ["600519"]

        with patch(
            "src.services.spi.rotation_service._get_latest",
            return_value=self._bars_with_pullback(),
        ):
            result = svc.check_entry(BOARD_ID, TRADE_DATE)

        assert "600519" in result
        repo.upsert_rotation_signal.assert_called_once()
        call_kwargs = repo.upsert_rotation_signal.call_args.kwargs
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["stock_code"] == "600519"

    def test_entry_not_triggered_when_volume_ratio_low(self):
        svc, repo, constituent_repo = _make_service(constituents=["600519"])
        ema_period = 20
        closes = [100.0 + i * 0.1 for i in range(ema_period + 5)]
        # Uniform volume → volume_ratio == 1.0 < 1.2
        bars_asc = [_make_bar(c, 1_000_000) for c in closes]
        bars_desc = list(reversed(bars_asc))

        with patch("src.services.spi.rotation_service._get_latest", return_value=bars_desc):
            result = svc.check_entry(BOARD_ID, TRADE_DATE, volume_ratio_threshold=1.2)

        assert result == []
        repo.upsert_rotation_signal.assert_not_called()

    def test_empty_constituents_returns_empty_no_exception(self):
        svc, repo, constituent_repo = _make_service(constituents=[])
        result = svc.check_entry(BOARD_ID, TRADE_DATE)
        assert result == []
        repo.upsert_rotation_signal.assert_not_called()

    def test_no_stock_data_skips_gracefully(self):
        svc, repo, constituent_repo = _make_service(constituents=["600519"])
        with patch("src.services.spi.rotation_service._get_latest", return_value=[]):
            result = svc.check_entry(BOARD_ID, TRADE_DATE)
        assert result == []
        repo.upsert_rotation_signal.assert_not_called()


class TestCheckExitSignal:

    def test_exit_board_out_of_top_m(self):
        # find_top_boards_v2 returns boards that do NOT include BOARD_ID
        svc, repo, constituent_repo = _make_service(
            top_boards=[{"board_id": 999999, "v2_score": 90.0, "spi": 8, "board_name": "X"}],
            constituents=["600519", "000858"],
        )
        result = svc.check_exit(BOARD_ID, TRADE_DATE, top_m=50)

        assert set(result) == {"600519", "000858"}
        assert repo.upsert_rotation_signal.call_count == 2
        for call in repo.upsert_rotation_signal.call_args_list:
            assert call.kwargs["action"] == "SELL"
            assert "board_rank_out_of_top_50" in call.kwargs["reason"]

    def test_exit_stock_below_ema(self):
        # BOARD_ID is in top_m; stock close < EMA → SELL
        svc, repo, constituent_repo = _make_service(
            top_boards=[{"board_id": BOARD_ID, "v2_score": 80.0, "spi": 7, "board_name": "A"}],
            constituents=["600519"],
        )
        exit_period = 5
        # Descending closes so last close is lowest → below EMA
        bars_asc = [_make_bar(100.0 - i * 2) for i in range(exit_period + 5)]
        bars_desc = list(reversed(bars_asc))

        with patch("src.services.spi.rotation_service._get_latest", return_value=bars_desc):
            result = svc.check_exit(BOARD_ID, TRADE_DATE, exit_ema_period=exit_period)

        assert "600519" in result
        call_kwargs = repo.upsert_rotation_signal.call_args.kwargs
        assert call_kwargs["action"] == "SELL"
        assert f"EMA{exit_period}" in call_kwargs["reason"]

    def test_exit_stock_above_ema_no_signal(self):
        # BOARD_ID is in top_m; stock close > EMA → no SELL
        svc, repo, constituent_repo = _make_service(
            top_boards=[{"board_id": BOARD_ID, "v2_score": 80.0, "spi": 7, "board_name": "A"}],
            constituents=["600519"],
        )
        exit_period = 5
        # Ascending closes → last close above EMA
        bars_asc = [_make_bar(100.0 + i * 2) for i in range(exit_period + 5)]
        bars_desc = list(reversed(bars_asc))

        with patch("src.services.spi.rotation_service._get_latest", return_value=bars_desc):
            result = svc.check_exit(BOARD_ID, TRADE_DATE, exit_ema_period=exit_period)

        assert result == []
        repo.upsert_rotation_signal.assert_not_called()

    def test_upsert_rotation_signal_called_for_buy(self):
        """Idempotency is the repo's concern; verify the call is made."""
        svc, repo, constituent_repo = _make_service(constituents=["600519"])
        ema_period = 20
        closes = [100.0 + i * 0.1 for i in range(ema_period + 5)]
        base_vol = 1_000_000.0
        bars_asc = [_make_bar(c, base_vol) for c in closes]
        bars_asc[-1].volume = base_vol * 2.0
        bars_desc = list(reversed(bars_asc))

        with patch("src.services.spi.rotation_service._get_latest", return_value=bars_desc):
            svc.check_entry(BOARD_ID, TRADE_DATE)

        repo.upsert_rotation_signal.assert_called()
