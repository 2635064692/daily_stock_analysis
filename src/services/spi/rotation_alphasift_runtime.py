# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.services.pricing_service import PricingService
from src.services.run_diagnostics import record_custom_flow_event
from src.services.spi.rotation_daily_history_hydrator import RotationDailyHistoryHydrator
from src.services.spi.plate_spi_service import PlateSpiService
from src.services.spi.rotation_service import RotationService, RotationStrategyConfig, load_rotation_strategy_config

logger = logging.getLogger(__name__)

_PER_BOARD_CANDIDATE_LIMIT = 5
_SNAPSHOT_SOURCE = "spi_v2:realtime_rotation_runtime"


class AlphaSiftSectorRotationRuntime:
    """Realtime sector-rotation orchestration for AlphaSift task execution."""

    def __init__(
        self,
        *,
        plate_service: Optional[PlateSpiService] = None,
        plate_repo: Optional[PlateSpiRepository] = None,
        rotation_service: Optional[RotationService] = None,
        pricing_service: Optional[PricingService] = None,
        daily_history_hydrator: Optional[RotationDailyHistoryHydrator] = None,
        strategy_config: Optional[RotationStrategyConfig] = None,
    ) -> None:
        self._plate_service = plate_service or PlateSpiService()
        self._plate_repo = plate_repo or PlateSpiRepository()
        self._strategy_config = strategy_config or load_rotation_strategy_config()
        self._rotation_service = rotation_service or RotationService(
            repo=self._plate_repo,
            strategy_config=self._strategy_config,
        )
        self._pricing_service = pricing_service or PricingService()
        self._daily_history_hydrator = daily_history_hydrator or RotationDailyHistoryHydrator()

    def run(self, *, trade_date: date, max_results: int) -> Dict[str, Any]:
        warnings: List[str] = []
        candidates: List[Dict[str, Any]] = []

        boards = self._load_watchpool(trade_date=trade_date)
        if not boards:
            warnings.append("无可用v2板块")
            self._emit_terminal_stage(
                node_id="rotation_candidates",
                title="输出候选股票",
                status="skipped",
                message="未找到可用 v2 板块，跳过候选输出",
                metadata={
                    "tradeDate": str(trade_date),
                    "rotationBoards": 0,
                    "candidateCount": 0,
                    "maxResults": max_results,
                    "sortKeys": ["board_v2_score", "total"],
                },
            )
            return {
                "candidates": [],
                "rotation_boards": 0,
                "snapshot_source": _SNAPSHOT_SOURCE,
                "warnings": warnings,
            }

        boards_with_buy = 0
        for board in boards:
            board_id = int(board["board_id"])
            board_name = str(board.get("board_name") or board_id)
            board_v2_score = board.get("v2_score")

            try:
                resolved = self._resolve_constituents(board_id=board_id, board_name=board_name, trade_date=trade_date)
            except Exception as exc:
                warnings.append(f"{board_name}: constituent_resolve_failed: {exc}")
                logger.warning("AlphaSift rotation constituents failed board=%s date=%s", board_id, trade_date, exc_info=True)
                continue

            codes = resolved["codes"]
            if not codes:
                warnings.append(f"{board_name}: constituents_missing")
                continue

            self._hydrate_daily_history(
                board_id=board_id,
                board_name=board_name,
                trade_date=trade_date,
                stock_codes=codes,
                warnings=warnings,
            )
            entry_results = self._scan_buy_signals(
                board_id=board_id,
                board_name=board_name,
                trade_date=trade_date,
                stock_codes=codes,
            )
            buy_matches = [item for item in entry_results if item.get("matched")]
            buy_codes = [str(item["stock_code"]) for item in buy_matches]
            if not buy_codes:
                continue

            boards_with_buy += 1
            board_candidates = self._price_buy_candidates(
                board_id=board_id,
                board_name=board_name,
                board_v2_score=board_v2_score,
                trade_date=trade_date,
                buy_matches=buy_matches,
                warnings=warnings,
            )
            candidates.extend(board_candidates)

        candidates.sort(
            key=lambda item: (
                float(item.get("board_v2_score") or 0.0),
                float(item.get("total")) if item.get("total") is not None else -1.0,
            ),
            reverse=True,
        )
        selected = candidates[:max_results]

        self._emit_terminal_stage(
            node_id="rotation_candidates",
            title="输出候选股票",
            status="success" if selected else ("skipped" if boards_with_buy == 0 else "degraded"),
            message=f"已输出 {len(selected)} 条候选股票",
            metadata={
                "tradeDate": str(trade_date),
                "rotationBoards": boards_with_buy,
                "candidateCount": len(selected),
                "maxResults": max_results,
                "sortKeys": ["board_v2_score", "total"],
                "candidateCodesPreview": [str(item["code"]) for item in selected[:5]],
            },
        )

        return {
            "candidates": selected,
            "rotation_boards": boards_with_buy,
            "snapshot_source": _SNAPSHOT_SOURCE,
            "warnings": warnings,
        }

    def _load_watchpool(self, *, trade_date: date) -> List[Dict[str, Any]]:
        start = time.perf_counter()
        self._emit_started_stage(
            node_id="rotation_watchpool",
            title="Top v2 板块池",
            lane="analysis",
            kind="analysis",
            message="正在计算并加载 top v2 板块池",
            metadata={
                "tradeDate": str(trade_date),
                "topN": self._strategy_config.watchpool_top_n,
            },
        )
        try:
            boards = self._plate_repo.find_top_boards_v2(
                anchor_date=trade_date,
                top_n=self._strategy_config.watchpool_top_n,
            )
            source = "snapshot"
            if len(boards) < self._strategy_config.watchpool_top_n:
                # v2 scores are backfilled onto existing snapshot rows (UPDATE),
                # so the day's base rows must exist first. refresh_all() creates
                # them; refresh_all_v2() only fills v2_score onto them.
                try:
                    self._plate_service.refresh_all(anchor_date=trade_date)
                except Exception:
                    logger.warning(
                        "rotation snapshot creation failed date=%s",
                        trade_date,
                        exc_info=True,
                    )
                self._plate_service.refresh_all_v2(anchor_date=trade_date)
                boards = self._plate_repo.find_top_boards_v2(
                    anchor_date=trade_date,
                    top_n=self._strategy_config.watchpool_top_n,
                )
                source = "realtime_refresh"
        except Exception as exc:
            self._emit_terminal_stage(
                node_id="rotation_watchpool",
                title="Top v2 板块池",
                status="failed",
                message=f"Top v2 板块池计算失败：{exc}",
                duration_ms=_duration_ms(start),
                metadata={
                    "tradeDate": str(trade_date),
                    "topN": self._strategy_config.watchpool_top_n,
                },
            )
            raise

        self._emit_terminal_stage(
            node_id="rotation_watchpool",
            title="Top v2 板块池",
            status="success" if boards else "skipped",
            message=f"已加载 {len(boards)} 个 v2 板块",
            duration_ms=_duration_ms(start),
            metadata={
                "tradeDate": str(trade_date),
                "topN": self._strategy_config.watchpool_top_n,
                "boardCount": len(boards),
                "boardIds": [int(item["board_id"]) for item in boards[:8]],
                "source": source,
            },
        )
        return boards

    def _resolve_constituents(self, *, board_id: int, board_name: str, trade_date: date) -> Dict[str, Any]:
        node_id = f"rotation_constituents_{board_id}"
        start = time.perf_counter()
        self._emit_started_stage(
            node_id=node_id,
            title=f"解析成分股 · {board_name}",
            lane="data_source",
            kind="data_source",
            message=f"正在解析 {board_name} 成分股",
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
            },
            provider="constituent_resolver",
        )
        resolved = self._rotation_service.resolve_constituents(board_id, trade_date)
        snapshot = resolved.get("snapshot")
        codes = resolved.get("codes") or []
        source = resolved.get("source") or "missing"
        status = "success" if codes else "skipped"
        if snapshot is not None and getattr(snapshot, "is_stale", False):
            status = "degraded" if codes else "skipped"
        self._emit_terminal_stage(
            node_id=node_id,
            title=f"解析成分股 · {board_name}",
            status=status,
            message=f"{board_name} 解析到 {len(codes)} 个成分股",
            duration_ms=_duration_ms(start),
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
                "constituentCount": len(codes),
                "source": source,
                "snapshotAgeDays": getattr(snapshot, "snapshot_age_days", None),
                "originTradeDate": getattr(snapshot, "origin_trade_date", None),
            },
            provider="constituent_resolver",
        )
        return {
            "codes": [str(code) for code in codes],
            "source": str(source),
            "snapshot": snapshot,
        }

    def _hydrate_daily_history(
        self,
        *,
        board_id: int,
        board_name: str,
        trade_date: date,
        stock_codes: List[str],
        warnings: List[str],
    ) -> None:
        if not self._strategy_config.daily_history_enabled:
            return

        node_id = f"rotation_daily_history_{board_id}"
        start = time.perf_counter()
        required_history_days = self._strategy_config.required_daily_history_days()
        self._emit_started_stage(
            node_id=node_id,
            title=f"日线补齐 · {board_name}",
            lane="data_source",
            kind="data_source",
            message=f"正在为 {board_name} 补齐缺失日线",
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
                "codeCount": len(stock_codes),
                "requiredHistoryDays": required_history_days,
                "maxWorkers": self._strategy_config.daily_history_max_workers,
            },
            provider="daily_history",
        )
        summary = self._daily_history_hydrator.hydrate_codes(
            trade_date=trade_date,
            stock_codes=stock_codes,
            required_history_days=required_history_days,
            max_workers=self._strategy_config.daily_history_max_workers,
        )
        status = self._daily_history_status(summary.failed_count, summary.requested_count)
        if summary.failed_count:
            warnings.append(
                self._build_daily_history_warning(
                    board_name=board_name,
                    errors=summary.errors,
                    failed_count=summary.failed_count,
                    requested_count=summary.requested_count,
                )
            )
        self._emit_terminal_stage(
            node_id=node_id,
            title=f"日线补齐 · {board_name}",
            status=status,
            message=self._build_daily_history_message(
                board_name=board_name,
                requested_count=summary.requested_count,
                hydrated_count=summary.hydrated_count,
                satisfied_count=summary.satisfied_count,
                failed_count=summary.failed_count,
            ),
            duration_ms=_duration_ms(start),
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
                "codeCount": summary.total_codes,
                "satisfiedCount": summary.satisfied_count,
                "requestedCount": summary.requested_count,
                "hydratedCount": summary.hydrated_count,
                "failedCount": summary.failed_count,
                "maxWorkers": summary.max_workers,
                "failedCodesPreview": summary.errors[:5],
            },
            provider="daily_history",
        )

    @staticmethod
    def _daily_history_status(failed_count: int, requested_count: int) -> str:
        if requested_count == 0:
            return "skipped"
        if failed_count == 0:
            return "success"
        if failed_count < requested_count:
            return "degraded"
        return "failed"

    @staticmethod
    def _build_daily_history_message(
        *,
        board_name: str,
        requested_count: int,
        hydrated_count: int,
        satisfied_count: int,
        failed_count: int,
    ) -> str:
        if requested_count == 0:
            return f"{board_name} 成分股日线已齐备，无需补齐"
        return (
            f"{board_name} 日线补齐完成：命中缓存 {satisfied_count} 只，"
            f"新补齐 {hydrated_count} 只，失败 {failed_count} 只"
        )

    @staticmethod
    def _build_daily_history_warning(
        *,
        board_name: str,
        errors: List[str],
        failed_count: int,
        requested_count: int,
    ) -> str:
        preview = ", ".join(errors[:3]) if errors else "unknown"
        return (
            f"{board_name}: daily_history_partial: "
            f"{failed_count}/{requested_count} failed ({preview})"
        )

    def _scan_buy_signals(
        self,
        *,
        board_id: int,
        board_name: str,
        trade_date: date,
        stock_codes: List[str],
    ) -> List[Dict[str, Any]]:
        node_id = f"rotation_entry_{board_id}"
        start = time.perf_counter()
        self._emit_started_stage(
            node_id=node_id,
            title=f"回踩 BUY 扫描 · {board_name}",
            lane="analysis",
            kind="analysis",
            message=f"正在扫描 {board_name} 的回踩 BUY 信号",
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
                "scannedCount": len(stock_codes),
                "emaPeriod": self._strategy_config.entry_ema_period,
                "volumeRatioThreshold": self._strategy_config.volume_ratio_threshold,
                "pullbackTolerance": self._strategy_config.pullback_tolerance,
            },
        )
        results = self._rotation_service.scan_entry_signals(
            board_id=board_id,
            trade_date=trade_date,
            entry_ema_period=self._strategy_config.entry_ema_period,
            volume_ratio_threshold=self._strategy_config.volume_ratio_threshold,
            pullback_tolerance=self._strategy_config.pullback_tolerance,
            stock_codes=stock_codes,
            persist=True,
        )
        buy_codes = [str(item["stock_code"]) for item in results if item.get("matched")]
        self._emit_terminal_stage(
            node_id=node_id,
            title=f"回踩 BUY 扫描 · {board_name}",
            status="success" if buy_codes else "skipped",
            message=f"{board_name} 命中 {len(buy_codes)} 个 BUY 股票",
            duration_ms=_duration_ms(start),
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
                "scannedCount": len(stock_codes),
                "buyCount": len(buy_codes),
                "emaPeriod": self._strategy_config.entry_ema_period,
                "volumeRatioThreshold": self._strategy_config.volume_ratio_threshold,
                "pullbackTolerance": self._strategy_config.pullback_tolerance,
                "buyCodesPreview": buy_codes[:5],
            },
        )
        return results

    def _price_buy_candidates(
        self,
        *,
        board_id: int,
        board_name: str,
        board_v2_score: Any,
        trade_date: date,
        buy_matches: List[Dict[str, Any]],
        warnings: List[str],
    ) -> List[Dict[str, Any]]:
        node_id = f"rotation_pricing_{board_id}"
        start = time.perf_counter()
        buy_codes = [str(item["stock_code"]) for item in buy_matches]
        self._emit_started_stage(
            node_id=node_id,
            title=f"板块内比价 · {board_name}",
            lane="analysis",
            kind="analysis",
            message=f"正在对 {board_name} 的 BUY 股票做板块内比价",
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
                "buyCount": len(buy_codes),
                "buyCodesPreview": buy_codes[:5],
            },
        )
        try:
            result = self._pricing_service.price_board(
                board_id=board_id,
                trade_date=trade_date,
                codes=buy_codes,
            )
        except Exception as exc:
            warnings.append(f"{board_name}: pricing_failed: {exc}")
            logger.warning("AlphaSift rotation pricing failed board=%s date=%s", board_id, trade_date, exc_info=True)
            self._emit_terminal_stage(
                node_id=node_id,
                title=f"板块内比价 · {board_name}",
                status="failed",
                message=f"{board_name} 板块内比价失败：{exc}",
                duration_ms=_duration_ms(start),
                metadata={
                    "tradeDate": str(trade_date),
                    "boardId": board_id,
                    "boardName": board_name,
                    "buyCount": len(buy_codes),
                },
            )
            return [
                self._missing_pricing_candidate(
                    board_id=board_id,
                    board_name=board_name,
                    board_v2_score=board_v2_score,
                    stock_code=str(item["stock_code"]),
                )
                for item in buy_matches
            ]

        stocks = result.get("stocks") or []
        # 现在 stocks 已经只包含 buy_codes，直接排序
        stocks.sort(
            key=lambda item: float(item.get("total")) if item.get("total") is not None else -1.0,
            reverse=True,
        )

        board_candidates: List[Dict[str, Any]] = []
        for rank_idx, priced in enumerate(stocks[:_PER_BOARD_CANDIDATE_LIMIT], start=1):
            stock_code = str(priced["stock_code"])
            board_candidates.append(
                {
                    "code": stock_code,
                    "board_id": board_id,
                    "board_name": board_name,
                    "board_v2_score": board_v2_score,
                    "pricing_rank": rank_idx,
                    "total": priced.get("total"),
                    "sp_ratio": priced.get("sp_ratio"),
                    "sp_score": priced.get("sp_score"),
                    "cmf": priced.get("cmf"),
                    "flow_score": priced.get("flow_score"),
                    "status": priced.get("status"),
                    "factor_mask": priced.get("factor_mask"),
                    "selection_reason": "板块轮动BUY信号",
                }
            )

        priced_codes = {str(item["code"]) for item in board_candidates}
        for item in buy_matches:
            stock_code = str(item["stock_code"])
            if stock_code in priced_codes:
                continue
            board_candidates.append(
                self._missing_pricing_candidate(
                    board_id=board_id,
                    board_name=board_name,
                    board_v2_score=board_v2_score,
                    stock_code=stock_code,
                )
            )

        degraded = any(candidate.get("status") not in {"ok", None} for candidate in board_candidates)
        self._emit_terminal_stage(
            node_id=node_id,
            title=f"板块内比价 · {board_name}",
            status="degraded" if degraded else "success",
            message=f"{board_name} 已完成 {len(board_candidates)} 只 BUY 股票的比价整理",
            duration_ms=_duration_ms(start),
            metadata={
                "tradeDate": str(trade_date),
                "boardId": board_id,
                "boardName": board_name,
                "buyCount": len(buy_codes),
                "pricedCount": int(result.get("priced_count") or 0),
                "runId": result.get("run_id"),
                "flowCoverage": result.get("flow_coverage"),
                "status": result.get("status"),
                "candidateCodesPreview": [str(item["code"]) for item in board_candidates[:5]],
            },
        )
        return board_candidates

    @staticmethod
    def _missing_pricing_candidate(
        *,
        board_id: int,
        board_name: str,
        board_v2_score: Any,
        stock_code: str,
    ) -> Dict[str, Any]:
        return {
            "code": stock_code,
            "board_id": board_id,
            "board_name": board_name,
            "board_v2_score": board_v2_score,
            "pricing_rank": None,
            "total": None,
            "sp_ratio": None,
            "sp_score": None,
            "cmf": None,
            "flow_score": None,
            "status": "missing_pricing",
            "factor_mask": None,
            "selection_reason": "板块轮动BUY信号",
        }

    def _emit_started_stage(
        self,
        *,
        node_id: str,
        title: str,
        lane: str,
        kind: str,
        message: str,
        metadata: Dict[str, Any],
        provider: Optional[str] = None,
    ) -> None:
        record_custom_flow_event(
            event_type=f"{node_id}_started",
            node_id=node_id,
            title=title,
            severity="info",
            message=message,
            metadata=metadata,
            node={
                "id": node_id,
                "lane": lane,
                "kind": kind,
                "label": title,
                "status": "running",
                "provider": provider,
                "started_at": _utc_now_iso(),
                "message": message,
            },
        )

    def _emit_terminal_stage(
        self,
        *,
        node_id: str,
        title: str,
        status: str,
        message: str,
        metadata: Dict[str, Any],
        duration_ms: Optional[int] = None,
        provider: Optional[str] = None,
    ) -> None:
        severity = _severity_for_status(status)
        record_custom_flow_event(
            event_type=f"{node_id}_completed",
            node_id=node_id,
            title=title,
            severity=severity,
            message=message,
            metadata=metadata,
            node={
                "id": node_id,
                "lane": _lane_for_node(node_id),
                "kind": _kind_for_node(node_id),
                "label": title,
                "status": status,
                "provider": provider,
                "ended_at": _utc_now_iso(),
                "duration_ms": duration_ms,
                "message": message,
            },
        )


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _duration_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def _severity_for_status(status: str) -> str:
    if status == "success":
        return "success"
    if status in {"degraded", "fallback", "skipped"}:
        return "warning"
    if status in {"failed", "timeout"}:
        return "danger"
    return "info"


def _lane_for_node(node_id: str) -> str:
    if node_id.startswith(("rotation_constituents_", "rotation_daily_history_")):
        return "data_source"
    if node_id in {"rotation_candidates", "rotation_dsa_enrich"}:
        return "artifact"
    return "analysis"


def _kind_for_node(node_id: str) -> str:
    if node_id.startswith(("rotation_constituents_", "rotation_daily_history_")):
        return "data_source"
    if node_id in {"rotation_candidates", "rotation_dsa_enrich"}:
        return "artifact"
    return "analysis"
