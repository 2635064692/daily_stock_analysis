# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional

from src.repositories.pricing_repo import PricingRepository
from src.services.spi.spi_time import spi_time

logger = logging.getLogger(__name__)


class PricingFilter:
    """对候选股列表按板块内比价快照过滤并增强字段。"""

    def __init__(self, pricing_repo: Optional[PricingRepository] = None):
        self._repo = pricing_repo or PricingRepository()

    def apply(
        self,
        candidates: List[Dict],
        *,
        min_total_score: float = 0.5,
        trade_date: Optional[date] = None,
    ) -> List[Dict]:
        """
        按板块内比价快照过滤候选股，增强比价字段并重排序。

        :param candidates: 原始候选股列表，每项需含 "code" 和 "board_id" 键。
        :param min_total_score: total 低于此阈值的候选股被过滤（None 视为通过）。
        :param trade_date: 查询日期，默认取 spi_time() 锚点日期。
        :returns: 增强后并按 total DESC 重排序的候选股列表。
        """
        if not candidates:
            return []

        td = trade_date or spi_time()

        # Step 1: 按板块分组
        board_groups: Dict[int, List[Dict]] = defaultdict(list)
        no_board: List[Dict] = []
        for c in candidates:
            board_id = c.get("board_id")
            if board_id is not None:
                board_groups[int(board_id)].append(c)
            else:
                no_board.append(c)

        enriched: List[Dict] = []

        # Step 2: 逐板块增强
        for board_id, group in board_groups.items():
            try:
                rows = self._repo.find_board_pricing_rank(
                    board_id=board_id, trade_date=td
                )
            except Exception:
                logger.exception("pricing_filter: board %s query failed, skip", board_id)
                enriched.extend(group)
                continue

            if not rows:
                logger.debug("pricing_filter: board %s no pricing snapshot on %s", board_id, td)
                enriched.extend(group)
                continue

            # 排序并建索引（find_board_pricing_rank 已按 total DESC，保持一致）
            sorted_rows = sorted(rows, key=lambda r: r.total if r.total is not None else -1.0, reverse=True)
            pricing_map = {
                r.stock_code: {
                    "total": r.total,
                    "pricing_rank": idx + 1,
                    "sp_ratio": r.sp_ratio,
                    "sp_score": r.sp_score,
                    "cmf": r.cmf,
                    "flow_score": r.flow_score,
                    "status": getattr(r, "status", None),
                    "factor_mask": getattr(r, "factor_mask", None),
                }
                for idx, r in enumerate(sorted_rows)
            }

            # Step 3: 匹配候选股，增强字段，过滤低分
            for candidate in group:
                code = candidate.get("code")
                pricing = pricing_map.get(code) if code else None

                updated = dict(candidate)
                if pricing:
                    updated.update(pricing)
                    total = pricing["total"]
                    if total is not None and total < min_total_score:
                        logger.debug(
                            "pricing_filter: drop %s (total=%.3f < %.3f)",
                            code, total, min_total_score,
                        )
                        continue

                enriched.append(updated)

        # 无 board_id 的候选股直接透传
        enriched.extend(no_board)

        # Step 4: 全局按 total DESC 重排序（None 排末尾）
        enriched.sort(
            key=lambda x: x.get("total") if x.get("total") is not None else -1.0,
            reverse=True,
        )
        return enriched
