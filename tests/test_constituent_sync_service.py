from __future__ import annotations

import sys
from datetime import date
from types import ModuleType
from unittest.mock import MagicMock

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
from src.services.spi.constituent_sync_service import (
    ShenwanBoard,
    ShenwanBoardUniverseProvider,
    ShenwanConstituentSyncService,
)
from src.storage import DatabaseManager, PlateSpiSnapshot
from src.utils.constituents_snapshot import ConstituentSnapshotRepo


@pytest.fixture()
def isolated_db(tmp_path):
    db_path = tmp_path / "constituent_sync.sqlite"
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{db_path}")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def test_sync_trade_date_saves_snapshots_and_waits_between_requests(isolated_db):
    board_provider = MagicMock()
    board_provider.list_boards.return_value = [
        ShenwanBoard(board_id=801010, board_name="农林牧渔"),
        ShenwanBoard(board_id=801030, board_name="基础化工"),
    ]
    fetcher = MagicMock()
    fetcher.fetch.side_effect = [["600519"], ["000001", "000002"]]
    sleep_fn = MagicMock()
    clock_values = iter([0.0, 1.0, 30.0])
    service = ShenwanConstituentSyncService(
        board_provider=board_provider,
        snapshot_repo=ConstituentSnapshotRepo(db_manager=isolated_db),
        fetcher=fetcher,
        sleep_fn=sleep_fn,
        monotonic_fn=lambda: next(clock_values),
    )

    result = service.sync_trade_date(date(2026, 7, 3), interval_seconds=30.0)

    assert result["saved"] == 2
    assert result["failed_fetch"] == 0
    assert result["skipped_existing"] == 0
    assert fetcher.fetch.call_args_list[0].args == (801010,)
    assert fetcher.fetch.call_args_list[1].args == (801030,)
    assert sleep_fn.call_args_list[0].args == (29.0,)
    repo = ConstituentSnapshotRepo(db_manager=isolated_db)
    assert repo.get_constituents(801010, date(2026, 7, 3)) == ["600519"]
    assert repo.get_constituents(801030, date(2026, 7, 3)) == ["000001", "000002"]


def test_sync_trade_date_skips_existing_snapshot_by_default(isolated_db):
    snapshot_repo = ConstituentSnapshotRepo(db_manager=isolated_db)
    snapshot_repo.save_constituents(801010, date(2026, 7, 3), ["600519", "000858"])
    board_provider = MagicMock()
    board_provider.list_boards.return_value = [
        ShenwanBoard(board_id=801010, board_name="农林牧渔")
    ]
    fetcher = MagicMock()
    service = ShenwanConstituentSyncService(
        board_provider=board_provider,
        snapshot_repo=snapshot_repo,
        fetcher=fetcher,
    )

    result = service.sync_trade_date(date(2026, 7, 3))

    assert result["saved"] == 0
    assert result["skipped_existing"] == 1
    assert result["boards"][0]["constituent_count"] == 2
    fetcher.fetch.assert_not_called()


def test_board_universe_provider_falls_back_to_plate_spi_snapshot(isolated_db):
    repo = PlateSpiRepository(db_manager=isolated_db)
    repo.upsert_snapshot(
        board_id=801010,
        trade_date=date(2026, 7, 3),
        spi=6,
        board_name="农林牧渔",
    )
    repo.upsert_snapshot(
        board_id=801030,
        trade_date=date(2026, 7, 3),
        spi=5,
        board_name="基础化工",
    )
    adapter = MagicMock()
    adapter.get_sw_first_levels.side_effect = RuntimeError("upstream broken")
    provider = ShenwanBoardUniverseProvider(adapter=adapter, plate_repo=repo)

    boards = provider.list_boards(anchor_date=date(2026, 7, 3))

    assert [(board.board_id, board.board_name) for board in boards] == [
        (801010, "农林牧渔"),
        (801030, "基础化工"),
    ]


def test_find_board_universe_uses_latest_snapshot_before_anchor(isolated_db):
    with isolated_db.get_session() as session:
        session.add_all(
            [
                PlateSpiSnapshot(
                    board_id=801010,
                    trade_date=date(2026, 7, 2),
                    spi=6,
                    board_name="农林牧渔",
                ),
                PlateSpiSnapshot(
                    board_id=801030,
                    trade_date=date(2026, 7, 2),
                    spi=5,
                    board_name="基础化工",
                ),
                PlateSpiSnapshot(
                    board_id=801010,
                    trade_date=date(2026, 7, 4),
                    spi=7,
                    board_name="农林牧渔",
                ),
            ]
        )
        session.commit()

    repo = PlateSpiRepository(db_manager=isolated_db)

    rows = repo.find_board_universe(anchor_date=date(2026, 7, 3))

    assert rows == [
        {
            "board_id": 801010,
            "board_name": "农林牧渔",
            "trade_date": date(2026, 7, 2),
        },
        {
            "board_id": 801030,
            "board_name": "基础化工",
            "trade_date": date(2026, 7, 2),
        },
    ]
