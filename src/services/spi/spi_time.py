from __future__ import annotations

from datetime import date, datetime, timedelta

try:
    import exchange_calendars as xcals
except ImportError:  # pragma: no cover - optional dependency
    xcals = None

from src.core.trading_calendar import MARKET_EXCHANGE, get_effective_trading_date

DEFAULT_MARKET = "cn"


def spi_time(now: datetime | None = None, market: str = DEFAULT_MARKET) -> date:
    return get_effective_trading_date(market, current_time=now)


def iter_trading_dates(
    start_date: date,
    end_date: date,
    market: str = DEFAULT_MARKET,
) -> list[date]:
    if start_date > end_date:
        return []

    exchange = MARKET_EXCHANGE.get(market)
    if xcals is None or not exchange:
        return _iter_weekday_dates(start_date, end_date)

    try:
        cal = xcals.get_calendar(exchange)
        session = cal.date_to_session(start_date, direction="next")
        sessions = []
        while session.date() <= end_date:
            sessions.append(session.date())
            session = cal.next_session(session)
        return sessions
    except Exception:
        return _iter_weekday_dates(start_date, end_date)


def _iter_weekday_dates(start_date: date, end_date: date) -> list[date]:
    dates: list[date] = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates
