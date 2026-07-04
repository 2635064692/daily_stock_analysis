# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from src.services.spi.rotation_screening import SectorRotationScreener

TRADE_DATE = date(2026, 7, 4)
BOARD_A, BOARD_B = 801010, 801020
SNAPSHOT_SOURCE = "spi_v2:akshare_sw"
BUY = "BUY"
SELL = "SELL"


def _pricing_row(stock_code, total, **kwargs):
    row = MagicMock()
    row.stock_code = stock_code
    row.total = total
    row.sp_ratio = kwargs.get("sp_ratio", 1.0)
    row.sp_score = kwargs.get("sp_score", 0.5)
    row.cmf = kwargs.get("cmf", 0.15)
    row.flow_score = kwargs.get("flow_score", 0.65)
    row.status = kwargs.get("status", "ok")
    row.factor_mask = kwargs.get("factor_mask", "sp,cmf,flow")
    return row


def _buy_signal(board_id, stock_code):
    return {"board_id": board_id, "stock_code": stock_code, "action": BUY,
            "reason": "pullback to EMA20, vol_ratio=1.50", "trade_date": TRADE_DATE}


def _sell_signal(board_id, stock_code):
    return {"board_id": board_id, "stock_code": stock_code, "action": SELL,
            "reason": "close_below_EMA5", "trade_date": TRADE_DATE}


def _v2_board(board_id, board_name, v2_score):
    return {"board_id": board_id, "board_name": board_name, "v2_score": v2_score, "spi": 7}


def _make_screener(spi_signals=None, v2_boards=None, pricing_rows=None):
    spi_repo = MagicMock()
    spi_repo.find_rotation_signals.return_value = spi_signals or []
    spi_repo.find_top_boards_v2.return_value = v2_boards or []

    pricing_repo = MagicMock()
    if pricing_rows is None:
        pricing_repo.find_board_pricing_rank.return_value = []
    elif callable(pricing_rows):
        pricing_repo.find_board_pricing_rank.side_effect = pricing_rows
    else:
        pricing_repo.find_board_pricing_rank.return_value = pricing_rows

    screener = SectorRotationScreener(spi_repo=spi_repo, pricing_repo=pricing_repo)
    return screener, spi_repo, pricing_repo


class TestScreenNoSignals:

    def test_no_signals_at_all(self):
        screener, _, _ = _make_screener()

        result = screener.screen(TRADE_DATE)

        assert result["candidates"] == []
        assert result["rotation_boards"] == 0
        assert result["snapshot_source"] == SNAPSHOT_SOURCE
        assert result["warnings"] == ["无BUY信号"]

    def test_only_sell_signals_no_buy(self):
        screener, _, _ = _make_screener(
            spi_signals=[_sell_signal(BOARD_A, "600519")],
        )

        result = screener.screen(TRADE_DATE)

        assert result["candidates"] == []
        assert result["rotation_boards"] == 0
        assert "无BUY信号" in result["warnings"]


class TestScreenWithSignals:

    def test_single_board_two_candidates_with_pricing(self):
        screener, spi_repo, pricing_repo = _make_screener(
            spi_signals=[
                _buy_signal(BOARD_A, "600519"),
                _buy_signal(BOARD_A, "000858"),
            ],
            v2_boards=[_v2_board(BOARD_A, "食品饮料", 85.3)],
            pricing_rows=[
                _pricing_row("600519", 0.78, sp_ratio=0.85, sp_score=0.54, cmf=0.15, flow_score=0.65),
                _pricing_row("000858", 0.62, sp_ratio=0.90, sp_score=0.53, cmf=0.10, flow_score=0.50),
            ],
        )

        result = screener.screen(TRADE_DATE)

        assert result["rotation_boards"] == 1
        assert len(result["candidates"]) == 2
        cand = result["candidates"][0]
        assert cand["code"] == "600519"
        assert cand["board_id"] == BOARD_A
        assert cand["board_name"] == "食品饮料"
        assert cand["board_v2_score"] == 85.3
        assert cand["pricing_rank"] == 1
        assert cand["total"] == 0.78
        assert cand["sp_ratio"] == 0.85
        assert cand["sp_score"] == 0.54
        assert cand["cmf"] == 0.15
        assert cand["flow_score"] == 0.65
        assert cand["status"] == "ok"
        assert cand["factor_mask"] == "sp,cmf,flow"
        assert cand["selection_reason"] == "板块轮动BUY信号"

        cand2 = result["candidates"][1]
        assert cand2["code"] == "000858"
        assert cand2["pricing_rank"] == 2
        assert cand2["total"] == 0.62

        # repos called with correct params
        spi_repo.find_rotation_signals.assert_called_once_with(trade_date=TRADE_DATE)
        spi_repo.find_top_boards_v2.assert_called_once_with(anchor_date=TRADE_DATE, top_n=200)
        pricing_repo.find_board_pricing_rank.assert_called_once_with(
            board_id=BOARD_A, trade_date=TRADE_DATE,
        )

    def test_signal_stock_missing_pricing_fallback(self):
        screener, _, pricing_repo = _make_screener(
            spi_signals=[
                _buy_signal(BOARD_A, "600519"),
                _buy_signal(BOARD_A, "000858"),
            ],
            v2_boards=[_v2_board(BOARD_A, "食品饮料", 85.3)],
            pricing_rows=[
                _pricing_row("600519", 0.78),
            ],
        )

        result = screener.screen(TRADE_DATE)

        assert result["rotation_boards"] == 1
        assert len(result["candidates"]) == 2

        priced = [c for c in result["candidates"] if c["status"] != "missing_pricing"]
        missing = [c for c in result["candidates"] if c["status"] == "missing_pricing"]

        assert len(priced) == 1
        assert priced[0]["code"] == "600519"
        assert priced[0]["pricing_rank"] == 1

        assert len(missing) == 1
        assert missing[0]["code"] == "000858"
        assert missing[0]["total"] is None
        assert missing[0]["sp_ratio"] is None
        assert missing[0]["sp_score"] is None
        assert missing[0]["cmf"] is None
        assert missing[0]["flow_score"] is None
        assert missing[0]["pricing_rank"] is None
        assert missing[0]["status"] == "missing_pricing"

    def test_pricing_repo_throws_per_board(self):
        def _side_effect(board_id, trade_date):
            if board_id == BOARD_A:
                return [_pricing_row("600519", 0.78)]
            raise RuntimeError("db down")

        screener, _, pricing_repo = _make_screener(
            spi_signals=[
                _buy_signal(BOARD_A, "600519"),
                _buy_signal(BOARD_B, "000001"),
            ],
            v2_boards=[
                _v2_board(BOARD_A, "食品饮料", 85.3),
                _v2_board(BOARD_B, "银行", 72.1),
            ],
            pricing_rows=_side_effect,
        )

        result = screener.screen(TRADE_DATE)

        assert result["rotation_boards"] == 2
        # board A has pricing row, board B falls back to missing_pricing
        candidates = result["candidates"]
        priced = [c for c in candidates if c.get("pricing_rank") is not None]
        missing = [c for c in candidates if c["status"] == "missing_pricing"]

        assert len(priced) == 1
        assert priced[0]["code"] == "600519"
        assert len(missing) == 1
        assert missing[0]["code"] == "000001"


class TestScreenWithPricing:

    def test_top_5_per_board_limit(self):
        signals = [_buy_signal(BOARD_A, f"60051{i}") for i in range(9)]
        pricing = [_pricing_row(f"60051{i}", 0.9 - i * 0.01) for i in range(9)]

        screener, _, _ = _make_screener(
            spi_signals=signals,
            v2_boards=[_v2_board(BOARD_A, "食品饮料", 85.3)],
            pricing_rows=pricing,
        )

        result = screener.screen(TRADE_DATE)

        assert len(result["candidates"]) == 5  # _TOP_STOCKS_PER_BOARD
        codes = [c["code"] for c in result["candidates"]]
        assert codes == [f"60051{i}" for i in range(5)]
        assert [c["pricing_rank"] for c in result["candidates"]] == [1, 2, 3, 4, 5]

    def test_respects_max_results(self):
        signals = [_buy_signal(BOARD_A, f"60051{i}") for i in range(4)]
        pricing = [_pricing_row(f"60051{i}", 0.9 - i * 0.01) for i in range(4)]

        screener, _, _ = _make_screener(
            spi_signals=signals,
            v2_boards=[_v2_board(BOARD_A, "食品饮料", 85.3)],
            pricing_rows=pricing,
        )

        result = screener.screen(TRADE_DATE, max_results=2)

        assert len(result["candidates"]) == 2

    def test_sorts_by_board_v2_then_total_desc(self):
        screener, _, _ = _make_screener(
            spi_signals=[
                _buy_signal(BOARD_A, "600519"),
                _buy_signal(BOARD_B, "000001"),
            ],
            v2_boards=[
                _v2_board(BOARD_A, "食品饮料", 85.3),
                _v2_board(BOARD_B, "银行", 72.1),
            ],
            pricing_rows={
                BOARD_A: [_pricing_row("600519", 0.78)],
                BOARD_B: [_pricing_row("000001", 0.90)],
            },
        )

        def _board_pricing(board_id, trade_date):
            mapping = {
                BOARD_A: [_pricing_row("600519", 0.78)],
                BOARD_B: [_pricing_row("000001", 0.90)],
            }
            return mapping.get(board_id, [])

        screener._pricing_repo.find_board_pricing_rank.side_effect = _board_pricing

        result = screener.screen(TRADE_DATE)

        assert len(result["candidates"]) == 2
        # BOARD_A has higher v2 (85.3) than BOARD_B (72.1), even though 000001 total (0.90) > 600519 (0.78)
        assert result["candidates"][0]["code"] == "600519"  # from higher-v2 board
        assert result["candidates"][1]["code"] == "000001"

    def test_same_board_sorts_by_total_desc(self):
        screener, _, _ = _make_screener(
            spi_signals=[
                _buy_signal(BOARD_A, "600519"),
                _buy_signal(BOARD_A, "000858"),
            ],
            v2_boards=[_v2_board(BOARD_A, "食品饮料", 85.3)],
            pricing_rows=[
                _pricing_row("000858", 0.82),
                _pricing_row("600519", 0.78),
            ],
        )

        result = screener.screen(TRADE_DATE)

        assert result["candidates"][0]["code"] == "000858"  # higher total
        assert result["candidates"][0]["total"] == 0.82
        assert result["candidates"][1]["code"] == "600519"


class TestScreenEdgeCases:

    def test_v2_query_fails_gracefully(self):
        screener, spi_repo, _ = _make_screener(
            spi_signals=[_buy_signal(BOARD_A, "600519")],
            pricing_rows=[_pricing_row("600519", 0.78)],
        )
        spi_repo.find_top_boards_v2.side_effect = RuntimeError("db down")

        result = screener.screen(TRADE_DATE)

        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["board_name"] is None
        assert result["candidates"][0]["board_v2_score"] is None
        assert "板块v2评分查询失败" in result["warnings"]
        assert result["rotation_boards"] == 1

    def test_max_results_larger_than_candidates(self):
        screener, _, _ = _make_screener(
            spi_signals=[_buy_signal(BOARD_A, "600519")],
            v2_boards=[_v2_board(BOARD_A, "食品饮料", 85.3)],
            pricing_rows=[_pricing_row("600519", 0.78)],
        )

        result = screener.screen(TRADE_DATE, max_results=100)

        assert len(result["candidates"]) == 1  # no padding
