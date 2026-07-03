# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import date
from typing import List, Set

import pandas as pd

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.utils.constituents_snapshot import ConstituentSnapshotRepo

logger = logging.getLogger(__name__)


class RotationService:

    def __init__(self, repo=None, constituent_repo=None, db_manager=None):
        self._repo: PlateSpiRepository = repo or PlateSpiRepository()
        self._constituent_repo: ConstituentSnapshotRepo = (
            constituent_repo or ConstituentSnapshotRepo()
        )
        self._watchpool: Set[int] = set()

    def update_watchpool(self, trade_date: date, top_n: int = 30) -> Set[int]:
        boards = self._repo.find_top_boards_v2(anchor_date=trade_date, top_n=top_n)
        self._watchpool = {d["board_id"] for d in boards}
        return self._watchpool

    def check_entry(
        self,
        board_id: int,
        trade_date: date,
        entry_ema_period: int = 20,
        volume_ratio_threshold: float = 1.2,
    ) -> List[str]:
        codes = self._constituent_repo.get_constituents(board_id, trade_date)
        if not codes:
            return []

        triggered: List[str] = []
        for stock_code in codes:
            try:
                rows = _get_latest(stock_code, entry_ema_period + 5)
                if not rows:
                    continue
                rows_asc = list(reversed(rows))
                closes = [r.close for r in rows_asc if r.close is not None]
                volumes = [r.volume for r in rows_asc if r.volume is not None]
                if len(closes) < 2:
                    continue

                ema_val = pd.Series(closes).ewm(span=entry_ema_period, adjust=False).mean().iloc[-1]
                last_close = closes[-1]

                if len(volumes) >= entry_ema_period:
                    vol_mean = sum(volumes[-entry_ema_period:]) / entry_ema_period
                    volume_ratio = (volumes[-1] / vol_mean) if vol_mean > 0 else 0.0
                else:
                    volume_ratio = 0.0

                if last_close <= ema_val * 1.02 and volume_ratio >= volume_ratio_threshold:
                    self._repo.upsert_rotation_signal(
                        board_id=board_id,
                        stock_code=stock_code,
                        trade_date=trade_date,
                        action="BUY",
                        reason=f"pullback to EMA{entry_ema_period}, vol_ratio={volume_ratio:.2f}",
                    )
                    triggered.append(stock_code)
            except Exception:
                logger.warning(
                    "check_entry failed for board=%s stock=%s", board_id, stock_code, exc_info=True
                )

        return triggered

    def check_exit(
        self,
        board_id: int,
        trade_date: date,
        top_m: int = 50,
        exit_ema_period: int = 5,
    ) -> List[str]:
        top_boards = self._repo.find_top_boards_v2(anchor_date=trade_date, top_n=top_m)
        board_ids_in_top = {d["board_id"] for d in top_boards}

        codes = self._constituent_repo.get_constituents(board_id, trade_date)
        triggered: List[str] = []

        if board_id not in board_ids_in_top:
            for stock_code in codes:
                try:
                    self._repo.upsert_rotation_signal(
                        board_id=board_id,
                        stock_code=stock_code,
                        trade_date=trade_date,
                        action="SELL",
                        reason=f"board_rank_out_of_top_{top_m}",
                    )
                    triggered.append(stock_code)
                except Exception:
                    logger.warning(
                        "check_exit sell signal failed board=%s stock=%s",
                        board_id, stock_code, exc_info=True,
                    )
            return triggered

        for stock_code in codes:
            try:
                rows = _get_latest(stock_code, exit_ema_period + 5)
                if not rows:
                    continue
                rows_asc = list(reversed(rows))
                closes = [r.close for r in rows_asc if r.close is not None]
                if len(closes) < 2:
                    continue

                ema_val = pd.Series(closes).ewm(span=exit_ema_period, adjust=False).mean().iloc[-1]
                last_close = closes[-1]

                if last_close < ema_val:
                    self._repo.upsert_rotation_signal(
                        board_id=board_id,
                        stock_code=stock_code,
                        trade_date=trade_date,
                        action="SELL",
                        reason=f"close_below_EMA{exit_ema_period}",
                    )
                    triggered.append(stock_code)
            except Exception:
                logger.warning(
                    "check_exit ema failed board=%s stock=%s", board_id, stock_code, exc_info=True
                )

        return triggered


def _get_latest(stock_code: str, days: int):
    from src.repositories.stock_repo import StockRepository
    return StockRepository().get_latest(stock_code, days)
