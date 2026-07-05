# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from src.services.run_diagnostics import activate_run_diagnostic_context, reset_run_diagnostic_context
from src.services.spi.rotation_alphasift_runtime import AlphaSiftSectorRotationRuntime
from src.services.spi.rotation_service import RotationStrategyConfig


TRADE_DATE = date(2026, 7, 5)


def test_runtime_builds_candidates_from_realtime_chain():
    plate_service = MagicMock()
    plate_repo = MagicMock()
    rotation_service = MagicMock()
    pricing_service = MagicMock()

    plate_repo.find_top_boards_v2.return_value = [
        {"board_id": 801010, "board_name": "农林牧渔", "v2_score": 88.0},
    ]
    rotation_service.resolve_constituents.return_value = {
        "codes": ["600519", "000858"],
        "source": "snapshot",
        "snapshot": None,
    }
    rotation_service.scan_entry_signals.return_value = [
        {"stock_code": "600519", "matched": True, "reason": "pullback to EMA20, vol_ratio=1.50"},
        {"stock_code": "000858", "matched": False, "reason": "entry_rule_not_matched"},
    ]
    pricing_service.price_board.return_value = {
        "run_id": 7,
        "flow_coverage": 0.8,
        "status": "ok",
        "priced_count": 2,
        "stocks": [
            {
                "stock_code": "600519",
                "status": "ok",
                "total": 0.93,
                "sp_ratio": 0.75,
                "sp_score": 0.81,
                "cmf": 0.12,
                "flow_score": 0.66,
                "factor_mask": "sp,cmf,flow",
            },
            {
                "stock_code": "000858",
                "status": "ok",
                "total": 0.66,
                "sp_ratio": 0.62,
                "sp_score": 0.70,
                "cmf": 0.05,
                "flow_score": 0.44,
                "factor_mask": "sp,cmf,flow",
            },
        ],
    }

    runtime = AlphaSiftSectorRotationRuntime(
        plate_service=plate_service,
        plate_repo=plate_repo,
        rotation_service=rotation_service,
        pricing_service=pricing_service,
        strategy_config=RotationStrategyConfig(watchpool_top_n=1),
    )

    result = runtime.run(trade_date=TRADE_DATE, max_results=5)

    plate_service.refresh_all_v2.assert_not_called()
    plate_repo.find_top_boards_v2.assert_called_once_with(anchor_date=TRADE_DATE, top_n=1)
    rotation_service.resolve_constituents.assert_called_once_with(801010, TRADE_DATE)
    rotation_service.scan_entry_signals.assert_called_once()
    pricing_service.price_board.assert_called_once_with(board_id=801010, trade_date=TRADE_DATE)

    assert result["rotation_boards"] == 1
    assert result["snapshot_source"] == "spi_v2:realtime_rotation_runtime"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["code"] == "600519"
    assert result["candidates"][0]["board_v2_score"] == 88.0
    assert result["warnings"] == []
    plate_service.refresh_all_v2.assert_not_called()


def test_runtime_refreshes_v2_watchpool_when_snapshot_insufficient():
    plate_service = MagicMock()
    plate_repo = MagicMock()
    rotation_service = MagicMock()
    pricing_service = MagicMock()

    plate_repo.find_top_boards_v2.side_effect = [
        [{"board_id": 801010, "board_name": "农林牧渔", "v2_score": 88.0}],
        [
            {"board_id": 801010, "board_name": "农林牧渔", "v2_score": 88.0},
            {"board_id": 801020, "board_name": "采掘", "v2_score": 77.0},
        ],
    ]
    rotation_service.resolve_constituents.return_value = {"codes": [], "source": "missing", "snapshot": None}

    runtime = AlphaSiftSectorRotationRuntime(
        plate_service=plate_service,
        plate_repo=plate_repo,
        rotation_service=rotation_service,
        pricing_service=pricing_service,
        strategy_config=RotationStrategyConfig(watchpool_top_n=2),
    )

    result = runtime.run(trade_date=TRADE_DATE, max_results=5)

    plate_service.refresh_all_v2.assert_called_once_with(anchor_date=TRADE_DATE)
    assert result["rotation_boards"] == 0
    assert "constituents_missing" in result["warnings"][0]


def test_runtime_emits_flow_events_for_each_stage():
    flow_events: list[dict] = []
    token = activate_run_diagnostic_context(
        trace_id="trace-rotation",
        task_id="task-rotation",
        query_id="task-rotation",
        stock_code="alphasift_screen",
        trigger_source="api",
        event_sink=flow_events.append,
    )
    try:
        plate_service = MagicMock()
        plate_repo = MagicMock()
        rotation_service = MagicMock()
        pricing_service = MagicMock()

        plate_repo.find_top_boards_v2.return_value = [
            {"board_id": 801010, "board_name": "农林牧渔", "v2_score": 91.0},
        ]
        rotation_service.resolve_constituents.return_value = {
            "codes": ["600519"],
            "source": "snapshot",
            "snapshot": None,
        }
        rotation_service.scan_entry_signals.return_value = [
            {"stock_code": "600519", "matched": True, "reason": "pullback to EMA20, vol_ratio=1.50"},
        ]
        pricing_service.price_board.return_value = {
            "run_id": 11,
            "flow_coverage": 1.0,
            "status": "ok",
            "priced_count": 1,
            "stocks": [
                {
                    "stock_code": "600519",
                    "status": "ok",
                    "total": 0.97,
                    "sp_ratio": 0.80,
                    "sp_score": 0.84,
                    "cmf": 0.13,
                    "flow_score": 0.71,
                    "factor_mask": "sp,cmf,flow",
                }
            ],
        }

        runtime = AlphaSiftSectorRotationRuntime(
            plate_service=plate_service,
            plate_repo=plate_repo,
            rotation_service=rotation_service,
            pricing_service=pricing_service,
            strategy_config=RotationStrategyConfig(watchpool_top_n=5),
        )

        runtime.run(trade_date=TRADE_DATE, max_results=3)
    finally:
        reset_run_diagnostic_context(token)

    event_types = [event["type"] for event in flow_events]
    assert "rotation_watchpool_started" in event_types
    assert "rotation_watchpool_completed" in event_types
    assert "rotation_constituents_801010_started" in event_types
    assert "rotation_constituents_801010_completed" in event_types
    assert "rotation_entry_801010_started" in event_types
    assert "rotation_entry_801010_completed" in event_types
    assert "rotation_pricing_801010_started" in event_types
    assert "rotation_pricing_801010_completed" in event_types
    assert "rotation_candidates_completed" in event_types

    pricing_event = next(event for event in flow_events if event["type"] == "rotation_pricing_801010_completed")
    assert pricing_event["metadata"]["boardid"] == 801010
    assert pricing_event["metadata"]["buycount"] == 1
    assert pricing_event["metadata"]["runid"] == 11
