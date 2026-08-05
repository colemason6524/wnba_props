from __future__ import annotations


def american_to_decimal(odds: int | None) -> float | None:
    if odds is None or odds == 0:
        return None
    if odds > 0:
        return 1.0 + (odds / 100.0)
    return 1.0 + (100.0 / abs(odds))


def implied_probability(odds: int | None) -> float | None:
    decimal = american_to_decimal(odds)
    if decimal is None:
        return None
    return 1.0 / decimal


def fair_american_odds(probability: float) -> int | None:
    if probability <= 0.0 or probability >= 1.0:
        return None
    if probability >= 0.5:
        return -round(100.0 * probability / (1.0 - probability))
    return round(100.0 * (1.0 - probability) / probability)


def expected_profit_units(
    win_probability: float,
    loss_probability: float,
    odds: int | None,
) -> float | None:
    decimal = american_to_decimal(odds)
    if decimal is None:
        return None
    profit_on_win = decimal - 1.0
    return (win_probability * profit_on_win) - loss_probability


def settled_profit_units(outcome: str, odds: int | None) -> float | None:
    """Return flat-stake profit using only a price captured before the game."""
    decimal = american_to_decimal(odds)
    if decimal is None:
        return None
    if outcome == "win":
        return decimal - 1.0
    if outcome == "loss":
        return -1.0
    if outcome == "push":
        return 0.0
    return None
