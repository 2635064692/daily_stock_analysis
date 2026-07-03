# -*- coding: utf-8 -*-
"""API smoke tests for plate SPI v2 rankings and rotation signals endpoints."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import fastapi.testclient

from api.app import create_app


class PlateSpiApiSmokeTestCase(unittest.TestCase):

    def test_v2_rankings_returns_board_series(self):
        repo = MagicMock()
        repo.find_top_boards_v2.return_value = [
            {
                "board_id": 801010,
                "board_name": "基础化工",
                "spi": 7,
                "v2_score": 88.5,
            }
        ]
        repo.find_board_series.return_value = [
            SimpleNamespace(trade_date=date(2026, 7, 3), spi=7),
            SimpleNamespace(trade_date=date(2026, 7, 2), spi=6),
        ]

        with (
            patch("api.v1.endpoints.plate_spi.PlateSpiRepository", return_value=repo),
            tempfile.TemporaryDirectory() as temp_dir,
        ):
            client = fastapi.testclient.TestClient(create_app(static_dir=Path(temp_dir)))
            response = client.get(
                "/api/v1/plate-spi/v2/rankings",
                params={"date": "2026-07-03", "top_n": 1, "days": 2},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["date"], "2026-07-03")
        self.assertEqual(len(body["boards"]), 1)
        board = body["boards"][0]
        self.assertEqual(board["board_id"], 801010)
        self.assertEqual(board["board_name"], "基础化工")
        self.assertEqual(board["v2_score"], 88.5)
        self.assertEqual(board["series"][0], {"date": "2026-07-03", "spi": 7})
        repo.find_top_boards_v2.assert_called_once_with(anchor_date=date(2026, 7, 3), top_n=1)
        repo.find_board_series.assert_called_once_with(board_id=801010, days=2)

    def test_rotation_signals_returns_signal_payload(self):
        repo = MagicMock()
        repo.find_rotation_signals.return_value = [
            {
                "board_id": 801010,
                "stock_code": "600519",
                "trade_date": date(2026, 7, 3),
                "action": "BUY",
                "reason": "pullback to EMA20, vol_ratio=1.50",
            }
        ]

        with (
            patch("api.v1.endpoints.plate_spi.PlateSpiRepository", return_value=repo),
            tempfile.TemporaryDirectory() as temp_dir,
        ):
            client = fastapi.testclient.TestClient(create_app(static_dir=Path(temp_dir)))
            response = client.get(
                "/api/v1/plate-spi/rotation/signals",
                params={"date": "2026-07-03", "board_id": 801010},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["date"], "2026-07-03")
        self.assertEqual(
            body["signals"],
            [
                {
                    "board_id": 801010,
                    "stock_code": "600519",
                    "trade_date": "2026-07-03",
                    "action": "BUY",
                    "reason": "pullback to EMA20, vol_ratio=1.50",
                }
            ],
        )
        repo.find_rotation_signals.assert_called_once_with(
            trade_date=date(2026, 7, 3),
            board_id=801010,
        )


if __name__ == "__main__":
    unittest.main()
