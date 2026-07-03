# -*- coding: utf-8 -*-
from unittest.mock import patch

import pandas as pd
import pytest

from src.services.spi.akshare_sw_adapter import AkshareSwAdapter


def _make_sw_first_info_df():
    return pd.DataFrame([
        {"行业代码": "801010.SI", "行业名称": "农林牧渔", "成份个数": 104,
         "静态市盈率": 25.1, "TTM(滚动)市盈率": 24.3, "市净率": 3.2, "静态股息率": 1.5},
        {"行业代码": "801020.SI", "行业名称": "采掘", "成份个数": 60,
         "静态市盈率": 12.5, "TTM(滚动)市盈率": 11.8, "市净率": 1.8, "静态股息率": 3.2},
        {"行业代码": "801030.SI", "行业名称": "化工", "成份个数": 200,
         "静态市盈率": 18.9, "TTM(滚动)市盈率": 17.5, "市净率": 2.5, "静态股息率": 2.0},
    ])


def _make_index_hist_df(rows=5):
    data = []
    for i in range(rows):
        data.append({
            "代码": "801010",
            "日期": pd.Timestamp(f"2026-07-{i+1:02d}"),
            "收盘": 2300.0 + i * 10,
            "开盘": 2295.0 + i * 10,
            "最高": 2310.0 + i * 10,
            "最低": 2290.0 + i * 10,
            "成交量": 1000000 + i * 10000,
            "成交额": 5000000000 + i * 10000000,
        })
    return pd.DataFrame(data)


# ------------------------------------------------------------------
# get_sw_first_levels
# ------------------------------------------------------------------


def test_get_sw_first_levels_field_mapping():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "sw_index_first_info") as mock_fn:
        mock_fn.return_value = _make_sw_first_info_df()
        result = adapter.get_sw_first_levels()
        assert len(result) == 3
        assert result[0] == {
            "board_id": "801010", "board_name": "农林牧渔", "stock_count": 104
        }
        assert result[1]["board_id"] == "801020"
        assert result[2]["board_id"] == "801030"
        mock_fn.assert_called_once()


def test_get_sw_first_levels_strips_dot_si():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "sw_index_first_info") as mock_fn:
        mock_fn.return_value = _make_sw_first_info_df()
        result = adapter.get_sw_first_levels()
        for item in result:
            assert ".SI" not in item["board_id"]


def test_get_sw_first_levels_empty_df():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "sw_index_first_info") as mock_fn:
        mock_fn.return_value = pd.DataFrame()
        assert adapter.get_sw_first_levels() == []


def test_get_sw_first_levels_none_df():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "sw_index_first_info") as mock_fn:
        mock_fn.return_value = None
        assert adapter.get_sw_first_levels() == []


# ------------------------------------------------------------------
# get_index_kline
# ------------------------------------------------------------------


def test_get_index_kline_field_mapping():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "index_hist_sw") as mock_fn:
        mock_fn.return_value = _make_index_hist_df(3)
        result = adapter.get_index_kline("801010", back_count=5)
        assert len(result) == 3
        expected_date = "2026-07-01"
        assert result[0] == {
            "date": expected_date,
            "close": 2300.0,
            "open": 2295.0,
            "high": 2310.0,
            "low": 2290.0,
        }
        mock_fn.assert_called_once_with(symbol="801010", period="day")


def test_get_index_kline_respects_back_count():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "index_hist_sw") as mock_fn:
        mock_fn.return_value = _make_index_hist_df(10)
        result = adapter.get_index_kline("801010", back_count=5)
        assert len(result) == 5


def test_get_index_kline_ascending_date_order():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "index_hist_sw") as mock_fn:
        mock_fn.return_value = _make_index_hist_df(5)
        result = adapter.get_index_kline("801010", back_count=5)
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)


def test_get_index_kline_empty_df():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "index_hist_sw") as mock_fn:
        mock_fn.return_value = pd.DataFrame()
        assert adapter.get_index_kline("801010") == []


def test_get_index_kline_none_df():
    adapter = AkshareSwAdapter()
    with patch.object(adapter._ak, "index_hist_sw") as mock_fn:
        mock_fn.return_value = None
        assert adapter.get_index_kline("801010") == []


# ------------------------------------------------------------------
# import error
# ------------------------------------------------------------------


def test_import_error_on_missing_akshare():
    with patch.dict("sys.modules", {"akshare": None}):
        with pytest.raises(ImportError, match="akshare is required"):
            AkshareSwAdapter()
