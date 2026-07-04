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

from src.services.spi.data_hydrator import SpiDataHydrator
from src.services.spi.spi_time import spi_time


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronously hydrate SPI v1/v2, rotation, and pricing data.",
    )
    parser.add_argument(
        "--date",
        dest="trade_date",
        type=date.fromisoformat,
        help="Trade date to hydrate, default is spi_time().",
    )
    parser.add_argument(
        "--pricing-top-n",
        type=int,
        default=30,
        help="Top N v2 boards to run pricing for.",
    )
    parser.add_argument(
        "--allow-historical-derived-data",
        action="store_true",
        help="Allow rotation/pricing hydration for historical dates using derived constituent data.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    trade_date = args.trade_date or spi_time()
    payload = SpiDataHydrator().hydrate_trade_date(
        trade_date,
        pricing_top_n=max(1, args.pricing_top_n),
        allow_historical_derived_data=bool(args.allow_historical_derived_data),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
