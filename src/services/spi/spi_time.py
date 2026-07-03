from datetime import date, datetime

from src.core.trading_calendar import get_effective_trading_date

DEFAULT_MARKET = "cn"


def spi_time(now: datetime | None = None, market: str = DEFAULT_MARKET) -> date:
    return get_effective_trading_date(market, current_time=now)