# -*- coding: utf-8 -*-
"""SPI async task runner — fire-and-forget backfill + daily refresh via AnalysisTaskQueue."""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import date

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.services.spi.data_hydrator import SpiDataHydrator
from src.services.task_queue import AnalysisTaskQueue
from src.services.spi.plate_spi_service import PlateSpiService
from src.services.spi.spi_time import iter_trading_dates, spi_time

logger = logging.getLogger(__name__)

SPI_TASK_STOCK_CODE = "SPI_PLATE_ROTATION"


class SpiTaskRunner:
    _submit_lock = threading.Lock()

    def __init__(self, queue=None, service=None, task_repo=None, data_hydrator_factory=None):
        self.queue = queue or AnalysisTaskQueue()
        self.service = service or PlateSpiService()
        self.task_repo = task_repo or PlateSpiRepository()
        self._data_hydrator_factory = data_hydrator_factory

    def refresh_daily(self) -> str:
        def _run():
            anchor_date = spi_time()
            hydrator = self._build_data_hydrator()
            return hydrator.hydrate_trade_date(anchor_date)

        return self._submit_spi_task(
            run_task=_run,
            stock_name="SPI日终刷新",
            message="SPI日终刷新已加入队列",
        )

    def backfill_history(self, start_date: date, end_date: date) -> str:
        task_id = uuid.uuid4().hex
        trading_dates = iter_trading_dates(start_date, end_date)
        total_days = len(trading_dates)

        def _run():
            if total_days == 0:
                return {
                    "start": str(start_date),
                    "end": str(end_date),
                    "days_processed": 0,
                    "boards_per_day": [],
                }

            days_processed = 0
            boards_per_day = []
            board_count_recorded = False
            for current in trading_dates:
                day_result = self.service.refresh_all(anchor_date=current)
                if not board_count_recorded:
                    board_count = int(day_result.get("total") or 0)
                    self.task_repo.update_backfill_task_board_count(
                        task_queue_id=task_id,
                        board_count=board_count,
                    )
                    board_count_recorded = True
                boards_per_day.append(day_result["success"])
                days_processed += 1
                progress = int(days_processed / total_days * 100)
                self.queue.update_task_progress(
                    task_id, progress,
                    f"{current} 完成, 成功 {day_result['success']}/{day_result['total']} 板块",
                )
            return {
                "start": str(start_date),
                "end": str(end_date),
                "days_processed": days_processed,
                "boards_per_day": boards_per_day,
            }

        return self._submit_backfill_task(
            task_id=task_id,
            start_date=start_date,
            end_date=end_date,
            run_task=_run,
        )

    def _submit_backfill_task(
        self,
        *,
        task_id: str,
        start_date: date,
        end_date: date,
        run_task,
    ) -> str:
        with self._submit_lock:
            existing_task_id = self._find_active_spi_task_id()
            if existing_task_id is not None:
                return existing_task_id

            self.task_repo.create_backfill_task_run(
                task_queue_id=task_id,
                start_date=start_date,
                end_date=end_date,
            )
            try:
                task_info = self.queue.submit_background_task(
                    run_task,
                    stock_code=SPI_TASK_STOCK_CODE,
                    stock_name="SPI板块回算",
                    message="SPI回算已加入队列",
                    task_id=task_id,
                )
            except Exception:
                self.task_repo.delete_backfill_task_run(task_queue_id=task_id)
                raise
            return task_info.task_id

    def _submit_spi_task(self, *, run_task, stock_name: str, message: str) -> str:
        with self._submit_lock:
            existing_task_id = self._find_active_spi_task_id()
            if existing_task_id is not None:
                return existing_task_id
            task_info = self.queue.submit_background_task(
                run_task,
                stock_code=SPI_TASK_STOCK_CODE,
                stock_name=stock_name,
                message=message,
            )
            return task_info.task_id

    def _find_active_spi_task_id(self) -> str | None:
        for task in self.queue.list_pending_tasks():
            if task.stock_code == SPI_TASK_STOCK_CODE:
                return task.task_id
        return None

    def _build_data_hydrator(self) -> SpiDataHydrator:
        if self._data_hydrator_factory is not None:
            return self._data_hydrator_factory(
                plate_service=self.service,
                plate_repo=self.task_repo,
            )
        return SpiDataHydrator(
            plate_service=self.service,
            plate_repo=self.task_repo,
        )
