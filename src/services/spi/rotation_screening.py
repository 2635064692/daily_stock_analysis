# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.repositories.pricing_repo import PricingRepository

logger = logging.getLogger(__name__)

_TOP_STOCKS_PER_BOARD = 5
_SELECTION_REASON = "板块轮动BUY信号"
_SNAPSHOT_SOURCE = "spi_v2:akshare_sw"


class SectorRotationScreener:

    def __init__(
        self,
        spi_repo: Optional[PlateSpiRepository] = None,
        pricing_repo: Optional[PricingRepository] = None,
    ) -> None:
        self._spi_repo = spi_repo or PlateSpiRepository()
        self._pricing_repo = pricing_repo or PricingRepository()

    def screen(
        self,
        trade_date: date,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        warnings: List[str] = []

        # Step 1: get BUY signals for the date
        all_signals = self._spi_repo.find_rotation_signals(trade_date=trade_date)
        buy_signals = [s for s in all_signals if s.get("action") == "BUY"]
        if not buy_signals:
            return {
                "candidates": [],
                "rotation_boards": 0,
                "snapshot_source": _SNAPSHOT_SOURCE,
                "warnings": ["无BUY信号"],
            }

        # Step 2: group by board
        board_stocks: Dict[int, List[str]] = defaultdict(list)
        for sig in buy_signals:
            board_stocks[sig["board_id"]].append(sig["stock_code"])

        rotation_boards = len(board_stocks)

        # Step 3: fetch v2 board scores for all active boards
        board_map: Dict[int, Dict[str, Any]] = {}
        try:
            boards = self._spi_repo.find_top_boards_v2(
                anchor_date=trade_date,
                top_n=200,
            )
            board_map = {b["board_id"]: b for b in boards}
        except Exception:
            logger.warning("find_top_boards_v2 failed for %s", trade_date, exc_info=True)
            warnings.append("板块v2评分查询失败")

        # Step 4: per-board stock selection via pricing rank
        candidates: List[Dict[str, Any]] = []
        for board_id, signal_codes in board_stocks.items():
            board_info = board_map.get(board_id)
            board_name = board_info["board_name"] if board_info else None
            board_v2_score = board_info["v2_score"] if board_info else None

            try:
                pricing_rows = self._pricing_repo.find_board_pricing_rank(
                    board_id=board_id,
                    trade_date=trade_date,
                )
            except Exception:
                logger.warning(
                    "find_board_pricing_rank failed board=%s date=%s",
                    board_id, trade_date, exc_info=True,
                )
                pricing_rows = []

            signal_code_set = set(signal_codes)
            # filter to signal stocks only, already ordered by total DESC from repo
            filtered = [r for r in pricing_rows if r.stock_code in signal_code_set]
            ranked = sorted(
                filtered,
                key=lambda r: r.total if r.total is not None else -1.0,
                reverse=True,
            )[:_TOP_STOCKS_PER_BOARD]

            for rank_idx, row in enumerate(ranked, start=1):
                candidates.append({
                    "code": row.stock_code,
                    "board_id": board_id,
                    "board_name": board_name,
                    "board_v2_score": board_v2_score,
                    "pricing_rank": rank_idx,
                    "total": row.total,
                    "sp_ratio": row.sp_ratio,
                    "sp_score": row.sp_score,
                    "cmf": row.cmf,
                    "flow_score": row.flow_score,
                    "status": row.status,
                    "factor_mask": row.factor_mask,
                    "selection_reason": _SELECTION_REASON,
                })

            # fallback: signal stocks with no pricing snapshot
            priced_codes = {r.stock_code for r in filtered}
            for code in signal_codes:
                if code not in priced_codes:
                    candidates.append({
                        "code": code,
                        "board_id": board_id,
                        "board_name": board_name,
                        "board_v2_score": board_v2_score,
                        "pricing_rank": None,
                        "total": None,
                        "sp_ratio": None,
                        "sp_score": None,
                        "cmf": None,
                        "flow_score": None,
                        "status": "missing_pricing",
                        "factor_mask": None,
                        "selection_reason": _SELECTION_REASON,
                    })

        # Step 5: sort by board v2 score DESC, then by total DESC within same board
        candidates.sort(
            key=lambda x: (
                x.get("board_v2_score") or 0.0,
                x.get("total") or 0.0,
            ),
            reverse=True,
        )

        return {
            "candidates": candidates[:max_results],
            "rotation_boards": rotation_boards,
            "snapshot_source": _SNAPSHOT_SOURCE,
            "warnings": warnings,
        }
