from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from shutil import rmtree
from statistics import mean

from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from wnba_props.models import PlayerGameLog
from wnba_props.sources.espn import EspnSlateSource
from wnba_props.sources.espn_boxscore import EspnBoxscoreSource
from wnba_props.sources.basketball_reference import BasketballReferenceSource
from wnba_props.sources.espn_gamelog import EspnGameLogSource
from wnba_props.utils import normalize_name


@dataclass
class ResolvedPrediction:
    screen_date: date
    player_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    score: int
    flags: list[str]
    actual: float
    outcome: str
    edge: float
    resolution_method: str


@dataclass
class VoidPrediction:
    screen_date: date
    player_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    reason: str


@dataclass
class UnresolvedPrediction:
    screen_date: date
    player_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    reason: str


@dataclass
class ResolutionReport:
    resolved: list[ResolvedPrediction]
    voided: list[VoidPrediction]
    unresolved: list[UnresolvedPrediction]
    pending: int


def season_end_year(screen_date: date) -> int:
    return screen_date.year


def _is_playoff_window(screen_date: date) -> bool:
    return screen_date.month in {9, 10}


TEAM_ABBR_ALIASES = {
    "ATL": "ATL",
    "CHI": "CHI",
    "CON": "CON",
    "DAL": "DAL",
    "GS": "GS",
    "GSV": "GS",
    "IND": "IND",
    "LA": "LA",
    "LAS": "LA",
    "LV": "LV",
    "LVA": "LV",
    "MIN": "MIN",
    "NY": "NY",
    "NYL": "NY",
    "PHO": "PHX",
    "PHX": "PHX",
    "POR": "POR",
    "SEA": "SEA",
    "TOR": "TOR",
    "WAS": "WSH",
    "WSH": "WSH",
}


def _history_files() -> list[Path]:
    history_dir = OUTPUTS_DIR / "history"
    return sorted(history_dir.glob("screen_run_*.json"))


def all_history_mode_enabled() -> bool:
    return "--all-history" in sys.argv


def _load_latest_predictions() -> tuple[list[dict], list[str]]:
    payloads: list[tuple[datetime, str, list[dict]]] = []
    for path in _history_files():
        payload = json.loads(path.read_text())
        if payload.get("mode") != "screen":
            continue
        screen_date = payload.get("screen_date")
        if not screen_date:
            continue
        exported_at = datetime.fromisoformat(payload.get("exported_at") or datetime.now(timezone.utc).isoformat())
        payloads.append((exported_at, screen_date, payload.get("candidates", [])))

    if not payloads:
        return [], []

    if all_history_mode_enabled():
        selected_payloads: list[tuple[datetime, str, list[dict]]] = []
        latest_by_date: dict[str, tuple[datetime, str, list[dict]]] = {}
        for exported_at, screen_date, candidates in payloads:
            previous = latest_by_date.get(screen_date)
            if previous is None or exported_at > previous[0]:
                latest_by_date[screen_date] = (exported_at, screen_date, candidates)
        selected_payloads = list(latest_by_date.values())
        selected_dates = sorted({screen_date for _, screen_date, _ in payloads})
    else:
        latest_completed_screen_date = max(
            (
                screen_date
                for _, screen_date, _ in payloads
                if date.fromisoformat(screen_date) < date.today()
            ),
            default=max(screen_date for _, screen_date, _ in payloads),
        )
        selected_payloads = [
            item for item in payloads
            if item[1] == latest_completed_screen_date
        ]
        selected_payloads = [max(selected_payloads, key=lambda item: item[0])]
        selected_dates = [latest_completed_screen_date]

    loaded_predictions: list[dict] = []
    for _, screen_date, candidates in selected_payloads:
        loaded_predictions.extend(candidate | {"screen_date": screen_date} for candidate in candidates)
    return loaded_predictions, selected_dates


def _normalize_team_abbr(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z]", "", (value or "")).upper()
    if not cleaned:
        return ""
    return TEAM_ABBR_ALIASES.get(cleaned, cleaned)


def _get_prop_value(log: PlayerGameLog, prop_type: str) -> float:
    if prop_type == "PTS":
        return float(log.points)
    if prop_type == "REB":
        return float(log.rebounds)
    if prop_type == "AST":
        return float(log.assists)
    if prop_type == "PRA":
        return float(log.pra)
    if prop_type == "P+A":
        return float(log.points_assists)
    if prop_type == "P+R":
        return float(log.points_rebounds)
    if prop_type == "R+A":
        return float(log.rebounds_assists)
    if prop_type == "3PM":
        return float(log.threes_made)
    raise ValueError(f"Unsupported prop type: {prop_type}")


def _resolve_outcome(side: str, line: float, actual: float) -> tuple[str, float]:
    if actual == line:
        return "push", 0.0
    if side == "OVER":
        return ("win", actual - line) if actual > line else ("loss", actual - line)
    return ("win", line - actual) if actual < line else ("loss", line - actual)


def _game_key(screen_date: date, team: str, opponent: str) -> tuple[date, str, str]:
    return (screen_date, _normalize_team_abbr(team), _normalize_team_abbr(opponent))


def _latest_log_date(logs: list[PlayerGameLog]) -> date | None:
    if not logs:
        return None
    return max(log.game_date for log in logs)


def _merge_logs(primary_logs: list[PlayerGameLog], supplemental_logs: list[PlayerGameLog]) -> list[PlayerGameLog]:
    merged: dict[tuple[date, str, str], PlayerGameLog] = {}
    for log in supplemental_logs + primary_logs:
        key = (log.game_date, log.team, log.opponent)
        if key not in merged:
            merged[key] = log
    return sorted(merged.values(), key=lambda item: item.game_date, reverse=True)


def _logs_are_stale_for_date(screen_date: date, logs: list[PlayerGameLog]) -> bool:
    latest_date = _latest_log_date(logs)
    if latest_date is None:
        return True
    if not _is_playoff_window(screen_date):
        return latest_date < screen_date - timedelta(days=5)
    return latest_date < screen_date - timedelta(days=2)


def _load_logs_for_prediction(
    prediction: dict,
    bref_source: BasketballReferenceSource,
    fallback_source: EspnGameLogSource,
) -> list[PlayerGameLog]:
    screen_date = date.fromisoformat(prediction["screen_date"])
    season = season_end_year(screen_date)
    player_name = prediction["player_name"]
    team = prediction["team"]
    try:
        logs = bref_source.fetch_logs(player_name, team, season)
        if not _is_playoff_window(screen_date):
            return logs
        latest_date = _latest_log_date(logs)
        if latest_date is not None and latest_date >= screen_date - timedelta(days=2):
            return logs
        fallback_logs = fallback_source.fetch_logs(player_name, team, season)
        if not fallback_logs:
            return logs
        return _merge_logs(logs, fallback_logs)
    except Exception:
        return fallback_source.fetch_logs(player_name, team, season)


def _refresh_stale_logs_for_prediction(
    prediction: dict,
    bref_source: BasketballReferenceSource,
    fallback_source: EspnGameLogSource,
) -> list[PlayerGameLog]:
    try:
        return _load_logs_for_prediction(prediction, bref_source, fallback_source)
    except Exception:
        return []


def _find_game_id_for_prediction(
    prediction: dict,
    games_by_date: dict[date, list],
    slate_source: EspnSlateSource,
) -> str | None:
    screen_date = date.fromisoformat(prediction["screen_date"])
    if screen_date not in games_by_date:
        try:
            games_by_date[screen_date] = slate_source.fetch_games(screen_date)
        except Exception:
            games_by_date[screen_date] = []
    expected_key = {
        _normalize_team_abbr(prediction["team"]),
        _normalize_team_abbr(prediction["opponent"]),
    }
    for game in games_by_date[screen_date]:
        game_key = {_normalize_team_abbr(game.home_team), _normalize_team_abbr(game.away_team)}
        if game_key == expected_key:
            return game.game_id
    return None


def _resolve_via_boxscore(
    prediction: dict,
    games_by_date: dict[date, list],
    slate_source: EspnSlateSource,
    boxscore_cache: dict[str, dict],
    boxscore_source: EspnBoxscoreSource,
) -> ResolvedPrediction | None:
    game_id = _find_game_id_for_prediction(prediction, games_by_date, slate_source)
    if not game_id:
        return None
    if game_id not in boxscore_cache:
        try:
            boxscore_cache[game_id] = boxscore_source.fetch_boxscore(game_id)
        except Exception:
            boxscore_cache[game_id] = {}
    stat_lines = boxscore_cache[game_id]
    player_key = (normalize_name(prediction["player_name"]), _normalize_team_abbr(prediction["team"]))
    stat_line = stat_lines.get(player_key)
    if stat_line is None:
        return None
    pseudo_log = PlayerGameLog(
        player_name_raw=stat_line.player_name_raw,
        player_name_norm=stat_line.player_name_norm,
        game_date=date.fromisoformat(prediction["screen_date"]),
        team=stat_line.team,
        opponent=prediction["opponent"],
        minutes=stat_line.minutes,
        points=stat_line.points,
        rebounds=stat_line.rebounds,
        assists=stat_line.assists,
        threes_made=stat_line.threes_made,
        did_play=stat_line.minutes > 0 or any(
            value > 0 for value in (stat_line.points, stat_line.rebounds, stat_line.assists, stat_line.threes_made)
        ),
        source="espn_boxscore",
    )
    actual = _get_prop_value(pseudo_log, prediction["prop_type"])
    outcome, edge = _resolve_outcome(prediction["side"], float(prediction["line"]), actual)
    return ResolvedPrediction(
        screen_date=date.fromisoformat(prediction["screen_date"]),
        player_name=prediction["player_name"],
        team=prediction["team"],
        opponent=prediction["opponent"],
        prop_type=prediction["prop_type"],
        side=prediction["side"],
        line=float(prediction["line"]),
        score=int(prediction["score"]),
        flags=list(prediction.get("flags", [])),
        actual=actual,
        outcome=outcome,
        edge=edge,
        resolution_method="boxscore",
    )


def _match_log(prediction: dict, logs: list[PlayerGameLog]) -> tuple[PlayerGameLog | None, str | None, str | None]:
    screen_date = date.fromisoformat(prediction["screen_date"])
    normalized_opponent = _normalize_team_abbr(prediction["opponent"])
    same_date_logs = [log for log in logs if log.game_date == screen_date]
    if not same_date_logs:
        latest_log_date = _latest_log_date(logs)
        if latest_log_date is not None and latest_log_date < screen_date:
            return None, None, "source_stale"
        return None, None, "no_log_on_date"

    exact_opponent_logs = [log for log in same_date_logs if log.opponent == prediction["opponent"]]
    if len(exact_opponent_logs) == 1:
        return exact_opponent_logs[0], "exact", None
    if len(exact_opponent_logs) > 1:
        return None, None, "ambiguous_exact_opponent_match"

    normalized_opponent_logs = [
        log for log in same_date_logs if _normalize_team_abbr(log.opponent) == normalized_opponent
    ]
    if len(normalized_opponent_logs) == 1:
        return normalized_opponent_logs[0], "normalized_opponent", None
    if len(normalized_opponent_logs) > 1:
        return None, None, "ambiguous_normalized_opponent_match"

    if len(same_date_logs) == 1:
        return same_date_logs[0], "date_only", None
    return None, None, "ambiguous_date_match"


def _resolve_predictions(predictions: list[dict]) -> ResolutionReport:
    settings = load_settings()
    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    bref_source = BasketballReferenceSource(shared_cache, sticky_daily_cache=settings.sticky_daily_log_cache)
    fallback_source = EspnGameLogSource(shared_cache)
    slate_source = EspnSlateSource()
    boxscore_source = EspnBoxscoreSource(shared_cache)
    refresh_root = CACHE_DIR / "backtest_refresh"
    if refresh_root.exists():
        rmtree(refresh_root)
    refresh_cache = JsonCache(refresh_root, ttl_hours=settings.cache_ttl_hours)
    refresh_bref_source = BasketballReferenceSource(refresh_cache, sticky_daily_cache=False)
    refresh_fallback_source = EspnGameLogSource(refresh_cache)
    logs_cache: dict[tuple[str, str, int], list[PlayerGameLog]] = {}
    games_by_date: dict[date, list] = {}
    boxscore_cache: dict[str, dict] = {}
    resolved: list[ResolvedPrediction] = []
    unresolved: list[UnresolvedPrediction] = []
    voided: list[VoidPrediction] = []
    pending = 0
    today = date.today()
    resolved_game_keys: set[tuple[date, str, str]] = set()

    for prediction in predictions:
        screen_date = date.fromisoformat(prediction["screen_date"])
        if screen_date >= today:
            pending += 1
            continue
        cache_key = (prediction["player_name"], prediction["team"], season_end_year(screen_date))
        if cache_key not in logs_cache:
            try:
                logs_cache[cache_key] = _load_logs_for_prediction(prediction, bref_source, fallback_source)
            except Exception:
                logs_cache[cache_key] = []
        logs = logs_cache[cache_key]
        if _logs_are_stale_for_date(screen_date, logs):
            unresolved.append(
                UnresolvedPrediction(
                    screen_date=screen_date,
                    player_name=prediction["player_name"],
                    team=prediction["team"],
                    opponent=prediction["opponent"],
                    prop_type=prediction["prop_type"],
                    side=prediction["side"],
                    line=float(prediction["line"]),
                    reason="source_stale",
                )
            )
            continue
        latest_log_date = _latest_log_date(logs)
        if latest_log_date is not None and latest_log_date < screen_date:
            refreshed_logs = _refresh_stale_logs_for_prediction(prediction, refresh_bref_source, refresh_fallback_source)
            refreshed_latest_log_date = _latest_log_date(refreshed_logs)
            if refreshed_latest_log_date is not None and refreshed_latest_log_date >= latest_log_date:
                logs = refreshed_logs
                logs_cache[cache_key] = refreshed_logs
        if _logs_are_stale_for_date(screen_date, logs):
            unresolved.append(
                UnresolvedPrediction(
                    screen_date=screen_date,
                    player_name=prediction["player_name"],
                    team=prediction["team"],
                    opponent=prediction["opponent"],
                    prop_type=prediction["prop_type"],
                    side=prediction["side"],
                    line=float(prediction["line"]),
                    reason="source_stale",
                )
            )
            continue
        if not logs:
            unresolved.append(
                UnresolvedPrediction(
                    screen_date=screen_date,
                    player_name=prediction["player_name"],
                    team=prediction["team"],
                    opponent=prediction["opponent"],
                    prop_type=prediction["prop_type"],
                    side=prediction["side"],
                    line=float(prediction["line"]),
                    reason="no_logs_loaded",
                )
            )
            continue
        matching_log, resolution_method, unresolved_reason = _match_log(prediction, logs)
        if matching_log is None:
            if unresolved_reason == "source_stale":
                boxscore_resolution = _resolve_via_boxscore(
                    prediction=prediction,
                    games_by_date=games_by_date,
                    slate_source=slate_source,
                    boxscore_cache=boxscore_cache,
                    boxscore_source=boxscore_source,
                )
                if boxscore_resolution is not None:
                    resolved.append(boxscore_resolution)
                    resolved_game_keys.add(_game_key(screen_date, prediction["team"], prediction["opponent"]))
                    continue
            unresolved.append(
                UnresolvedPrediction(
                    screen_date=screen_date,
                    player_name=prediction["player_name"],
                    team=prediction["team"],
                    opponent=prediction["opponent"],
                    prop_type=prediction["prop_type"],
                    side=prediction["side"],
                    line=float(prediction["line"]),
                    reason=unresolved_reason or "unmatched",
                )
            )
            continue
        actual = _get_prop_value(matching_log, prediction["prop_type"])
        outcome, edge = _resolve_outcome(prediction["side"], float(prediction["line"]), actual)
        resolved.append(
            ResolvedPrediction(
                screen_date=screen_date,
                player_name=prediction["player_name"],
                team=prediction["team"],
                opponent=prediction["opponent"],
                prop_type=prediction["prop_type"],
                side=prediction["side"],
                line=float(prediction["line"]),
                score=int(prediction["score"]),
                flags=list(prediction.get("flags", [])),
                actual=actual,
                outcome=outcome,
                edge=edge,
                resolution_method=resolution_method or "exact",
            )
        )
        resolved_game_keys.add(_game_key(screen_date, prediction["team"], prediction["opponent"]))

    still_unresolved: list[UnresolvedPrediction] = []
    for row in unresolved:
        if row.reason == "no_log_on_date" and _game_key(row.screen_date, row.team, row.opponent) in resolved_game_keys:
            voided.append(
                VoidPrediction(
                    screen_date=row.screen_date,
                    player_name=row.player_name,
                    team=row.team,
                    opponent=row.opponent,
                    prop_type=row.prop_type,
                    side=row.side,
                    line=row.line,
                    reason="void_dnp",
                )
            )
            continue
        still_unresolved.append(row)

    return ResolutionReport(
        resolved=resolved,
        voided=voided,
        unresolved=still_unresolved,
        pending=pending,
    )


def _score_band(score: int) -> str:
    if score >= 11:
        return "11+"
    return str(score)


def _hit_rate(rows: list[ResolvedPrediction]) -> float:
    graded = [row for row in rows if row.outcome != "push"]
    if not graded:
        return 0.0
    wins = sum(1 for row in graded if row.outcome == "win")
    return wins / len(graded)


def _average_edge(rows: list[ResolvedPrediction]) -> float:
    if not rows:
        return 0.0
    return mean(row.edge for row in rows)


def _render_score_bands(rows: list[ResolvedPrediction]) -> list[str]:
    bands: dict[str, list[ResolvedPrediction]] = defaultdict(list)
    for row in rows:
        bands[_score_band(row.score)].append(row)

    order = sorted(bands, key=lambda band: (99 if band == "11+" else int(band)))
    lines = ["Score bands:"]
    for band in order:
        band_rows = bands[band]
        graded = [row for row in band_rows if row.outcome != "push"]
        pushes = len(band_rows) - len(graded)
        avg_edge = mean(row.edge for row in band_rows) if band_rows else 0.0
        lines.append(
            f"- {band}: {len(band_rows)} plays, {(_hit_rate(band_rows) * 100):.1f}% hit, {pushes} pushes, avg edge {avg_edge:+.2f}"
        )
    return lines


def _render_flag_performance(rows: list[ResolvedPrediction], min_samples: int = 15) -> list[str]:
    flags_to_rows: dict[str, list[ResolvedPrediction]] = defaultdict(list)
    for row in rows:
        for flag in row.flags:
            flags_to_rows[flag].append(row)

    ranked = sorted(
        (
            (flag, flag_rows)
            for flag, flag_rows in flags_to_rows.items()
            if len(flag_rows) >= min_samples
        ),
        key=lambda item: (-_hit_rate(item[1]), -len(item[1]), item[0]),
    )
    lines = [f"Flag performance (min {min_samples} samples):"]
    if not ranked:
        lines.append("- No flags met the sample threshold yet.")
        return lines
    for flag, flag_rows in ranked[:15]:
        graded = [row for row in flag_rows if row.outcome != "push"]
        pushes = len(flag_rows) - len(graded)
        avg_edge = mean(row.edge for row in flag_rows) if flag_rows else 0.0
        lines.append(
            f"- {flag}: {len(flag_rows)} plays, {(_hit_rate(flag_rows) * 100):.1f}% hit, {pushes} pushes, avg edge {avg_edge:+.2f}"
        )
    return lines


def _render_prop_type_performance(rows: list[ResolvedPrediction]) -> list[str]:
    buckets: dict[str, list[ResolvedPrediction]] = defaultdict(list)
    for row in rows:
        buckets[row.prop_type].append(row)
    lines = ["Prop type performance:"]
    for prop_type in sorted(buckets):
        prop_rows = buckets[prop_type]
        pushes = sum(1 for row in prop_rows if row.outcome == "push")
        avg_edge = mean(row.edge for row in prop_rows) if prop_rows else 0.0
        lines.append(
            f"- {prop_type}: {len(prop_rows)} plays, {(_hit_rate(prop_rows) * 100):.1f}% hit, {pushes} pushes, avg edge {avg_edge:+.2f}"
        )
    return lines


def _render_resolution_methods(rows: list[ResolvedPrediction]) -> list[str]:
    methods = Counter(row.resolution_method for row in rows)
    lines = ["Resolution methods:"]
    for method, count in sorted(methods.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {method}: {count}")
    return lines


def _render_unresolved_breakdown(rows: list[UnresolvedPrediction], max_examples: int = 8) -> list[str]:
    lines = ["Unresolved breakdown:"]
    if not rows:
        lines.append("- No unresolved finished predictions.")
        return lines

    by_reason = Counter(row.reason for row in rows)
    for reason, count in sorted(by_reason.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {reason}: {count}")

    grouped_rows: dict[tuple[str, str], list[UnresolvedPrediction]] = defaultdict(list)
    for row in rows:
        grouped_rows[(row.player_name, row.reason)].append(row)

    ranked_groups = sorted(
        grouped_rows.items(),
        key=lambda item: (-len(item[1]), item[0][1], item[0][0]),
    )
    lines.append("- Unresolved players:")
    for (player_name, reason), player_rows in ranked_groups[:max_examples]:
        prop_labels = ", ".join(
            f"{row.prop_type} {row.side} {row.line:g}"
            for row in sorted(player_rows, key=lambda row: (row.prop_type, row.side, row.line))
        )
        first_row = player_rows[0]
        lines.append(
            f"  {player_name} ({first_row.team} vs {first_row.opponent}) [{reason}] "
            f"- {len(player_rows)} props: {prop_labels}"
        )
    if len(ranked_groups) > max_examples:
        lines.append(f"  ... and {len(ranked_groups) - max_examples} more players")
    return lines


def _export_backtest_report(screen_date: str, report_text: str, mode: str = "single") -> Path:
    backtests_dir = OUTPUTS_DIR / "backtests"
    backtests_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if mode == "single" else "_all_history"
    path = backtests_dir / f"backtest_{screen_date}{suffix}.txt"
    path.write_text(report_text)
    return path


def main() -> int:
    predictions, included_dates = _load_latest_predictions()
    if not predictions:
        print("No screen run history found in outputs/history yet.")
        print("Run python3 run_nightly.py first to generate backtest snapshots.")
        return 0

    settings = load_settings()
    report = _resolve_predictions(predictions)
    resolved = report.resolved
    if not resolved and not report.voided:
        print("No finished predictions could be resolved yet.")
        print(f"- Stored predictions found: {len(predictions)}")
        print(f"- Pending/future predictions skipped: {report.pending}")
        print(f"- Unresolved finished predictions: {len(report.unresolved)}")
        return 0

    pushes = sum(1 for row in resolved if row.outcome == "push")
    by_side = Counter(row.side for row in resolved)
    displayed_resolved = [row for row in resolved if row.score >= settings.min_display_score]
    displayed_pushes = sum(1 for row in displayed_resolved if row.outcome == "push")
    displayed_by_side = Counter(row.side for row in displayed_resolved)

    lines: list[str] = []
    lines.append("Backtest summary:")
    if all_history_mode_enabled():
        lines.append(f"- Screen dates included: {included_dates[0]} to {included_dates[-1]} ({len(included_dates)} slates)")
    else:
        lines.append(f"- Screen date included: {included_dates[0]}")
    lines.append(f"- Latest unique predictions loaded: {len(predictions)}")
    lines.append(f"- Finished predictions graded: {len(resolved)}")
    lines.append(f"- Void/DNP predictions: {len(report.voided)}")
    lines.append(f"- Unresolved finished predictions: {len(report.unresolved)}")
    lines.append(f"- Pending/future predictions skipped: {report.pending}")
    if resolved:
        lines.append(f"- Overall hit rate (all qualified props): {(_hit_rate(resolved) * 100):.1f}%")
        lines.append(f"- Pushes: {pushes}")
        lines.append(f"- Average edge vs line: {_average_edge(resolved):+.2f}")
        lines.append(f"- Overs resolved: {by_side.get('OVER', 0)}")
        lines.append(f"- Unders resolved: {by_side.get('UNDER', 0)}")
        lines.append(
            f"- Displayed props graded (score >= {settings.min_display_score}): {len(displayed_resolved)}"
        )
        lines.append(f"- Displayed hit rate: {(_hit_rate(displayed_resolved) * 100):.1f}%")
        lines.append(f"- Displayed pushes: {displayed_pushes}")
        lines.append(f"- Displayed average edge vs line: {_average_edge(displayed_resolved):+.2f}")
        lines.append(f"- Displayed overs resolved: {displayed_by_side.get('OVER', 0)}")
        lines.append(f"- Displayed unders resolved: {displayed_by_side.get('UNDER', 0)}")
    lines.append("")
    if resolved:
        lines.extend(_render_score_bands(resolved))
        lines.append("")
        lines.extend(_render_prop_type_performance(resolved))
        lines.append("")
        lines.extend(_render_flag_performance(resolved))
        lines.append("")
        lines.extend(_render_resolution_methods(resolved))
        lines.append("")
    lines.extend(_render_unresolved_breakdown(report.unresolved))

    report_text = "\n".join(lines)
    print(report_text)
    export_path = _export_backtest_report(
        screen_date=included_dates[0] if len(included_dates) == 1 else f"{included_dates[0]}_to_{included_dates[-1]}",
        report_text=report_text + "\n",
        mode="all_history" if all_history_mode_enabled() else "single",
    )
    print(f"\n- Backtest report exported to {export_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
