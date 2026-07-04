# -*- coding: utf-8 -*-
import json
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, select, desc, nulls_last, func

from src.storage import DatabaseManager, PricingFactorRun, PricingSnapshot


class PricingRepository:

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def upsert_pricing(
        self,
        *,
        board_id: int,
        stock_code: str,
        trade_date: date,
        rs_score: Optional[float],
        sp_ratio: Optional[float],
        sp_score: Optional[float],
        cmf: Optional[float],
        flow_score: Optional[float],
        total: Optional[float],
        status: str,
        factor_mask: Optional[str] = None,
        run_id: Optional[int] = None,
    ) -> None:
        with self.db.get_session() as session:
            self._upsert_pricing_in_session(
                session=session,
                board_id=board_id,
                stock_code=stock_code,
                trade_date=trade_date,
                rs_score=rs_score,
                sp_ratio=sp_ratio,
                sp_score=sp_score,
                cmf=cmf,
                flow_score=flow_score,
                total=total,
                status=status,
                factor_mask=factor_mask,
                run_id=run_id,
            )
            session.commit()

    def insert_factor_run(
        self,
        *,
        board_id: int,
        trade_date: date,
        constituent_source: str,
        rs_window: int,
        cmf_window: int,
        base_weights: Dict,
        effective_weights: Dict,
        flow_coverage: Optional[float] = None,
        constituent_count: Optional[int] = None,
        priced_count: Optional[int] = None,
        degraded_count: Optional[int] = None,
        status: str,
        error: Optional[str] = None,
    ) -> int:
        with self.db.get_session() as session:
            run = PricingFactorRun(
                board_id=board_id,
                trade_date=trade_date,
                constituent_source=constituent_source,
                rs_window=rs_window,
                cmf_window=cmf_window,
                base_weights_json=json.dumps(base_weights),
                effective_weights_json=json.dumps(effective_weights),
                flow_coverage=flow_coverage,
                constituent_count=constituent_count,
                priced_count=priced_count,
                degraded_count=degraded_count,
                status=status,
                error=error,
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            return run.id

    def save_pricing_batch(
        self,
        *,
        board_id: int,
        trade_date: date,
        constituent_source: str,
        rs_window: int,
        cmf_window: int,
        base_weights: Dict[str, float],
        effective_weights: Dict[str, float],
        flow_coverage: Optional[float] = None,
        constituent_count: Optional[int] = None,
        priced_count: Optional[int] = None,
        degraded_count: Optional[int] = None,
        status: str,
        error: Optional[str] = None,
        snapshots: Sequence[Dict[str, Any]] = (),
    ) -> int:
        with self.db.get_session() as session:
            try:
                run = PricingFactorRun(
                    board_id=board_id,
                    trade_date=trade_date,
                    constituent_source=constituent_source,
                    rs_window=rs_window,
                    cmf_window=cmf_window,
                    base_weights_json=json.dumps(base_weights),
                    effective_weights_json=json.dumps(effective_weights),
                    flow_coverage=flow_coverage,
                    constituent_count=constituent_count,
                    priced_count=priced_count,
                    degraded_count=degraded_count,
                    status=status,
                    error=error,
                )
                session.add(run)
                session.flush()

                for snapshot in snapshots:
                    self._upsert_pricing_in_session(
                        session=session,
                        board_id=board_id,
                        stock_code=snapshot["stock_code"],
                        trade_date=trade_date,
                        rs_score=snapshot.get("rs_score"),
                        sp_ratio=snapshot.get("sp_ratio"),
                        sp_score=snapshot.get("sp_score"),
                        cmf=snapshot.get("cmf"),
                        flow_score=snapshot.get("flow_score"),
                        total=snapshot.get("total"),
                        status=snapshot["status"],
                        factor_mask=snapshot.get("factor_mask"),
                        run_id=run.id,
                    )

                session.commit()
                session.refresh(run)
                return run.id
            except Exception:
                session.rollback()
                raise

    def find_board_pricing_rank(
        self, *, board_id: int, trade_date: date,
    ) -> List[PricingSnapshot]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(PricingSnapshot)
                .where(
                    and_(
                        PricingSnapshot.board_id == board_id,
                        PricingSnapshot.trade_date == trade_date,
                    )
                )
                .order_by(
                    nulls_last(desc(PricingSnapshot.total))
                )
            ).scalars().all()
            return list(rows)

    def find_latest_trade_date(self, *, board_id: int) -> Optional[date]:
        with self.db.get_session() as session:
            row = session.execute(
                select(PricingSnapshot.trade_date)
                .where(PricingSnapshot.board_id == board_id)
                .order_by(desc(PricingSnapshot.trade_date))
                .limit(1)
            ).scalar_one_or_none()
            return row

    def count_snapshots(self, *, trade_date: date, board_id: Optional[int] = None) -> int:
        conditions = [PricingSnapshot.trade_date == trade_date]
        if board_id is not None:
            conditions.append(PricingSnapshot.board_id == board_id)
        with self.db.get_session() as session:
            count = session.execute(
                select(func.count())
                .select_from(PricingSnapshot)
                .where(and_(*conditions))
            ).scalar_one()
            return int(count or 0)

    def _upsert_pricing_in_session(
        self,
        *,
        session,
        board_id: int,
        stock_code: str,
        trade_date: date,
        rs_score: Optional[float],
        sp_ratio: Optional[float],
        sp_score: Optional[float],
        cmf: Optional[float],
        flow_score: Optional[float],
        total: Optional[float],
        status: str,
        factor_mask: Optional[str],
        run_id: Optional[int],
    ) -> None:
        existing = session.execute(
            select(PricingSnapshot).where(
                and_(
                    PricingSnapshot.board_id == board_id,
                    PricingSnapshot.stock_code == stock_code,
                    PricingSnapshot.trade_date == trade_date,
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.rs_score = rs_score
            existing.sp_ratio = sp_ratio
            existing.sp_score = sp_score
            existing.cmf = cmf
            existing.flow_score = flow_score
            existing.total = total
            existing.status = status
            existing.factor_mask = factor_mask
            existing.run_id = run_id
            return

        session.add(PricingSnapshot(
            board_id=board_id,
            stock_code=stock_code,
            trade_date=trade_date,
            rs_score=rs_score,
            sp_ratio=sp_ratio,
            sp_score=sp_score,
            cmf=cmf,
            flow_score=flow_score,
            total=total,
            status=status,
            factor_mask=factor_mask,
            run_id=run_id,
        ))
