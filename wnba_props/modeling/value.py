from __future__ import annotations

from typing import Optional


FAVORABLE_THRESHOLD = 0.03
CLOSE_THRESHOLD = -0.02


def american_payout(price: Optional[int]) -> Optional[float]:
    if price is None:
        return None
    if price > 0:
        return price / 100.0
    if price < 0:
        return 100.0 / abs(price)
    return None


def american_to_decimal(price: Optional[int]) -> Optional[float]:
    if price is None or price == 0:
        return None
    if price > 0:
        return 1.0 + (price / 100.0)
    return 1.0 + (100.0 / abs(price))


def decimal_to_american(decimal: float) -> Optional[int]:
    if decimal is None or decimal <= 1.0:
        return None
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100.0)
    return -round(100.0 / (decimal - 1.0))


def expected_value(pick_probability: float, price: Optional[int]) -> Optional[float]:
    payout = american_payout(price)
    if payout is None:
        return None
    return pick_probability * payout - (1.0 - pick_probability)


def expected_value_with_push(
    p_win: float,
    p_loss: float,
    price: Optional[int],
) -> Optional[float]:
    payout = american_payout(price)
    if payout is None:
        return None
    return p_win * payout - p_loss


def value_label(ev: Optional[float]) -> str:
    if ev is None:
        return "UNPRICED"
    if ev >= FAVORABLE_THRESHOLD:
        return "FAVORABLE"
    if ev >= CLOSE_THRESHOLD:
        return "CLOSE"
    return "UNFAVORABLE"
