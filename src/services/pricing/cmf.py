from typing import Optional, Sequence


def calc_cmf(bars: Sequence, period: int = 20, min_period: int = 5) -> Optional[float]:
    """Chaikin Money Flow over the last `period` bars.

    Each bar must expose .high / .low / .close / .volume (StockDaily) or
    dict-compatible access via bar['high'] etc.

    Boundary rules:
    - high == low for a bar → MFM = 0 (avoid zero-division, window not shortened)
    - Σvolume over window == 0 → None  (undefined, not neutral)
    - available bars < min_period → None
    - available bars < period but >= min_period → compute over available bars

    Returns raw CMF ∈ [-1, 1]. Caller is responsible for ensuring bars use
    a consistent adjustment basis (e.g. qfq throughout).
    """
    n = len(bars)
    if n < min_period:
        return None

    window = bars[-period:] if n >= period else bars

    mfv_sum = 0.0
    vol_sum = 0.0
    for bar in window:
        if hasattr(bar, 'high'):
            h, l, c, v = bar.high, bar.low, bar.close, bar.volume
        else:
            h, l, c, v = bar['high'], bar['low'], bar['close'], bar['volume']
        v = v or 0.0
        vol_sum += v
        hl = (h or 0.0) - (l or 0.0)
        if hl == 0.0:
            continue
        mfm = ((c - l) - (h - c)) / hl
        mfv_sum += mfm * v

    if vol_sum == 0.0:
        return None

    return mfv_sum / vol_sum


def normalize_cmf(cmf_value: Optional[float]) -> Optional[float]:
    """Map raw CMF [-1, 1] to [0, 1] via (x + 1) / 2. None → None."""
    if cmf_value is None:
        return None
    return (cmf_value + 1.0) / 2.0
