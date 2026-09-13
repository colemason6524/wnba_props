from __future__ import annotations

from typing import Optional

from .ledger import LOSS, PUSH, WIN
from .modeling.game_forecast import GameForecast
from .modeling.value import american_payout


def proposition_id(market: str, subject: str, game_date: str, line: Optional[float]) -> str:
    line_text = "" if line is None else f":{line}"
    return f"{market}:{subject}:{game_date}{line_text}"


def outcome_for_over(value: float, line: float) -> str:
    if value > line:
        return WIN
    if value < line:
        return LOSS
    return PUSH


def grade_over_under(pick_side: str, value: float, line: float) -> str:
    over_outcome = outcome_for_over(value, line)
    if over_outcome == PUSH:
        return PUSH
    if pick_side == "OVER":
        return over_outcome
    return LOSS if over_outcome == WIN else WIN


def settle_units(outcome: str, price: Optional[int]) -> Optional[float]:
    payout = american_payout(price)
    if outcome == WIN:
        return payout
    if outcome == LOSS:
        return -1.0
    if outcome in (PUSH, "VOID"):
        return 0.0
    return None


def grade_winner_pick(forecast: GameForecast, home_score: int, away_score: int) -> str:
    home_won = home_score > away_score
    if forecast.winner_pick == "HOME":
        return WIN if home_won else LOSS
    return WIN if not home_won else LOSS


def grade_spread_pick(
    forecast: GameForecast,
    home_score: int,
    away_score: int,
) -> str:
    if forecast.spread_pick is None or forecast.spread_line is None:
        return PUSH
    margin = home_score - away_score
    adjusted = margin + forecast.spread_line
    if abs(adjusted) < 1e-9:
        return PUSH
    if forecast.spread_pick == "HOME":
        return WIN if adjusted > 0 else LOSS
    return WIN if adjusted < 0 else LOSS


def grade_total_pick(forecast: GameForecast, home_score: int, away_score: int) -> str:
    if forecast.total_pick is None or forecast.total_line is None:
        return PUSH
    total = home_score + away_score
    return grade_over_under(forecast.total_pick, total, forecast.total_line)
