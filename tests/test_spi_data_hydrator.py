# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from datetime import date
from types import ModuleType
from unittest.mock import MagicMock, call, patch

if "dotenv" not in sys.modules:
    dotenv = ModuleType("dotenv")
    dotenv.load_dotenv = lambda *a, **kw: None
    dotenv.dotenv_values = lambda *a, **kw: {}
    sys.modules["dotenv"] = dotenv

if "fake_useragent" not in sys.modules:
    fake_useragent = ModuleType("fake_useragent")

    class UserAgent:
        def __init__(self, *args, **kwargs):
            pass

    fake_useragent.UserAgent = UserAgent
    sys.modules["fake_useragent"] = fake_useragent

from src.services.spi.data_hydrator import SpiDataHydrator


def test_hydrate_trade_date_runs_full_current_chain():
    trade_date = date(2026, 7, 3)
    plate_service = MagicMock()
    plate_service.refresh_all.return_value = {"success": 31, "total": 31}
    plate_service.refresh_all_v2.return_value = {"success": 30, "total": 31}

    plate_repo = MagicMock()
    plate_repo.find_top_boards_v2.return_value = [
        {"board_id": 801010},
        {"board_id": 801020},
    ]
    plate_repo.count_snapshots.side_effect = [31, 30]
    plate_repo.count_rotation_signals.return_value = 4

    pricing_repo = MagicMock()
    pricing_repo.count_snapshots.return_value = 12

    rotation_service = MagicMock()
    rotation_service.generate_signals.return_value = {"buy_signals": 4}

    pricing_service = MagicMock()
    pricing_service.price_board.side_effect = [
        {"status": "ok", "priced_count": 3, "degraded_count": 1},
        {"status": "partial", "priced_count": 2, "degraded_count": 0},
    ]

    hydrator = SpiDataHydrator(
        plate_service=plate_service,
        plate_repo=plate_repo,
        pricing_repo=pricing_repo,
        rotation_factory=lambda: rotation_service,
        pricing_factory=lambda: pricing_service,
    )

    with patch("src.services.spi.data_hydrator.spi_time", return_value=trade_date):
        payload = hydrator.hydrate_trade_date(trade_date)

    plate_service.refresh_all.assert_called_once_with(anchor_date=trade_date)
    plate_service.refresh_all_v2.assert_called_once_with(anchor_date=trade_date)
    rotation_service.generate_signals.assert_called_once_with(trade_date)
    plate_repo.find_top_boards_v2.assert_called_once_with(anchor_date=trade_date, top_n=30)
    assert pricing_service.price_board.call_args_list == [
        call(801010, trade_date),
        call(801020, trade_date),
    ]
    assert payload["stages"]["v1"]["status"] == "ok"
    assert payload["stages"]["v2"]["status"] == "ok"
    assert payload["stages"]["rotation"]["status"] == "ok"
    assert payload["stages"]["pricing"]["status"] == "ok"
    assert payload["readiness"]["sector_rotation_smoke_ready"] is True
    assert payload["readiness"]["sector_rotation_candidate_ready"] is True


def test_hydrate_trade_date_skips_historical_rotation_and_pricing_by_default():
    trade_date = date(2026, 7, 2)
    plate_service = MagicMock()
    plate_service.refresh_all.return_value = {"success": 31, "total": 31}
    plate_service.refresh_all_v2.return_value = {"success": 31, "total": 31}

    plate_repo = MagicMock()
    plate_repo.count_snapshots.side_effect = [31, 31]
    plate_repo.count_rotation_signals.return_value = 0

    pricing_repo = MagicMock()
    pricing_repo.count_snapshots.return_value = 0

    rotation_service = MagicMock()
    pricing_service = MagicMock()

    hydrator = SpiDataHydrator(
        plate_service=plate_service,
        plate_repo=plate_repo,
        pricing_repo=pricing_repo,
        rotation_factory=lambda: rotation_service,
        pricing_factory=lambda: pricing_service,
    )

    with patch("src.services.spi.data_hydrator.spi_time", return_value=date(2026, 7, 3)):
        payload = hydrator.hydrate_trade_date(trade_date)

    rotation_service.generate_signals.assert_not_called()
    pricing_service.price_board.assert_not_called()
    assert payload["stages"]["rotation"]["status"] == "skipped"
    assert payload["stages"]["pricing"]["status"] == "skipped"
    assert payload["stages"]["rotation"]["reason"] == "historical_anchor_requires_point_in_time_constituents"
    assert payload["readiness"]["historical_restriction_applied"] is True
