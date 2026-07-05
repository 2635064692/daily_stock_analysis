# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from datetime import date
from typing import Callable, Optional, Sequence

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.services.spi.akshare_sw_adapter import AkshareSwAdapter
from src.services.spi.spi_time import spi_time
from src.utils.constituents_snapshot import (
    ConstituentFetcher,
    ConstituentSnapshotRepo,
    DEFAULT_REFETCH_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShenwanBoard:
    board_id: int
    board_name: str


class ShenwanBoardUniverseProvider:
    def __init__(self, adapter=None, plate_repo=None):
        self._adapter = adapter or AkshareSwAdapter()
        self._plate_repo = plate_repo or PlateSpiRepository()

    def list_boards(self, *, anchor_date: Optional[date] = None) -> list[ShenwanBoard]:
        boards = self._load_from_adapter()
        if boards:
            return boards
        return self._load_from_snapshot(anchor_date=anchor_date)

    def _load_from_adapter(self) -> list[ShenwanBoard]:
        try:
            rows = self._adapter.get_sw_first_levels()
        except Exception:
            logger.warning("failed to load Shenwan board universe from akshare", exc_info=True)
            return []
        return [
            ShenwanBoard(
                board_id=int(item["board_id"]),
                board_name=str(item.get("board_name") or ""),
            )
            for item in rows
        ]

    def _load_from_snapshot(self, *, anchor_date: Optional[date]) -> list[ShenwanBoard]:
        rows = self._plate_repo.find_board_universe(anchor_date=anchor_date)
        if rows:
            logger.warning(
                "using plate_spi_snapshot board universe fallback for constituent sync"
            )
        return [
            ShenwanBoard(
                board_id=int(item["board_id"]),
                board_name=str(item.get("board_name") or ""),
            )
            for item in rows
        ]


class ShenwanConstituentSyncService:
    def __init__(
        self,
        *,
        board_provider: Optional[ShenwanBoardUniverseProvider] = None,
        snapshot_repo: Optional[ConstituentSnapshotRepo] = None,
        fetcher: Optional[ConstituentFetcher] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._board_provider = board_provider or ShenwanBoardUniverseProvider()
        self._snapshot_repo = snapshot_repo or ConstituentSnapshotRepo()
        self._fetcher = fetcher or ConstituentFetcher()
        self._sleep_fn = sleep_fn
        self._monotonic_fn = monotonic_fn

    def sync_trade_date(
        self,
        trade_date: Optional[date] = None,
        *,
        board_ids: Optional[Sequence[int]] = None,
        interval_seconds: float = DEFAULT_REFETCH_INTERVAL_SECONDS,
        force: bool = False,
    ) -> dict:
        resolved_trade_date = trade_date or spi_time()
        boards = self._resolve_boards(
            trade_date=resolved_trade_date,
            board_ids=board_ids,
        )
        result = self._init_result(resolved_trade_date, boards, interval_seconds, force)

        for index, board in enumerate(boards):
            started_at = self._monotonic_fn()
            board_result = self._sync_single_board(board, resolved_trade_date, force=force)
            result["boards"].append(board_result)
            self._apply_counters(result, board_result)
            if self._should_wait(index=index, total=len(boards), board_result=board_result):
                self._wait_remaining_interval(started_at, interval_seconds)

        return result

    def _resolve_boards(
        self,
        *,
        trade_date: date,
        board_ids: Optional[Sequence[int]],
    ) -> list[ShenwanBoard]:
        boards = self._board_provider.list_boards(anchor_date=trade_date)
        if not board_ids:
            return boards
        selected = {int(board_id) for board_id in board_ids}
        return [board for board in boards if board.board_id in selected]

    @staticmethod
    def _init_result(
        trade_date: date,
        boards: Sequence[ShenwanBoard],
        interval_seconds: float,
        force: bool,
    ) -> dict:
        return {
            "trade_date": trade_date.isoformat(),
            "board_count": len(boards),
            "interval_seconds": interval_seconds,
            "force": force,
            "saved": 0,
            "skipped_existing": 0,
            "failed_fetch": 0,
            "boards": [],
        }

    @staticmethod
    def _apply_counters(result: dict, board_result: dict) -> None:
        status = board_result["status"]
        if status in ("saved", "skipped_existing", "failed_fetch"):
            result[status] += 1

    @staticmethod
    def _should_wait(*, index: int, total: int, board_result: dict) -> bool:
        return index < total - 1 and board_result.get("requested") is True

    def _sync_single_board(self, board: ShenwanBoard, trade_date: date, *, force: bool) -> dict:
        if not force:
            existing = self._snapshot_repo.get_snapshot_state(board.board_id, trade_date)
            if existing is not None:
                return {
                    "board_id": board.board_id,
                    "board_name": board.board_name,
                    "status": "skipped_existing",
                    "requested": False,
                    "constituent_count": len(existing.stock_codes),
                }

        stock_codes = self._fetcher.fetch(board.board_id)
        if not stock_codes:
            logger.warning(
                "constituent sync failed board_id=%s board_name=%s trade_date=%s",
                board.board_id,
                board.board_name,
                trade_date,
            )
            return {
                "board_id": board.board_id,
                "board_name": board.board_name,
                "status": "failed_fetch",
                "requested": True,
                "constituent_count": 0,
            }

        self._snapshot_repo.save_constituents(
            board.board_id,
            trade_date,
            stock_codes,
            origin_trade_date=trade_date,
            is_stale=False,
            snapshot_age_days=0,
        )
        return {
            "board_id": board.board_id,
            "board_name": board.board_name,
            "status": "saved",
            "requested": True,
            "constituent_count": len(stock_codes),
        }

    def _wait_remaining_interval(self, started_at: float, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            return
        elapsed = max(0.0, self._monotonic_fn() - started_at)
        remaining = interval_seconds - elapsed
        if remaining > 0:
            self._sleep_fn(remaining)
