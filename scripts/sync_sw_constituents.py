#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.spi.constituent_sync_service import ShenwanConstituentSyncService
from src.services.spi.spi_time import spi_time


def _board_ids(value: str) -> list[int]:
    items = [item.strip() for item in value.split(",")]
    board_ids = [int(item) for item in items if item]
    if not board_ids:
        raise argparse.ArgumentTypeError("board ids must not be empty")
    return board_ids


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronously hydrate Shenwan constituent snapshots.",
    )
    parser.add_argument(
        "--date",
        dest="trade_date",
        type=date.fromisoformat,
        help="Trade date to sync, default is spi_time().",
    )
    parser.add_argument(
        "--board-ids",
        type=_board_ids,
        help="Comma-separated Shenwan level-1 board ids to sync.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=30.0,
        help="Minimum interval between requested boards.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force refetch even when the target trade date already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    trade_date = args.trade_date or spi_time()
    payload = ShenwanConstituentSyncService().sync_trade_date(
        trade_date,
        board_ids=args.board_ids,
        interval_seconds=max(0.0, float(args.interval_seconds)),
        force=bool(args.force),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
