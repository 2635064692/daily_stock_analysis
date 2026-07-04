from __future__ import annotations

import sys
from datetime import date
from types import ModuleType

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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

from src.repositories.pricing_repo import PricingRepository
from src.storage import DatabaseManager, PricingFactorRun, PricingSnapshot


@pytest.fixture()
def isolated_db(tmp_path):
    db_path = tmp_path / "pricing_repo.sqlite"
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{db_path}")
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()


def _snapshot(
    stock_code: str,
    *,
    total: float | None,
    rs_score: float | None = 0.5,
    cmf: float | None = 0.1,
    flow_score: float | None = None,
    status: str = "ok",
    factor_mask: str | None = "rs,cmf",
) -> dict:
    return {
        "stock_code": stock_code,
        "rs_score": rs_score,
        "cmf": cmf,
        "flow_score": flow_score,
        "total": total,
        "status": status,
        "factor_mask": factor_mask,
    }


def test_save_pricing_batch_rolls_back_run_and_snapshots_on_failure(isolated_db):
    repo = PricingRepository(db_manager=isolated_db)

    with pytest.raises(IntegrityError):
        repo.save_pricing_batch(
            board_id=801010,
            trade_date=date(2024, 6, 3),
            constituent_source="snapshot",
            rs_window=20,
            cmf_window=20,
            base_weights={"rs": 0.5, "cmf": 0.3, "flow": 0.2},
            effective_weights={"rs": 0.625, "cmf": 0.375},
            flow_coverage=0.4,
            constituent_count=2,
            priced_count=1,
            degraded_count=0,
            status="partial",
            error=None,
            snapshots=[
                {
                    "stock_code": "000001",
                    "rs_score": 0.8,
                    "cmf": 0.2,
                    "flow_score": None,
                    "total": 0.575,
                    "status": "ok",
                    "factor_mask": "rs,cmf",
                },
                {
                    "stock_code": "000002",
                    "rs_score": 0.1,
                    "cmf": -0.3,
                    "flow_score": None,
                    "total": None,
                    "status": None,
                    "factor_mask": None,
                },
            ],
        )

    with isolated_db.get_session() as session:
        assert session.execute(select(PricingFactorRun)).scalars().all() == []
        assert session.execute(select(PricingSnapshot)).scalars().all() == []


def test_save_pricing_batch_persists_rows_and_rank_order(isolated_db):
    repo = PricingRepository(db_manager=isolated_db)
    trade_date = date(2024, 6, 3)

    run_id = repo.save_pricing_batch(
        board_id=801010,
        trade_date=trade_date,
        constituent_source="snapshot",
        rs_window=20,
        cmf_window=20,
        base_weights={"rs": 0.5, "cmf": 0.3, "flow": 0.2},
        effective_weights={"rs": 0.625, "cmf": 0.375},
        flow_coverage=0.4,
        constituent_count=3,
        priced_count=2,
        degraded_count=0,
        status="partial",
        error=None,
        snapshots=[
            _snapshot("000001", total=0.55, rs_score=0.6, cmf=0.2),
            _snapshot("000002", total=0.88, rs_score=0.9, cmf=0.4),
            _snapshot("000003", total=None, rs_score=None, cmf=None, status="missing_core_factor", factor_mask=None),
        ],
    )

    assert run_id > 0

    ranked = repo.find_board_pricing_rank(board_id=801010, trade_date=trade_date)
    assert [row.stock_code for row in ranked] == ["000002", "000001", "000003"]
    assert repo.find_latest_trade_date(board_id=801010) == trade_date

    with isolated_db.get_session() as session:
        runs = session.execute(select(PricingFactorRun)).scalars().all()
        snapshots = session.execute(select(PricingSnapshot)).scalars().all()

    assert len(runs) == 1
    assert runs[0].priced_count == 2
    assert len(snapshots) == 3
    assert {row.run_id for row in snapshots} == {run_id}


def test_save_pricing_batch_upserts_existing_snapshot_for_same_day(isolated_db):
    repo = PricingRepository(db_manager=isolated_db)
    trade_date = date(2024, 6, 3)

    first_run = repo.save_pricing_batch(
        board_id=801010,
        trade_date=trade_date,
        constituent_source="snapshot",
        rs_window=20,
        cmf_window=20,
        base_weights={"rs": 0.5, "cmf": 0.3, "flow": 0.2},
        effective_weights={"rs": 0.625, "cmf": 0.375},
        flow_coverage=0.0,
        constituent_count=1,
        priced_count=1,
        degraded_count=0,
        status="ok",
        error=None,
        snapshots=[_snapshot("000001", total=0.50, rs_score=0.5, cmf=0.0)],
    )
    second_run = repo.save_pricing_batch(
        board_id=801010,
        trade_date=trade_date,
        constituent_source="snapshot",
        rs_window=20,
        cmf_window=20,
        base_weights={"rs": 0.5, "cmf": 0.3, "flow": 0.2},
        effective_weights={"rs": 0.625, "cmf": 0.375},
        flow_coverage=0.0,
        constituent_count=1,
        priced_count=1,
        degraded_count=0,
        status="ok",
        error=None,
        snapshots=[_snapshot("000001", total=0.91, rs_score=0.95, cmf=0.3)],
    )

    assert second_run > first_run

    ranked = repo.find_board_pricing_rank(board_id=801010, trade_date=trade_date)
    assert len(ranked) == 1
    assert ranked[0].total == pytest.approx(0.91)
    assert ranked[0].rs_score == pytest.approx(0.95)
    assert ranked[0].run_id == second_run

    with isolated_db.get_session() as session:
        runs = session.execute(select(PricingFactorRun)).scalars().all()
        snapshots = session.execute(select(PricingSnapshot)).scalars().all()

    assert len(runs) == 2
    assert len(snapshots) == 1
