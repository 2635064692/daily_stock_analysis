# -*- coding: utf-8 -*-
"""SPI async task runner — fire-and-forget backfill + daily refresh via AnalysisTaskQueue."""
from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta

from src.services.task_queue import AnalysisTaskQueue
from src.services.spi.plate_spi_service import PlateSpiService

logger = logging.getLogger(__name__)

SPI_TASK_STOCK_CODE = "SPI_PLATE_ROTATION"


class SpiTaskRunner:

    def __init__(self, queue=None, service=None):
        self.queue = queue or AnalysisTaskQueue()
        self.service = service or PlateSpiService()

    def refresh_daily(self) -> str:
        def _run():
            return self.service.refresh_all()

        task_info = self.queue.submit_background_task(
            _run,
            stock_code=SPI_TASK_STOCK_CODE,
            stock_name="SPI日终刷新",
            message="SPI日终刷新已加入队列",
        )
        return task_info.task_id

    def backfill_history(self, start_date: date, end_date: date) -> str:
        task_id = uuid.uuid4().hex
        total_days = (end_date - start_date).days + 1

        def _run():
            days_processed = 0
            boards_per_day = []
            current = start_date
            while current <= end_date:
                day_result = self.service.refresh_all(anchor_date=current)
                boards_per_day.append(day_result["success"])
                days_processed += 1
                progress = int(days_processed / total_days * 100)
                self.queue.update_task_progress(
                    task_id, progress,
                    f"{current} 完成, 成功 {day_result['success']}/{day_result['total']} 板块",
                )
                current += timedelta(days=1)
            return {
                "start": str(start_date),
                "end": str(end_date),
                "days_processed": days_processed,
                "boards_per_day": boards_per_day,
            }

        self.queue.submit_background_task(
            _run,
            stock_code=SPI_TASK_STOCK_CODE,
            stock_name="SPI板块回算",
            message="SPI回算已加入队列",
            task_id=task_id,
        )
        return task_id