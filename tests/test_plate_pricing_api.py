# -*- coding: utf-8 -*-
"""Unit tests for the plate pricing endpoint module."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

for _mod in ("dotenv", "fake_useragent"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"].dotenv_values = lambda *a, **kw: {}


def _load_plate_pricing_module():
    api_pkg = sys.modules.setdefault("api", ModuleType("api"))
    v1_pkg = sys.modules.setdefault("api.v1", ModuleType("api.v1"))
    errors_mod = ModuleType("api.v1.errors")

    def api_error(status_code: int, error: str, message: str, *, detail=None) -> HTTPException:
        body = {"error": error, "message": message}
        if detail is not None:
            body["detail"] = detail
        return HTTPException(status_code=status_code, detail=body)

    errors_mod.api_error = api_error
    sys.modules["api.v1.errors"] = errors_mod
    api_pkg.v1 = v1_pkg
    v1_pkg.errors = errors_mod

    module_name = "test_plate_pricing_module"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path("api/v1/endpoints/plate_pricing.py"),
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_router_registers_board_pricing_path():
    module = _load_plate_pricing_module()
    paths = {route.path for route in module.router.routes}
    assert "/board/{board_id}/pricing" in paths


def test_get_board_pricing_uses_latest_trade_date_by_default():
    module = _load_plate_pricing_module()
    repo = MagicMock()
    repo.find_latest_trade_date.return_value = date(2026, 7, 3)
    repo.find_board_pricing_rank.return_value = [
        SimpleNamespace(
            stock_code="600519",
            total=0.92,
            rs_score=0.95,
            cmf=0.12,
            flow_score=0.88,
            status="ok",
            factor_mask="rs,cmf,flow",
        )
    ]
    module.PricingRepository = MagicMock(return_value=repo)

    body = module.get_board_pricing(board_id=801010, trade_date=None)

    assert body == {
        "board_id": 801010,
        "trade_date": "2026-07-03",
        "count": 1,
        "items": [
            {
                "stock_code": "600519",
                "total": 0.92,
                "rs_score": 0.95,
                "cmf": 0.12,
                "flow_score": 0.88,
                "status": "ok",
                "factor_mask": "rs,cmf,flow",
            }
        ],
    }
    repo.find_latest_trade_date.assert_called_once_with(board_id=801010)
    repo.find_board_pricing_rank.assert_called_once_with(
        board_id=801010,
        trade_date=date(2026, 7, 3),
    )


def test_get_board_pricing_honors_explicit_trade_date():
    module = _load_plate_pricing_module()
    repo = MagicMock()
    repo.find_board_pricing_rank.return_value = []
    module.PricingRepository = MagicMock(return_value=repo)

    body = module.get_board_pricing(board_id=801010, trade_date=date(2026, 7, 2))

    assert body == {
        "board_id": 801010,
        "trade_date": "2026-07-02",
        "count": 0,
        "items": [],
    }
    repo.find_latest_trade_date.assert_not_called()
    repo.find_board_pricing_rank.assert_called_once_with(
        board_id=801010,
        trade_date=date(2026, 7, 2),
    )


def test_get_board_pricing_raises_404_when_no_data():
    module = _load_plate_pricing_module()
    repo = MagicMock()
    repo.find_latest_trade_date.return_value = None
    module.PricingRepository = MagicMock(return_value=repo)

    with pytest.raises(HTTPException) as exc_info:
        module.get_board_pricing(board_id=801010, trade_date=None)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == {
        "error": "pricing_no_data",
        "message": "No pricing data found for board 801010",
    }
    repo.find_board_pricing_rank.assert_not_called()
