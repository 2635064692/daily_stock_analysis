# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from src.services.spi.rotation_daily_history_hydrator import RotationDailyHistoryHydrator


TRADE_DATE = date(2026, 7, 5)


def _bars(days: int) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(date=TRADE_DATE - timedelta(days=offset))
        for offset in range(days)
    ]


def test_hydrator_fetches_only_missing_daily_history():
    stock_repo = MagicMock()
    db_manager = MagicMock()
    fetcher_manager = MagicMock()

    stock_repo.get_range.side_effect = [
        _bars(30),
        [],
    ]
    frame = pd.DataFrame(
        [
            {
                "date": TRADE_DATE,
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10500.0,
                "pct_chg": 1.0,
            }
        ]
    )
    fetcher_manager.get_daily_data.return_value = (frame, "MockFetcher")

    hydrator = RotationDailyHistoryHydrator(
        stock_repo=stock_repo,
        db_manager=db_manager,
        fetcher_manager=fetcher_manager,
    )

    summary = hydrator.hydrate_codes(
        trade_date=TRADE_DATE,
        stock_codes=["600519", "000858"],
        required_history_days=30,
        max_workers=4,
    )

    fetcher_manager.get_daily_data.assert_called_once_with("000858", days=30)
    db_manager.save_daily_data.assert_called_once_with(frame, "000858", "MockFetcher")
    assert summary.total_codes == 2
    assert summary.satisfied_count == 1
    assert summary.requested_count == 1
    assert summary.hydrated_count == 1
    assert summary.failed_count == 0
    assert summary.max_workers == 1
    assert summary.errors == []


def test_hydrator_collects_partial_failures():
    stock_repo = MagicMock()
    db_manager = MagicMock()
    fetcher_manager = MagicMock()

    stock_repo.get_range.side_effect = [
        [],
        [],
    ]

    frame = pd.DataFrame(
        [
            {
                "date": TRADE_DATE,
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10500.0,
                "pct_chg": 1.0,
            }
        ]
    )

    def _fetch(code: str, *, days: int):
        if code == "600519":
            return frame, "MockFetcher"
        raise RuntimeError("provider timeout")

    fetcher_manager.get_daily_data.side_effect = _fetch

    hydrator = RotationDailyHistoryHydrator(
        stock_repo=stock_repo,
        db_manager=db_manager,
        fetcher_manager=fetcher_manager,
    )

    summary = hydrator.hydrate_codes(
        trade_date=TRADE_DATE,
        stock_codes=["600519", "000858"],
        required_history_days=30,
        max_workers=2,
    )

    db_manager.save_daily_data.assert_called_once_with(frame, "600519", "MockFetcher")
    assert summary.requested_count == 2
    assert summary.hydrated_count == 1
    assert summary.failed_count == 1
    assert summary.max_workers == 2
    assert len(summary.errors) == 1
    assert summary.errors[0].startswith("000858:")
