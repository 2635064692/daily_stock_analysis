# -*- coding: utf-8 -*-
import os
import sqlite3
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy import inspect

from src.config import Config
from src.storage import DatabaseManager
from src.utils.constituents_snapshot import (
    ConstituentFetcher,
    ConstituentRuntimeResolver,
    ConstituentSnapshotRepo,
)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_constituents.db"
    old = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old


@pytest.fixture
def repo(db):
    return ConstituentSnapshotRepo(db_manager=db)


@pytest.fixture(autouse=True)
def reset_fetcher_runtime_state():
    ConstituentFetcher.reset_runtime_state()
    yield
    ConstituentFetcher.reset_runtime_state()


D1 = date(2026, 1, 12)
D2 = date(2026, 1, 13)
D3 = date(2026, 1, 14)
BOARD = 801010


class TestSaveAndGetConstituents:

    def test_basic_save_and_get(self, repo):
        codes = ["600519", "000858", "002304"]
        repo.save_constituents(BOARD, D1, codes)
        result = repo.get_constituents(BOARD, D1)
        assert result == codes

    def test_duplicate_write_updates_existing_snapshot(self, repo):
        codes = ["600519", "000858"]
        repo.save_constituents(BOARD, D1, codes)
        repo.save_constituents(BOARD, D1, ["999999"])  # second write upserts current snapshot
        result = repo.get_constituents(BOARD, D1)
        assert result == ["999999"]

    def test_get_missing_date_returns_empty(self, repo):
        repo.save_constituents(BOARD, D1, ["600519"])
        result = repo.get_constituents(BOARD, D2)
        assert result == []

    def test_get_nonexistent_board_returns_empty(self, repo):
        result = repo.get_constituents(999999, D1)
        assert result == []

    def test_date_isolation(self, repo):
        repo.save_constituents(BOARD, D1, ["600519"])
        repo.save_constituents(BOARD, D2, ["000858"])
        assert repo.get_constituents(BOARD, D1) == ["600519"]
        assert repo.get_constituents(BOARD, D2) == ["000858"]

    def test_stock_code_zero_padding(self, repo):
        codes = ["000519", "600519", "000001"]
        repo.save_constituents(BOARD, D1, codes)
        result = repo.get_constituents(BOARD, D1)
        assert result == codes

    def test_empty_list_is_stored_and_returned(self, repo):
        repo.save_constituents(BOARD, D1, [])
        result = repo.get_constituents(BOARD, D1)
        assert result == []

    def test_snapshot_state_persists_stale_metadata(self, repo):
        repo.save_constituents(
            BOARD,
            D1,
            ["600519", "000858"],
            origin_trade_date=D1,
            is_stale=True,
            snapshot_age_days=3,
        )

        snapshot = repo.get_snapshot_state(BOARD, D1)

        assert snapshot is not None
        assert snapshot.origin_trade_date == D1
        assert snapshot.is_stale is True
        assert snapshot.snapshot_age_days == 3

    def test_materialize_recent_snapshot_reuses_latest_codes(self, repo):
        repo.save_constituents(BOARD, D1, ["600519", "000858"])

        snapshot = repo.materialize_recent_snapshot(BOARD, D2, max_stale_trading_days=22)

        assert snapshot is not None
        assert snapshot.trade_date == D2
        assert snapshot.stock_codes == ["600519", "000858"]
        assert snapshot.origin_trade_date == D1
        assert snapshot.is_stale is True
        assert snapshot.snapshot_age_days == 1

    def test_materialize_recent_snapshot_respects_max_stale_days(self, repo):
        repo.save_constituents(BOARD, date(2026, 1, 2), ["600519"])

        snapshot = repo.materialize_recent_snapshot(
            BOARD,
            date(2026, 2, 16),
            max_stale_trading_days=5,
        )

        assert snapshot is None
        assert repo.get_constituents(BOARD, date(2026, 2, 16)) == []


class TestConstituentFetcher:

    def test_fetch_uses_browser_headers_and_normalizes_codes(self):
        fetcher = ConstituentFetcher()
        response = MagicMock(status_code=200, text="<html></html>")
        frame = pd.DataFrame({"股票代码": ["000019.SZ", "600519.SH", 300999]})

        with (
            patch("src.utils.constituents_snapshot.requests.get", return_value=response) as get_mock,
            patch("src.utils.constituents_snapshot.pd.read_html", return_value=[frame]) as read_html_mock,
            patch("src.utils.constituents_snapshot.time.sleep"),
        ):
            result = fetcher.fetch(801010)

        assert result == ["000019", "600519", "300999"]
        get_mock.assert_called_once_with(
            "https://legulegu.com/stockdata/index-composition?industryCode=801010.SI",
            headers=fetcher.REQUEST_HEADERS,
            timeout=15,
        )
        read_html_mock.assert_called_once()

    def test_fetch_returns_empty_on_non_200(self):
        fetcher = ConstituentFetcher()
        response = MagicMock(status_code=403, text="")

        with (
            patch("src.utils.constituents_snapshot.requests.get", return_value=response),
            patch("src.utils.constituents_snapshot.time.sleep"),
        ):
            result = fetcher.fetch(801010)

        assert result == []

    def test_fetch_throttles_same_board_within_refetch_interval(self):
        fetcher = ConstituentFetcher()
        response = MagicMock(status_code=200, text="<html></html>")
        frame = pd.DataFrame({"股票代码": ["000019.SZ"]})

        with (
            patch("src.utils.constituents_snapshot.requests.get", return_value=response) as get_mock,
            patch("src.utils.constituents_snapshot.pd.read_html", return_value=[frame]),
            patch("src.utils.constituents_snapshot.time.sleep"),
            patch("src.utils.constituents_snapshot.time.monotonic", side_effect=[0.0, 10.0]),
        ):
            first = fetcher.fetch(801010)
            second = fetcher.fetch(801010)

        assert first == ["000019"]
        assert second == []
        get_mock.assert_called_once()


class TestConstituentRuntimeResolver:

    def test_resolve_current_day_materializes_stale_snapshot_without_refetch(self, repo):
        repo.save_constituents(BOARD, D1, ["600519", "000858"])
        fetcher = MagicMock()
        resolver = ConstituentRuntimeResolver(
            repo=repo,
            fetcher=fetcher,
            current_trade_date_provider=lambda: D2,
            max_stale_trading_days=22,
        )

        codes, source, snapshot = resolver.resolve(BOARD, D2)

        assert codes == ["600519", "000858"]
        assert source == "stale_snapshot"
        assert snapshot is not None
        assert snapshot.trade_date == D2
        assert snapshot.origin_trade_date == D1
        assert snapshot.is_stale is True
        fetcher.fetch.assert_not_called()
        fetcher.defer_refetch.assert_called_once_with(BOARD)

    def test_resolve_current_day_refreshes_existing_stale_snapshot_when_fetch_succeeds(self, repo):
        repo.save_constituents(
            BOARD,
            D2,
            ["600519"],
            origin_trade_date=D1,
            is_stale=True,
            snapshot_age_days=1,
        )
        fetcher = MagicMock()
        fetcher.fetch.return_value = ["600519", "000858"]
        resolver = ConstituentRuntimeResolver(
            repo=repo,
            fetcher=fetcher,
            current_trade_date_provider=lambda: D2,
            max_stale_trading_days=22,
        )

        codes, source, snapshot = resolver.resolve(BOARD, D2)

        assert codes == ["600519", "000858"]
        assert source == "current"
        assert snapshot is not None
        assert snapshot.is_stale is False
        assert snapshot.origin_trade_date == D2
        assert snapshot.snapshot_age_days == 0
        fetcher.fetch.assert_called_once_with(BOARD)

    def test_resolve_ignores_overdue_stale_snapshot_when_refresh_fails(self, repo):
        repo.save_constituents(
            BOARD,
            D3,
            ["600519"],
            origin_trade_date=date(2025, 12, 1),
            is_stale=True,
            snapshot_age_days=40,
        )
        fetcher = MagicMock()
        fetcher.fetch.return_value = []
        resolver = ConstituentRuntimeResolver(
            repo=repo,
            fetcher=fetcher,
            current_trade_date_provider=lambda: D3,
            max_stale_trading_days=22,
        )

        codes, source, snapshot = resolver.resolve(BOARD, D3)

        assert codes == []
        assert source == "missing"
        assert snapshot is None


def test_database_manager_backfills_constituent_snapshot_metadata_columns(tmp_path):
    db_path = tmp_path / "legacy_constituent_snapshot.sqlite"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE constituent_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                board_id INTEGER NOT NULL,
                trade_date DATE NOT NULL,
                stock_codes_json TEXT NOT NULL,
                created_at DATETIME
            );
            INSERT INTO constituent_snapshot (
                board_id, trade_date, stock_codes_json, created_at
            ) VALUES (
                801010, '2026-01-12', '["600519","000858"]', CURRENT_TIMESTAMP
            );
            """
        )

    old = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = str(db_path)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    try:
        db = DatabaseManager.get_instance()
        columns = {column["name"] for column in inspect(db._engine).get_columns("constituent_snapshot")}
        assert {"origin_trade_date", "is_stale", "snapshot_age_days"} <= columns

        repo = ConstituentSnapshotRepo(db_manager=db)
        snapshot = repo.get_snapshot_state(BOARD, D1)
        assert snapshot is not None
        assert snapshot.origin_trade_date == D1
        assert snapshot.is_stale is False
        assert snapshot.snapshot_age_days == 0
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        if old is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old
