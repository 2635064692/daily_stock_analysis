from __future__ import annotations

import sys
from datetime import date
from types import ModuleType

import pytest

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

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.storage import DatabaseManager, PlateSpiSnapshot, SpiRotationSignal


@pytest.fixture()
def isolated_db(tmp_path):
    db_path = tmp_path / "plate_spi_repo.sqlite"
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{db_path}")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_count_snapshots_and_v2_rows(isolated_db):
    repo = PlateSpiRepository(db_manager=isolated_db)
    trade_date = date(2026, 7, 3)

    with isolated_db.get_session() as session:
        session.add_all(
            [
                PlateSpiSnapshot(board_id=801010, trade_date=trade_date, spi=7, v2_score=88.5),
                PlateSpiSnapshot(board_id=801020, trade_date=trade_date, spi=6, v2_score=None),
            ]
        )
        session.commit()

    assert repo.count_snapshots(trade_date=trade_date) == 2
    assert repo.count_snapshots(trade_date=trade_date, require_v2=True) == 1


def test_count_rotation_signals_by_action(isolated_db):
    repo = PlateSpiRepository(db_manager=isolated_db)
    trade_date = date(2026, 7, 3)

    with isolated_db.get_session() as session:
        session.add_all(
            [
                SpiRotationSignal(board_id=801010, stock_code="600519", trade_date=trade_date, action="BUY"),
                SpiRotationSignal(board_id=801010, stock_code="000858", trade_date=trade_date, action="BUY"),
                SpiRotationSignal(board_id=801020, stock_code="000001", trade_date=trade_date, action="SELL"),
            ]
        )
        session.commit()

    assert repo.count_rotation_signals(trade_date=trade_date) == 3
    assert repo.count_rotation_signals(trade_date=trade_date, action="BUY") == 2
    assert repo.count_rotation_signals(trade_date=trade_date, action="SELL", board_id=801020) == 1
