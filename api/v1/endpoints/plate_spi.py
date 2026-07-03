# -*- coding: utf-8 -*-
"""SPI plate rotation API endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.v1.errors import api_error
from src.repositories.plate_spi_repo import PlateSpiRepository
from src.services.spi.spi_task_runner import SpiTaskRunner
from src.services.spi.spi_time import spi_time
from src.services.task_queue import TaskStatus, get_task_queue

router = APIRouter()


class BackfillRequest(BaseModel):
    start_date: date
    end_date: date


@router.post("/refresh", status_code=202)
def refresh_spi() -> Dict[str, str]:
    task_id = SpiTaskRunner().refresh_daily()
    return {"task_id": task_id}


@router.post("/backfill", status_code=202)
def backfill_spi(request: BackfillRequest) -> Dict[str, str]:
    if request.start_date > request.end_date:
        raise api_error(
            400, "spi_invalid_date_range",
            "start_date must be <= end_date",
        )
    task_id = SpiTaskRunner().backfill_history(request.start_date, request.end_date)
    return {"task_id": task_id}


@router.get("/status/{task_id}")
def get_spi_task_status(task_id: str) -> Dict[str, Any]:
    task = get_task_queue().get_task(task_id)
    if task is None:
        raise api_error(
            404, "spi_task_not_found",
            f"SPI task {task_id} not found or expired",
        )
    return {
        "task_id": task.task_id,
        "status": task.status.value if isinstance(task.status, TaskStatus) else str(task.status),
        "progress": task.progress,
        "message": task.message,
        "result": task.result if task.status == TaskStatus.COMPLETED else None,
        "error": task.error,
    }


@router.get("/rankings")
def get_spi_rankings(
    date_param: Optional[date] = Query(None, alias="date"),
    top_n: int = Query(30, ge=1, le=100),
    days: int = Query(100, ge=1, le=365),
) -> Dict[str, Any]:
    anchor = date_param or spi_time()
    repo = PlateSpiRepository()
    boards = repo.find_top_boards(anchor_date=anchor, top_n=top_n)
    result = []
    for b in boards:
        entry = {
            "board_id": b.board_id,
            "board_name": b.board_name,
            "trade_date": b.trade_date.isoformat(),
            "spi": b.spi,
            "confidence": b.confidence,
            "coverage": b.coverage,
        }
        series = repo.find_board_series(board_id=b.board_id, days=days)
        entry["series"] = [
            {"date": s.trade_date.isoformat(), "spi": s.spi}
            for s in series
        ]
        result.append(entry)
    return {"anchor_date": anchor.isoformat(), "top_n": top_n, "boards": result}