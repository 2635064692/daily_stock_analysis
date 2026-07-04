from __future__ import annotations

from typing import Optional


def is_eligible_for_sp(
    stock_mktcap: Optional[float],
    stock_profit: Optional[float],
) -> bool:
    return (
        stock_mktcap is not None
        and stock_mktcap > 0
        and stock_profit is not None
        and stock_profit > 0
    )


def calc_sp_ratio(
    stock_mktcap: Optional[float],
    board_total_mktcap: Optional[float],
    stock_profit: Optional[float],
    board_total_profit: Optional[float],
) -> Optional[float]:
    """
    S = stock_mktcap / board_total_mktcap
    P = stock_profit / board_total_profit
    SP = S / P
    """
    if not is_eligible_for_sp(stock_mktcap, stock_profit):
        return None
    if board_total_mktcap is None or board_total_profit is None:
        return None
    if board_total_mktcap <= 0 or board_total_profit <= 0:
        return None

    stock_share = stock_mktcap / board_total_mktcap
    profit_share = stock_profit / board_total_profit
    if profit_share <= 0:
        return None
    return stock_share / profit_share


def sp_ratio_to_score(sp_ratio: Optional[float]) -> Optional[float]:
    if sp_ratio is None or sp_ratio <= 0:
        return None
    return 1.0 / (1.0 + sp_ratio)
