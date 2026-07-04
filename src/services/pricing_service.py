# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import Counter
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from src.repositories.pricing_repo import PricingRepository
from src.services.pricing.cmf import calc_cmf, normalize_cmf
from src.services.pricing.capital_proxy import extract_flow, normalize_flow_scores
from src.services.pricing.relative_strength import calc_period_return, calc_rs_scores_nullable
from src.services.pricing.sp_ratio import calc_sp_ratio, is_eligible_for_sp, sp_ratio_to_score
from src.services.spi.spi_time import spi_time
from src.utils.constituents_snapshot import ConstituentFetcher, ConstituentSnapshotRepo

logger = logging.getLogger(__name__)

_RS_WINDOW = 20
_CMF_WINDOW = 20
_FLOW_COVERAGE_THRESHOLD = 0.6
_BASE_W_SP = 0.6
_BASE_W_CMF = 0.3
_BASE_W_FLOW = 0.1
_BASE_W_SP_NO_FLOW = 2.0 / 3.0
_BASE_W_CMF_NO_FLOW = 1.0 / 3.0


def _get_fetcher_manager():
    from data_provider import DataFetcherManager
    return DataFetcherManager()


def _fetch_bars(stock_code: str, trade_date: date, window: int):
    from src.repositories.stock_repo import StockRepository
    start = trade_date - timedelta(days=int(window * 1.8) + 10)
    return StockRepository().get_range(stock_code, start, trade_date)


def _extract_total_mv(quote_payload: Any) -> Optional[float]:
    if not quote_payload:
        return None
    raw_value = quote_payload.get("total_mv") if isinstance(quote_payload, dict) else getattr(quote_payload, "total_mv", None)
    try:
        return float(raw_value) if raw_value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_profit_context(fctx: Any) -> tuple[Optional[float], Optional[str]]:
    if not isinstance(fctx, dict):
        return None, None
    earnings = fctx.get("earnings") or {}
    if not isinstance(earnings, dict):
        return None, None
    nested_report = ((earnings.get("data") or {}).get("financial_report") or {})
    if not isinstance(nested_report, dict):
        nested_report = {}
    direct_report = earnings.get("financial_report") or {}
    if not isinstance(direct_report, dict):
        direct_report = {}
    financial_report = nested_report or direct_report
    profit = financial_report.get("net_profit_parent")
    report_date = financial_report.get("report_date")
    try:
        profit_value = float(profit) if profit is not None else None
    except (TypeError, ValueError):
        profit_value = None
    return profit_value, report_date


def _log_profit_report_dates(
    board_id: int,
    trade_date: date,
    report_dates: List[Optional[str]],
) -> None:
    valid_dates = [value for value in report_dates if value]
    if not valid_dates:
        return
    counts = Counter(valid_dates)
    dominant_date, dominant_count = counts.most_common(1)[0]
    logger.info(
        "pricing profit report dates board_id=%s trade_date=%s mixed=%s dominant=%s "
        "dominant_count=%s distinct=%s",
        board_id,
        trade_date,
        len(counts) > 1,
        dominant_date,
        dominant_count,
        sorted(counts.keys()),
    )


class PricingService:

    def __init__(
        self,
        repo: Optional[PricingRepository] = None,
        constituent_repo: Optional[ConstituentSnapshotRepo] = None,
        fetcher: Optional[ConstituentFetcher] = None,
    ):
        self._repo = repo or PricingRepository()
        self._constituent_repo = constituent_repo or ConstituentSnapshotRepo()
        self._fetcher = fetcher or ConstituentFetcher()

    def _get_constituents(
        self, board_id: int, trade_date: date
    ) -> tuple[List[str], str]:
        """Returns (stock_codes, constituent_source).

        Historical dates without a snapshot return ([], 'missing') to protect
        point-in-time semantics — no fallback to latest.
        """
        codes = self._constituent_repo.get_constituents(board_id, trade_date)
        if codes:
            return codes, "snapshot"

        if trade_date != spi_time():
            return [], "missing"

        fetched = self._fetcher.fetch(board_id)
        if not fetched:
            return [], "missing"

        self._constituent_repo.save_constituents(board_id, trade_date, fetched)
        return fetched, "current"

    def price_board(self, board_id: int, trade_date: date) -> Dict[str, Any]:
        """Compute intra-board pricing scores for all constituents on trade_date.

        Serial execution: one constituent at a time, 0.5 s sleep per legulegu
        capital-flow fetch (rate-limit compliance).
        """
        codes, constituent_source = self._get_constituents(board_id, trade_date)
        if not codes:
            run_id = self._repo.insert_factor_run(
                board_id=board_id,
                trade_date=trade_date,
                constituent_source="missing",
                rs_window=_RS_WINDOW,
                cmf_window=_CMF_WINDOW,
                base_weights={},
                effective_weights={},
                flow_coverage=None,
                constituent_count=0,
                priced_count=0,
                degraded_count=0,
                status="failed",
                error="no_constituents",
            )
            return {
                "board_id": board_id,
                "trade_date": str(trade_date),
                "status": "failed",
                "run_id": run_id,
                "stocks": [],
            }

        manager = _get_fetcher_manager()
        raw_rs: List[Optional[float]] = []
        raw_cmf: List[Optional[float]] = []
        raw_flow: List[Optional[float]] = []
        raw_mktcap: List[Optional[float]] = []
        raw_profit: List[Optional[float]] = []
        raw_profit_report_date: List[Optional[str]] = []

        for code in codes:
            bars = _fetch_bars(code, trade_date, _CMF_WINDOW)
            closes = [b.close for b in bars if b.close is not None]

            raw_rs.append(calc_period_return(closes, period=_RS_WINDOW))
            raw_cmf.append(calc_cmf(bars, period=_CMF_WINDOW))

            try:
                quote_payload = manager.get_realtime_quote(code)
                raw_mktcap.append(_extract_total_mv(quote_payload))
            except Exception:
                logger.debug("realtime quote fetch failed code=%s", code, exc_info=True)
                raw_mktcap.append(None)

            try:
                fctx = manager.get_fundamental_context(code)
                profit, report_date = _extract_profit_context(fctx)
                raw_profit.append(profit)
                raw_profit_report_date.append(report_date)
            except Exception:
                logger.debug("fundamental context fetch failed code=%s", code, exc_info=True)
                raw_profit.append(None)
                raw_profit_report_date.append(None)

            try:
                ctx = manager.get_capital_flow_context(code)
                stock_flow = (ctx.get("data") or {}).get("stock_flow") or {}
                raw_flow.append(extract_flow(stock_flow))
            except Exception:
                logger.debug("capital flow fetch failed code=%s", code, exc_info=True)
                raw_flow.append(None)

            time.sleep(0.5)

        rs_scores = calc_rs_scores_nullable(raw_rs)
        flow_scores = normalize_flow_scores(raw_flow)
        eligible_flags = [
            is_eligible_for_sp(mktcap, profit)
            for mktcap, profit in zip(raw_mktcap, raw_profit)
        ]
        board_total_mktcap = sum(
            float(mktcap)
            for mktcap, eligible in zip(raw_mktcap, eligible_flags)
            if eligible and mktcap is not None
        )
        board_total_profit = sum(
            float(profit)
            for profit, eligible in zip(raw_profit, eligible_flags)
            if eligible and profit is not None
        )
        _log_profit_report_dates(board_id, trade_date, raw_profit_report_date)

        valid_flow = sum(1 for v in raw_flow if v is not None)
        flow_coverage = valid_flow / len(codes)
        flow_enabled = flow_coverage >= _FLOW_COVERAGE_THRESHOLD

        if flow_enabled:
            base_w = {"sp": _BASE_W_SP, "cmf": _BASE_W_CMF, "flow": _BASE_W_FLOW}
            eff_w = {"sp": _BASE_W_SP, "cmf": _BASE_W_CMF, "flow": _BASE_W_FLOW}
        else:
            base_w = {"sp": _BASE_W_SP, "cmf": _BASE_W_CMF, "flow": _BASE_W_FLOW}
            eff_w = {"sp": _BASE_W_SP_NO_FLOW, "cmf": _BASE_W_CMF_NO_FLOW}

        stock_results: List[Dict[str, Any]] = []
        priced_count = 0
        degraded_count = 0

        for idx, code in enumerate(codes):
            rs_score = rs_scores[idx]
            sp_ratio = calc_sp_ratio(
                raw_mktcap[idx],
                board_total_mktcap,
                raw_profit[idx],
                board_total_profit,
            )
            sp_score = sp_ratio_to_score(sp_ratio)
            cmf_raw = raw_cmf[idx]
            cmf_score = normalize_cmf(cmf_raw)
            flow_score = flow_scores[idx] if flow_enabled else None
            single_flow_missing = flow_enabled and flow_score is None

            if sp_score is None or cmf_score is None:
                snap_status = "missing_core_factor"
                total = None
                active_factors: List[str] = []
            elif single_flow_missing:
                snap_status = "degraded"
                total = _BASE_W_SP_NO_FLOW * sp_score + _BASE_W_CMF_NO_FLOW * cmf_score
                active_factors = ["sp", "cmf"]
                degraded_count += 1
                priced_count += 1
            elif flow_enabled:
                snap_status = "ok"
                total = (
                    _BASE_W_SP * sp_score
                    + _BASE_W_CMF * cmf_score
                    + _BASE_W_FLOW * flow_score
                )
                active_factors = ["sp", "cmf", "flow"]
                priced_count += 1
            else:
                snap_status = "ok"
                total = _BASE_W_SP_NO_FLOW * sp_score + _BASE_W_CMF_NO_FLOW * cmf_score
                active_factors = ["sp", "cmf"]
                priced_count += 1

            stock_results.append({
                "stock_code": code,
                "status": snap_status,
                "rs_score": rs_score,
                "sp_ratio": sp_ratio,
                "sp_score": sp_score,
                "cmf": cmf_raw,
                "flow_score": flow_score,
                "total": total,
                "factor_mask": ",".join(active_factors) if active_factors else None,
            })

        if priced_count == 0:
            batch_status = "failed"
        elif all(r["status"] == "ok" for r in stock_results):
            batch_status = "ok"
        else:
            batch_status = "partial"

        run_id = self._repo.save_pricing_batch(
            board_id=board_id,
            trade_date=trade_date,
            constituent_source=constituent_source,
            rs_window=_RS_WINDOW,
            cmf_window=_CMF_WINDOW,
            base_weights=base_w,
            effective_weights=eff_w,
            flow_coverage=flow_coverage,
            constituent_count=len(codes),
            priced_count=priced_count,
            degraded_count=degraded_count,
            status=batch_status,
            error="all_constituents_missing_core_factor" if priced_count == 0 else None,
            snapshots=stock_results,
        )

        return {
            "board_id": board_id,
            "trade_date": str(trade_date),
            "status": batch_status,
            "run_id": run_id,
            "flow_coverage": flow_coverage,
            "flow_enabled": flow_enabled,
            "constituent_count": len(codes),
            "priced_count": priced_count,
            "degraded_count": degraded_count,
            "stocks": [
                {"stock_code": r["stock_code"], "status": r["status"], "total": r["total"]}
                for r in stock_results
            ],
        }
