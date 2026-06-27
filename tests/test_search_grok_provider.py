# -*- coding: utf-8 -*-
"""
Tests for GrokSearchProvider — offline unit tests + one live network smoke test.

Offline tests mock requests.post to avoid real HTTP calls.
The live test is marked @pytest.mark.network and skipped in CI.
"""

import json
import sys
import unittest
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import GrokSearchProvider, SearchService

_BASE_URL = "http://claw.592121.xyz/v1"
_API_KEY = "sk-kQKMTKyEQA7X6eZ_wHbsGtoNbrfoc6T3KNWX2kgeA1rRmljOKVw8KA9U6rE"
_MODEL = "grok-4.20-fast"


# ---------------------------------------------------------------------------
# SSE stream helpers
# ---------------------------------------------------------------------------

def _sse_lines(*content_chunks: str, done: bool = True) -> list:
    """Build SSE byte lines as requests.iter_lines() would yield."""
    lines = []
    for chunk in content_chunks:
        payload = json.dumps({"choices": [{"delta": {"content": chunk}}]})
        lines.append(f"data: {payload}".encode())
    if done:
        lines.append(b"data: [DONE]")
    return lines


def _make_fake_response(lines: list, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    resp.iter_lines = MagicMock(return_value=iter(lines))
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Offline tests
# ---------------------------------------------------------------------------

class TestGrokSearchProviderParsing(unittest.TestCase):
    """Unit tests for _parse_json_results — no HTTP needed."""

    def _provider(self):
        return GrokSearchProvider([_API_KEY], base_url=_BASE_URL, model=_MODEL)

    def test_parses_valid_json_array(self):
        p = self._provider()
        raw = json.dumps([
            {"title": "T1", "content": "C1", "sourceUrl": "https://a.com/1", "publishedDate": "2026-06-01"},
            {"title": "T2", "content": "C2", "sourceUrl": "https://b.com/2", "publishedDate": None},
        ])
        results = p._parse_json_results(raw, max_results=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "T1")
        self.assertEqual(results[0].url, "https://a.com/1")
        self.assertEqual(results[0].published_date, "2026-06-01")
        self.assertEqual(results[1].published_date, None)

    def test_strips_markdown_code_fence(self):
        p = self._provider()
        inner = json.dumps([{"title": "T", "content": "C", "sourceUrl": "https://c.com/3", "publishedDate": "2026-06-02"}])
        raw = f"```json\n{inner}\n```"
        results = p._parse_json_results(raw, max_results=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://c.com/3")

    def test_extracts_array_from_noisy_text(self):
        p = self._provider()
        array_part = json.dumps([{"title": "T", "content": "C", "sourceUrl": "https://d.com/4", "publishedDate": None}])
        raw = f"Here are the results:\n{array_part}\nEnd."
        results = p._parse_json_results(raw, max_results=10)
        self.assertEqual(len(results), 1)

    def test_omits_elements_without_source_url(self):
        p = self._provider()
        raw = json.dumps([
            {"title": "T1", "content": "C1", "sourceUrl": "", "publishedDate": None},
            {"title": "T2", "content": "C2", "sourceUrl": "https://e.com/5", "publishedDate": None},
        ])
        results = p._parse_json_results(raw, max_results=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://e.com/5")

    def test_respects_max_results(self):
        p = self._provider()
        items = [{"title": f"T{i}", "content": "C", "sourceUrl": f"https://f.com/{i}", "publishedDate": None} for i in range(10)]
        raw = json.dumps(items)
        results = p._parse_json_results(raw, max_results=3)
        self.assertEqual(len(results), 3)

    def test_returns_empty_on_invalid_json(self):
        p = self._provider()
        results = p._parse_json_results("not json at all", max_results=10)
        self.assertEqual(results, [])

    def test_snippet_truncated_to_500(self):
        p = self._provider()
        long_content = "x" * 600
        raw = json.dumps([{"title": "T", "content": long_content, "sourceUrl": "https://g.com/6", "publishedDate": None}])
        results = p._parse_json_results(raw, max_results=10)
        self.assertEqual(len(results[0].snippet), 500)


class TestGrokSearchProviderStream(unittest.TestCase):
    """Unit tests for _call_stream SSE parsing via mocked requests.post."""

    def _provider(self):
        return GrokSearchProvider([_API_KEY], base_url=_BASE_URL, model=_MODEL)

    def _patch_post(self, lines):
        return patch("src.search_service.requests.post", return_value=_make_fake_response(lines))

    def test_assembles_content_from_chunks(self):
        p = self._provider()
        lines = _sse_lines('["chunk1"', ', "chunk2"]')
        with self._patch_post(lines):
            result = p._call_stream(_API_KEY, "test prompt")
        self.assertEqual(result, '["chunk1", "chunk2"]')

    def test_ignores_non_data_lines(self):
        p = self._provider()
        lines = [b"event: ping", b""] + _sse_lines('["ok"]')
        with self._patch_post(lines):
            result = p._call_stream(_API_KEY, "test")
        self.assertEqual(result, '["ok"]')

    def test_stops_at_done(self):
        p = self._provider()
        array = json.dumps([{"title": "T", "content": "C", "sourceUrl": "https://h.com/7", "publishedDate": None}])
        lines = _sse_lines(array) + [b"data: should not appear"]
        with self._patch_post(lines):
            result = p._call_stream(_API_KEY, "test")
        self.assertEqual(result, array)

    def test_raises_on_http_error(self):
        p = self._provider()
        import requests as _req
        resp = _make_fake_response([])
        resp.raise_for_status.side_effect = _req.exceptions.HTTPError("401")
        with patch("src.search_service.requests.post", return_value=resp):
            with self.assertRaises(_req.exceptions.HTTPError):
                p._call_stream(_API_KEY, "test")


class TestGrokDoSearch(unittest.TestCase):
    """Unit tests for _do_search retry + full pipeline."""

    def _provider(self):
        return GrokSearchProvider([_API_KEY], base_url=_BASE_URL, model=_MODEL)

    def _patch_post(self, lines):
        return patch("src.search_service.requests.post", return_value=_make_fake_response(lines))

    def _good_payload(self):
        items = [
            {"title": "Moutai Q1 earnings beat", "content": "Moutai reported...", "sourceUrl": "https://news.cn/moutai", "publishedDate": "2026-06-20"},
            {"title": "Moutai dividend plan", "content": "Board approved...", "sourceUrl": "https://sse.com/moutai-div", "publishedDate": "2026-06-18"},
            {"title": "Moutai market outlook", "content": "Analysts say...", "sourceUrl": "https://eastmoney.com/moutai", "publishedDate": "2026-06-15"},
        ]
        return json.dumps(items)

    def test_returns_success_response(self):
        p = self._provider()
        lines = _sse_lines(self._good_payload())
        with self._patch_post(lines):
            resp = p._do_search("贵州茅台 600519 股票 最新消息", _API_KEY, max_results=5, days=7)
        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "Grok")
        self.assertEqual(len(resp.results), 3)
        self.assertEqual(resp.results[0].title, "Moutai Q1 earnings beat")
        self.assertEqual(resp.results[0].published_date, "2026-06-20")

    def test_retries_on_empty_response_and_returns_failure(self):
        p = self._provider()
        empty_lines = _sse_lines("")
        call_count = 0

        def fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_fake_response(_sse_lines(""))

        with patch("src.search_service.requests.post", side_effect=fake_post):
            resp = p._do_search("test", _API_KEY, max_results=3)

        self.assertFalse(resp.success)
        self.assertEqual(call_count, 3)
        self.assertIn("empty", resp.error_message)

    def test_retries_on_exception_and_succeeds_on_third(self):
        p = self._provider()
        attempt = 0
        good_lines = _sse_lines(self._good_payload())

        import requests as _req

        def fake_post(*args, **kwargs):
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise _req.exceptions.ConnectionError("timeout")
            return _make_fake_response(good_lines)

        with patch("src.search_service.requests.post", side_effect=fake_post):
            resp = p._do_search("test", _API_KEY, max_results=5)

        self.assertTrue(resp.success)
        self.assertEqual(attempt, 3)

    def test_topic_param_is_ignored(self):
        p = self._provider()
        lines = _sse_lines(self._good_payload())
        captured = {}

        def fake_post(url, **kwargs):
            captured["payload"] = kwargs.get("json", {})
            return _make_fake_response(lines)

        with patch("src.search_service.requests.post", side_effect=fake_post):
            resp = p._do_search("test", _API_KEY, max_results=3, topic="news")

        self.assertTrue(resp.success)
        messages = captured["payload"].get("messages", [])
        full_text = " ".join(m.get("content", "") for m in messages)
        self.assertNotIn("topic", full_text)


class TestGrokSearchServiceIntegration(unittest.TestCase):
    """Integration: SearchService routes through GrokSearchProvider correctly."""

    def _good_sse_lines(self):
        items = [
            {"title": "T1", "content": "C1", "sourceUrl": "https://x.com/1", "publishedDate": "2026-06-25"},
        ]
        return _sse_lines(json.dumps(items))

    def test_search_stock_news_via_grok(self):
        lines = self._good_sse_lines()

        def fake_post(*args, **kwargs):
            return _make_fake_response(lines)

        with patch("src.search_service.requests.post", side_effect=fake_post):
            service = SearchService(
                grok_keys=[_API_KEY],
                grok_base_url=_BASE_URL,
                grok_model=_MODEL,
                searxng_public_instances_enabled=False,
                news_max_age_days=7,
                news_strategy_profile="short",
            )
            resp = service.search_stock_news("600519", "贵州茅台", max_results=3)

        self.assertTrue(resp.success)
        self.assertEqual(resp.provider, "Grok")
        self.assertGreaterEqual(len(resp.results), 1)


# ---------------------------------------------------------------------------
# Live network smoke test (CI-skipped)
# ---------------------------------------------------------------------------

@pytest.mark.network
class TestGrokSearchProviderLive(unittest.TestCase):
    """Live end-to-end test against the real Grok endpoint. Skipped in CI."""

    def _provider(self):
        return GrokSearchProvider([_API_KEY], base_url=_BASE_URL, model=_MODEL)

    def test_live_search_returns_results(self):
        p = self._provider()
        resp = p._do_search("贵州茅台 600519 股票 最新消息", _API_KEY, max_results=3, days=7)
        print(f"\n[Live] success={resp.success}, count={len(resp.results)}, error={resp.error_message}")
        for r in resp.results:
            print(f"  - {r.title} | {r.published_date} | {r.url}")
        self.assertTrue(resp.success, msg=f"Live search failed: {resp.error_message}")
        self.assertGreater(len(resp.results), 0)
        for r in resp.results:
            self.assertTrue(r.url.startswith("http"), msg=f"Invalid URL: {r.url}")
            self.assertTrue(r.title, msg="Empty title")


if __name__ == "__main__":
    unittest.main()
