# -*- coding: utf-8 -*-
import os
import pytest
from datetime import date

from src.config import Config
from src.storage import DatabaseManager
from src.utils.constituents_snapshot import ConstituentSnapshotRepo


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


D1 = date(2026, 1, 10)
D2 = date(2026, 1, 11)
BOARD = 801010


class TestSaveAndGetConstituents:

    def test_basic_save_and_get(self, repo):
        codes = ["600519", "000858", "002304"]
        repo.save_constituents(BOARD, D1, codes)
        result = repo.get_constituents(BOARD, D1)
        assert result == codes

    def test_idempotency_duplicate_write(self, repo):
        codes = ["600519", "000858"]
        repo.save_constituents(BOARD, D1, codes)
        repo.save_constituents(BOARD, D1, ["999999"])  # second write ignored
        result = repo.get_constituents(BOARD, D1)
        assert result == codes

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
