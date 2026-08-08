# -*- coding: utf-8 -*-
"""
Tests for fundamental adapter helpers.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.fundamental_adapter import (
    AkshareFundamentalAdapter,
    _build_dividend_payload,
    _extract_latest_row,
    _parse_dividend_plan_to_per_share,
)


class TestFundamentalAdapter(unittest.TestCase):
    def test_parse_dividend_plan_to_per_share_supports_cn_patterns(self) -> None:
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("10派3元(含税)"), 0.3, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每10股派发2.5元"), 0.25, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每股派0.8元"), 0.8, places=6)
        self.assertIsNone(_parse_dividend_plan_to_per_share("仅送股，不现金分红"))

    def test_extract_latest_row_returns_none_when_code_mismatch(self) -> None:
        df = pd.DataFrame(
            {
                "股票代码": ["600000", "000001"],
                "值": [1, 2],
            }
        )
        row = _extract_latest_row(df, "600519")
        self.assertIsNone(row)

    def test_extract_latest_row_fallback_when_no_code_column(self) -> None:
        df = pd.DataFrame({"值": [1, 2]})
        row = _extract_latest_row(df, "600519")
        self.assertIsNotNone(row)
        self.assertEqual(row["值"], 1)

    def test_dragon_tiger_no_match_with_code_column_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        df = pd.DataFrame(
            {
                "股票代码": ["600000"],
                "日期": ["2026-01-01"],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["is_on_list"])
        self.assertEqual(result["recent_count"], 0)

    def test_dragon_tiger_match_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "日期": [today],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["is_on_list"])
        self.assertGreaterEqual(result["recent_count"], 1)

    def test_fundamental_bundle_includes_financial_report_and_dividend_payload(self) -> None:
        adapter = AkshareFundamentalAdapter()
        now = datetime.now()
        within_ttm = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        future_day = (now + timedelta(days=10)).strftime("%Y-%m-%d")
        old_day = (now - timedelta(days=500)).strftime("%Y-%m-%d")
        fin_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "报告期": [within_ttm],
                "营业总收入": [1000.0],
                "归母净利润": [300.0],
                "经营活动产生的现金流量净额": [500.0],
                "净资产收益率": [18.2],
                "营业收入同比": [12.0],
                "净利润同比": [9.5],
            }
        )
        forecast_df = pd.DataFrame({"股票代码": ["600519"], "预告": ["预增"]})
        quick_df = pd.DataFrame({"股票代码": ["600519"], "快报": ["快报摘要"]})
        dividend_df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519", "600519", "600519"],
                "除息日": [within_ttm, within_ttm, future_day, old_day],
                "分配方案": ["10派3元(含税)", "10派3元(含税)", "10派5元", "10派1元"],
            }
        )

        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (fin_df, "stock_financial_abstract", []),
                (dividend_df, "stock_fhps_detail_em", []),
                (None, None, []),
                (None, None, []),
            ],
        ), patch.object(adapter, "_fetch_batch_and_filter", return_value=(None, None, [])):
            result = adapter.get_fundamental_bundle("600519")

        financial_report = result["earnings"].get("financial_report", {})
        self.assertEqual(financial_report.get("report_date"), within_ttm)
        self.assertEqual(financial_report.get("revenue"), 1000.0)
        self.assertEqual(financial_report.get("net_profit_parent"), 300.0)
        self.assertEqual(financial_report.get("operating_cash_flow"), 500.0)
        self.assertEqual(financial_report.get("roe"), 18.2)

        dividend_payload = result["earnings"].get("dividend", {})
        events = dividend_payload.get("events", [])
        self.assertEqual(len(events), 2)  # duplicate + future day filtered
        self.assertEqual(dividend_payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(dividend_payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)

    def test_profit_snapshot_returns_financial_report_only(self) -> None:
        adapter = AkshareFundamentalAdapter()
        fin_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "报告期": ["2026-03-31"],
                "营业总收入": [1000.0],
                "归母净利润": [300.0],
                "经营活动产生的现金流量净额": [500.0],
                "净资产收益率": [18.2],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(fin_df, "stock_financial_abstract", [])):
            result = adapter.get_profit_snapshot("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["financial_report"]["net_profit_parent"], 300.0)
        self.assertEqual(result["financial_report"]["report_date"], "2026-03-31")
        self.assertEqual(result["source_chain"], ["profit_snapshot:stock_financial_abstract"])

    def test_extract_financial_metrics_parses_vertical_layout(self) -> None:
        # AkShare stock_financial_abstract returns one row per indicator with
        # report-period dates as columns (vertical layout). Ensure the latest
        # period is picked and values map by indicator name.
        from data_provider.fundamental_adapter import _extract_financial_metrics
        df = pd.DataFrame(
            {
                "选项": ["常用指标", "常用指标", "常用指标"],
                "指标": ["归母净利润", "营业总收入", "净资产收益率(ROE)"],
                "20251231": [500.0, 8000.0, 10.0],
                "20260331": [600.0, 9000.0, 12.0],
            }
        )
        metrics, latest = _extract_financial_metrics(df)
        self.assertEqual(latest, "20260331")
        self.assertEqual(metrics["归母净利润"], 600.0)
        self.assertEqual(metrics["营业总收入"], 9000.0)
        self.assertEqual(metrics["净资产收益率(ROE)"], 12.0)

    def test_extract_financial_metrics_returns_none_for_horizontal_layout(self) -> None:
        from data_provider.fundamental_adapter import _extract_financial_metrics
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "报告期": ["2026-03-31"],
                "营业总收入": [1000.0],
            }
        )
        metrics, latest = _extract_financial_metrics(df)
        self.assertIsNone(metrics)
        self.assertIsNone(latest)

    def test_fetch_batch_and_filter_probes_periods_and_filters_symbol(self) -> None:
        adapter = AkshareFundamentalAdapter()
        batch_df = pd.DataFrame(
            {
                "股票代码": ["000001", "002043"],
                "业绩变动": ["预增", "预增"],
                "公告日期": ["2026-01-01", "2026-01-02"],
            }
        )
        import sys
        import types
        fake_ak = types.SimpleNamespace(
            stock_yjyg_em=lambda date: batch_df,
            stock_yjkb_em=lambda date: pd.DataFrame(),
        )
        with patch(
            "data_provider.fundamental_adapter._recent_report_periods",
            return_value=["20260331"],
        ), patch.dict(sys.modules, {"akshare": fake_ak}):
            df, source, errors = adapter._fetch_batch_and_filter(
                "stock_yjyg_em", "002043.SZ"
            )
        self.assertEqual(source, "stock_yjyg_em")
        self.assertEqual(errors, [])
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["股票代码"], "002043")

    def test_stock_capital_flow_prefers_datacenter_when_push2his_blocked(self) -> None:
        adapter = AkshareFundamentalAdapter()
        dc_df = pd.DataFrame(
            {
                "股票代码": ["002043"],
                "主力净流入": [-10206248.0],
                "5日净流入": [0.17],
            }
        )
        # datacenter-first: when datacenter succeeds, push2his (_call_df_candidates)
        # must NOT be invoked.
        with patch.object(
            adapter, "_fetch_datacenter_capital_flow", return_value=(dc_df, "datacenter:RPT_DMSK_TS_STOCKNEW", [])
        ), patch.object(
            adapter, "_call_df_candidates", return_value=(None, None, ["unexpected_push2his_call"])
        ) as call_mock:
            result = adapter.get_stock_capital_flow("002043")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stock_flow"]["main_net_inflow"], -10206248.0)
        self.assertEqual(result["source_chain"], ["capital_stock:datacenter:RPT_DMSK_TS_STOCKNEW"])
        call_mock.assert_not_called()

    def test_stock_capital_flow_falls_back_to_push2his_when_datacenter_misses(self) -> None:
        adapter = AkshareFundamentalAdapter()
        stock_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "主力净流入": [123.0],
                "5日净流入": [200.0],
            }
        )
        with patch.object(
            adapter, "_fetch_datacenter_capital_flow", return_value=(None, None, ["dc_miss"])
        ), patch.object(
            adapter, "_call_df_candidates", return_value=(stock_df, "stock_individual_fund_flow", [])
        ):
            result = adapter.get_stock_capital_flow("600519")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stock_flow"]["main_net_inflow"], 123.0)
        self.assertEqual(result["source_chain"], ["capital_stock:stock_individual_fund_flow"])

    def test_fetch_datacenter_capital_flow_builds_expected_df(self) -> None:
        adapter = AkshareFundamentalAdapter()
        import types
        fake_resp = types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "result": {"data": [
                    {
                        "SECURITY_CODE": "002043",
                        "SUPERDEAL_INFLOW": 5854680.0,
                        "SUPERDEAL_OUTFLOW": 16060928.0,
                        "RATIO_3DAYS": 0.171866666667,
                        "RATIO": 0.2863,
                    }
                ]}
            },
        )
        with patch("requests.get", return_value=fake_resp) as get_mock:
            df, source, errors = adapter._fetch_datacenter_capital_flow("002043.SZ")
        self.assertEqual(source, "datacenter:RPT_DMSK_TS_STOCKNEW")
        self.assertEqual(errors, [])
        self.assertEqual(df.iloc[0]["主力净流入"], 5854680.0 - 16060928.0)
        self.assertEqual(get_mock.call_count, 1)

    def test_build_dividend_payload_returns_empty_when_code_not_matched(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "除息日": [now],
                "分配方案": ["10派3元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_skips_after_tax_plan(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "除息日": [now],
                "分配方案": ["10派3元(税后)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_ttm_window_boundary(self) -> None:
        now = datetime.now()
        day_365 = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        day_366 = (now - timedelta(days=366)).strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519"],
                "除息日": [day_365, day_366],
                "分配方案": ["10派3元(含税)", "10派5元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)


if __name__ == "__main__":
    unittest.main()
