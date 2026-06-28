# -*- coding: utf-8 -*-
"""Unit tests for search_comprehensive_intel dimension-level parallelization.

Covers: parallel speedup, fixed provider assignment, result ordering,
max_searches truncation, single-dimension failure isolation, freshness
filtering pipeline, empty-provider early return, and concurrent-write safety.

All providers are offline mocks — no network access.
"""

import sys
import threading
import time
from datetime import date, timedelta
from unittest.mock import MagicMock

if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import (  # noqa: E402
    BaseSearchProvider,
    SearchResponse,
    SearchResult,
    SearchService,
)

_DIM_NAMES = [
    "latest_news",
    "market_analysis",
    "risk_check",
    "announcements",
    "earnings",
    "industry",
]


def _make_service(providers):
    """Build a SearchService with injected providers (bypass key-based init)."""
    service = SearchService()
    service._providers = list(providers)
    return service


class _TimedDummyProvider(BaseSearchProvider):
    """Records invoked queries and simulates latency for parallel verification."""

    def __init__(self, api_keys, name, delay=0.0, results=None, raise_on_query=None):
        super().__init__(api_keys, name)
        self.delay = delay
        self._results = results if results is not None else []
        self._raise_on_query = raise_on_query or set()
        self.invoked_queries = []
        self._lock = threading.Lock()

    def _do_search(self, query, api_key, max_results, days=7, topic=None):
        if query in self._raise_on_query:
            raise RuntimeError(f"forced failure for {query}")
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.invoked_queries.append(query)
        return SearchResponse(
            query=query,
            results=list(self._results),
            provider=self.name,
            success=True,
        )


# ---------------------------------------------------------------------------
# T1: parallel speedup
# ---------------------------------------------------------------------------

def test_parallel_runs_faster_than_serial_sum():
    providers = [_TimedDummyProvider(["k"], f"P{i}", delay=0.3) for i in range(3)]
    service = _make_service(providers)
    start = time.monotonic()
    results = service.search_comprehensive_intel("002043", "兔宝宝", max_searches=6)
    elapsed = time.monotonic() - start

    assert len(results) == 6
    # Serial would be 6*0.3s + 5*0.5s sleep ≈ 4.3s; parallel ≈ 0.3s.
    assert elapsed < 1.0, f"expected parallel (<1.0s), got {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# T2: fixed provider assignment (round-robin)
# ---------------------------------------------------------------------------

def test_fixed_round_robin_assignment():
    providers = [_TimedDummyProvider(["k"], f"P{i}") for i in range(3)]
    service = _make_service(providers)
    service.search_comprehensive_intel("002043", "兔宝宝", max_searches=6)

    # dim idx i -> P[i % 3]
    expected = {
        0: "P0", 1: "P1", 2: "P2", 3: "P0", 4: "P1", 5: "P2",
    }
    # Reconstruct which dim went to which provider by querying each provider
    # with the exact A-share dimension queries.
    # Build the same query strings the service uses (A-share branch).
    queries_by_dim = [
        "兔宝宝 002043 最新 新闻 重大 事件",
        "兔宝宝 研报 目标价 评级 深度分析",
        "兔宝宝 减持 处罚 违规 诉讼 利空 风险",
        "兔宝宝 002043 公司公告 重要公告 上交所 深交所 cninfo",
        "兔宝宝 业绩预告 财报 营收 净利润 同比增长",
        "兔宝宝 所在行业 竞争对手 市场份额 行业前景",
    ]
    for dim_idx, query in enumerate(queries_by_dim):
        owner = next(
            (p.name for p in providers if query in p.invoked_queries),
            None,
        )
        assert owner == expected[dim_idx], (
            f"dim {dim_idx} ({_DIM_NAMES[dim_idx]}) expected {expected[dim_idx]}, got {owner}"
        )


# ---------------------------------------------------------------------------
# T3: result ordering preserved
# ---------------------------------------------------------------------------

def test_result_keys_preserve_dimension_order():
    providers = [_TimedDummyProvider(["k"], f"P{i}") for i in range(3)]
    service = _make_service(providers)
    results = service.search_comprehensive_intel("002043", "兔宝宝", max_searches=6)
    assert list(results.keys()) == _DIM_NAMES


# ---------------------------------------------------------------------------
# T4: max_searches truncation
# ---------------------------------------------------------------------------

def test_max_searches_truncates_dimensions():
    providers = [_TimedDummyProvider(["k"], f"P{i}") for i in range(3)]
    service = _make_service(providers)
    results = service.search_comprehensive_intel("002043", "兔宝宝", max_searches=3)
    assert len(results) == 3
    assert list(results.keys()) == _DIM_NAMES[:3]


# ---------------------------------------------------------------------------
# T5: single-dimension failure isolation (no degradation, no blocking)
# ---------------------------------------------------------------------------

def test_failing_dimension_isolated():
    failing_query = "兔宝宝 减持 处罚 违规 诉讼 利空 风险"  # risk_check dim (-> P2)
    providers = [
        _TimedDummyProvider(["k"], "P0"),
        _TimedDummyProvider(["k"], "P1"),
        _TimedDummyProvider(["k"], "P2", raise_on_query={failing_query}),
    ]
    service = _make_service(providers)
    results = service.search_comprehensive_intel("002043", "兔宝宝", max_searches=6)
    # Failing dim is dropped; the other 5 remain.
    assert "risk_check" not in results
    assert len(results) == 5


# ---------------------------------------------------------------------------
# T6: freshness filtering pipeline still runs
# ---------------------------------------------------------------------------

def test_freshness_pipeline_filters_stale_results():
    today = date.today()
    stale = [
        SearchResult(
            title=f"old {i}",
            snippet="旧闻",
            url=f"https://old.com/{i}",
            source="old.com",
            published_date=(today - timedelta(days=30)).isoformat(),  # out of 3-day window
        )
        for i in range(5)
    ]
    providers = [
        _TimedDummyProvider(["k"], "P0", results=stale),
        _TimedDummyProvider(["k"], "P1", results=stale),
        _TimedDummyProvider(["k"], "P2", results=stale),
    ]
    service = _make_service(providers)
    results = service.search_comprehensive_intel("002043", "兔宝宝", max_searches=6)
    # strict_freshness dims (latest_news/announcements) drop all stale items.
    for dim_name, resp in results.items():
        if dim_name in ("latest_news", "announcements"):
            assert len(resp.results) == 0, f"{dim_name} should drop stale results"


# ---------------------------------------------------------------------------
# T7: empty providers early return
# ---------------------------------------------------------------------------

def test_no_available_providers_returns_empty():
    providers = [_TimedDummyProvider(["k"], "P0")]
    providers[0]._api_keys = []  # force is_available False
    service = _make_service(providers)
    results = service.search_comprehensive_intel("002043", "兔宝宝", max_searches=6)
    assert results == {}


# ---------------------------------------------------------------------------
# T8: concurrent-write safety under repeated parallel runs
# ---------------------------------------------------------------------------

def test_concurrent_runs_are_stable():
    for _ in range(20):
        providers = [_TimedDummyProvider(["k"], f"P{i}", delay=0.02) for i in range(3)]
        service = _make_service(providers)
        results = service.search_comprehensive_intel("002043", "兔宝宝", max_searches=6)
        assert list(results.keys()) == _DIM_NAMES
        assert len(results) == 6
