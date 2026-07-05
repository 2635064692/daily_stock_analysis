# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from src.services.spi.rotation_service import (
    RotationService,
    RotationStrategyConfig,
    load_rotation_strategy_config,
)

TRADE_DATE = date(2026, 7, 1)
BOARD_ID = 801010


def _make_bar(close, volume=1_000_000):
    bar = MagicMock()
    bar.close = close
    bar.volume = float(volume)
    return bar


def _make_service(top_boards=None, constituents=None, strategy_config=None):
    repo = MagicMock()
    constituent_repo = MagicMock()
    fetcher = MagicMock()
    repo.find_top_boards_v2.return_value = top_boards or []
    repo.find_active_rotation_positions.return_value = {}
    constituent_repo.get_constituents.return_value = constituents or []
    fetcher.fetch.return_value = constituents or []
    service = RotationService(
        repo=repo,
        constituent_repo=constituent_repo,
        fetcher=fetcher,
        strategy_config=strategy_config or RotationStrategyConfig(),
    )
    return service, repo, constituent_repo, fetcher


class TestUpdateWatchpool:

    def test_sets_watchpool_from_repo(self):
        service, repo, _, _ = _make_service(
            top_boards=[
                {"board_id": 801010, "board_name": "A", "v2_score": 80.0, "spi": 7},
                {"board_id": 801020, "board_name": "B", "v2_score": 75.0, "spi": 6},
                {"board_id": 801030, "board_name": "C", "v2_score": 70.0, "spi": 5},
            ]
        )

        result = service.update_watchpool(TRADE_DATE, top_n=30)

        assert result == {801010, 801020, 801030}
        assert service._watchpool == {801010, 801020, 801030}
        repo.find_top_boards_v2.assert_called_once_with(anchor_date=TRADE_DATE, top_n=30)


class TestCheckEntrySignal:

    def _bars_with_pullback(self, ema_period=20):
        closes = [100.0 + i * 0.1 for i in range(ema_period + 5)]
        base_vol = 1_000_000.0
        bars = [_make_bar(close, base_vol) for close in closes]
        bars[-1].close = closes[-1]
        bars[-1].volume = base_vol * 2.0
        return list(reversed(bars))

    def test_entry_buy_signal_triggered(self):
        service, repo, constituent_repo, _ = _make_service(constituents=["600519"])
        constituent_repo.get_constituents.return_value = ["600519"]

        with patch(
            "src.services.spi.rotation_service._get_latest",
            return_value=self._bars_with_pullback(),
        ):
            result = service.check_entry(BOARD_ID, TRADE_DATE)

        assert "600519" in result
        repo.upsert_rotation_signal.assert_called_once()
        call_kwargs = repo.upsert_rotation_signal.call_args.kwargs
        assert call_kwargs["action"] == "BUY"
        assert call_kwargs["stock_code"] == "600519"

    def test_entry_not_triggered_when_volume_ratio_low(self):
        service, repo, _, _ = _make_service(constituents=["600519"])
        ema_period = 20
        closes = [100.0 + i * 0.1 for i in range(ema_period + 5)]
        bars_desc = list(reversed([_make_bar(close, 1_000_000) for close in closes]))

        with patch("src.services.spi.rotation_service._get_latest", return_value=bars_desc):
            result = service.check_entry(BOARD_ID, TRADE_DATE, volume_ratio_threshold=1.2)

        assert result == []
        repo.upsert_rotation_signal.assert_not_called()

    def test_empty_constituents_returns_empty_no_exception(self):
        service, repo, _, _ = _make_service(constituents=[])

        result = service.check_entry(BOARD_ID, TRADE_DATE)

        assert result == []
        repo.upsert_rotation_signal.assert_not_called()

    def test_no_stock_data_skips_gracefully(self):
        service, repo, _, _ = _make_service(constituents=["600519"])

        with patch("src.services.spi.rotation_service._get_latest", return_value=[]):
            result = service.check_entry(BOARD_ID, TRADE_DATE)

        assert result == []
        repo.upsert_rotation_signal.assert_not_called()

    def test_scan_entry_signals_supports_non_persistent_mode(self):
        service, repo, constituent_repo, _ = _make_service(constituents=["600519"])
        constituent_repo.get_constituents.return_value = ["600519"]

        with patch(
            "src.services.spi.rotation_service._get_latest",
            return_value=self._bars_with_pullback(),
        ):
            result = service.scan_entry_signals(BOARD_ID, TRADE_DATE, persist=False)

        assert result[0]["stock_code"] == "600519"
        assert result[0]["matched"] is True
        repo.upsert_rotation_signal.assert_not_called()


class TestResolveConstituents:

    def test_returns_source_metadata_for_snapshot_constituents(self):
        service, _, constituent_repo, _ = _make_service(constituents=["600519", "000858"])
        constituent_repo.get_constituents.return_value = ["600519", "000858"]

        result = service.resolve_constituents(BOARD_ID, TRADE_DATE)

        assert result["codes"] == ["600519", "000858"]
        assert result["source"] == "snapshot"
        assert result["snapshot"] is None


class TestCheckExitSignal:

    def test_exit_board_out_of_top_m(self):
        service, repo, _, _ = _make_service(
            top_boards=[{"board_id": 999999, "v2_score": 90.0, "spi": 8, "board_name": "X"}],
            constituents=["600519", "000858"],
        )

        result = service.check_exit(BOARD_ID, TRADE_DATE, top_m=50)

        assert set(result) == {"600519", "000858"}
        assert repo.upsert_rotation_signal.call_count == 2
        for call in repo.upsert_rotation_signal.call_args_list:
            assert call.kwargs["action"] == "SELL"
            assert "board_rank_out_of_top_50" in call.kwargs["reason"]

    def test_exit_stock_below_ema(self):
        service, repo, _, _ = _make_service(
            top_boards=[{"board_id": BOARD_ID, "v2_score": 80.0, "spi": 7, "board_name": "A"}],
            constituents=["600519"],
        )
        exit_period = 5
        bars_desc = list(reversed([_make_bar(100.0 - i * 2) for i in range(exit_period + 5)]))

        with patch("src.services.spi.rotation_service._get_latest", return_value=bars_desc):
            result = service.check_exit(BOARD_ID, TRADE_DATE, exit_ema_period=exit_period)

        assert "600519" in result
        call_kwargs = repo.upsert_rotation_signal.call_args.kwargs
        assert call_kwargs["action"] == "SELL"
        assert f"EMA{exit_period}" in call_kwargs["reason"]

    def test_exit_stock_above_ema_no_signal(self):
        service, repo, _, _ = _make_service(
            top_boards=[{"board_id": BOARD_ID, "v2_score": 80.0, "spi": 7, "board_name": "A"}],
            constituents=["600519"],
        )
        exit_period = 5
        bars_desc = list(reversed([_make_bar(100.0 + i * 2) for i in range(exit_period + 5)]))

        with patch("src.services.spi.rotation_service._get_latest", return_value=bars_desc):
            result = service.check_exit(BOARD_ID, TRADE_DATE, exit_ema_period=exit_period)

        assert result == []
        repo.upsert_rotation_signal.assert_not_called()


class TestGenerateSignals:

    def test_fetches_and_saves_constituents_before_entry(self):
        service, repo, constituent_repo, fetcher = _make_service(
            top_boards=[{"board_id": BOARD_ID, "board_name": "A", "v2_score": 80.0, "spi": 7}],
            constituents=[],
        )
        repo.find_top_boards_v2.side_effect = [
            [{"board_id": BOARD_ID, "board_name": "A", "v2_score": 80.0, "spi": 7}],
        ]
        constituent_repo.get_constituents.return_value = []
        fetcher.fetch.return_value = ["600519"]

        with patch.object(service, "check_entry", return_value=["600519"]) as check_entry:
            result = service.generate_signals(TRADE_DATE)

        constituent_repo.save_constituents.assert_called_once_with(BOARD_ID, TRADE_DATE, ["600519"])
        check_entry.assert_called_once()
        assert result["buy_signals"] == 1
        assert result["watchpool_size"] == 1

    def test_skips_entry_when_constituent_snapshot_unavailable(self):
        service, repo, constituent_repo, fetcher = _make_service(
            top_boards=[{"board_id": BOARD_ID, "board_name": "A", "v2_score": 80.0, "spi": 7}],
            constituents=[],
        )
        repo.find_top_boards_v2.side_effect = [
            [{"board_id": BOARD_ID, "board_name": "A", "v2_score": 80.0, "spi": 7}],
        ]
        constituent_repo.get_constituents.return_value = []
        fetcher.fetch.return_value = []

        with patch.object(service, "check_entry", return_value=[]) as check_entry:
            result = service.generate_signals(TRADE_DATE)

        check_entry.assert_not_called()
        constituent_repo.save_constituents.assert_not_called()
        assert result["skipped_snapshot_boards"] == 1

    def test_exit_uses_active_positions_not_current_watchpool(self):
        service, repo, constituent_repo, _ = _make_service(
            top_boards=[{"board_id": 999999, "board_name": "X", "v2_score": 80.0, "spi": 7}],
            constituents=[],
        )
        repo.find_active_rotation_positions.return_value = {BOARD_ID: ["600519"]}
        repo.find_top_boards_v2.side_effect = [
            [{"board_id": 999999, "board_name": "X", "v2_score": 80.0, "spi": 7}],
            [{"board_id": 999999, "board_name": "X", "v2_score": 80.0, "spi": 7}],
        ]
        constituent_repo.get_constituents.return_value = []

        result = service.generate_signals(TRADE_DATE)

        repo.upsert_rotation_signal.assert_called_once()
        call_kwargs = repo.upsert_rotation_signal.call_args.kwargs
        assert call_kwargs["board_id"] == BOARD_ID
        assert call_kwargs["stock_code"] == "600519"
        assert call_kwargs["action"] == "SELL"
        assert result["sell_signals"] == 1

    def test_watchpool_drop_without_active_position_does_not_emit_sell(self):
        service, repo, constituent_repo, _ = _make_service(
            top_boards=[{"board_id": 999999, "board_name": "X", "v2_score": 80.0, "spi": 7}],
            constituents=[],
        )
        repo.find_active_rotation_positions.return_value = {}
        repo.find_top_boards_v2.side_effect = [
            [{"board_id": 999999, "board_name": "X", "v2_score": 80.0, "spi": 7}],
        ]
        constituent_repo.get_constituents.return_value = []

        result = service.generate_signals(TRADE_DATE)

        repo.upsert_rotation_signal.assert_not_called()
        assert result["sell_signals"] == 0


class TestRotationStrategyConfigLoading:

    def test_loads_rotation_yaml_values(self, tmp_path, monkeypatch):
        config_path = tmp_path / "rotation_entry.yaml"
        config_path.write_text(
            "\n".join([
                "entry:",
                "  ema_period: 15",
                "  volume_ratio_threshold: 1.5",
                "  pullback_tolerance: 0.03",
                "exit:",
                "  top_m: 40",
                "  ema_period: 8",
                "watchpool:",
                "  top_n: 12",
            ]),
            encoding="utf-8",
        )

        monkeypatch.setattr("src.services.spi.rotation_service._ROTATION_CONFIG_PATH", config_path)
        load_rotation_strategy_config.cache_clear()
        try:
            cfg = load_rotation_strategy_config()
        finally:
            load_rotation_strategy_config.cache_clear()

        assert cfg == RotationStrategyConfig(
            watchpool_top_n=12,
            entry_ema_period=15,
            volume_ratio_threshold=1.5,
            pullback_tolerance=0.03,
            exit_top_m=40,
            exit_ema_period=8,
        )
