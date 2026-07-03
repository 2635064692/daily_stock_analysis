# -*- coding: utf-8 -*-
"""SPI plate service — business orchestration for Shenwan industry index SPI computation."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from src.services.spi.akshare_sw_adapter import AkshareSwAdapter
from src.services.spi.spi_calculator import cal_index_spi, cal_stock_spi_v2
from src.services.spi.spi_time import spi_time
from src.repositories.plate_spi_repo import PlateSpiRepository

logger = logging.getLogger(__name__)
MIN_SPI_HISTORY = 233


class PlateSpiService:

    def __init__(self, adapter=None, repo=None, max_workers=None):
        self.adapter = adapter or AkshareSwAdapter()
        self.repo = repo or PlateSpiRepository()
        self.max_workers = max_workers or min(32, (os.cpu_count() or 1) * 4)

    # ------------------------------------------------------------------
    # single board
    # ------------------------------------------------------------------

    def compute_board_spi(self, board_id_str: str, anchor_date: date,
                          board_name: str = "") -> tuple:
        """Returns (board_id_int, spi, board_name, coverage)."""
        board_id_int = int(board_id_str)
        klines = self.adapter.get_index_kline(
            board_id_str,
            back_count=300,
            end_date=anchor_date,
        )
        if not klines:
            return (board_id_int, -1, board_name, 0.0)

        closes = [k["close"] for k in klines]
        spi = cal_index_spi(closes)
        coverage = 1.0 if len(closes) >= MIN_SPI_HISTORY else len(closes) / MIN_SPI_HISTORY
        return (board_id_int, spi, board_name, coverage)

    # ------------------------------------------------------------------
    # full refresh
    # ------------------------------------------------------------------

    def refresh_all(self, anchor_date: date | None = None) -> dict:
        anchor_date = anchor_date or spi_time()
        levels = self.adapter.get_sw_first_levels()
        if not levels:
            return {"anchor_date": str(anchor_date), "total": 0,
                    "success": 0, "low_confidence": 0}

        name_map = {item["board_id"]: item["board_name"] for item in levels}

        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self.compute_board_spi,
                    item["board_id"], anchor_date,
                    name_map.get(item["board_id"], ""),
                ): item["board_id"]
                for item in levels
            }
            for future in as_completed(futures):
                bid = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        results.append(result)
                except Exception:
                    logger.warning("board %s compute failed", bid, exc_info=True)

        success = 0
        low_confidence = 0
        for bid_int, spi, board_name, coverage in results:
            if spi == -1:
                continue
            confidence = "low" if coverage < 1.0 else "normal"
            self.repo.upsert_snapshot(
                board_id=bid_int,
                trade_date=anchor_date,
                spi=spi,
                confidence=confidence,
                coverage=coverage,
                board_name=board_name,
            )
            success += 1
            if confidence == "low":
                low_confidence += 1

        return {
            "anchor_date": str(anchor_date),
            "total": len(levels),
            "success": success,
            "low_confidence": low_confidence,
        }

    # ------------------------------------------------------------------
    # v2 single board
    # ------------------------------------------------------------------

    def compute_board_spi_v2(self, board_id_str: str, anchor_date: date) -> tuple:
        """Returns (board_id_int, v2_score_or_None)."""
        board_id_int = int(board_id_str)
        try:
            klines = self.adapter.get_index_kline(
                board_id_str,
                back_count=300,
                end_date=anchor_date,
            )
            if not klines:
                return (board_id_int, None)
            closes = [k["close"] for k in klines]
            if len(closes) < 5:
                return (board_id_int, None)
            score = cal_stock_spi_v2(closes)
            self.repo.upsert_v2_score(
                board_id=board_id_int,
                trade_date=anchor_date,
                v2_score=score,
            )
            return (board_id_int, score)
        except Exception:
            logger.warning("board %s v2 compute failed", board_id_str, exc_info=True)
            return (board_id_int, None)

    # ------------------------------------------------------------------
    # v2 full refresh
    # ------------------------------------------------------------------

    def refresh_all_v2(self, anchor_date: date | None = None) -> dict:
        anchor_date = anchor_date or spi_time()
        boards = self.adapter.get_sw_first_levels()
        if not boards:
            return {"success": 0, "total": 0}

        success = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.compute_board_spi_v2, b["board_id"], anchor_date): b["board_id"]
                for b in boards
            }
            for future in as_completed(futures):
                bid = futures[future]
                try:
                    _, v2_score = future.result()
                    if v2_score is not None:
                        success += 1
                except Exception:
                    logger.warning("board %s v2 future failed", bid, exc_info=True)

        return {"success": success, "total": len(boards)}
