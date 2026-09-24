from __future__ import annotations

from typing import Optional


PLAYABLE_THRESHOLD = 0.03
THIN_THRESHOLD = -0.02


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
        return "unpriced"
    if ev >= PLAYABLE_THRESHOLD:
        return "playable"
    if ev >= THIN_THRESHOLD:
        return "thin"
    return "no_value"


def implied_probability(price: Optional[int]) -> Optional[float]:
    """Raw implied probability from American odds, before de-vigging."""
    if price is None or price == 0:
        return None
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


def no_vig_probabilities(
    price_a: Optional[int],
    price_b: Optional[int],
) -> tuple[Optional[float], Optional[float]]:
    """Normalize two implied probabilities so they sum to one.

    Returns ``(None, None)`` when either price is missing or invalid. This is
    the market baseline the model probability can be blended toward.
    """
    prob_a = implied_probability(price_a)
    prob_b = implied_probability(price_b)
    if prob_a is None or prob_b is None:
        return None, None
    total = prob_a + prob_b
    if total <= 0.0:
        return None, None
    return prob_a / total, prob_b / total


def blend_non_push(
    model_a: float,
    model_b: float,
    market_a: Optional[float],
    market_b: Optional[float],
    weight: float,
) -> tuple[float, float]:
    """Blend two model probabilities toward the no-vig market probabilities.

    The blend happens on the conditional (non-push) mass, then is rescaled to
    the model's total decided probability. A zero weight returns the model
    probabilities unchanged; missing market probabilities also leave them alone.
    """
    if weight <= 0.0 or market_a is None or market_b is None:
        return model_a, model_b
    total = model_a + model_b
    market_total = market_a + market_b
    if total <= 0.0 or market_total <= 0.0:
        return model_a, model_b
    w = max(0.0, min(1.0, weight))
    model_cond_a = model_a / total
    market_cond_a = market_a / market_total
    blended_a = (1.0 - w) * model_cond_a + w * market_cond_a
    return blended_a * total, (1.0 - blended_a) * total
