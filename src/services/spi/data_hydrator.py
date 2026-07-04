# -*- coding: utf-8 -*-
"""Synchronous SPI data hydration for local readiness checks and repair."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Dict, Optional

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.repositories.pricing_repo import PricingRepository
from src.services.pricing_service import PricingService
from src.services.spi.plate_spi_service import PlateSpiService
from src.services.spi.rotation_service import RotationService
from src.services.spi.spi_time import spi_time

logger = logging.getLogger(__name__)

_HISTORICAL_SKIP_REASON = "historical_anchor_requires_point_in_time_constituents"


class SpiDataHydrator:
    """Hydrate SPI daily chain data and report readiness for sector rotation flows."""

    def __init__(
        self,
        *,
        plate_service: Optional[PlateSpiService] = None,
        plate_repo: Optional[PlateSpiRepository] = None,
        pricing_repo: Optional[PricingRepository] = None,
        rotation_factory: Optional[Callable[[], RotationService]] = None,
        pricing_factory: Optional[Callable[[], PricingService]] = None,
    ) -> None:
        self._plate_service = plate_service or PlateSpiService()
        self._plate_repo = plate_repo or PlateSpiRepository()
        self._pricing_repo = pricing_repo or PricingRepository()
        self._rotation_factory = rotation_factory or RotationService
        self._pricing_factory = pricing_factory or PricingService

    def hydrate_trade_date(
        self,
        trade_date: date,
        *,
        pricing_top_n: int = 30,
        allow_historical_derived_data: bool = False,
    ) -> Dict[str, Any]:
        current_anchor = spi_time()
        v1_stage = self._run_stage(
            "v1_refresh",
            lambda: self._plate_service.refresh_all(anchor_date=trade_date),
        )
        v2_stage = self._run_stage(
            "v2_refresh",
            lambda: self._plate_service.refresh_all_v2(anchor_date=trade_date),
        )
        if trade_date != current_anchor and not allow_historical_derived_data:
            rotation_stage = self._skipped_stage(_HISTORICAL_SKIP_REASON)
            pricing_stage = self._skipped_stage(_HISTORICAL_SKIP_REASON)
        else:
            rotation_service = self._rotation_factory()
            pricing_service = self._pricing_factory()
            rotation_stage = self._run_stage(
                "rotation_refresh",
                lambda: rotation_service.generate_signals(trade_date),
            )
            pricing_stage = self._refresh_pricing_stage(
                trade_date=trade_date,
                pricing_service=pricing_service,
                pricing_top_n=pricing_top_n,
            )

        readiness = self.readiness_summary(trade_date)
        readiness["historical_restriction_applied"] = (
            trade_date != current_anchor and not allow_historical_derived_data
        )
        return {
            "trade_date": trade_date.isoformat(),
            "effective_trade_date": current_anchor.isoformat(),
            "stages": {
                "v1": v1_stage,
                "v2": v2_stage,
                "rotation": rotation_stage,
                "pricing": pricing_stage,
            },
            "readiness": readiness,
        }

    def readiness_summary(self, trade_date: date) -> Dict[str, Any]:
        v1_count = self._plate_repo.count_snapshots(trade_date=trade_date)
        v2_count = self._plate_repo.count_snapshots(trade_date=trade_date, require_v2=True)
        buy_signal_count = self._plate_repo.count_rotation_signals(
            trade_date=trade_date,
            action="BUY",
        )
        pricing_count = self._pricing_repo.count_snapshots(trade_date=trade_date)
        return {
            "trade_date": trade_date.isoformat(),
            "v1_snapshot_count": v1_count,
            "v2_ready_count": v2_count,
            "buy_signal_count": buy_signal_count,
            "pricing_snapshot_count": pricing_count,
            "sector_rotation_smoke_ready": v2_count > 0,
            "sector_rotation_candidate_ready": (
                v2_count > 0 and buy_signal_count > 0 and pricing_count > 0
            ),
        }

    def _refresh_pricing_stage(
        self,
        *,
        trade_date: date,
        pricing_service: PricingService,
        pricing_top_n: int,
    ) -> Dict[str, Any]:
        top_boards = self._plate_repo.find_top_boards_v2(
            anchor_date=trade_date,
            top_n=pricing_top_n,
        )
        if not top_boards:
            return {
                "status": "empty",
                "result": {
                    "board_count": 0,
                    "priced_count": 0,
                    "degraded_count": 0,
                    "failures": [],
                },
            }

        priced_count = 0
        degraded_count = 0
        failures = []
        for board in top_boards:
            board_id = int(board["board_id"])
            try:
                result = pricing_service.price_board(board_id, trade_date)
                priced_count += int(result.get("priced_count") or 0)
                degraded_count += int(result.get("degraded_count") or 0)
            except Exception as exc:
                logger.warning("pricing hydration failed board=%s date=%s: %s", board_id, trade_date, exc)
                failures.append({"board_id": board_id, "error": str(exc)})
        status = "ok" if not failures else ("partial" if priced_count > 0 else "error")
        return {
            "status": status,
            "result": {
                "board_count": len(top_boards),
                "priced_count": priced_count,
                "degraded_count": degraded_count,
                "failures": failures,
            },
        }

    def _run_stage(self, stage_name: str, operation: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        try:
            result = operation()
        except Exception as exc:
            logger.warning("spi hydrate stage failed: %s", stage_name, exc, exc_info=True)
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "result": result}

    @staticmethod
    def _skipped_stage(reason: str) -> Dict[str, Any]:
        return {"status": "skipped", "reason": reason}
