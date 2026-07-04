# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

for _mod in ("dotenv", "fake_useragent"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

from src.services.spi.spi_task_runner import SPI_TASK_STOCK_CODE, SpiTaskRunner
from src.services.task_queue import TaskInfo, TaskStatus


class ImmediateQueue:
    def __init__(self):
        self.progress_updates = []
        self.submissions = []

    def list_pending_tasks(self):
        return []

    def submit_background_task(self, run_task, **kwargs):
        self.submissions.append(kwargs)
        run_task()
        return SimpleNamespace(task_id=kwargs.get("task_id", "task-1"))

    def update_task_progress(self, task_id, progress, message):
        self.progress_updates.append((task_id, progress, message))


def test_backfill_history_uses_trading_dates_and_persists_metadata():
    queue = ImmediateQueue()
    service = MagicMock()
    service.refresh_all.side_effect = [
        {"success": 31, "total": 31},
        {"success": 29, "total": 31},
    ]
    task_repo = MagicMock()
    runner = SpiTaskRunner(queue=queue, service=service, task_repo=task_repo)

    with patch(
        "src.services.spi.spi_task_runner.iter_trading_dates",
        return_value=[date(2026, 7, 3), date(2026, 7, 7)],
    ):
        task_id = runner.backfill_history(date(2026, 7, 3), date(2026, 7, 6))

    assert service.refresh_all.call_args_list == [
        call(anchor_date=date(2026, 7, 3)),
        call(anchor_date=date(2026, 7, 7)),
    ]
    task_repo.create_backfill_task_run.assert_called_once_with(
        task_queue_id=task_id,
        start_date=date(2026, 7, 3),
        end_date=date(2026, 7, 6),
    )
    task_repo.update_backfill_task_board_count.assert_called_once_with(
        task_queue_id=task_id,
        board_count=31,
    )
    assert queue.progress_updates == [
        (task_id, 50, "2026-07-03 完成, 成功 31/31 板块"),
        (task_id, 100, "2026-07-07 完成, 成功 29/31 板块"),
    ]


def test_refresh_daily_reuses_existing_spi_task():
    active_task = TaskInfo(
        task_id="existing-task",
        stock_code=SPI_TASK_STOCK_CODE,
        status=TaskStatus.PROCESSING,
    )
    queue = MagicMock()
    queue.list_pending_tasks.return_value = [active_task]
    runner = SpiTaskRunner(queue=queue, service=MagicMock(), task_repo=MagicMock())

    task_id = runner.refresh_daily()

    assert task_id == "existing-task"
    queue.submit_background_task.assert_not_called()


def test_backfill_history_reuses_existing_spi_task_without_new_metadata():
    active_task = TaskInfo(
        task_id="existing-task",
        stock_code=SPI_TASK_STOCK_CODE,
        status=TaskStatus.PENDING,
    )
    queue = MagicMock()
    queue.list_pending_tasks.return_value = [active_task]
    task_repo = MagicMock()
    runner = SpiTaskRunner(queue=queue, service=MagicMock(), task_repo=task_repo)

    task_id = runner.backfill_history(date(2026, 7, 3), date(2026, 7, 6))

    assert task_id == "existing-task"
    task_repo.create_backfill_task_run.assert_not_called()
    queue.submit_background_task.assert_not_called()


def test_backfill_history_cleans_up_metadata_when_submit_fails():
    queue = MagicMock()
    queue.list_pending_tasks.return_value = []
    queue.submit_background_task.side_effect = RuntimeError("submit failed")
    task_repo = MagicMock()
    runner = SpiTaskRunner(queue=queue, service=MagicMock(), task_repo=task_repo)

    with patch(
        "src.services.spi.spi_task_runner.iter_trading_dates",
        return_value=[date(2026, 7, 3)],
    ), pytest.raises(RuntimeError, match="submit failed"):
        runner.backfill_history(date(2026, 7, 3), date(2026, 7, 3))

    task_repo.create_backfill_task_run.assert_called_once()
    task_repo.delete_backfill_task_run.assert_called_once()


def test_refresh_daily_v2_exception_does_not_affect_v1():
    queue = ImmediateQueue()
    service = MagicMock()
    service.refresh_all.return_value = {"anchor_date": "2026-07-03", "total": 31, "success": 31}
    service.refresh_all_v2.side_effect = RuntimeError("v2 exploded")
    runner = SpiTaskRunner(queue=queue, service=service, task_repo=MagicMock())

    with patch("src.services.spi.spi_task_runner.spi_time", return_value=date(2026, 7, 3)):
        task_id = runner.refresh_daily()

    assert task_id is not None
    service.refresh_all.assert_called_once_with(anchor_date=date(2026, 7, 3))


def test_refresh_daily_rotation_skipped_when_no_constituents():
    queue = ImmediateQueue()
    service = MagicMock()
    service.refresh_all.return_value = {"anchor_date": "2026-07-03", "total": 31, "success": 31}
    service.refresh_all_v2.return_value = {"success": 31, "total": 31}

    rotation_mock = MagicMock()

    runner = SpiTaskRunner(queue=queue, service=service, task_repo=MagicMock())

    with (
        patch("src.services.spi.spi_task_runner.spi_time", return_value=date(2026, 7, 3)),
        patch("src.services.spi.rotation_service.RotationService", return_value=rotation_mock),
    ):
        task_id = runner.refresh_daily()

    assert task_id is not None
    rotation_mock.generate_signals.assert_called_once_with(date(2026, 7, 3))


def test_refresh_daily_rotation_exception_does_not_affect_v1():
    queue = ImmediateQueue()
    service = MagicMock()
    service.refresh_all.return_value = {"anchor_date": "2026-07-03", "total": 31, "success": 31}
    service.refresh_all_v2.return_value = {"success": 31, "total": 31}

    rotation_mock = MagicMock()
    rotation_mock.generate_signals.side_effect = RuntimeError("rotation exploded")

    runner = SpiTaskRunner(queue=queue, service=service, task_repo=MagicMock())

    with (
        patch("src.services.spi.spi_task_runner.spi_time", return_value=date(2026, 7, 3)),
        patch("src.services.spi.rotation_service.RotationService", return_value=rotation_mock),
    ):
        task_id = runner.refresh_daily()

    assert task_id is not None
    service.refresh_all.assert_called_once_with(anchor_date=date(2026, 7, 3))


def test_refresh_daily_runs_pricing_for_top_boards():
    queue = ImmediateQueue()
    service = MagicMock()
    service.refresh_all.return_value = {"anchor_date": "2026-07-03", "total": 31, "success": 31}
    service.refresh_all_v2.return_value = {"success": 31, "total": 31}
    task_repo = MagicMock()
    task_repo.find_top_boards_v2.return_value = [
        {"board_id": 801010},
        {"board_id": 801020},
    ]
    rotation_mock = MagicMock()
    pricing_mock = MagicMock()
    pricing_mock.price_board.side_effect = [
        {"status": "ok", "priced_count": 3, "degraded_count": 1},
        {"status": "partial", "priced_count": 2, "degraded_count": 0},
    ]
    runner = SpiTaskRunner(queue=queue, service=service, task_repo=task_repo)

    with (
        patch("src.services.spi.spi_task_runner.spi_time", return_value=date(2026, 7, 3)),
        patch("src.services.spi.rotation_service.RotationService", return_value=rotation_mock),
        patch("src.services.pricing_service.PricingService", return_value=pricing_mock),
    ):
        task_id = runner.refresh_daily()

    assert task_id is not None
    task_repo.find_top_boards_v2.assert_called_once_with(anchor_date=date(2026, 7, 3), top_n=30)
    assert pricing_mock.price_board.call_args_list == [
        call(801010, date(2026, 7, 3)),
        call(801020, date(2026, 7, 3)),
    ]


def test_refresh_daily_pricing_board_exception_does_not_block_following_boards():
    queue = ImmediateQueue()
    service = MagicMock()
    service.refresh_all.return_value = {"anchor_date": "2026-07-03", "total": 31, "success": 31}
    service.refresh_all_v2.return_value = {"success": 31, "total": 31}
    task_repo = MagicMock()
    task_repo.find_top_boards_v2.return_value = [
        {"board_id": 801010},
        {"board_id": 801020},
    ]
    rotation_mock = MagicMock()
    pricing_mock = MagicMock()
    pricing_mock.price_board.side_effect = [
        RuntimeError("first board failed"),
        {"status": "ok", "priced_count": 2, "degraded_count": 0},
    ]
    runner = SpiTaskRunner(queue=queue, service=service, task_repo=task_repo)

    with (
        patch("src.services.spi.spi_task_runner.spi_time", return_value=date(2026, 7, 3)),
        patch("src.services.spi.rotation_service.RotationService", return_value=rotation_mock),
        patch("src.services.pricing_service.PricingService", return_value=pricing_mock),
    ):
        task_id = runner.refresh_daily()

    assert task_id is not None
    assert pricing_mock.price_board.call_args_list == [
        call(801010, date(2026, 7, 3)),
        call(801020, date(2026, 7, 3)),
    ]
