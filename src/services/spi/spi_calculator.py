"""SPI (Plate Strength Indicator) calculator — TA-Lib EMA hand-rolled SMA-seed recurrence.
ema_for_spi + cal_stock_spi + cal_index_spi operate on industry index close series directly.
"""

PERIODS = [5, 13, 21, 34, 55, 89, 144, 233]


def ema_for_spi(close_series, periods=PERIODS):
    """TA-Lib default EMA: first period-1 values NaN, seed = SMA(period), then recurse.
    Returns {period: last_ema_value}. Skips period if len(close_series) < period."""
    closes = list(close_series)
    n = len(closes)
    out = {}
    for period in periods:
        if n < period:
            continue
        seed = sum(closes[:period]) / period
        prev = seed
        k1 = 2 / (period + 1)
        for i in range(period, n):
            prev = (closes[i] - prev) * k1 + prev
        out[period] = prev
    return out


def cal_stock_spi(last_close, ema_map):
    """Count how many EMAs the last_close stands above → integer 0~8."""
    return sum(1 for v in ema_map.values() if last_close > v)


def cal_index_spi(close_series, periods=PERIODS):
    """SPI of an index from its close series. Returns -1 if insufficient data for any EMA, else 0~8."""
    ema_map = ema_for_spi(close_series, periods)
    if not ema_map:
        return -1
    last_close = list(close_series)[-1]
    return cal_stock_spi(last_close, ema_map)
