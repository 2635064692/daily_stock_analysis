from __future__ import annotations

import sys
import threading
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

for _mod in ("dotenv",):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}

from src.services.pricing_service import PricingService, _STOCK_INPUT_WORKERS


def _bar(h: float, l: float, c: float, v: float):
    return SimpleNamespace(high=h, low=l, close=c, volume=v)


def _make_bars(close: float = 10.0, n: int = 25) -> list:
    return [_bar(close + 1, close - 1, close, 100.0) for _ in range(n)]


def _make_service(repo=None, constituent_repo=None):
    return PricingService(
        repo=repo or MagicMock(),
        constituent_repo=constituent_repo or MagicMock(),
    )


def _manager_for_codes(codes: list[str]) -> MagicMock:
    """创建支持并发调用的 mock manager"""
    mgr = MagicMock()

    def _quote_side_effect(code: str):
        return SimpleNamespace(total_mv=1000.0)

    def _profit_side_effect(code: str):
        return {
            "data": {
                "financial_report": {
                    "net_profit_parent": 100.0,
                    "report_date": "2026-03-31",
                }
            }
        }

    def _flow_side_effect(code: str):
        return {"data": {"stock_flow": {"main_net_inflow": 100.0}}}

    mgr.get_realtime_quote.side_effect = _quote_side_effect
    mgr.get_profit_snapshot.side_effect = _profit_side_effect
    mgr.get_stock_capital_flow_context.side_effect = _flow_side_effect
    mgr.prefetch_realtime_quotes = MagicMock()
    return mgr


class TestConcurrentExecution:
    """测试并发执行逻辑"""

    def test_single_stock_uses_serial_execution(self):
        """单只股票使用串行执行"""
        codes = ["000001"]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch.object(svc, "_collect_stock_inputs_parallel") as parallel_mock,
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        # 单只股票不应该调用并发逻辑
        parallel_mock.assert_not_called()

    def test_multiple_stocks_use_parallel_execution(self):
        """多只股票使用并发执行"""
        codes = ["000001", "000002", "000003", "000004", "000005"]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch.object(svc, "_collect_stock_inputs_parallel", wraps=svc._collect_stock_inputs_parallel) as parallel_mock,
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        # 多只股票应该调用并发逻辑
        parallel_mock.assert_called_once()
        assert parallel_mock.call_args.kwargs["max_workers"] == min(len(codes), _STOCK_INPUT_WORKERS)

    def test_parallel_execution_preserves_order(self):
        """并发执行保持结果顺序"""
        codes = ["000001", "000002", "000003", "000004"]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        # 验证返回的股票顺序与输入一致
        returned_codes = [item["stock_code"] for item in result["stocks"]]
        assert returned_codes == codes

    def test_parallel_execution_handles_individual_failures(self):
        """并发执行正确处理单个股票失败"""
        codes = ["000001", "000002", "000003"]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)

        # 让第二只股票的基本面查询失败
        def _profit_side_effect_with_failure(code: str):
            if code == "000002":
                raise Exception("profit fetch failed")
            return {
                "data": {
                    "financial_report": {
                        "net_profit_parent": 100.0,
                        "report_date": "2026-03-31",
                    }
                }
            }

        mgr.get_profit_snapshot.side_effect = _profit_side_effect_with_failure
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
        ):
            result = svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        # 验证返回了所有股票，失败的股票状态为 missing_core_factor
        assert len(result["stocks"]) == 3
        stocks_by_code = {item["stock_code"]: item for item in result["stocks"]}
        assert stocks_by_code["000001"]["status"] == "ok"
        assert stocks_by_code["000002"]["status"] == "missing_core_factor"
        assert stocks_by_code["000003"]["status"] == "ok"

    def test_parallel_execution_respects_max_workers(self):
        """并发执行遵守最大工作线程数"""
        codes = [f"00000{i}" for i in range(10)]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)
        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
            patch.object(svc, "_collect_stock_inputs_parallel", wraps=svc._collect_stock_inputs_parallel) as parallel_mock,
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        # 验证工作线程数不超过 _STOCK_INPUT_WORKERS
        assert parallel_mock.call_args.kwargs["max_workers"] == _STOCK_INPUT_WORKERS


class TestConcurrentPerformance:
    """测试并发性能提升"""

    def test_parallel_execution_is_faster_than_serial(self):
        """并发执行比串行快（带模拟延迟）"""
        codes = ["000001", "000002", "000003", "000004"]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        # 创建带延迟的 manager
        mgr = MagicMock()

        def _slow_quote(code: str):
            time.sleep(0.1)  # 模拟网络延迟
            return SimpleNamespace(total_mv=1000.0)

        def _slow_profit(code: str):
            time.sleep(0.1)
            return {
                "data": {
                    "financial_report": {
                        "net_profit_parent": 100.0,
                        "report_date": "2026-03-31",
                    }
                }
            }

        def _slow_flow(code: str):
            time.sleep(0.1)
            return {"data": {"stock_flow": {"main_net_inflow": 100.0}}}

        mgr.get_realtime_quote.side_effect = _slow_quote
        mgr.get_profit_snapshot.side_effect = _slow_profit
        mgr.get_stock_capital_flow_context.side_effect = _slow_flow
        mgr.prefetch_realtime_quotes = MagicMock()

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
        ):
            start = time.perf_counter()
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))
            elapsed = time.perf_counter() - start

        # 串行执行需要 4 * 3 * 0.1 = 1.2s
        # 并发执行（4 个 worker）应该接近 3 * 0.1 = 0.3s
        # 给予一定的容错范围
        assert elapsed < 0.8, f"并发执行耗时 {elapsed:.2f}s，预期 < 0.8s"

    def test_parallel_execution_uses_actual_threads(self):
        """并发执行使用真实的多线程"""
        codes = ["000001", "000002", "000003"]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes

        # 记录每次调用的线程 ID
        thread_ids = []
        lock = threading.Lock()

        mgr = MagicMock()

        def _track_thread_quote(code: str):
            with lock:
                thread_ids.append(threading.current_thread().ident)
            return SimpleNamespace(total_mv=1000.0)

        def _track_thread_profit(code: str):
            with lock:
                thread_ids.append(threading.current_thread().ident)
            return {
                "data": {
                    "financial_report": {
                        "net_profit_parent": 100.0,
                        "report_date": "2026-03-31",
                    }
                }
            }

        def _track_thread_flow(code: str):
            with lock:
                thread_ids.append(threading.current_thread().ident)
            return {"data": {"stock_flow": {"main_net_inflow": 100.0}}}

        mgr.get_realtime_quote.side_effect = _track_thread_quote
        mgr.get_profit_snapshot.side_effect = _track_thread_profit
        mgr.get_stock_capital_flow_context.side_effect = _track_thread_flow
        mgr.prefetch_realtime_quotes = MagicMock()

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        # 验证使用了多个不同的线程
        unique_threads = set(thread_ids)
        assert len(unique_threads) > 1, f"应该使用多个线程，实际只用了 {len(unique_threads)} 个"


class TestPrefetchIntegration:
    """测试预取机制与并发的集成"""

    def test_prefetch_called_before_parallel_execution(self):
        """预取在并发执行前被调用"""
        codes = ["000001", "000002", "000003"]
        repo = MagicMock()
        repo.save_pricing_batch.return_value = 1
        c_repo = MagicMock()
        c_repo.get_constituents.return_value = codes
        mgr = _manager_for_codes(codes)

        call_order = []

        def _track_prefetch(codes_list):
            call_order.append("prefetch")

        def _track_quote(code):
            call_order.append(f"quote_{code}")
            return SimpleNamespace(total_mv=1000.0)

        mgr.prefetch_realtime_quotes.side_effect = _track_prefetch
        mgr.get_realtime_quote.side_effect = _track_quote

        svc = _make_service(repo=repo, constituent_repo=c_repo)

        with (
            patch("src.services.pricing_service._fetch_bars", return_value=_make_bars()),
            patch("src.services.pricing_service._get_fetcher_manager", return_value=mgr),
        ):
            svc.price_board(board_id=801010, trade_date=date(2024, 6, 3))

        # 验证 prefetch 在所有 get_realtime_quote 之前
        assert call_order[0] == "prefetch"
        assert all(item.startswith("quote_") for item in call_order[1:])
