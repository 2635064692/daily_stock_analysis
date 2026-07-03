# -*- coding: utf-8 -*-
import logging
from datetime import date
from typing import Optional, List

from sqlalchemy import and_, select, desc

from src.storage import DatabaseManager, PlateSpiSnapshot, utc_naive_now

logger = logging.getLogger(__name__)


class PlateSpiRepository:

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def upsert_snapshot(
        self, *, board_id: int, trade_date: date, spi: int,
        confidence: str = 'normal', coverage: Optional[float] = None,
        board_name: Optional[str] = None,
    ) -> None:
        if spi == -1:
            return
        with self.db.get_session() as session:
            existing = session.execute(
                select(PlateSpiSnapshot).where(
                    and_(
                        PlateSpiSnapshot.board_id == board_id,
                        PlateSpiSnapshot.trade_date == trade_date,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                existing.spi = spi
                existing.confidence = confidence
                existing.coverage = coverage
                existing.board_name = board_name
                existing.updated_at = utc_naive_now()
            else:
                session.add(PlateSpiSnapshot(
                    board_id=board_id,
                    trade_date=trade_date,
                    spi=spi,
                    confidence=confidence,
                    coverage=coverage,
                    board_name=board_name,
                ))
            session.commit()

    def find_top_boards(
        self, *, anchor_date: date, top_n: int = 30,
    ) -> List[PlateSpiSnapshot]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(PlateSpiSnapshot)
                .where(
                    and_(
                        PlateSpiSnapshot.trade_date == anchor_date,
                        PlateSpiSnapshot.spi != -1,
                    )
                )
                .order_by(desc(PlateSpiSnapshot.spi))
                .limit(top_n)
            ).scalars().all()
            return list(rows)

    def find_board_series(
        self, *, board_id: int, days: int = 100,
    ) -> List[PlateSpiSnapshot]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(PlateSpiSnapshot)
                .where(
                    and_(
                        PlateSpiSnapshot.board_id == board_id,
                        PlateSpiSnapshot.spi != -1,
                    )
                )
                .order_by(desc(PlateSpiSnapshot.trade_date))
                .limit(days)
            ).scalars().all()
            return list(rows)