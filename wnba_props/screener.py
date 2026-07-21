from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import mean, median, pstdev
from typing import Iterable

from .config import Settings
from .models import Candidate, PlayerGameLog, PropLine, ScreeningResult, TeamInjury
from .utils import normalize_name


def screen_candidates(
    settings: Settings,
    prop_lines: Iterable[PropLine],
    logs_by_player: dict[str, list[PlayerGameLog]],
    game_contexts: dict[tuple[str, str], object] | None = None,
    team_injuries: dict[str, list[TeamInjury]] | None = None,
) -> ScreeningResult:
    candidates: list[Candidate] = []
    evaluated_prop_lines = 0
    non_qualifying_prop_lines = 0
    opponent_environment = _build_opponent_environment(logs_by_player)
    teammate_return_context = _build_teammate_return_context(
        logs_by_player,
        team_injuries or {},
        settings.screen_date,
    )
    for line in prop_lines:
        player_key = normalize_name(line.player_name_raw)
        logs = logs_by_player.get(player_key, [])
        if not logs:
            continue

        if not _line_matches_recent_team(line.team, logs):
            continue

        if not line.team:
            resolved_team = _resolve_team_from_logs(logs, line.opponent)
            line.team = resolved_team
            if line.team:
                line.opponent = line.opponent or _resolve_opponent_from_logs(logs, line.team)

        values = [_get_prop_value(log, line.prop_type) for log in logs]
        last_5 = values[:5]
        last_10 = values[:10]
        played_last_5 = len(last_5)
        played_last_10 = len(last_10)
        if played_last_5 <= 1 or played_last_10 < settings.thresholds.min_played_last_10:
            evaluated_prop_lines += 1
            non_qualifying_prop_lines += 1
            continue

        avg_last_5 = mean(last_5)
        avg_last_10 = mean(last_10)
        median_last_5 = median(last_5)
        median_last_10 = median(last_10)
        season_avg = mean(values)
        avg_minutes_last_5 = mean(log.minutes for log in logs[:5])
        avg_minutes_last_10 = mean(log.minutes for log in logs[:10])
        game_context = _resolve_game_context(game_contexts, line.team, line.opponent)
        line_candidates = _build_candidates_for_line(
            settings=settings,
            player_key=player_key,
            logs=logs,
            teammate_return_context=teammate_return_context,
            line=line,
            played_last_5=played_last_5,
            played_last_10=played_last_10,
            avg_last_5=avg_last_5,
            avg_last_10=avg_last_10,
            median_last_5=median_last_5,
            median_last_10=median_last_10,
            season_avg=season_avg,
            avg_minutes_last_5=avg_minutes_last_5,
            avg_minutes_last_10=avg_minutes_last_10,
            last_5=last_5,
            last_10=last_10,
            game_context=game_context,
            opp_avg=_get_opponent_average(opponent_environment, line.opponent, line.prop_type),
            opponent_matchup=_get_opponent_matchup(opponent_environment, line.opponent, line.prop_type),
            team_injuries=team_injuries.get(line.team, []) if team_injuries else [],
        )
        evaluated_prop_lines += 1
        if line_candidates:
            candidates.extend(line_candidates)
        else:
            non_qualifying_prop_lines += 1
    return ScreeningResult(
        candidates=sorted(
            candidates,
            key=lambda item: (item.score, item.hits_last_5, item.delta_avg_last_5, item.hits_last_10),
            reverse=True,
        ),
        evaluated_prop_lines=evaluated_prop_lines,
        non_qualifying_prop_lines=non_qualifying_prop_lines,
    )


def summarize_return_context(
    candidates: Iterable[Candidate],
    logs_by_player: dict[str, list[PlayerGameLog]],
    team_injuries: dict[str, list[TeamInjury]],
    screen_date: date,
) -> dict[str, list[str]]:
    teammate_return_context = _build_teammate_return_context(logs_by_player, team_injuries, screen_date)
    player_names_by_key = {
        player_key: logs[0].player_name_raw
        for player_key, logs in logs_by_player.items()
        if logs
    }
    summaries: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for candidate in candidates:
        player_key = normalize_name(candidate.player_name)
        logs = logs_by_player.get(player_key, [])
        if len(logs) < 4:
            continue
        sample_dates = [log.game_date for log in logs[:5]]
        for teammate_key, teammate_context in teammate_return_context.get(candidate.team, {}).items():
            if teammate_key == player_key:
                continue
            if teammate_context["status"] != "active":
                continue
            played_dates = teammate_context["played_dates"]
            overlap = sum(1 for game_date in sample_dates if game_date in played_dates)
            if overlap > 2:
                continue
            teammate_name = player_names_by_key.get(teammate_key, teammate_key)
            summaries[candidate.team][teammate_name].add(candidate.player_name)

    compact: dict[str, list[str]] = {}
    for team, teammate_map in sorted(summaries.items()):
        lines: list[str] = []
        ranked = sorted(
            teammate_map.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )
        for teammate_name, impacted_players in ranked[:3]:
            impacted = sorted(impacted_players)
            if len(impacted) <= 2:
                impacted_text = ", ".join(impacted)
            else:
                impacted_text = f"{', '.join(impacted[:2])}, +{len(impacted) - 2} more"
            lines.append(f"{teammate_name} -> {impacted_text}")
        if lines:
            compact[team] = lines
    return compact


def _build_candidates_for_line(
    settings: Settings,
    player_key: str,
    logs: list[PlayerGameLog],
    teammate_return_context: dict[str, dict[str, set]],
    line: PropLine,
    played_last_5: int,
    played_last_10: int,
    avg_last_5: float,
    avg_last_10: float,
    median_last_5: float,
    median_last_10: float,
    season_avg: float,
    avg_minutes_last_5: float,
    avg_minutes_last_10: float,
    last_5: list[int],
    last_10: list[int],
    game_context: object | None,
    opp_avg: float | None,
    opponent_matchup: dict[str, float | int] | None,
    team_injuries: list[TeamInjury],
) -> list[Candidate]:
    candidates: list[Candidate] = []
    side_configs = [
        ("OVER", sum(1 for value in last_5 if value > line.line), sum(1 for value in last_10 if value > line.line), avg_last_5 - line.line, season_avg - line.line),
    ]
    if settings.include_under_candidates:
        side_configs.append(
            ("UNDER", sum(1 for value in last_5 if value < line.line), sum(1 for value in last_10 if value < line.line), line.line - avg_last_5, line.line - season_avg)
        )

    for side, hits_last_5, hits_last_10, delta_avg_last_5, delta_season in side_configs:
        if not _passes_recent_rule(hits_last_5, played_last_5, hits_last_10, played_last_10):
            continue
        score, flags = _score_candidate(
            settings,
            hits_last_5,
            played_last_5,
            hits_last_10,
            delta_avg_last_5,
            delta_season,
            avg_minutes_last_5,
        )
        if side == "UNDER":
            flags.append("U")
        flags.extend(_context_flags(line.team, game_context, opp_avg))
        matchup_adjustment, matchup_flags = _matchup_adjustment(
            screen_date=settings.screen_date,
            prop_type=line.prop_type,
            side=side,
            matchup=opponent_matchup,
        )
        flags.extend(matchup_flags)
        flags.extend(_injury_flags(line.player_name_raw, team_injuries))
        flags.extend(_teammate_return_flags(player_key, line.team, logs, teammate_return_context))
        split_adjustment, split_flags = _teammate_split_adjustment(
            side=side,
            player_key=player_key,
            team=line.team,
            prop_type=line.prop_type,
            logs=logs,
            teammate_return_context=teammate_return_context,
        )
        flags.extend(split_flags)
        sample_quality_adjustment, sample_quality_flags = _sample_quality_adjustment(
            team=line.team,
            logs=logs,
            avg_minutes_last_10=avg_minutes_last_10,
            existing_flags=flags,
            teammate_return_context=teammate_return_context,
            player_key=player_key,
        )
        flags.extend(sample_quality_flags)
        flags.extend(
            _hot_hand_flags(
                side=side,
                hits_last_5=hits_last_5,
                hits_last_10=hits_last_10,
                delta_avg_last_5=delta_avg_last_5,
                avg_last_10=avg_last_10,
                median_last_10=median_last_10,
                season_avg=season_avg,
                line=line.line,
                avg_minutes_last_5=avg_minutes_last_5,
                avg_minutes_last_10=avg_minutes_last_10,
                existing_flags=flags,
            )
        )
        flags.extend(
            _playoff_context_flags(
                screen_date=settings.screen_date,
                side=side,
                avg_minutes_last_10=avg_minutes_last_10,
                existing_flags=flags,
            )
        )
        injury_adjustment = _injury_score_adjustment(side, flags)
        prop_type_adjustment = _prop_type_score_adjustment(line.prop_type)
        stacked_risk_adjustment = _stacked_risk_penalty(flags)
        playoff_adjustment = _playoff_score_adjustment(flags)
        score += _combine_secondary_adjustments(
            matchup_adjustment,
            split_adjustment,
            sample_quality_adjustment,
            injury_adjustment,
            prop_type_adjustment,
            stacked_risk_adjustment,
            playoff_adjustment,
        )
        candidates.append(
            Candidate(
                player_name=line.player_name_raw,
                team=line.team,
                opponent=line.opponent,
                prop_type=line.prop_type,
                side=side,
                line=line.line,
                bookmaker=line.bookmaker,
                hits_last_5=hits_last_5,
                played_last_5=played_last_5,
                hits_last_10=hits_last_10,
                played_last_10=played_last_10,
                avg_last_5=avg_last_5,
                avg_last_10=avg_last_10,
                median_last_5=median_last_5,
                median_last_10=median_last_10,
                season_avg=season_avg,
                avg_minutes_last_5=avg_minutes_last_5,
                avg_minutes_last_10=avg_minutes_last_10,
                delta_avg_last_5=delta_avg_last_5,
                score=score,
                flags=flags,
                spread=_team_spread(line.team, game_context),
                total=getattr(game_context, "total", None),
                opp_avg=opp_avg,
            )
        )
    return candidates


def _resolve_game_context(
    game_contexts: dict[tuple[str, str], object] | None,
    team: str,
    opponent: str,
) -> object | None:
    if not game_contexts or not team or not opponent:
        return None
    return game_contexts.get((team, opponent)) or game_contexts.get((opponent, team))


def _resolve_team_from_logs(logs: list[PlayerGameLog], opponent: str) -> str:
    counts: dict[str, int] = defaultdict(int)
    for log in logs[:5]:
        if opponent and log.opponent == opponent:
            counts[log.team] += 2
        counts[log.team] += 1
    return max(counts, key=counts.get) if counts else ""


def _resolve_opponent_from_logs(logs: list[PlayerGameLog], team: str) -> str:
    for log in logs[:5]:
        if log.team == team:
            return log.opponent
    return ""


def _line_matches_recent_team(line_team: str, logs: list[PlayerGameLog]) -> bool:
    if not line_team:
        return True
    recent_team = logs[0].team if logs else ""
    if not recent_team:
        return True
    return line_team == recent_team


def _get_prop_value(log: PlayerGameLog, prop_type: str) -> int:
    if prop_type == "PTS":
        return log.points
    if prop_type == "REB":
        return log.rebounds
    if prop_type == "AST":
        return log.assists
    if prop_type == "PRA":
        return log.pra
    if prop_type == "P+A":
        return log.points_assists
    if prop_type == "P+R":
        return log.points_rebounds
    if prop_type == "R+A":
        return log.rebounds_assists
    if prop_type == "3PM":
        return log.threes_made
    raise ValueError(f"Unsupported prop type: {prop_type}")


def _passes_recent_rule(hits_last_5: int, played_last_5: int, hits_last_10: int, played_last_10: int) -> bool:
    if played_last_5 >= 4:
        return hits_last_5 >= 4
    if played_last_5 == 3:
        return hits_last_5 == 3 and (played_last_10 < 8 or hits_last_10 >= 6)
    if played_last_5 == 2:
        return hits_last_5 == 2 and (played_last_10 < 8 or hits_last_10 >= 5)
    return False


def _score_candidate(
    settings: Settings,
    hits_last_5: int,
    played_last_5: int,
    hits_last_10: int,
    delta_avg_last_5: float,
    delta_season: float,
    avg_minutes_last_5: float,
) -> tuple[int, list[str]]:
    score = 0
    flags: list[str] = []
    if hits_last_5 >= settings.thresholds.primary_hits_last_5:
        score += 4
    if hits_last_10 >= settings.thresholds.support_hits_last_10:
        score += 2
        flags.append("L10+")
    if delta_avg_last_5 >= settings.thresholds.min_delta_avg_last_5:
        score += 1
    if delta_avg_last_5 >= 2.0:
        score += 1
    if delta_season >= 0:
        score += 1
    else:
        flags.append("SEASON-")
    if avg_minutes_last_5 >= settings.thresholds.strong_minutes_threshold:
        score += 1
    if played_last_5 == 2:
        score -= 1
        flags.append("THIN")
    elif played_last_5 == 3:
        flags.append("THIN")
    if avg_minutes_last_5 < settings.thresholds.low_minutes_warning:
        score -= 1
        flags.append("LOW_MIN")
    return score, flags


def _build_opponent_environment(logs_by_player: dict[str, list[PlayerGameLog]]) -> dict[str, object]:
    recent_window = 8
    opponent_samples: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(
        lambda: defaultdict(lambda: {"season": [], "recent": []})
    )
    league_samples: dict[str, list[int]] = defaultdict(list)
    for logs in logs_by_player.values():
        for index, log in enumerate(logs):
            values = {
                "PTS": log.points,
                "REB": log.rebounds,
                "AST": log.assists,
                "3PM": log.threes_made,
                "PRA": log.pra,
                "P+A": log.points_assists,
                "P+R": log.points_rebounds,
                "R+A": log.rebounds_assists,
            }
            for prop_type, value in values.items():
                opponent_samples[log.opponent][prop_type]["season"].append(value)
                league_samples[prop_type].append(value)
                if index < recent_window:
                    opponent_samples[log.opponent][prop_type]["recent"].append(value)
    return {
        "opponents": opponent_samples,
        "league": {prop_type: mean(values) for prop_type, values in league_samples.items() if values},
    }


def _get_opponent_average(env: dict[str, object], opponent: str, prop_type: str) -> float | None:
    opponent_samples = env.get("opponents", {})
    values = opponent_samples.get(opponent, {}).get(prop_type, {}).get("season", [])
    return mean(values) if values else None


def _get_opponent_matchup(
    env: dict[str, object],
    opponent: str,
    prop_type: str,
) -> dict[str, float | int] | None:
    opponent_samples = env.get("opponents", {})
    league_averages = env.get("league", {})
    sample = opponent_samples.get(opponent, {}).get(prop_type)
    league_avg = league_averages.get(prop_type)
    if not sample or league_avg is None:
        return None

    season_values = sample.get("season", [])
    if not season_values:
        return None
    recent_values = sample.get("recent", [])
    season_avg = mean(season_values)
    recent_avg = mean(recent_values) if recent_values else season_avg
    return {
        "season_avg": season_avg,
        "recent_avg": recent_avg,
        "league_avg": float(league_avg),
        "season_sample": len(season_values),
        "recent_sample": len(recent_values),
    }


def _team_spread(team: str, game_context: object | None) -> float | None:
    if game_context is None:
        return None
    if getattr(game_context, "away_team", "") == team:
        return getattr(game_context, "away_spread", None)
    if getattr(game_context, "home_team", "") == team:
        return getattr(game_context, "home_spread", None)
    return None


def _context_flags(team: str, game_context: object | None, opp_avg: float | None) -> list[str]:
    flags: list[str] = []
    spread = _team_spread(team, game_context)
    total = getattr(game_context, "total", None) if game_context is not None else None
    if spread is not None:
        if abs(spread) >= 10:
            flags.append("BLOWOUT")
        elif abs(spread) <= 4:
            flags.append("CLOSE")
    if total is not None:
        if total >= 232:
            flags.append("HIGH_TOT")
        elif total <= 218:
            flags.append("LOW_TOT")
    if opp_avg is not None:
        flags.append("OPPCTX")
    return flags


def _matchup_adjustment(
    screen_date: date,
    prop_type: str,
    side: str,
    matchup: dict[str, float | int] | None,
) -> tuple[int, list[str]]:
    if not matchup:
        return 0, []

    season_avg = float(matchup["season_avg"])
    recent_avg = float(matchup["recent_avg"])
    league_avg = float(matchup["league_avg"])
    recent_sample = int(matchup["recent_sample"])
    season_sample = int(matchup["season_sample"])
    if season_sample < 12:
        return 0, []

    recent_weight = 0.65 if _is_playoff_window(screen_date) else 0.45
    if recent_sample < 8:
        recent_weight -= 0.15
    recent_weight = max(0.25, min(0.7, recent_weight))
    season_weight = 1.0 - recent_weight
    blended_avg = (recent_avg * recent_weight) + (season_avg * season_weight)
    edge = blended_avg - league_avg
    threshold = _matchup_signal_threshold(prop_type)

    if edge >= threshold:
        return (1, ["MATCHUP_PLUS"]) if side == "OVER" else (-1, ["MATCHUP_RISK"])
    if edge <= -threshold:
        return (-1, ["MATCHUP_RISK"]) if side == "OVER" else (1, ["MATCHUP_PLUS"])
    return 0, []


def _matchup_signal_threshold(prop_type: str) -> float:
    if prop_type == "3PM":
        return 0.25
    if prop_type in {"PTS", "REB", "AST"}:
        return 1.0
    return 2.0


def _injury_flags(player_name: str, team_injuries: list[TeamInjury]) -> list[str]:
    flags: list[str] = []
    player_norm = normalize_name(player_name)
    key_out = False
    key_q = False
    team_out = False
    team_q = False

    for injury in team_injuries:
        status = injury.status.lower()
        if injury.player_name_norm == player_norm:
            if _is_uncertain_status(status):
                flags.append("SELF_Q")
            continue
        if _is_out_status(status):
            if injury.impact_level == "key":
                key_out = True
            elif injury.impact_level == "team":
                team_out = True
        elif _is_uncertain_status(status):
            if injury.impact_level == "key":
                key_q = True
            elif injury.impact_level == "team":
                team_q = True

    if key_out:
        flags.append("KEY_OUT")
    if key_q:
        flags.append("KEY_Q")
    if team_out:
        flags.append("TEAM_OUT")
    if team_q:
        flags.append("TEAM_Q")
    return flags


def _build_teammate_return_context(
    logs_by_player: dict[str, list[PlayerGameLog]],
    team_injuries: dict[str, list[TeamInjury]],
    screen_date: date,
) -> dict[str, dict[str, dict[str, object]]]:
    injured_status_by_team = {
        team: {injury.player_name_norm: injury.status.lower() for injury in injuries}
        for team, injuries in team_injuries.items()
    }
    team_profiles: dict[str, list[tuple[str, float, set]]] = defaultdict(list)

    for player_key, logs in logs_by_player.items():
        if not logs:
            continue
        latest_log_date = logs[0].game_date
        if not _is_recent_teammate_context(latest_log_date, screen_date):
            continue
        team = logs[0].team
        recent_logs = logs[:10]
        impact_score = (
            0.40 * mean(log.minutes for log in recent_logs)
            + 0.35 * mean(log.points for log in logs)
            + 0.50 * mean(log.assists for log in logs)
            + 0.15 * mean(log.rebounds for log in logs)
        )
        played_dates = {log.game_date for log in recent_logs}
        team_profiles[team].append((player_key, impact_score, played_dates))

    context: dict[str, dict[str, dict[str, object]]] = {}
    for team, profiles in team_profiles.items():
        injured_statuses = injured_status_by_team.get(team, {})
        ranked = sorted(profiles, key=lambda item: item[1], reverse=True)
        key_teammates = {}
        for index, (player_key, impact_score, played_dates) in enumerate(ranked):
            if index > 2 or impact_score < 18.0:
                continue
            key_teammates[player_key] = {
                "played_dates": played_dates,
                "status": injured_statuses.get(player_key, "active"),
                "impact_score": impact_score,
            }
        if key_teammates:
            context[team] = key_teammates
    return context


def _is_recent_teammate_context(latest_log_date: date, screen_date: date) -> bool:
    return (screen_date - latest_log_date).days <= 10


def _teammate_return_flags(
    player_key: str,
    team: str,
    logs: list[PlayerGameLog],
    teammate_return_context: dict[str, dict[str, dict[str, object]]],
) -> list[str]:
    key_teammates = teammate_return_context.get(team, {})
    if not key_teammates:
        return []
    sample_dates = [log.game_date for log in logs[:5]]
    if len(sample_dates) < 4:
        return []
    for teammate_key, teammate_context in key_teammates.items():
        if teammate_key == player_key:
            continue
        if teammate_context["status"] != "active":
            continue
        played_dates = teammate_context["played_dates"]
        overlap = sum(1 for game_date in sample_dates if game_date in played_dates)
        if overlap <= 2:
            return ["KEY_BACK"]
    return []


def _teammate_split_adjustment(
    side: str,
    player_key: str,
    team: str,
    prop_type: str,
    logs: list[PlayerGameLog],
    teammate_return_context: dict[str, dict[str, dict[str, object]]],
) -> tuple[int, list[str]]:
    if len(logs) < 6:
        return 0, []

    best_signal: tuple[int, list[str]] = (0, [])
    player_dates = {log.game_date for log in logs}
    for teammate_key, teammate_context in teammate_return_context.get(team, {}).items():
        if teammate_key == player_key:
            continue

        played_dates = teammate_context["played_dates"] & player_dates
        with_values = [_get_prop_value(log, prop_type) for log in logs if log.game_date in played_dates]
        without_values = [_get_prop_value(log, prop_type) for log in logs if log.game_date not in played_dates]
        if len(with_values) < 3 or len(without_values) < 3:
            continue

        with_avg = mean(with_values)
        without_avg = mean(without_values)
        threshold = _split_signal_threshold(prop_type)
        tonight_status = teammate_context["status"]
        if tonight_status == "active":
            diff = with_avg - without_avg
        elif _is_out_status(tonight_status):
            diff = without_avg - with_avg
        else:
            continue

        if abs(diff) < threshold:
            continue

        if diff > 0:
            signal = (1, ["SPLIT_BOOST"]) if side == "OVER" else (-1, ["SPLIT_RISK"])
        else:
            signal = (-1, ["SPLIT_RISK"]) if side == "OVER" else (1, ["SPLIT_BOOST"])

        if abs(signal[0]) > abs(best_signal[0]):
            best_signal = signal

    return best_signal


def _split_signal_threshold(prop_type: str) -> float:
    if prop_type == "3PM":
        return 0.6
    if prop_type in {"PTS", "REB", "AST"}:
        return 1.5
    return 2.5


def _injury_score_adjustment(side: str, flags: list[str]) -> int:
    adjustment = 0

    if "SELF_Q" in flags:
        adjustment -= 1 if side == "OVER" else 0

    if side == "OVER":
        if "TEAM_OUT" in flags:
            adjustment += 1
        if "KEY_BACK" in flags:
            adjustment -= 2
        if "KEY_Q" in flags:
            adjustment -= 1

    return adjustment


def _prop_type_score_adjustment(prop_type: str) -> int:
    if prop_type == "R+A":
        return -1
    return 0


def _stacked_risk_penalty(flags: list[str]) -> int:
    risk_flags = {"SPLIT_RISK", "SAMPLE_CTX", "SAMPLE_VOL", "FRAGILE_HOT"}
    risk_count = sum(1 for flag in risk_flags if flag in flags)
    return -1 if risk_count >= 2 else 0


def _playoff_score_adjustment(flags: list[str]) -> int:
    if "PLAYOFF_ROLE" not in flags:
        return 0

    adjustment = -1
    if any(flag in flags for flag in ("ROLE_UP", "HOT_MOD", "SAMPLE_CTX")):
        adjustment -= 1
    return adjustment


def _combine_secondary_adjustments(*adjustments: int) -> int:
    positive = sum(adjustment for adjustment in adjustments if adjustment > 0)
    negative = sum(adjustment for adjustment in adjustments if adjustment < 0)
    return min(positive, 2) + negative


def _sample_quality_adjustment(
    team: str,
    logs: list[PlayerGameLog],
    avg_minutes_last_10: float,
    existing_flags: list[str],
    teammate_return_context: dict[str, dict[str, dict[str, object]]],
    player_key: str,
) -> tuple[int, list[str]]:
    flags: list[str] = []
    penalty = 0

    recent_logs = logs[:5]
    if len(recent_logs) >= 4:
        recent_minutes = [log.minutes for log in recent_logs]
        minutes_range = max(recent_minutes) - min(recent_minutes)
        minutes_std = pstdev(recent_minutes)
        if minutes_range >= 10.0 or minutes_std >= 4.5:
            flags.append("SAMPLE_VOL")
            penalty -= 1

        low_min_games = sum(1 for minutes in recent_minutes if minutes <= avg_minutes_last_10 - 6.0)
        if avg_minutes_last_10 >= 24.0 and low_min_games >= 2:
            flags.append("SAMPLE_BLOW")
            penalty -= 1

    if _has_context_distortion(player_key, team, logs, teammate_return_context):
        flags.append("SAMPLE_CTX")
        penalty -= 2

    if penalty < -3:
        penalty = -3
    deduped_flags = [flag for flag in flags if flag not in existing_flags]
    return penalty, deduped_flags


def _has_context_distortion(
    player_key: str,
    team: str,
    logs: list[PlayerGameLog],
    teammate_return_context: dict[str, dict[str, dict[str, object]]],
) -> bool:
    sample_dates = [log.game_date for log in logs[:5]]
    if len(sample_dates) < 4:
        return False

    mismatch_count = 0
    for teammate_key, teammate_context in teammate_return_context.get(team, {}).items():
        if teammate_key == player_key:
            continue
        played_dates = teammate_context["played_dates"]
        overlap = sum(1 for game_date in sample_dates if game_date in played_dates)
        status = teammate_context["status"]
        if status == "active" and overlap <= 2:
            mismatch_count += 1
        elif _is_out_status(status) and overlap >= 3:
            mismatch_count += 1
    return mismatch_count >= 1


def _is_out_status(status: str) -> bool:
    return status in {"out", "injured reserve", "suspended"}


def _is_uncertain_status(status: str) -> bool:
    return status in {"day-to-day", "questionable", "doubtful", "probable", "game-time decision"}


def _hot_hand_flags(
    side: str,
    hits_last_5: int,
    hits_last_10: int,
    delta_avg_last_5: float,
    avg_last_10: float,
    median_last_10: float,
    season_avg: float,
    line: float,
    avg_minutes_last_5: float,
    avg_minutes_last_10: float,
    existing_flags: list[str],
) -> list[str]:
    flags: list[str] = []

    hot_strong = (
        hits_last_5 >= 4
        and hits_last_10 >= 7
        and delta_avg_last_5 >= 2.0
        and avg_minutes_last_5 >= 26.0
    )
    hot_mod = hits_last_5 >= 4 and delta_avg_last_5 >= 1.0

    if hot_strong:
        flags.append("HOT_STRONG")
    elif hot_mod:
        flags.append("HOT_MOD")

    if avg_minutes_last_5 - avg_minutes_last_10 >= 2.0:
        flags.append("ROLE_UP")

    if side == "OVER":
        line_high = (line - season_avg) >= 2.0 or (line - avg_last_10) >= 1.5
        priced_in = ("HOT_STRONG" in flags or "HOT_MOD" in flags) and (
            line > avg_last_10 or line > median_last_10 or (line - season_avg) >= 2.0 or delta_avg_last_5 < 1.0
        )
    else:
        line_high = (season_avg - line) >= 2.0 or (avg_last_10 - line) >= 1.5
        priced_in = False

    if line_high:
        flags.append("LINE_HIGH")
    if priced_in:
        flags.append("PRICED_IN")
    if ("HOT_STRONG" in flags or "HOT_MOD" in flags) and any(flag in existing_flags for flag in ("LOW_MIN", "THIN", "BLOWOUT")):
        flags.append("FRAGILE_HOT")

    return flags


def _playoff_context_flags(
    screen_date: date,
    side: str,
    avg_minutes_last_10: float,
    existing_flags: list[str],
) -> list[str]:
    if not _is_playoff_window(screen_date):
        return []
    if side != "OVER":
        return []
    if avg_minutes_last_10 >= 32.0:
        return []
    if not any(flag in existing_flags for flag in ("ROLE_UP", "HOT_MOD", "HOT_STRONG")):
        return []
    return ["PLAYOFF_ROLE"]


def _is_playoff_window(screen_date: date) -> bool:
    return screen_date.month in {4, 5, 6}
