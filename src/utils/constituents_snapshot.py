# -*- coding: utf-8 -*-
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from datetime import date
from io import StringIO
from typing import Callable, Optional

import pandas as pd
import requests
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

from src.storage import ConstituentSnapshot, DatabaseManager
from src.services.spi.spi_time import iter_trading_dates, spi_time

logger = logging.getLogger(__name__)

DEFAULT_MAX_STALE_TRADING_DAYS = 22
DEFAULT_REFETCH_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True)
class ConstituentSnapshotState:
    board_id: int
    trade_date: date
    stock_codes: list[str]
    origin_trade_date: date
    is_stale: bool
    snapshot_age_days: int


class ConstituentFetcher:
    BASE_URL = "https://legulegu.com/stockdata/index-composition"
    REFETCH_INTERVAL_SECONDS = DEFAULT_REFETCH_INTERVAL_SECONDS
    REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://legulegu.com/",
        "Upgrade-Insecure-Requests": "1",
    }
    _last_fetch_started_at: dict[int, float] = {}

    def fetch(self, board_id: int) -> list:
        """Fetch current constituent stock codes for a Shenwan L1 industry.
        Returns list of stock code strings (e.g. ["600519", "000858"]).
        Returns [] on any failure (network error, 429, parse error).
        """
        if self._is_refetch_throttled(board_id):
            return []

        url = f"{self.BASE_URL}?industryCode={board_id}.SI"
        try:
            resp = requests.get(url, headers=self.REQUEST_HEADERS, timeout=15)
            time.sleep(0.5)
            if resp.status_code != 200:
                logger.warning("legulegu fetch failed board_id=%s status=%s", board_id, resp.status_code)
                return []
            tables = pd.read_html(StringIO(resp.text), flavor="lxml")
            if not tables:
                logger.warning("legulegu no table found board_id=%s", board_id)
                return []
            df = tables[0]
            if "股票代码" not in df.columns:
                logger.warning("legulegu missing '股票代码' column board_id=%s cols=%s", board_id, list(df.columns))
                return []
            codes = []
            for raw_code in df["股票代码"].dropna().tolist():
                code = _normalize_stock_code(raw_code)
                if code:
                    codes.append(code)
            return codes
        except Exception as exc:
            logger.warning("legulegu fetch error board_id=%s: %s", board_id, exc)
            return []

    @classmethod
    def reset_runtime_state(cls) -> None:
        cls._last_fetch_started_at.clear()

    @classmethod
    def defer_refetch(cls, board_id: int) -> None:
        cls._last_fetch_started_at[board_id] = time.monotonic()

    def _is_refetch_throttled(self, board_id: int) -> bool:
        now = time.monotonic()
        last_started = self._last_fetch_started_at.get(board_id)
        if last_started is not None and now - last_started < self.REFETCH_INTERVAL_SECONDS:
            logger.info(
                "legulegu fetch throttled board_id=%s wait_remaining=%.1fs",
                board_id,
                self.REFETCH_INTERVAL_SECONDS - (now - last_started),
            )
            return True
        self._last_fetch_started_at[board_id] = now
        return False


def _normalize_stock_code(raw_code) -> str:
    text = str(raw_code or "").strip().upper()
    if not text:
        return ""
    code = text.split(".", 1)[0]
    return code.zfill(6)


class ConstituentSnapshotRepo:

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        *,
        mirror_db_path: Optional[str] = None,
    ):
        self.db = db_manager or DatabaseManager.get_instance()
        self._mirror_db_path = _resolve_constituent_snapshot_mirror_path(
            mirror_db_path or os.getenv("CONSTITUENT_SNAPSHOT_DATABASE_PATH")
        )

    def save_constituents(
        self,
        board_id: int,
        trade_date: date,
        stock_codes: list,
        *,
        origin_trade_date: Optional[date] = None,
        is_stale: bool = False,
        snapshot_age_days: int = 0,
    ) -> None:
        """Upsert constituents snapshot with stale-source metadata."""
        with self.db.get_session() as session:
            try:
                existing = session.execute(
                    select(ConstituentSnapshot).where(
                        and_(
                            ConstituentSnapshot.board_id == board_id,
                            ConstituentSnapshot.trade_date == trade_date,
                        )
                    )
                ).scalar_one_or_none()
                resolved_origin_trade_date = origin_trade_date or trade_date
                if existing is None:
                    session.add(ConstituentSnapshot(
                        board_id=board_id,
                        trade_date=trade_date,
                        stock_codes_json=json.dumps(stock_codes),
                        origin_trade_date=resolved_origin_trade_date,
                        is_stale=is_stale,
                        snapshot_age_days=snapshot_age_days,
                    ))
                else:
                    existing.stock_codes_json = json.dumps(stock_codes)
                    existing.origin_trade_date = resolved_origin_trade_date
                    existing.is_stale = is_stale
                    existing.snapshot_age_days = snapshot_age_days
                session.commit()
            except IntegrityError:
                session.rollback()

    def get_constituents(self, board_id: int, trade_date: date) -> list:
        """Returns stock code list for the given board+date. Returns [] if not found."""
        with self.db.get_session() as session:
            row = session.execute(
                select(ConstituentSnapshot).where(
                    and_(
                        ConstituentSnapshot.board_id == board_id,
                        ConstituentSnapshot.trade_date == trade_date,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return []
            return json.loads(row.stock_codes_json)

    def get_snapshot_state(self, board_id: int, trade_date: date) -> Optional[ConstituentSnapshotState]:
        with self.db.get_session() as session:
            row = session.execute(
                select(ConstituentSnapshot).where(
                    and_(
                        ConstituentSnapshot.board_id == board_id,
                        ConstituentSnapshot.trade_date == trade_date,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return self._read_snapshot_state_from_mirror(board_id, trade_date)
            return _snapshot_state_from_row(row)

    def get_latest_snapshot_state(
        self,
        board_id: int,
        trade_date: date,
    ) -> Optional[ConstituentSnapshotState]:
        with self.db.get_session() as session:
            row = session.execute(
                select(ConstituentSnapshot)
                .where(
                    and_(
                        ConstituentSnapshot.board_id == board_id,
                        ConstituentSnapshot.trade_date <= trade_date,
                    )
                )
                .order_by(ConstituentSnapshot.trade_date.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return self._read_latest_snapshot_state_from_mirror(board_id, trade_date)
            return _snapshot_state_from_row(row)

    def materialize_recent_snapshot(
        self,
        board_id: int,
        trade_date: date,
        *,
        max_stale_trading_days: int = DEFAULT_MAX_STALE_TRADING_DAYS,
    ) -> Optional[ConstituentSnapshotState]:
        latest = self.get_latest_snapshot_state(board_id, trade_date)
        if latest is None or latest.trade_date == trade_date or not latest.stock_codes:
            return latest if latest and latest.trade_date == trade_date else None

        origin_trade_date = latest.origin_trade_date or latest.trade_date
        snapshot_age_days = _calculate_snapshot_age_days(origin_trade_date, trade_date)
        if snapshot_age_days > max_stale_trading_days:
            return None

        self.save_constituents(
            board_id,
            trade_date,
            latest.stock_codes,
            origin_trade_date=origin_trade_date,
            is_stale=True,
            snapshot_age_days=snapshot_age_days,
        )
        return self.get_snapshot_state(board_id, trade_date)

    def _read_snapshot_state_from_mirror(
        self,
        board_id: int,
        trade_date: date,
    ) -> Optional[ConstituentSnapshotState]:
        row = self._query_mirror_snapshot(
            """
            SELECT board_id, trade_date, stock_codes_json, origin_trade_date, is_stale, snapshot_age_days
            FROM constituent_snapshot
            WHERE board_id = ? AND trade_date = ?
            LIMIT 1
            """,
            (int(board_id), str(trade_date)),
        )
        return _snapshot_state_from_sqlite_row(row) if row is not None else None

    def _read_latest_snapshot_state_from_mirror(
        self,
        board_id: int,
        trade_date: date,
    ) -> Optional[ConstituentSnapshotState]:
        row = self._query_mirror_snapshot(
            """
            SELECT board_id, trade_date, stock_codes_json, origin_trade_date, is_stale, snapshot_age_days
            FROM constituent_snapshot
            WHERE board_id = ? AND trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            (int(board_id), str(trade_date)),
        )
        return _snapshot_state_from_sqlite_row(row) if row is not None else None

    def _query_mirror_snapshot(
        self,
        sql: str,
        params: tuple,
    ) -> Optional[sqlite3.Row]:
        if self._mirror_db_path is None:
            return None
        try:
            with sqlite3.connect(self._mirror_db_path) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(sql, params).fetchone()
                return row
        except Exception as exc:
            logger.warning(
                "constituent mirror db query failed path=%s params=%s err=%s",
                self._mirror_db_path,
                params,
                exc,
            )
            return None


class ConstituentRuntimeResolver:
    def __init__(
        self,
        *,
        repo: ConstituentSnapshotRepo,
        fetcher: ConstituentFetcher,
        current_trade_date_provider: Callable[[], date] = spi_time,
        max_stale_trading_days: int = DEFAULT_MAX_STALE_TRADING_DAYS,
    ) -> None:
        self._repo = repo
        self._fetcher = fetcher
        self._current_trade_date_provider = current_trade_date_provider
        self._max_stale_trading_days = max_stale_trading_days

    def resolve(self, board_id: int, trade_date: date) -> tuple[list[str], str, Optional[ConstituentSnapshotState]]:
        exact_snapshot = self._repo.get_snapshot_state(board_id, trade_date)
        current_trade_date = self._current_trade_date_provider()
        if exact_snapshot is not None:
            if exact_snapshot.is_stale and exact_snapshot.snapshot_age_days > self._max_stale_trading_days:
                if trade_date == current_trade_date:
                    refreshed = self._refresh_current_snapshot(board_id, trade_date)
                    if refreshed is not None:
                        return refreshed.stock_codes, "current", refreshed
                return [], "missing", None
            if (
                exact_snapshot.is_stale
                and trade_date == current_trade_date
                and exact_snapshot.snapshot_age_days <= self._max_stale_trading_days
            ):
                refreshed = self._refresh_current_snapshot(board_id, trade_date)
                if refreshed is not None:
                    return refreshed.stock_codes, "current", refreshed
            source = "stale_snapshot" if exact_snapshot.is_stale else "snapshot"
            return exact_snapshot.stock_codes, source, exact_snapshot

        if trade_date != current_trade_date:
            return [], "missing", None

        stale_snapshot = self._repo.materialize_recent_snapshot(
            board_id,
            trade_date,
            max_stale_trading_days=self._max_stale_trading_days,
        )
        if stale_snapshot is not None:
            defer_refetch = getattr(self._fetcher, "defer_refetch", None)
            if callable(defer_refetch):
                defer_refetch(board_id)
            return stale_snapshot.stock_codes, "stale_snapshot", stale_snapshot

        refreshed = self._refresh_current_snapshot(board_id, trade_date)
        if refreshed is not None:
            return refreshed.stock_codes, "current", refreshed
        return [], "missing", None

    def _refresh_current_snapshot(self, board_id: int, trade_date: date) -> Optional[ConstituentSnapshotState]:
        stock_codes = self._fetcher.fetch(board_id)
        if not stock_codes:
            return None
        self._repo.save_constituents(
            board_id,
            trade_date,
            stock_codes,
            origin_trade_date=trade_date,
            is_stale=False,
            snapshot_age_days=0,
        )
        return self._repo.get_snapshot_state(board_id, trade_date)


def _snapshot_state_from_row(row: ConstituentSnapshot) -> ConstituentSnapshotState:
    trade_date = row.trade_date
    origin_trade_date = row.origin_trade_date or trade_date
    stored_age = row.snapshot_age_days if row.snapshot_age_days is not None else 0
    if stored_age == 0 and trade_date != origin_trade_date:
        stored_age = _calculate_snapshot_age_days(origin_trade_date, trade_date)
    return ConstituentSnapshotState(
        board_id=row.board_id,
        trade_date=trade_date,
        stock_codes=json.loads(row.stock_codes_json),
        origin_trade_date=origin_trade_date,
        is_stale=bool(row.is_stale),
        snapshot_age_days=int(stored_age or 0),
    )


def _snapshot_state_from_sqlite_row(row: sqlite3.Row) -> ConstituentSnapshotState:
    trade_date = _parse_sqlite_date(row["trade_date"])
    origin_trade_date = _parse_sqlite_date(row["origin_trade_date"]) or trade_date
    raw_age = row["snapshot_age_days"]
    stored_age = int(raw_age or 0)
    is_stale = bool(row["is_stale"])
    if stored_age == 0 and trade_date != origin_trade_date:
        stored_age = _calculate_snapshot_age_days(origin_trade_date, trade_date)
    return ConstituentSnapshotState(
        board_id=int(row["board_id"]),
        trade_date=trade_date,
        stock_codes=json.loads(row["stock_codes_json"]),
        origin_trade_date=origin_trade_date,
        is_stale=is_stale,
        snapshot_age_days=stored_age,
    )


def _parse_sqlite_date(value: object) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _resolve_constituent_snapshot_mirror_path(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.exists():
        logger.warning("constituent snapshot mirror db not found: %s", path)
        return None
    return str(path)


def _calculate_snapshot_age_days(origin_trade_date: date, trade_date: date) -> int:
    if trade_date <= origin_trade_date:
        return 0
    trading_dates = iter_trading_dates(origin_trade_date, trade_date)
    if not trading_dates:
        return 0
    return max(0, len(trading_dates) - 1)
