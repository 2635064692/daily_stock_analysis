# -*- coding: utf-8 -*-
"""
AkShare fundamental adapter (fail-open).

This adapter intentionally uses capability probing against multiple AkShare
endpoint candidates. It should never raise to caller; partial data is allowed.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DIVIDEND_KEYWORD_MAP: Dict[str, List[str]] = {
    "per_share": [
        "每股派息",
        "每股现金红利",
        "每股分红",
        "每股派现",
        "派现(元/股)",
        "派息(元/股)",
        "税前派息(元/股)",
        "现金分红(税前)",
    ],
    "plan_text": [
        "分配方案",
        "分红方案",
        "实施方案",
        "派息方案",
        "方案",
        "预案",
        "方案说明",
    ],
    "ex_dividend_date": ["除权除息日", "除息日", "除权日", "除权除息", "除息日期"],
    "record_date": ["股权登记日", "登记日"],
    "announce_date": ["公告日期", "公告日", "实施公告日", "预案公告日"],
    "report_date": ["报告期", "报告日期", "截止日期", "统计截止日期"],
}


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float conversion."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    try:
        return parsed.to_pydatetime()
    except Exception:
        return None


def _normalize_code(raw: Any) -> str:
    s = _safe_str(raw).upper()
    if "." in s:
        s = s.split(".", 1)[0]
    s = re.sub(r"^(SH|SZ|BJ)", "", s)
    return s


def _pick_by_keywords(row: Any, keywords: List[str]) -> Optional[Any]:
    """
    Return first non-empty row value whose column name contains any keyword.

    Accepts both pd.Series (row from a DataFrame) and dict (metric maps).
    """
    keys = row.index if hasattr(row, "index") else row.keys()
    for col in keys:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            val = row.get(col) if hasattr(row, "get") else row[col]
            if val is not None and str(val).strip() not in ("", "-", "nan", "None"):
                return val
    return None


def _parse_dividend_plan_to_per_share(plan_text: str) -> Optional[float]:
    """Parse per-share cash dividend from Chinese plan text."""
    text = _safe_str(plan_text)
    if not text:
        return None

    for pattern in (
        r"(?:每)?\s*10\s*股?\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _safe_float(match.group(1))
            if parsed is not None and parsed > 0:
                return parsed / 10.0

    match_per_share = re.search(r"每\s*股\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", text)
    if match_per_share:
        parsed = _safe_float(match_per_share.group(1))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _extract_cash_dividend_per_share(row: pd.Series) -> Optional[float]:
    """Extract pre-tax cash dividend per share from a row."""
    plan_text = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["plan_text"]))
    # Keep pre-tax semantics; skip explicit after-tax plans unless pre-tax marker exists.
    if "税后" in plan_text and "税前" not in plan_text and "含税" not in plan_text:
        return None

    direct = _safe_float(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["per_share"]))
    if direct is not None and direct > 0:
        return direct
    return _parse_dividend_plan_to_per_share(plan_text)


def _filter_rows_by_code(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "symbol", "ts_code"))]
    if not code_cols:
        return df

    target = _normalize_code(stock_code)
    for col in code_cols:
        try:
            series = df[col].astype(str).map(_normalize_code)
            filtered = df[series == target]
            if not filtered.empty:
                return filtered
        except Exception:
            continue
    return pd.DataFrame()


def _normalize_report_date(value: Any) -> Optional[str]:
    parsed = _safe_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _recent_report_periods(count: int = 1) -> List[str]:
    """Return recent quarter-end report periods as akshare date strings (YYYYMMDD).

    AkShare earnings-forecast endpoints (stock_yjyg_em / stock_yjkb_em) accept a
    report-period date (e.g. 20260331) and return ALL listed companies for that
    period; there is no per-symbol query. We probe several recent periods so a
    symbol that filed in an earlier quarter is still found.
    """
    now = datetime.now()
    periods: List[str] = []
    year, quarter = now.year, (now.month - 1) // 3 + 1
    # Quarter-end day differs: Q1 0331, Q2 0630, Q3 0930, Q4 1231.
    _QUARTER_END_DAY = {1: 31, 2: 30, 3: 30, 4: 31}
    for _ in range(max(1, count)):
        month_end = quarter * 3
        periods.append(f"{year}{month_end:02d}{_QUARTER_END_DAY[quarter]:02d}")
        quarter -= 1
        if quarter < 1:
            quarter = 4
            year -= 1
    return periods


def _build_dividend_payload(
    dividend_df: pd.DataFrame,
    stock_code: str,
    max_events: int = 5,
) -> Dict[str, Any]:
    work_df = _filter_rows_by_code(dividend_df, stock_code)
    if work_df.empty:
        return {}

    now_date = datetime.now().date()
    ttm_start_date = now_date - timedelta(days=365)
    dedupe_keys = set()
    events: List[Dict[str, Any]] = []

    for _, row in work_df.iterrows():
        if not isinstance(row, pd.Series):
            continue
        ex_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["ex_dividend_date"]))
        record_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["record_date"]))
        announce_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"]))
        event_dt = ex_dt or record_dt or announce_dt
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if event_date > now_date:
            continue

        per_share = _extract_cash_dividend_per_share(row)
        if per_share is None or per_share <= 0:
            continue

        dedupe_key = (event_date.isoformat(), round(per_share, 6))
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)

        events.append(
            {
                "event_date": event_date.isoformat(),
                "ex_dividend_date": ex_dt.date().isoformat() if ex_dt else None,
                "record_date": record_dt.date().isoformat() if record_dt else None,
                "announcement_date": announce_dt.date().isoformat() if announce_dt else None,
                "cash_dividend_per_share": round(per_share, 6),
                "is_pre_tax": True,
            }
        )

    if not events:
        return {}

    events.sort(key=lambda item: item.get("event_date") or "", reverse=True)
    ttm_events: List[Dict[str, Any]] = []
    for item in events:
        event_dt = _safe_datetime(item.get("event_date"))
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if ttm_start_date <= event_date <= now_date:
            ttm_events.append(item)

    return {
        "events": events[:max(1, max_events)],
        "ttm_event_count": len(ttm_events),
        "ttm_cash_dividend_per_share": (
            round(sum(float(item.get("cash_dividend_per_share") or 0.0) for item in ttm_events), 6)
            if ttm_events else None
        ),
        "coverage": "cash_dividend_pre_tax",
        "as_of": now_date.isoformat(),
    }


def _extract_latest_row(df: pd.DataFrame, stock_code: str) -> Optional[pd.Series]:
    """
    Select the most relevant row for the given stock.
    """
    if df is None or df.empty:
        return None

    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))]
    target = _normalize_code(stock_code)
    if code_cols:
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                matched = df[series == target]
                if not matched.empty:
                    return matched.iloc[0]
            except Exception:
                continue
        return None

    # Fallback: use latest row
    return df.iloc[0]


def _extract_financial_metrics(df: pd.DataFrame) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Extract (metrics, latest_period) from AkShare's vertical layout.

    `stock_financial_abstract` returns one row per indicator (归母净利润,
    毛利率, ...) with report-period dates as columns. Flip it into a
    name -> latest value map so callers can _pick_by_keywords on indicator
    names. latest_period is the newest report-period column (e.g. 20260331).
    Returns (None, None) for non-vertical layouts (horizontal, per-symbol).
    """
    if df is None or df.empty:
        return None, None
    if "指标" not in df.columns:
        return None, None
    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))]
    if code_cols:
        return None, None
    # Pick the most recent report-period column (highest date string).
    period_cols = [
        c for c in df.columns
        if isinstance(c, str) and len(c) == 8 and c.isdigit()
    ]
    if not period_cols:
        return None, None
    latest = max(period_cols)
    metrics: Dict[str, Any] = {}
    for _, row in df.iterrows():
        name = _safe_str(row.get("指标"))
        if not name:
            continue
        metrics[name] = row.get(latest)
    return metrics, latest


class AkshareFundamentalAdapter:
    """AkShare adapter for fundamentals, capital flow and dragon-tiger signals."""

    def _call_df_candidates(
        self,
        candidates: List[Tuple[str, Dict[str, Any]]],
        call_timeout: float = 3.0,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        errors: List[str] = []
        try:
            import akshare as ak
        except Exception as exc:
            return None, None, [f"import_akshare:{type(exc).__name__}"]

        for func_name, kwargs in candidates:
            fn = getattr(ak, func_name, None)
            if fn is None:
                continue
            try:
                if call_timeout > 0:
                    ex = ThreadPoolExecutor(max_workers=1)
                    try:
                        future = ex.submit(fn, **kwargs)
                        df = future.result(timeout=call_timeout)
                    finally:
                        # Shutdown without waiting: a timed-out request may
                        # still be running and blocking shutdown() indefinitely.
                        ex.shutdown(wait=False, cancel_futures=True)
                else:
                    df = fn(**kwargs)
                if isinstance(df, pd.Series):
                    df = df.to_frame().T
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df, func_name, errors
            except Exception as exc:
                errors.append(f"{func_name}:{type(exc).__name__}")
                continue
        return None, None, errors

    def _fetch_batch_and_filter(
        self,
        func_name: str,
        stock_code: str,
        period_param: str = "date",
        periods: Optional[List[str]] = None,
        fetch_timeout: float = 3.0,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        """Fetch a batch endpoint (keyed by report period, not symbol) and filter.

        AkShare endpoints like stock_yjyg_em / stock_yjkb_em take a report-period
        date and return ALL companies for that period. Probe recent periods and
        return the first one containing the target symbol. Each period call is
        bounded by ``fetch_timeout`` so a slow batch never stalls the whole
        fundamental bundle (which runs under a tight stage timeout).
        """
        import akshare as ak
        fn = getattr(ak, func_name, None)
        if fn is None:
            return None, None, [f"{func_name}:not_found"]
        errors: List[str] = []
        for period in periods or _recent_report_periods():
            df = None
            try:
                ex = ThreadPoolExecutor(max_workers=1)
                try:
                    future = ex.submit(fn, **{period_param: period})
                    df = future.result(timeout=fetch_timeout)
                finally:
                    ex.shutdown(wait=False, cancel_futures=True)
                if isinstance(df, pd.Series):
                    df = df.to_frame().T
                if isinstance(df, pd.DataFrame) and not df.empty:
                    filtered = _filter_rows_by_code(df, stock_code)
                    if not filtered.empty:
                        return filtered, func_name, errors
            except Exception as exc:
                errors.append(f"{func_name}:{type(exc).__name__}")
                continue
        return None, None, errors

    def _fetch_datacenter_capital_flow(
        self,
        stock_code: str,
        timeout: float = 5.0,
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        """Fetch stock capital flow from datacenter-web (eastmoney) as fallback.

        `stock_individual_fund_flow` hits push2his.eastmoney.com which is
        sometimes WAF-blocked on specific exit IPs. datacenter-web uses a
        different host and the RPT_DMSK_TS_STOCKNEW report carries the same
        money-flow fields, so we query it directly and reshape into the
        columns the existing parser expects (主力净流入 etc.).
        """
        import requests
        url = "https://datacenter-web.eastmoney.com/api/data/v1/get"
        params = {
            "reportName": "RPT_DMSK_TS_STOCKNEW",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{stock_code}")',
            "pageNumber": "1",
            "pageSize": "5",
            "sortTypes": "-1",
            "sortColumns": "TRADE_DATE",
        }
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            return None, None, [f"datacenter_capital_flow:{type(exc).__name__}"]

        rows = (payload.get("result") or {}).get("data") or []
        if not rows:
            return None, None, ["datacenter_capital_flow:empty"]

        norm = _normalize_code(stock_code)
        matched = [r for r in rows if str(r.get("SECURITY_CODE", "")).zfill(6) == norm.zfill(6)]
        if not matched:
            return None, None, ["datacenter_capital_flow:code_not_found"]

        rec = matched[0]

        def _as_float(val):
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        # 主力净流入 = 超大单净流入 - 超大单净流出 (net of super-large orders).
        super_in = _as_float(rec.get("SUPERDEAL_INFLOW"))
        super_out = _as_float(rec.get("SUPERDEAL_OUTFLOW"))
        main_net = (super_in - super_out) if (super_in is not None and super_out is not None) else None
        df = pd.DataFrame(
            [{
                "股票代码": norm,
                "主力净流入": main_net,
                "5日净流入": _as_float(rec.get("RATIO_3DAYS")),
                "主力净流入-净占比": _as_float(rec.get("RATIO")),
            }]
        )
        return df, "datacenter:RPT_DMSK_TS_STOCKNEW", []

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        """
        Return normalized fundamental blocks from AkShare with partial tolerance.
        """
        # AkShare endpoints expect plain codes (002043) not suffixed variants
        # (002043.SZ / SZ002043); normalize before passing as query params.
        stock_code = _normalize_code(stock_code)
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }

        # Financial indicators
        fin_df, fin_source, fin_errors = self._call_df_candidates([
            ("stock_financial_abstract", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {}),
        ])
        result["errors"].extend(fin_errors)
        if fin_df is not None:
            metrics, latest_period = _extract_financial_metrics(fin_df)
            if metrics is not None:
                revenue_yoy = _safe_float(_pick_by_keywords(metrics, ["营业收入同比", "营收同比", "收入同比", "同比增长"]))
                profit_yoy = _safe_float(_pick_by_keywords(metrics, ["净利润同比", "净利同比", "归母净利润同比"]))
                roe = _safe_float(_pick_by_keywords(metrics, ["净资产收益率", "ROE", "净资产收益"]))
                gross_margin = _safe_float(_pick_by_keywords(metrics, ["毛利率"]))
                report_date = _normalize_report_date(latest_period)
                revenue = _safe_float(_pick_by_keywords(metrics, ["营业总收入", "营业收入", "营收"]))
                net_profit_parent = _safe_float(_pick_by_keywords(metrics, ["归母净利润", "母公司股东净利润", "净利润"]))
                operating_cash_flow = _safe_float(
                    _pick_by_keywords(metrics, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
                )
            else:
                # Fallback: horizontal per-symbol layout (row = one stock).
                row = _extract_latest_row(fin_df, stock_code)
                revenue_yoy = _safe_float(_pick_by_keywords(row, ["营业收入同比", "营收同比", "收入同比", "同比增长"])) if row is not None else None
                profit_yoy = _safe_float(_pick_by_keywords(row, ["净利润同比", "净利同比", "归母净利润同比"])) if row is not None else None
                roe = _safe_float(_pick_by_keywords(row, ["净资产收益率", "ROE", "净资产收益"])) if row is not None else None
                gross_margin = _safe_float(_pick_by_keywords(row, ["毛利率"])) if row is not None else None
                report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"])) if row is not None else None
                revenue = _safe_float(_pick_by_keywords(row, ["营业总收入", "营业收入", "营收"])) if row is not None else None
                net_profit_parent = _safe_float(_pick_by_keywords(row, ["归母净利润", "母公司股东净利润", "净利润"])) if row is not None else None
                operating_cash_flow = _safe_float(
                    _pick_by_keywords(row, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
                ) if row is not None else None
            if any(v is not None for v in (revenue_yoy, profit_yoy, roe, gross_margin, revenue, net_profit_parent, operating_cash_flow)):
                result["growth"] = {
                    "revenue_yoy": revenue_yoy,
                    "net_profit_yoy": profit_yoy,
                    "roe": roe,
                    "gross_margin": gross_margin,
                }
                financial_report_payload = {
                    "report_date": report_date,
                    "revenue": revenue,
                    "net_profit_parent": net_profit_parent,
                    "operating_cash_flow": operating_cash_flow,
                    "roe": roe,
                }
                if any(v is not None for v in financial_report_payload.values()):
                    result["earnings"]["financial_report"] = financial_report_payload
                result["source_chain"].append(f"growth:{fin_source}")

        # Earnings forecast (batch endpoint keyed by report period, not symbol)
        forecast_df, forecast_source, forecast_errors = self._fetch_batch_and_filter(
            "stock_yjyg_em",
            stock_code,
        )
        result["errors"].extend(forecast_errors)
        if forecast_df is not None:
            row = _extract_latest_row(forecast_df, stock_code)
            if row is not None:
                result["earnings"]["forecast_summary"] = _safe_str(
                    _pick_by_keywords(row, ["预告", "业绩变动", "内容", "摘要", "公告"])
                )[:200]
                result["source_chain"].append(f"earnings_forecast:{forecast_source}")

        # Earnings quick report (batch endpoint keyed by report period, not symbol)
        quick_df, quick_source, quick_errors = self._fetch_batch_and_filter(
            "stock_yjkb_em",
            stock_code,
        )
        result["errors"].extend(quick_errors)
        if quick_df is not None:
            row = _extract_latest_row(quick_df, stock_code)
            if row is not None:
                result["earnings"]["quick_report_summary"] = _safe_str(
                    _pick_by_keywords(row, ["快报", "摘要", "公告", "说明"])
                )[:200]
                result["source_chain"].append(f"earnings_quick:{quick_source}")

        # Dividend details (cash dividend, pre-tax)
        dividend_df, dividend_source, dividend_errors = self._call_df_candidates([
            ("stock_fhps_detail_em", {"symbol": stock_code}),
            ("stock_history_dividend_detail", {"symbol": stock_code, "indicator": "分红", "date": ""}),
            ("stock_dividend_cninfo", {"symbol": stock_code}),
        ])
        result["errors"].extend(dividend_errors)
        if dividend_df is not None:
            dividend_payload = _build_dividend_payload(dividend_df, stock_code, max_events=5)
            if dividend_payload:
                result["earnings"]["dividend"] = dividend_payload
                result["source_chain"].append(f"dividend:{dividend_source}")

        # Institution / top shareholders
        inst_df, inst_source, inst_errors = self._call_df_candidates([
            ("stock_institute_hold", {}),
            ("stock_institute_recommend", {}),
        ])
        result["errors"].extend(inst_errors)
        if inst_df is not None:
            row = _extract_latest_row(inst_df, stock_code)
            if row is not None:
                inst_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "变动", "持股变化"]))
                result["institution"]["institution_holding_change"] = inst_change
                result["source_chain"].append(f"institution:{inst_source}")

        top10_df, top10_source, top10_errors = self._call_df_candidates([
            ("stock_gdfx_top_10_em", {"symbol": stock_code}),
            ("stock_gdfx_top_10_em", {}),
            ("stock_zh_a_gdhs_detail_em", {"symbol": stock_code}),
            ("stock_zh_a_gdhs_detail_em", {}),
        ])
        result["errors"].extend(top10_errors)
        if top10_df is not None:
            row = _extract_latest_row(top10_df, stock_code)
            if row is not None:
                holder_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "持股变化", "变动"]))
                result["institution"]["top10_holder_change"] = holder_change
                result["source_chain"].append(f"top10:{top10_source}")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_profit_snapshot(self, stock_code: str) -> Dict[str, Any]:
        """Return a lightweight profit snapshot for pricing use-cases."""
        stock_code = _normalize_code(stock_code)
        result: Dict[str, Any] = {
            "status": "not_supported",
            "financial_report": {},
            "source_chain": [],
            "errors": [],
        }

        fin_df, fin_source, fin_errors = self._call_df_candidates([
            ("stock_financial_abstract", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {"symbol": stock_code}),
            ("stock_financial_analysis_indicator", {}),
            ("stock_yjbb_em", {"symbol": stock_code}),
            ("stock_yjbb_em", {}),
        ])
        result["errors"].extend(fin_errors)
        if fin_df is None:
            return result

        row = _extract_latest_row(fin_df, stock_code)
        if row is None:
            return result

        report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"]))
        revenue = _safe_float(_pick_by_keywords(row, ["营业总收入", "营业收入", "营收"]))
        net_profit_parent = _safe_float(_pick_by_keywords(row, ["归母净利润", "母公司股东净利润", "净利润"]))
        operating_cash_flow = _safe_float(
            _pick_by_keywords(row, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
        )
        roe = _safe_float(_pick_by_keywords(row, ["净资产收益率", "ROE", "净资产收益"]))

        payload = {
            "report_date": report_date,
            "revenue": revenue,
            "net_profit_parent": net_profit_parent,
            "operating_cash_flow": operating_cash_flow,
            "roe": roe,
        }
        if any(value is not None for value in payload.values()):
            result["financial_report"] = payload
            result["source_chain"].append(f"profit_snapshot:{fin_source}")
            result["status"] = "ok"
        return result

    def get_stock_capital_flow(self, stock_code: str) -> Dict[str, Any]:
        """Return stock-level capital flow only, without sector ranking calls."""
        stock_code = _normalize_code(stock_code)
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "source_chain": [],
            "errors": [],
        }

        # datacenter-web first: on exit IPs where push2his is WAF-blocked it is
        # the reliable path and returns in one request; push2his retries several
        # failing endpoints serially which blows the stage budget.
        stock_df, stock_source, dc_errors = self._fetch_datacenter_capital_flow(stock_code)
        result["errors"].extend(dc_errors)
        if stock_df is None:
            stock_df, stock_source, stock_errors = self._call_df_candidates([
                ("stock_individual_fund_flow", {"stock": stock_code}),
                ("stock_individual_fund_flow", {"symbol": stock_code}),
                ("stock_individual_fund_flow", {}),
                ("stock_main_fund_flow", {"symbol": stock_code}),
                ("stock_main_fund_flow", {}),
            ])
            result["errors"].extend(stock_errors)
        if stock_df is None:
            return result

        row = _extract_latest_row(stock_df, stock_code)
        if row is None:
            return result

        net_inflow = _safe_float(_pick_by_keywords(row, ["主力净流入", "净流入", "净额"]))
        inflow_5d = _safe_float(_pick_by_keywords(row, ["5日", "五日"]))
        inflow_10d = _safe_float(_pick_by_keywords(row, ["10日", "十日"]))
        result["stock_flow"] = {
            "main_net_inflow": net_inflow,
            "inflow_5d": inflow_5d,
            "inflow_10d": inflow_10d,
        }
        if any(value is not None for value in result["stock_flow"].values()):
            result["status"] = "ok"
            result["source_chain"].append(f"capital_stock:{stock_source}")
        return result

    def get_sector_capital_flow_rankings(self, top_n: int = 5) -> Dict[str, Any]:
        """Return sector-level capital flow rankings only."""
        result: Dict[str, Any] = {
            "status": "not_supported",
            "sector_rankings": {"top": [], "bottom": []},
            "source_chain": [],
            "errors": [],
        }

        sector_df, sector_source, sector_errors = self._call_df_candidates([
            ("stock_sector_fund_flow_rank", {}),
            ("stock_sector_fund_flow_summary", {}),
        ], call_timeout=0.5)
        result["errors"].extend(sector_errors)
        if sector_df is None:
            return result

        name_col = next((c for c in sector_df.columns if any(k in str(c) for k in ("板块", "行业", "名称", "name"))), None)
        flow_col = next((c for c in sector_df.columns if any(k in str(c) for k in ("净流入", "主力", "flow", "净额"))), None)
        if not name_col or not flow_col:
            return result

        work_df = sector_df[[name_col, flow_col]].copy()
        work_df[flow_col] = pd.to_numeric(work_df[flow_col], errors="coerce")
        work_df = work_df.dropna(subset=[flow_col])
        top_df = work_df.nlargest(top_n, flow_col)
        bottom_df = work_df.nsmallest(top_n, flow_col)
        result["sector_rankings"] = {
            "top": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in top_df.iterrows()],
            "bottom": [{"name": _safe_str(r[name_col]), "net_inflow": float(r[flow_col])} for _, r in bottom_df.iterrows()],
        }
        if result["sector_rankings"]["top"] or result["sector_rankings"]["bottom"]:
            result["status"] = "ok"
            result["source_chain"].append(f"capital_sector:{sector_source}")
        return result

    def get_capital_flow(self, stock_code: str, top_n: int = 5) -> Dict[str, Any]:
        """
        Return stock + sector capital flow.
        """
        stock_result = self.get_stock_capital_flow(stock_code)
        sector_result = self.get_sector_capital_flow_rankings(top_n=top_n)
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": stock_result.get("stock_flow", {}),
            "sector_rankings": sector_result.get("sector_rankings", {"top": [], "bottom": []}),
            "source_chain": list(stock_result.get("source_chain", [])) + list(sector_result.get("source_chain", [])),
            "errors": list(stock_result.get("errors", [])) + list(sector_result.get("errors", [])),
        }
        has_content = bool(result["stock_flow"] or result["sector_rankings"]["top"] or result["sector_rankings"]["bottom"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_dragon_tiger_flag(self, stock_code: str, lookback_days: int = 20) -> Dict[str, Any]:
        """
        Return dragon-tiger signal in lookback window.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "is_on_list": False,
            "recent_count": 0,
            "latest_date": None,
            "source_chain": [],
            "errors": [],
        }

        df, source, errors = self._call_df_candidates([
            ("stock_lhb_stock_statistic_em", {}),
            ("stock_lhb_detail_em", {}),
            ("stock_lhb_jgmmtj_em", {}),
        ])
        result["errors"].extend(errors)
        if df is None:
            return result

        # Try code filter
        code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码"))]
        target = _normalize_code(stock_code)
        matched = pd.DataFrame()
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                cur = df[series == target]
                if not cur.empty:
                    matched = cur
                    break
            except Exception:
                continue
        if matched.empty:
            result["source_chain"].append(f"dragon_tiger:{source}")
            result["status"] = "ok" if code_cols else "partial"
            return result

        date_col = next((c for c in matched.columns if any(k in str(c) for k in ("日期", "上榜", "交易日", "time"))), None)
        parsed_dates: List[datetime] = []
        if date_col is not None:
            for val in matched[date_col].astype(str).tolist():
                try:
                    parsed_dates.append(pd.to_datetime(val).to_pydatetime())
                except Exception:
                    continue
        now = datetime.now()
        start = now - timedelta(days=max(1, lookback_days))
        recent_dates = [d for d in parsed_dates if start <= d <= now]

        result["is_on_list"] = bool(recent_dates)
        result["recent_count"] = len(recent_dates) if recent_dates else int(len(matched))
        result["latest_date"] = max(recent_dates).date().isoformat() if recent_dates else (
            max(parsed_dates).date().isoformat() if parsed_dates else None
        )
        result["status"] = "ok"
        result["source_chain"].append(f"dragon_tiger:{source}")
        return result
