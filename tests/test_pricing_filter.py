# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from src.services.spi.pricing_filter import PricingFilter

TRADE_DATE = date(2026, 7, 4)
BOARD_A = 801010


def _pricing_row(stock_code, total, **kwargs):
    row = MagicMock()
    row.stock_code = stock_code
    row.total = total
    row.sp_ratio = kwargs.get("sp_ratio", 0.9)
    row.sp_score = kwargs.get("sp_score", 0.55)
    row.cmf = kwargs.get("cmf", 0.2)
    row.flow_score = kwargs.get("flow_score", 0.6)
    row.status = kwargs.get("status", "ok")
    row.factor_mask = kwargs.get("factor_mask", "sp,cmf,flow")
    return row


def _make_filter(pricing_rows):
    pricing_repo = MagicMock()
    pricing_repo.find_board_pricing_rank.return_value = pricing_rows
    return PricingFilter(pricing_repo=pricing_repo), pricing_repo


def test_apply_filter_basic():
    pricing_filter, pricing_repo = _make_filter(
        [
            _pricing_row("600519", 0.82, sp_ratio=0.8),
            _pricing_row("000858", 0.66, sp_ratio=0.95),
        ]
    )
    candidates = [
        {"code": "600519", "board_id": BOARD_A, "name": "贵州茅台"},
        {"code": "000858", "board_id": BOARD_A, "name": "五粮液"},
    ]

    result = pricing_filter.apply(candidates, trade_date=TRADE_DATE)

    assert [item["code"] for item in result] == ["600519", "000858"]
    assert result[0]["pricing_rank"] == 1
    assert result[0]["total"] == 0.82
    assert result[0]["sp_ratio"] == 0.8
    assert result[1]["pricing_rank"] == 2
    pricing_repo.find_board_pricing_rank.assert_called_once_with(
        board_id=BOARD_A,
        trade_date=TRADE_DATE,
    )


def test_apply_filter_threshold():
    pricing_filter, _ = _make_filter(
        [
            _pricing_row("600519", 0.82),
            _pricing_row("000858", 0.41),
        ]
    )
    candidates = [
        {"code": "600519", "board_id": BOARD_A},
        {"code": "000858", "board_id": BOARD_A},
    ]

    result = pricing_filter.apply(candidates, trade_date=TRADE_DATE, min_total_score=0.5)

    assert [item["code"] for item in result] == ["600519"]
    assert result[0]["pricing_rank"] == 1


def test_apply_missing_pricing():
    pricing_filter, _ = _make_filter([_pricing_row("600519", 0.82)])
    candidates = [
        {"code": "600519", "board_id": BOARD_A},
        {"code": "000858", "board_id": BOARD_A, "selection_reason": "raw"},
    ]

    result = pricing_filter.apply(candidates, trade_date=TRADE_DATE)

    assert [item["code"] for item in result] == ["600519", "000858"]
    assert result[0]["pricing_rank"] == 1
    assert result[1]["code"] == "000858"
    assert result[1]["selection_reason"] == "raw"
    assert "pricing_rank" not in result[1]


def test_apply_uses_spi_time_when_trade_date_missing():
    pricing_filter, pricing_repo = _make_filter([_pricing_row("600519", 0.88)])
    candidates = [{"code": "600519", "board_id": BOARD_A}]

    with patch("src.services.spi.pricing_filter.spi_time", return_value=TRADE_DATE) as spi_time_mock:
        result = pricing_filter.apply(candidates)

    assert result[0]["code"] == "600519"
    spi_time_mock.assert_called_once_with()
    pricing_repo.find_board_pricing_rank.assert_called_once_with(
        board_id=BOARD_A,
        trade_date=TRADE_DATE,
    )
