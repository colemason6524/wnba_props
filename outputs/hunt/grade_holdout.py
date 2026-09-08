"""Exact capped Discord-policy grader for the August 2026 production holdout.

Read-only: history snapshots + copied caches. All writes stay under outputs/hunt/.
Protocol: outputs/hunt/HOLDOUT_PROTOCOL.md (frozen before grading).
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean, median

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from wnba_props.config import DISCORD_SUPPRESS_FLAGS  # noqa: E402
from wnba_props.models import Candidate  # noqa: E402
from wnba_props.output import _discord_sort_key  # noqa: E402

HISTORY_DIR = REPO_ROOT / "outputs" / "hunt" / "history"
SHARED_DIR = REPO_ROOT / "outputs" / "hunt" / "windows_cache" / "shared"
REFRESH_DIR = REPO_ROOT / "outputs" / "hunt" / "windows_cache" / "run_refresh"

START_DATE = "2026-08-03"
END_DATE = "2026-08-30"
SEED = 20260901
DRAWS = 10_000
DISCORD_MIN_SCORE = 8
DISCORD_LIMIT = 5

TEAM_ABBR_ALIASES = {
    "ATL": "ATL", "CHI": "CHI", "CON": "CON", "DAL": "DAL", "GS": "GS",
    "GSV": "GS", "IND": "IND", "LA": "LA", "LAS": "LA", "LV": "LV",
    "LVA": "LV", "MIN": "MIN", "NY": "NY", "NYL": "NY", "PHO": "PHX",
    "PHX": "PHX", "POR": "POR", "SEA": "SEA", "TOR": "TOR", "WAS": "WSH",
    "WSH": "WSH",
}

PROP_FIELDS = {
    "PTS": lambda r: r["points"],
    "REB": lambda r: r["rebounds"],
    "AST": lambda r: r["assists"],
    "PRA": lambda r: r["points"] + r["rebounds"] + r["assists"],
    "P+A": lambda r: r["points"] + r["assists"],
    "P+R": lambda r: r["points"] + r["rebounds"],
    "R+A": lambda r: r["rebounds"] + r["assists"],
    "3PM": lambda r: r["threes_made"],
}


def normalize_team(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z]", "", (value or "")).upper()
    return TEAM_ABBR_ALIASES.get(cleaned, cleaned)


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def american_to_decimal(american: int) -> float | None:
    if not american:
        return None
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def american_to_profit(american: int) -> float | None:
    if not american:
        return None
    return american / 100.0 if american > 0 else 100.0 / abs(american)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_slate_payloads(start: str, end: str) -> list[dict]:
    latest: dict[str, dict] = {}
    for path in sorted(HISTORY_DIR.glob("screen_run_*.json")):
        payload = json.loads(path.read_text())
        if payload.get("mode") != "screen":
            continue
        screen_date = payload.get("screen_date", "")
        if not (start <= screen_date <= end):
            continue
        exported = parse_iso(payload.get("exported_at", ""))
        key = exported or datetime.min.replace(tzinfo=timezone.utc)
        previous = latest.get(screen_date)
        if previous is None or key > (parse_iso(previous["exported_at"]) or datetime.min.replace(tzinfo=timezone.utc)):
            latest[screen_date] = payload | {"_file": path.name}
    return [latest[screen_date] for screen_date in sorted(latest)]


def build_log_store() -> tuple[dict[str, list[dict]], dict[str, str], dict[str, str]]:
    """Returns (logs_by_code, lookup_name_to_code, reverse_norm_to_code)."""
    logs_by_code: dict[str, list[dict]] = {}
    reverse: dict[str, str] = {}
    for path in sorted(SHARED_DIR.glob("bref_parsed_gamelog_*_2026_v2.json")):
        match = re.search(r"bref_parsed_gamelog_(.+?)_2026_v2", path.name)
        if not match:
            continue
        code = match.group(1)
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        rows = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            continue
        logs_by_code[code] = rows
        for row in rows:
            norm = row.get("player_name_norm")
            if norm and norm not in reverse:
                reverse[norm] = code

    lookup: dict[str, str] = {}
    for path in sorted(SHARED_DIR.glob("bref_player_lookup_*.json")):
        try:
            data = json.loads(path.read_text())["data"]
        except (json.JSONDecodeError, KeyError):
            continue
        code = data.get("player_code")
        norm = data.get("player_name_norm")
        if code and norm:
            lookup[norm] = code
            reverse.setdefault(norm, code)
    return logs_by_code, lookup, reverse


def build_boxscore_store(payloads: list[dict]) -> tuple[dict[str, list[dict]], set[tuple[str, frozenset]]]:
    """Returns (rows_by_event_id, confirmed_games) — boxscores keyed by ESPN event id."""
    event_to_date: dict[str, str] = {}
    for payload in payloads:
        for game in payload.get("games", []):
            event_id = str(game.get("game_id") or "")
            if event_id:
                event_to_date.setdefault(event_id, game.get("game_date", ""))
        for line in payload.get("prop_lines", []):
            event_id = str(line.get("event_id") or "")
            game_date = str(line.get("game_date") or "")
            if event_id and game_date:
                event_to_date.setdefault(event_id, game_date)

    rows_by_event: dict[str, list[dict]] = {}
    if REFRESH_DIR.exists():
        for path in sorted(REFRESH_DIR.glob("espn_boxscore_*.json")):
            event_id = re.sub(r"^espn_boxscore_|\.json$", "", path.name)
            try:
                payload = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if isinstance(rows, list):
                rows_by_event[event_id] = rows
    return rows_by_event, event_to_date


def build_confirmed_games(payloads: list[dict], logs_by_code: dict[str, list[dict]], rows_by_event: dict[str, list[dict]], event_to_date: dict[str, str]) -> set[tuple[str, frozenset]]:
    confirmed: set[tuple[str, frozenset]] = set()
    for rows in logs_by_code.values():
        for row in rows:
            game_date = row.get("game_date")
            team = normalize_team(row.get("team", ""))
            opponent = normalize_team(row.get("opponent", ""))
            if game_date and team and opponent:
                confirmed.add((str(game_date), frozenset({team, opponent})))
    for event_id, rows in rows_by_event.items():
        game_date = event_to_date.get(event_id, "")
        for row in rows:
            team = normalize_team(row.get("team", ""))
            if game_date and team:
                confirmed.add((str(game_date), frozenset({team})))
    return confirmed


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_row(
    row: dict,
    logs_by_code: dict[str, list[dict]],
    lookup: dict[str, str],
    reverse: dict[str, str],
    rows_by_event: dict[str, list[dict]],
    event_to_date: dict[str, str],
    confirmed_games: set[tuple[str, frozenset]],
) -> dict:
    screen_date = row["screen_date"]
    player_norm = re.sub(r"[^a-z0-9 ]", "", row["player_name"].lower()).strip()
    code = lookup.get(player_norm) or reverse.get(player_norm)
    team = normalize_team(row["team"])
    opponent = normalize_team(row["opponent"])
    base = {
        "screen_date": screen_date,
        "player_name": row["player_name"],
        "team": team,
        "opponent": opponent,
        "prop_type": row["prop_type"],
        "side": row["side"],
        "line": float(row["line"]),
        "score": int(row["score"]),
        "flags": list(row.get("flags", [])),
        "american_odds": row.get("american_odds"),
        "decimal_odds": row.get("decimal_odds"),
    }
    if code is None:
        return base | {"status": "unresolved", "reason": "player_not_in_cache", "actual": None, "method": None}
    logs = logs_by_code.get(code, [])
    same_date = [log for log in logs if str(log.get("game_date")) == screen_date]
    exact = [log for log in same_date if log.get("opponent") == row["opponent"]]
    normalized = [log for log in same_date if normalize_team(log.get("opponent", "")) == opponent]
    if len(exact) == 1:
        match, method = exact[0], "exact"
    elif not exact and len(normalized) == 1:
        match, method = normalized[0], "normalized_opponent"
    elif not exact and not normalized and len(same_date) == 1:
        match, method = same_date[0], "date_only"
    elif len(exact) > 1 or len(normalized) > 1 or len(same_date) > 1:
        return base | {"status": "unresolved", "reason": "ambiguous_log_match", "actual": None, "method": None}
    else:
        if (screen_date, frozenset({team, opponent})) in confirmed_games:
            return base | {"status": "void_dnp", "reason": "dnp_no_log_row", "actual": None, "method": None}
        boxscore = _resolve_via_boxscore(screen_date, team, opponent, player_norm, rows_by_event, event_to_date)
        if boxscore is not None:
            match, method = boxscore, "espn_boxscore"
        else:
            return base | {"status": "unresolved", "reason": "game_not_confirmed", "actual": None, "method": None}

    extractor = PROP_FIELDS.get(row["prop_type"])
    if extractor is None:
        return base | {"status": "unresolved", "reason": f"unsupported_prop_type:{row['prop_type']}", "actual": None, "method": method}
    actual = float(extractor(match))
    line = float(row["line"])
    if actual == line:
        outcome = "push"
    elif row["side"] == "OVER":
        outcome = "win" if actual > line else "loss"
    else:
        outcome = "win" if actual < line else "loss"
    return base | {"status": "resolved", "reason": None, "actual": actual, "outcome": outcome, "method": method}


def _resolve_via_boxscore(screen_date, team, opponent, player_norm, rows_by_event, event_to_date):
    for event_id, rows in rows_by_event.items():
        if event_to_date.get(event_id) != screen_date:
            continue
        for row in rows:
            row_team = normalize_team(row.get("team", ""))
            row_norm = row.get("player_name_norm", "")
            if row_team == team and row_norm == player_norm:
                return row
    return None


# ---------------------------------------------------------------------------
# Policy application
# ---------------------------------------------------------------------------

def apply_policy(rows: list[dict], cap: bool) -> tuple[list[dict], dict]:
    counts = {"total": len(rows)}
    eligible = [row for row in rows if row["score"] >= DISCORD_MIN_SCORE]
    counts["score_eligible"] = len(eligible)
    suppressed = [row for row in eligible if any(flag in DISCORD_SUPPRESS_FLAGS for flag in row.get("flags", []))]
    counts["suppressed"] = len(suppressed)
    clean = [row for row in eligible if row not in suppressed]

    seen: set[tuple] = set()
    deduped: list[dict] = []
    duplicates = 0
    for row in sorted(clean, key=_sort_key, reverse=True):
        key = (row["screen_date"], row["player_name"], row["prop_type"], row["side"], float(row["line"]))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        deduped.append(row)
    counts["duplicates_removed"] = duplicates
    counts["uncapped"] = len(deduped)
    if not cap:
        return deduped, counts
    capped: list[dict] = []
    by_slate: dict[str, list[dict]] = defaultdict(list)
    for row in deduped:
        by_slate[row["screen_date"]].append(row)
    dropped_over = dropped_under = 0
    for slate_rows in by_slate.values():
        overs = [row for row in slate_rows if row["side"] == "OVER"][:DISCORD_LIMIT]
        unders = [row for row in slate_rows if row["side"] == "UNDER"][:DISCORD_LIMIT]
        dropped_over += sum(1 for row in slate_rows if row["side"] == "OVER") - len(overs)
        dropped_under += sum(1 for row in slate_rows if row["side"] == "UNDER") - len(unders)
        capped.extend(overs + unders)
    counts["cap_dropped_over"] = dropped_over
    counts["cap_dropped_under"] = dropped_under
    return capped, counts


class _SortAdapter:
    """Adapts dict rows to the production _discord_sort_key via Candidate."""

    def __init__(self, row: dict) -> None:
        self.row = row
        self.candidate = Candidate(
            player_name=row["player_name"], team=row["team"], opponent=row["opponent"],
            prop_type=row["prop_type"], side=row["side"], line=float(row["line"]),
            bookmaker=row.get("bookmaker", ""), hits_last_5=int(row.get("hits_last_5", 0)),
            played_last_5=int(row.get("played_last_5", 0)), hits_last_10=int(row.get("hits_last_10", 0)),
            played_last_10=int(row.get("played_last_10", 0)), avg_last_5=float(row.get("avg_last_5", 0.0)),
            avg_last_10=float(row.get("avg_last_10", 0.0)), median_last_5=float(row.get("median_last_5", 0.0)),
            median_last_10=float(row.get("median_last_10", 0.0)), season_avg=float(row.get("season_avg", 0.0)),
            avg_minutes_last_5=float(row.get("avg_minutes_last_5", 0.0)),
            avg_minutes_last_10=float(row.get("avg_minutes_last_10", 0.0)),
            delta_avg_last_5=float(row.get("delta_avg_last_5", 0.0)), score=int(row["score"]),
            american_odds=row.get("american_odds"), decimal_odds=row.get("decimal_odds"),
            flags=list(row.get("flags", [])),
        )

    def key(self):
        return _discord_sort_key(self.candidate)


def _sort_key(row: dict):
    return _SortAdapter(row).key()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def profit_units(resolved: dict, dnp_as_loss: bool = False) -> float | None:
    if resolved["status"] == "void_dnp":
        return -1.0 if dnp_as_loss else 0.0
    if resolved["status"] != "resolved":
        return None
    if resolved["outcome"] == "push":
        return 0.0
    american = resolved.get("american_odds")
    if american:
        if resolved["outcome"] == "loss":
            return -1.0
        return american_to_profit(int(american))
    decimal = resolved.get("decimal_odds")
    if decimal and decimal > 1.0:
        return -1.0 if resolved["outcome"] == "loss" else decimal - 1.0
    return None


def tally_record(priced: list[tuple[dict, float]], dnp_as_loss: bool = False) -> dict:
    """Pure W-L-push-void tally over priced ``(row, units)`` pairs.

    Void (DNP) rows carry 0 units and are reported separately — they are not
    losses and do not enter the hit-rate denominator. Only under the
    ``dnp_as_loss`` sensitivity (where the void row already carries -1u) do
    they count as losses, so the record stays consistent with the units.
    """
    wins = losses = pushes = voids = 0
    for row, _ in priced:
        if row.get("status") == "void_dnp":
            if dnp_as_loss:
                losses += 1
            else:
                voids += 1
        elif row.get("outcome") == "win":
            wins += 1
        elif row.get("outcome") == "loss":
            losses += 1
        elif row.get("outcome") == "push":
            pushes += 1
    decided = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": voids,
        "decided": decided,
        "hit_rate": wins / decided if decided else 0.0,
    }


def summarize_policy(resolved_rows: list[dict], dnp_as_loss: bool = False) -> dict:
    priced = []
    unpriced = 0
    for row in resolved_rows:
        units = profit_units(row, dnp_as_loss=dnp_as_loss)
        if units is None:
            unpriced += 1
        else:
            priced.append((row, units))
    record = tally_record(priced, dnp_as_loss=dnp_as_loss)
    units_total = sum(units for _, units in priced)
    n = len(priced)
    roi = units_total / n if n else 0.0
    implied = [american_to_decimal(int(row["american_odds"])) for row, _ in priced if row.get("american_odds")]
    implied = [value for value in implied if value]
    breakeven = mean(1.0 / value for value in implied) if implied else 0.0
    return {
        "n": n,
        "unpriced": unpriced,
        "decided": record["decided"],
        "pushes": record["pushes"],
        "voids": record["voids"],
        "wins": record["wins"],
        "losses": record["losses"],
        "hit_rate": record["hit_rate"],
        "units": units_total,
        "roi": roi,
        "breakeven_hit_rate": breakeven,
        "rows": priced,
    }


def record_label(summary: dict) -> str:
    """``W-L`` plus optional ``-Np`` push and ``(void N)`` suffixes."""
    text = f"{summary['wins']}-{summary['losses']}"
    if summary.get("pushes"):
        text += f"-{summary['pushes']}p"
    if summary.get("voids"):
        text += f" (void {summary['voids']})"
    return text


def clustered_bootstrap(slate_rows: dict[str, list[dict]], draws: int, seed: int, dnp_as_loss: bool = False) -> dict:
    slates = sorted(slate_rows)
    rng = random.Random(seed)
    rois: list[float] = []
    hits: list[float] = []
    for _ in range(draws):
        sample = [rng.choice(slates) for _ in range(len(slates))]
        rows = [row for slate in sample for row in slate_rows[slate]]
        summary = summarize_policy(rows, dnp_as_loss=dnp_as_loss)
        if summary["n"]:
            rois.append(summary["roi"])
        if summary["decided"]:
            hits.append(summary["hit_rate"])

    def interval(values: list[float]) -> dict:
        if not values:
            return {"low": None, "high": None}
        ordered = sorted(values)
        low = ordered[int(0.025 * len(ordered))]
        high = ordered[min(len(ordered) - 1, int(0.975 * len(ordered)))]
        return {"low": low, "high": high}

    return {
        "roi_ci95": interval(rois),
        "hit_rate_ci95": interval(hits),
        "roi_draws": len(rois),
        "hit_rate_draws": len(hits),
    }


def paired_diff(slate_rows_capped: dict[str, list[dict]], slate_rows_uncapped: dict[str, list[dict]], draws: int, seed: int) -> dict:
    slates = sorted(slate_rows_capped)
    rng = random.Random(seed ^ 0x5EED)
    diffs: list[float] = []
    for _ in range(draws):
        sample = [rng.choice(slates) for _ in range(len(slates))]
        capped_rows = [row for slate in sample for row in slate_rows_capped[slate]]
        uncapped_rows = [row for slate in sample for row in slate_rows_uncapped[slate]]
        capped = summarize_policy(capped_rows)
        uncapped = summarize_policy(uncapped_rows)
        if capped["n"] and uncapped["n"]:
            diffs.append(capped["roi"] - uncapped["roi"])
    if not diffs:
        return {"low": None, "high": None, "mean": None}
    ordered = sorted(diffs)
    return {
        "low": ordered[int(0.025 * len(ordered))],
        "high": ordered[min(len(ordered) - 1, int(0.975 * len(ordered)))],
        "mean": mean(diffs),
    }


def split_table(rows: list[dict], key_fn, title: str) -> list[str]:
    groups: dict = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    lines = [f"{title}:"]
    for key in sorted(groups, key=str):
        summary = summarize_policy(groups[key])
        decided = summary["decided"]
        lines.append(
            f"- {key}: n={summary['n']}, record {record_label(summary)}"
            f", hit {summary['hit_rate'] * 100:.1f}%, units {summary['units']:+.2f}, ROI {summary['roi'] * 100:+.2f}%"
            if decided else f"- {key}: n={summary['n']}, no decided rows"
        )
    return lines


def odds_band(row: dict) -> str:
    american = row.get("american_odds")
    if not american:
        return "unpriced"
    magnitude = abs(int(american))
    if magnitude < 150:
        return "|odds|<150"
    if magnitude <= 200:
        return "150<=|odds|<=200"
    return "|odds|>200"


# ---------------------------------------------------------------------------
# Price integrity audit
# ---------------------------------------------------------------------------

def price_integrity_audit(payloads: list[dict]) -> dict:
    mismatches = 0
    checked = 0
    vigs: list[float] = []
    leads: list[float] = []
    post_tip = []
    missing_tip = 0
    lines_total = 0
    per_slate: list[dict] = []
    for payload in payloads:
        exported = parse_iso(payload.get("exported_at", ""))
        games = payload.get("games", [])
        tips = [parse_iso(game.get("game_time", "")) for game in games]
        tips = [tip for tip in tips if tip]
        slate_info = {
            "screen_date": payload["screen_date"],
            "file": payload.get("_file"),
            "games": len(games),
            "prop_lines": len(payload.get("prop_lines", [])),
            "snapshot_before_tip": bool(exported and tips and exported < min(tips)),
            "minutes_before_tip": round((min(tips) - exported).total_seconds() / 60.0, 1) if exported and tips else None,
        }
        if exported and tips:
            slate_info["earliest_tip"] = min(tips).isoformat()
            slate_info["exported_at"] = exported.isoformat()
        per_slate.append(slate_info)

        for line in payload.get("prop_lines", []):
            lines_total += 1
            american_over = line.get("over_odds")
            decimal_over = line.get("over_decimal")
            if american_over and decimal_over:
                checked += 1
                expected = american_to_decimal(int(american_over))
                if expected is None or abs(expected - float(decimal_over)) > 0.005:
                    mismatches += 1
            if american_over and line.get("under_odds"):
                p_over = 1.0 / (american_to_decimal(int(american_over)) or 2.0)
                p_under = 1.0 / (american_to_decimal(int(line["under_odds"])) or 2.0)
                vigs.append(p_over + p_under - 1.0)
            collected = parse_iso(line.get("collected_at", ""))
            game_tip = None
            for game in games:
                if str(game.get("game_id") or "") == str(line.get("event_id") or ""):
                    game_tip = parse_iso(game.get("game_time", ""))
                    break
            if collected and game_tip:
                lead = (game_tip - collected).total_seconds() / 60.0
                leads.append(lead)
                if lead < 0:
                    post_tip.append((payload["screen_date"], line.get("player_name_raw"), round(lead, 1)))
            elif collected and not game_tip:
                missing_tip += 1
    return {
        "lines_total": lines_total,
        "am_dec_checked": checked,
        "am_dec_mismatches": mismatches,
        "vig_n": len(vigs),
        "vig_mean": mean(vigs) if vigs else None,
        "vig_median": median(vigs) if vigs else None,
        "vig_min": min(vigs) if vigs else None,
        "vig_max": max(vigs) if vigs else None,
        "lead_minutes_n": len(leads),
        "lead_mean": mean(leads) if leads else None,
        "lead_median": median(leads) if leads else None,
        "post_tip_lines": post_tip,
        "missing_tip_mapping": missing_tip,
        "per_slate": per_slate,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    payloads = load_slate_payloads(START_DATE, END_DATE)
    if not payloads:
        print("No history payloads found in the window.")
        return 1

    logs_by_code, lookup, reverse = build_log_store()
    rows_by_event, event_to_date = build_boxscore_store(payloads)
    confirmed_games = build_confirmed_games(payloads, logs_by_code, rows_by_event, event_to_date)

    all_candidates: list[dict] = []
    for payload in payloads:
        for candidate in payload.get("candidates", []):
            all_candidates.append(candidate | {"screen_date": payload["screen_date"]})

    capped_rows, capped_counts = apply_policy(all_candidates, cap=True)
    uncapped_rows, uncapped_counts = apply_policy(all_candidates, cap=False)

    resolved_capped = [
        resolve_row(row, logs_by_code, lookup, reverse, rows_by_event, event_to_date, confirmed_games)
        for row in capped_rows
    ]
    resolved_uncapped = [
        resolve_row(row, logs_by_code, lookup, reverse, rows_by_event, event_to_date, confirmed_games)
        for row in uncapped_rows
    ]

    summary_capped = summarize_policy(resolved_capped)
    summary_uncapped = summarize_policy(resolved_uncapped)
    slate_rows_capped: dict[str, list[dict]] = defaultdict(list)
    slate_rows_uncapped: dict[str, list[dict]] = defaultdict(list)
    for row in resolved_capped:
        slate_rows_capped[row["screen_date"]].append(row)
    for row in resolved_uncapped:
        slate_rows_uncapped[row["screen_date"]].append(row)
    bootstrap = clustered_bootstrap(slate_rows_capped, DRAWS, SEED)
    bootstrap_loss = clustered_bootstrap(slate_rows_capped, DRAWS, SEED, dnp_as_loss=True)
    diff = paired_diff(slate_rows_capped, slate_rows_uncapped, DRAWS, SEED)

    unresolved = [row for row in resolved_capped if row["status"] == "unresolved"]
    suppressed = [row for row in all_candidates if row["score"] >= DISCORD_MIN_SCORE and any(flag in DISCORD_SUPPRESS_FLAGS for flag in row.get("flags", []))]
    resolved_suppressed = [
        resolve_row(row, logs_by_code, lookup, reverse, rows_by_event, event_to_date, confirmed_games)
        for row in suppressed
    ]
    summary_suppressed = summarize_policy(resolved_suppressed)

    integrity = price_integrity_audit(payloads)

    lines: list[str] = []
    lines.append("AUGUST 2026 PRODUCTION HOLDOUT — EXACT CAPPED DISCORD POLICY")
    lines.append(f"Window: {START_DATE}..{END_DATE} | slates: {len(payloads)} | seed: {SEED} | draws: {DRAWS}")
    lines.append(f"Policy: score >= {DISCORD_MIN_SCORE}, suppress {', '.join(sorted(DISCORD_SUPPRESS_FLAGS))}, sort = production _discord_sort_key, cap {DISCORD_LIMIT}/side, flat 1u at captured price")
    lines.append("Void (DNP) rows: 0u stake returned, excluded from W-L and hit rate (reports before 2026-09-08 counted voids as losses)")
    lines.append("")
    lines.append("Policy funnel (capped / uncapped):")
    lines.append(f"- Candidates in snapshots: {capped_counts['total']}")
    lines.append(f"- Score-eligible: {capped_counts['score_eligible']}")
    lines.append(f"- Suppressed by flags: {capped_counts['suppressed']}")
    lines.append(f"- Duplicates removed: {capped_counts['duplicates_removed']}")
    lines.append(f"- Uncapped eligible: {capped_counts['uncapped']} (cap dropped {capped_counts['cap_dropped_over']} over / {capped_counts['cap_dropped_under']} under)")
    lines.append(f"- Capped digest rows: {capped_counts['uncapped'] - capped_counts['cap_dropped_over'] - capped_counts['cap_dropped_under']}")
    lines.append("")
    lines.append("Resolution:")
    statuses = Counter(row["status"] for row in resolved_capped)
    reasons = Counter(row.get("reason") or "-" for row in resolved_capped if row["status"] != "resolved")
    lines.append(f"- Resolved: {statuses.get('resolved', 0)} | Void DNP: {statuses.get('void_dnp', 0)} | Unresolved: {statuses.get('unresolved', 0)}")
    for reason, count in reasons.most_common():
        lines.append(f"  - {reason}: {count}")
    methods = Counter(row.get("method") or "-" for row in resolved_capped if row["status"] == "resolved")
    for method, count in methods.most_common():
        lines.append(f"  - method {method}: {count}")
    for row in unresolved:
        lines.append(f"  unresolved: {row['screen_date']} {row['player_name']} {row['prop_type']} {row['side']} {row['line']:g} ({row.get('reason')})")
    lines.append("")

    def fmt_summary(name: str, summary: dict, boot: dict) -> list[str]:
        out = [
            f"{name}:",
            f"- Selections: {summary['n']} (unpriced {summary['unpriced']}), decided {summary['decided']}, pushes {summary['pushes']}, void DNP {summary['voids']}",
            f"- Record: {summary['wins']}-{summary['losses']}-{summary['voids']} (W-L-void; voids excluded from hit rate) | hit rate {summary['hit_rate'] * 100:.2f}% "
            f"(CI95 {boot['hit_rate_ci95']['low'] * 100:.2f}%..{boot['hit_rate_ci95']['high'] * 100:.2f}%)" if summary["decided"] else "- No decided rows",
            f"- Flat-stake units: {summary['units']:+.2f} | ROI {summary['roi'] * 100:+.2f}% "
            f"(CI95 {boot['roi_ci95']['low'] * 100:+.2f}%..{boot['roi_ci95']['high'] * 100:+.2f}%)" if summary["n"] else "- No priced rows",
            f"- Break-even hit rate at captured prices: {summary['breakeven_hit_rate'] * 100:.2f}%",
        ]
        return out

    lines.extend(fmt_summary("PRIMARY — capped digest", summary_capped, bootstrap))
    lines.append("")
    lines.extend(fmt_summary("Uncapped eligible (no 5/side cap)", summary_uncapped, clustered_bootstrap(slate_rows_uncapped, DRAWS, SEED)))
    lines.append(f"- Paired ROI diff (capped - uncapped): {diff['mean'] * 100:+.2f}% (CI95 {diff['low'] * 100:+.2f}%..{diff['high'] * 100:+.2f}%)" if diff["mean"] is not None else "- Paired diff unavailable")
    lines.append("")
    lines.extend(fmt_summary("Suppressed group (score-eligible, flag-suppressed)", summary_suppressed, clustered_bootstrap({payload["screen_date"]: [row for row in resolved_suppressed if row["screen_date"] == payload["screen_date"]] for payload in payloads}, DRAWS, SEED)))
    lines.append("")
    lines.append("Per-slate (capped digest):")
    for slate in sorted(slate_rows_capped):
        summary = summarize_policy(slate_rows_capped[slate])
        lines.append(
            f"- {slate}: n={summary['n']}, {record_label(summary)}, units {summary['units']:+.2f}, ROI {summary['roi'] * 100:+.1f}%"
        )
    loso = []
    for slate in sorted(slate_rows_capped):
        subset = {key: rows for key, rows in slate_rows_capped.items() if key != slate}
        summary = summarize_policy([row for rows in subset.values() for row in rows])
        if summary["n"]:
            loso.append((slate, summary["roi"], summary["n"]))
    if loso:
        worst = min(loso, key=lambda item: item[1])
        best = max(loso, key=lambda item: item[1])
        lines.append(f"- Leave-one-slate-out ROI range: {worst[1] * 100:+.2f}% (drop {worst[0]}) .. {best[1] * 100:+.2f}% (drop {best[0]})")
    lines.append("")
    lines.extend(split_table(resolved_capped, lambda row: row["side"], "By side (capped)"))
    lines.extend(split_table(resolved_capped, lambda row: row["prop_type"], "By prop type (capped)"))
    lines.extend(split_table(resolved_capped, odds_band, "By price band (capped)"))
    lines.extend(split_table(resolved_capped, lambda row: str(row["score"]), "By score (capped)"))
    lines.append("")
    lines.append("Sensitivity — DNP counted as loss:")
    summary_loss = summarize_policy(resolved_capped, dnp_as_loss=True)
    lines.append(f"- ROI {summary_loss['roi'] * 100:+.2f}% "
                 f"(CI95 {bootstrap_loss['roi_ci95']['low'] * 100:+.2f}%..{bootstrap_loss['roi_ci95']['high'] * 100:+.2f}%)")
    lines.append("")
    lines.append("Price integrity audit:")
    lines.append(f"- Lines in snapshots: {integrity['lines_total']} | american/decimal checked: {integrity['am_dec_checked']} | mismatches: {integrity['am_dec_mismatches']}")
    if integrity["vig_n"]:
        lines.append(f"- Two-sided vig (n={integrity['vig_n']}): mean {integrity['vig_mean'] * 100:.2f}%, median {integrity['vig_median'] * 100:.2f}%, range {integrity['vig_min'] * 100:.2f}%..{integrity['vig_max'] * 100:.2f}%")
    if integrity["lead_minutes_n"]:
        lines.append(f"- Line-collection lead vs tip (n={integrity['lead_minutes_n']}): median {integrity['lead_median']:.1f} min, mean {integrity['lead_mean']:.1f} min")
    lines.append(f"- Post-tip line collections: {len(integrity['post_tip_lines'])}")
    for entry in integrity["post_tip_lines"][:10]:
        lines.append(f"  - {entry}")
    bad_slate_timing = [info for info in integrity["per_slate"] if info["exported_at"] and not info["snapshot_before_tip"]]
    lines.append(f"- Snapshots exported after earliest tip: {len(bad_slate_timing)} of {len(payloads)}")
    for info in bad_slate_timing:
        lines.append(f"  - {info['screen_date']} ({info['file']}): exported {info['exported_at']}, earliest tip {info.get('earliest_tip')}")

    roi_low = bootstrap["roi_ci95"]["low"]
    verdict = (
        "POSITIVE — clustered ROI CI excludes zero"
        if roi_low is not None and roi_low > 0
        else "NOT ESTABLISHED — clustered ROI CI includes zero or negative"
    )
    lines.append("")
    lines.append(f"GATE G1 VERDICT: {verdict}")

    report_text = "\n".join(lines) + "\n"
    print(report_text)

    out_dir = REPO_ROOT / "outputs" / "hunt"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    text_path = out_dir / f"holdout_report_{stamp}.txt"
    text_path.write_text(report_text)
    json_payload = {
        "protocol": "outputs/hunt/HOLDOUT_PROTOCOL.md",
        "window": [START_DATE, END_DATE],
        "seed": SEED,
        "draws": DRAWS,
        "slates": len(payloads),
        "capped_counts": capped_counts,
        "uncapped_counts": uncapped_counts,
        "summary_capped": {key: value for key, value in summary_capped.items() if key != "rows"},
        "summary_uncapped": {key: value for key, value in summary_uncapped.items() if key != "rows"},
        "summary_suppressed": {key: value for key, value in summary_suppressed.items() if key != "rows"},
        "bootstrap_capped": bootstrap,
        "bootstrap_dnp_loss": bootstrap_loss,
        "paired_diff": diff,
        "resolved_capped": resolved_capped,
        "price_integrity": integrity | {"post_tip_lines": integrity["post_tip_lines"][:20]},
        "verdict": verdict,
    }
    json_path = out_dir / f"holdout_results_{stamp}.json"
    json_path.write_text(json.dumps(json_payload, indent=2, default=str))
    print(f"\nReport: {text_path}\nResults: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
