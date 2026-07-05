# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
import logging
from typing import List, Optional

from data_provider import DataFetcherManager

from src.repositories.stock_repo import StockRepository
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyHistoryHydrationSummary:
    total_codes: int
    satisfied_count: int
    requested_count: int
    hydrated_count: int
    failed_count: int
    max_workers: int
    errors: List[str]


@dataclass(frozen=True)
class _HydrationResult:
    code: str
    status: str
    source: Optional[str] = None
    error: Optional[str] = None


class RotationDailyHistoryHydrator:
    """Hydrate missing sector-rotation daily bars into ``stock_daily``."""

    def __init__(
        self,
        *,
        stock_repo: Optional[StockRepository] = None,
        db_manager: Optional[DatabaseManager] = None,
        fetcher_manager: Optional[DataFetcherManager] = None,
    ) -> None:
        self._stock_repo = stock_repo or StockRepository()
        self._db = db_manager or DatabaseManager.get_instance()
        self._fetcher_manager = fetcher_manager or DataFetcherManager()

    def hydrate_codes(
        self,
        *,
        trade_date: date,
        stock_codes: List[str],
        required_history_days: int,
        max_workers: int,
    ) -> DailyHistoryHydrationSummary:
        codes = self._normalize_codes(stock_codes)
        if not codes:
            return DailyHistoryHydrationSummary(
                total_codes=0,
                satisfied_count=0,
                requested_count=0,
                hydrated_count=0,
                failed_count=0,
                max_workers=0,
                errors=[],
            )

        required_days = max(int(required_history_days or 0), 1)
        codes_to_fetch = [
            code for code in codes
            if not self._has_sufficient_history(
                code=code,
                trade_date=trade_date,
                required_history_days=required_days,
            )
        ]
        satisfied_count = len(codes) - len(codes_to_fetch)
        if not codes_to_fetch:
            return DailyHistoryHydrationSummary(
                total_codes=len(codes),
                satisfied_count=satisfied_count,
                requested_count=0,
                hydrated_count=0,
                failed_count=0,
                max_workers=0,
                errors=[],
            )

        worker_count = max(1, min(int(max_workers or 1), len(codes_to_fetch)))
        results = self._fetch_missing_codes(
            codes=codes_to_fetch,
            required_history_days=required_days,
            max_workers=worker_count,
        )
        errors = [result.error for result in results if result.error]
        hydrated_count = sum(1 for result in results if result.status == "hydrated")
        failed_count = sum(1 for result in results if result.status == "failed")
        return DailyHistoryHydrationSummary(
            total_codes=len(codes),
            satisfied_count=satisfied_count,
            requested_count=len(codes_to_fetch),
            hydrated_count=hydrated_count,
            failed_count=failed_count,
            max_workers=worker_count,
            errors=errors,
        )

    def _fetch_missing_codes(
        self,
        *,
        codes: List[str],
        required_history_days: int,
        max_workers: int,
    ) -> List[_HydrationResult]:
        if max_workers <= 1 or len(codes) <= 1:
            return [
                self._fetch_and_store(code=code, required_history_days=required_history_days)
                for code in codes
            ]

        results: List[_HydrationResult] = []
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="rotation-daily",
        ) as executor:
            future_map = {
                executor.submit(
                    self._fetch_and_store,
                    code=code,
                    required_history_days=required_history_days,
                ): code
                for code in codes
            }
            for future in as_completed(future_map):
                code = future_map[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # pragma: no cover - defensive guard
                    logger.warning("rotation daily hydration crashed code=%s: %s", code, exc)
                    results.append(
                        _HydrationResult(
                            code=code,
                            status="failed",
                            error=f"{code}: {exc}",
                        )
                    )
        return results

    def _has_sufficient_history(
        self,
        *,
        code: str,
        trade_date: date,
        required_history_days: int,
    ) -> bool:
        start = trade_date - timedelta(days=int(required_history_days * 1.8) + 10)
        bars = self._stock_repo.get_range(code, start, trade_date)
        if not bars or len(bars) < required_history_days:
            return False
        latest_date = max((bar.date for bar in bars if getattr(bar, "date", None)), default=None)
        return latest_date is not None and latest_date >= trade_date

    def _fetch_and_store(
        self,
        *,
        code: str,
        required_history_days: int,
    ) -> _HydrationResult:
        try:
            df, source = self._fetcher_manager.get_daily_data(code, days=required_history_days)
            if df is None or df.empty:
                return _HydrationResult(
                    code=code,
                    status="failed",
                    error=f"{code}: empty_daily_data",
                )
            self._db.save_daily_data(df, code, source)
            return _HydrationResult(code=code, status="hydrated", source=source)
        except Exception as exc:
            logger.warning("rotation daily hydration failed code=%s: %s", code, exc)
            return _HydrationResult(
                code=code,
                status="failed",
                error=f"{code}: {exc}",
            )

    @staticmethod
    def _normalize_codes(stock_codes: List[str]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for stock_code in stock_codes:
            code = str(stock_code or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            normalized.append(code)
        return normalized
