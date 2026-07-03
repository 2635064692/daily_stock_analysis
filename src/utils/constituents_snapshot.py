# -*- coding: utf-8 -*-
import json
import logging
import time
from datetime import date
from typing import Optional

import pandas as pd
import requests
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

from src.storage import ConstituentSnapshot, DatabaseManager

logger = logging.getLogger(__name__)


class ConstituentFetcher:
    BASE_URL = "https://legulegu.com/stockdata/index-composition"

    def fetch(self, board_id: int) -> list:
        """Fetch current constituent stock codes for a Shenwan L1 industry.
        Returns list of stock code strings (e.g. ["600519", "000858"]).
        Returns [] on any failure (network error, 429, parse error).
        """
        url = f"{self.BASE_URL}?industryCode={board_id}.SI"
        try:
            resp = requests.get(url, timeout=15)
            time.sleep(0.5)
            if resp.status_code != 200:
                logger.warning("legulegu fetch failed board_id=%s status=%s", board_id, resp.status_code)
                return []
            tables = pd.read_html(resp.text, flavor="lxml")
            if not tables:
                logger.warning("legulegu no table found board_id=%s", board_id)
                return []
            df = tables[0]
            if "股票代码" not in df.columns:
                logger.warning("legulegu missing '股票代码' column board_id=%s cols=%s", board_id, list(df.columns))
                return []
            codes = [str(c).zfill(6) for c in df["股票代码"].dropna().tolist()]
            return codes
        except Exception as exc:
            logger.warning("legulegu fetch error board_id=%s: %s", board_id, exc)
            return []


class ConstituentSnapshotRepo:

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_constituents(self, board_id: int, trade_date: date, stock_codes: list) -> None:
        """Idempotent write: INSERT OR IGNORE on UNIQUE(board_id, trade_date)."""
        with self.db.get_session() as session:
            try:
                session.add(ConstituentSnapshot(
                    board_id=board_id,
                    trade_date=trade_date,
                    stock_codes_json=json.dumps(stock_codes),
                ))
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
