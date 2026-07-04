# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging
from datetime import date
from pathlib import Path
from typing import List, Optional, Set

import pandas as pd
import yaml

from src.repositories.plate_spi_repo import PlateSpiRepository
from src.services.spi.spi_time import spi_time
from src.utils.constituents_snapshot import (
    ConstituentFetcher,
    ConstituentRuntimeResolver,
    ConstituentSnapshotRepo,
)

logger = logging.getLogger(__name__)
_ROTATION_CONFIG_PATH = Path(__file__).resolve().parents[3] / "strategies" / "rotation_entry.yaml"


@dataclass(frozen=True)
class RotationStrategyConfig:
    watchpool_top_n: int = 30
    entry_ema_period: int = 20
    volume_ratio_threshold: float = 1.2
    pullback_tolerance: float = 0.02
    exit_top_m: int = 50
    exit_ema_period: int = 5


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _nonnegative_float(value, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


@lru_cache(maxsize=1)
def load_rotation_strategy_config() -> RotationStrategyConfig:
    defaults = RotationStrategyConfig()
    try:
        with _ROTATION_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        logger.warning("rotation strategy config not found: %s", _ROTATION_CONFIG_PATH)
        return defaults
    except Exception:
        logger.warning("failed to load rotation strategy config", exc_info=True)
        return defaults

    entry = payload.get("entry") or {}
    exit_cfg = payload.get("exit") or {}
    watchpool = payload.get("watchpool") or {}
    return RotationStrategyConfig(
        watchpool_top_n=_positive_int(watchpool.get("top_n"), defaults.watchpool_top_n),
        entry_ema_period=_positive_int(entry.get("ema_period"), defaults.entry_ema_period),
        volume_ratio_threshold=_nonnegative_float(
            entry.get("volume_ratio_threshold"),
            defaults.volume_ratio_threshold,
        ),
        pullback_tolerance=_nonnegative_float(
            entry.get("pullback_tolerance"),
            defaults.pullback_tolerance,
        ),
        exit_top_m=_positive_int(exit_cfg.get("top_m"), defaults.exit_top_m),
        exit_ema_period=_positive_int(exit_cfg.get("ema_period"), defaults.exit_ema_period),
    )


class RotationService:

    def __init__(
        self,
        repo=None,
        constituent_repo=None,
        fetcher=None,
        strategy_config=None,
        db_manager=None,
    ):
        self._repo: PlateSpiRepository = repo or PlateSpiRepository()
        self._constituent_repo: ConstituentSnapshotRepo = (
            constituent_repo or ConstituentSnapshotRepo()
        )
        self._fetcher = fetcher or ConstituentFetcher()
        self._strategy_config = strategy_config or load_rotation_strategy_config()
        self._watchpool: Set[int] = set()

    def update_watchpool(self, trade_date: date, top_n: Optional[int] = None) -> Set[int]:
        top_n = top_n or self._strategy_config.watchpool_top_n
        boards = self._repo.find_top_boards_v2(anchor_date=trade_date, top_n=top_n)
        self._watchpool = {item["board_id"] for item in boards}
        return self._watchpool

    def generate_signals(self, trade_date: date) -> dict:
        strategy = self._strategy_config
        watchpool = self.update_watchpool(
            trade_date,
            top_n=strategy.watchpool_top_n,
        )
        buy_signal_count = 0
        skipped_snapshot_boards = 0

        for board_id in watchpool:
            stock_codes = self._get_snapshot_constituents(board_id, trade_date)
            if not stock_codes:
                skipped_snapshot_boards += 1
                continue
            buy_signal_count += len(
                self.check_entry(
                    board_id,
                    trade_date,
                    entry_ema_period=strategy.entry_ema_period,
                    volume_ratio_threshold=strategy.volume_ratio_threshold,
                    pullback_tolerance=strategy.pullback_tolerance,
                    stock_codes=stock_codes,
                )
            )

        active_positions = self._repo.find_active_rotation_positions(before_date=trade_date)
        board_ids_in_top_m = set()
        if active_positions:
            board_ids_in_top_m = {
                item["board_id"]
                for item in self._repo.find_top_boards_v2(
                    anchor_date=trade_date,
                    top_n=strategy.exit_top_m,
                )
            }

        sell_signal_count = 0
        for board_id, stock_codes in active_positions.items():
            sell_signal_count += len(
                self.check_exit(
                    board_id,
                    trade_date,
                    top_m=strategy.exit_top_m,
                    exit_ema_period=strategy.exit_ema_period,
                    stock_codes=stock_codes,
                    board_ids_in_top=board_ids_in_top_m,
                )
            )

        return {
            "watchpool_size": len(watchpool),
            "active_position_boards": len(active_positions),
            "buy_signals": buy_signal_count,
            "sell_signals": sell_signal_count,
            "skipped_snapshot_boards": skipped_snapshot_boards,
        }

    def check_entry(
        self,
        board_id: int,
        trade_date: date,
        entry_ema_period: int = 20,
        volume_ratio_threshold: float = 1.2,
        pullback_tolerance: float = 0.02,
        stock_codes: Optional[List[str]] = None,
    ) -> List[str]:
        codes = stock_codes if stock_codes is not None else self._constituent_repo.get_constituents(
            board_id,
            trade_date,
        )
        if not codes:
            return []

        triggered: List[str] = []
        for stock_code in codes:
            try:
                rows = _get_latest(stock_code, entry_ema_period + 5)
                if not rows:
                    continue
                rows_asc = list(reversed(rows))
                closes = [row.close for row in rows_asc if row.close is not None]
                volumes = [row.volume for row in rows_asc if row.volume is not None]
                if len(closes) < 2:
                    continue

                ema_val = pd.Series(closes).ewm(span=entry_ema_period, adjust=False).mean().iloc[-1]
                last_close = closes[-1]

                if len(volumes) >= entry_ema_period:
                    vol_mean = sum(volumes[-entry_ema_period:]) / entry_ema_period
                    volume_ratio = (volumes[-1] / vol_mean) if vol_mean > 0 else 0.0
                else:
                    volume_ratio = 0.0

                if last_close <= ema_val * (1 + pullback_tolerance) and volume_ratio >= volume_ratio_threshold:
                    self._repo.upsert_rotation_signal(
                        board_id=board_id,
                        stock_code=stock_code,
                        trade_date=trade_date,
                        action="BUY",
                        reason=f"pullback to EMA{entry_ema_period}, vol_ratio={volume_ratio:.2f}",
                    )
                    triggered.append(stock_code)
            except Exception:
                logger.warning(
                    "check_entry failed for board=%s stock=%s",
                    board_id,
                    stock_code,
                    exc_info=True,
                )

        return triggered

    def check_exit(
        self,
        board_id: int,
        trade_date: date,
        top_m: int = 50,
        exit_ema_period: int = 5,
        stock_codes: Optional[List[str]] = None,
        board_ids_in_top: Optional[Set[int]] = None,
    ) -> List[str]:
        if board_ids_in_top is None:
            top_boards = self._repo.find_top_boards_v2(anchor_date=trade_date, top_n=top_m)
            board_ids_in_top = {item["board_id"] for item in top_boards}

        codes = stock_codes if stock_codes is not None else self._constituent_repo.get_constituents(
            board_id,
            trade_date,
        )
        triggered: List[str] = []
        if not codes:
            return triggered

        if board_id not in board_ids_in_top:
            for stock_code in codes:
                try:
                    self._repo.upsert_rotation_signal(
                        board_id=board_id,
                        stock_code=stock_code,
                        trade_date=trade_date,
                        action="SELL",
                        reason=f"board_rank_out_of_top_{top_m}",
                    )
                    triggered.append(stock_code)
                except Exception:
                    logger.warning(
                        "check_exit sell signal failed board=%s stock=%s",
                        board_id,
                        stock_code,
                        exc_info=True,
                    )
            return triggered

        for stock_code in codes:
            try:
                rows = _get_latest(stock_code, exit_ema_period + 5)
                if not rows:
                    continue
                rows_asc = list(reversed(rows))
                closes = [row.close for row in rows_asc if row.close is not None]
                if len(closes) < 2:
                    continue

                ema_val = pd.Series(closes).ewm(span=exit_ema_period, adjust=False).mean().iloc[-1]
                last_close = closes[-1]

                if last_close < ema_val:
                    self._repo.upsert_rotation_signal(
                        board_id=board_id,
                        stock_code=stock_code,
                        trade_date=trade_date,
                        action="SELL",
                        reason=f"close_below_EMA{exit_ema_period}",
                    )
                    triggered.append(stock_code)
            except Exception:
                logger.warning(
                    "check_exit ema failed board=%s stock=%s",
                    board_id,
                    stock_code,
                    exc_info=True,
                )

        return triggered

    def _get_snapshot_constituents(self, board_id: int, trade_date: date) -> List[str]:
        if isinstance(self._constituent_repo, ConstituentSnapshotRepo) and isinstance(self._fetcher, ConstituentFetcher):
            resolver = ConstituentRuntimeResolver(
                repo=self._constituent_repo,
                fetcher=self._fetcher,
                current_trade_date_provider=spi_time,
            )
            codes, _source, snapshot = resolver.resolve(board_id, trade_date)
            if snapshot is not None and snapshot.is_stale:
                logger.info(
                    "rotation using stale constituent snapshot board_id=%s trade_date=%s "
                    "origin_trade_date=%s snapshot_age_days=%s",
                    board_id,
                    trade_date,
                    snapshot.origin_trade_date,
                    snapshot.snapshot_age_days,
                )
            return codes

        existing_codes = self._constituent_repo.get_constituents(board_id, trade_date)
        if existing_codes:
            return existing_codes

        fetched_codes = self._fetcher.fetch(board_id)
        if not fetched_codes:
            logger.warning(
                "constituents snapshot unavailable board=%s trade_date=%s",
                board_id,
                trade_date,
            )
            return []

        self._constituent_repo.save_constituents(board_id, trade_date, fetched_codes)
        return fetched_codes


def _get_latest(stock_code: str, days: int):
    from src.repositories.stock_repo import StockRepository
    return StockRepository().get_latest(stock_code, days)
