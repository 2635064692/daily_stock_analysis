# -*- coding: utf-8 -*-
"""Plate pricing API endpoints (read-only)."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from api.v1.errors import api_error
from src.repositories.pricing_repo import PricingRepository

router = APIRouter()


@router.get("/board/{board_id}/pricing")
def get_board_pricing(
    board_id: int,
    trade_date: Optional[date] = Query(None),
) -> Dict[str, Any]:
    repo = PricingRepository()
    anchor = trade_date or repo.find_latest_trade_date(board_id=board_id)
    if anchor is None:
        raise api_error(
            404, "pricing_no_data",
            f"No pricing data found for board {board_id}",
        )
    rows = repo.find_board_pricing_rank(board_id=board_id, trade_date=anchor)
    return {
        "board_id": board_id,
        "trade_date": anchor.isoformat(),
        "count": len(rows),
        "items": [
            {
                "stock_code": r.stock_code,
                "total": r.total,
                "rs_score": r.rs_score,
                "sp_ratio": r.sp_ratio,
                "sp_score": r.sp_score,
                "cmf": r.cmf,
                "flow_score": r.flow_score,
                "status": r.status,
                "factor_mask": r.factor_mask,
            }
            for r in rows
        ],
    }
