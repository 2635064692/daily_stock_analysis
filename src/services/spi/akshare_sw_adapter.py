# -*- coding: utf-8 -*-
"""SPI-private akshare Shenwan adapter — no BaseFetcher inheritance, no DataFetcherManager."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


class AkshareSwAdapter:
    """SPI-private adapter for akshare Shenwan first-level industry indices.

    Does NOT inherit BaseFetcher, does NOT register in DataFetcherManager.
    Held directly by PlateSpiService.
    board_id = 6-digit code without .SI suffix (e.g. "801010").
    """

    def __init__(self):
        try:
            import akshare as ak  # noqa: F401
            self._ak = ak
        except ImportError as e:
            raise ImportError(
                "akshare is required for AkshareSwAdapter. "
                "Install: pip install akshare"
            ) from e

    def get_sw_first_levels(self) -> list[dict[str, Any]]:
        df = self._ak.sw_index_first_info()
        if df is None or df.empty:
            return []
        return [
            {
                "board_id": str(row["行业代码"]).replace(".SI", ""),
                "board_name": str(row["行业名称"]),
                "stock_count": int(row["成份个数"]),
            }
            for _, row in df.iterrows()
        ]

    def get_index_kline(
        self,
        board_id: str,
        back_count: int = 300,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        df = self._ak.index_hist_sw(symbol=board_id, period="day")
        if df is None or df.empty:
            return []
        df = df.copy()
        df["日期"] = pd.to_datetime(df["日期"]).dt.date
        if end_date is not None:
            df = df[df["日期"] <= end_date]
            if df.empty:
                return []
        df = df.tail(back_count)
        return [
            {
                "date": row["日期"].isoformat(),
                "close": float(row["收盘"]),
                "open": float(row["开盘"]),
                "high": float(row["最高"]),
                "low": float(row["最低"]),
            }
            for _, row in df.iterrows()
        ]
