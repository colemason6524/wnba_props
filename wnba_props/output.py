from __future__ import annotations

from statistics import mean
from typing import Iterable
from .models import Candidate, PlayerGameLog, PropLine, TeamInjury


FLAG_LABELS = {
    "L10+": "Trend+",
    "SEASON-": "Season-",
    "THIN": "Thin",
    "LOW_MIN": "LowMin",
    "U": "Under",
    "BLOWOUT": "Blowout",
    "CLOSE": "Close",
    "HIGH_TOT": "HighTot",
    "LOW_TOT": "LowTot",
    "OPPCTX": "Opp",
    "MATCHUP_PLUS": "Matchup+",
    "MATCHUP_RISK": "MatchupRisk",
    "KEY_OUT": "KeyOut",
    "KEY_Q": "KeyQ",
    "TEAM_OUT": "TeamOut",
    "TEAM_Q": "TeamQ",
    "SELF_Q": "SelfQ",
    "KEY_BACK": "KeyBack",
    "SPLIT_BOOST": "Split+",
    "SPLIT_RISK": "SplitRisk",
    "SAMPLE_BLOW": "SampleBlow",
    "SAMPLE_VOL": "VolatileMin",
    "SAMPLE_CTX": "SampleCtx",
    "HOT_STRONG": "Hot+",
    "HOT_MOD": "Hot",
    "ROLE_UP": "RoleUp",
    "PLAYOFF_ROLE": "PlayoffRole",
    "LINE_HIGH": "LineHigh",
    "PRICED_IN": "Priced",
    "FRAGILE_HOT": "Fragile",
}


def render_candidates(
    candidates: Iterable[Candidate],
    limit: int = 25,
    min_score: int = 7,
    team_injuries: dict[str, list[TeamInjury]] | None = None,
    return_context: dict[str, list[str]] | None = None,
) -> str:
    candidate_list = [candidate for candidate in candidates if candidate.score >= min_score]
    over_candidates = [candidate for candidate in candidate_list if candidate.side == "OVER"]
    under_candidates = [candidate for candidate in candidate_list if candidate.side == "UNDER"]
    sections = []
    if team_injuries:
        injury_lines = _render_injury_summary(team_injuries)
        if injury_lines:
            sections.append(("Availability", injury_lines))
    if return_context:
        return_lines = _render_return_summary(return_context)
        if return_lines:
            sections.append(("Return Context", return_lines))
    if over_candidates:
        sections.append(("Overs", over_candidates))
    if under_candidates:
        sections.append(("Unders", under_candidates))
    if not sections:
        sections.append(("Candidates", []))

    rendered_sections = []
    for title, section_candidates in sections:
        rendered_sections.append(f"{title}:")
        if title in {"Availability", "Return Context"}:
            rendered_sections.extend(section_candidates)
        else:
            rendered_sections.extend(_render_table(section_candidates[:limit] if title == "Candidates" else section_candidates))
        rendered_sections.append("")

    rendered_sections.append("Legend: L5/L10 Hit = side-specific hits in recent played games; Spread = team spread; Total = game total; OppAvg = average stat value allowed by opponent from loaded season logs; Blowout/Close/HighTot/LowTot are game-context flags; Matchup+ / MatchupRisk = opponent matchup blended from season and recent allowance context; Hot+/Hot = recent momentum; RoleUp = minutes trend up; PlayoffRole = caution for role/momentum-driven overs in playoff context; LineHigh = elevated line; Priced = hot streak may be baked in; Fragile = hot streak with added risk; KeyOut/KeyQ/TeamOut/TeamQ/SelfQ are current availability flags; KeyBack = an important active teammate was absent for much of the recent sample; Split+ / SplitRisk = teammate splits support or push against tonight's setup; SampleBlow / VolatileMin / SampleCtx = recent sample may be game-scripted, minutes-unstable, or teammate-context distorted.")
    return "\n".join(rendered_sections)


def _render_table(candidates: list[Candidate]) -> list[str]:
    headers = [
        "Player",
        "Team",
        "Opp",
        "Prop",
        "Side",
        "Line",
        "Spread",
        "Total",
        "OppAvg",
        "L5 Hit",
        "L5 Ply",
        "L10 Hit",
        "L10 Ply",
        "Avg L5",
        "Avg L10",
        "Med L10",
        "Season",
        "Min L5",
        "Min L10",
        "Dlt L5",
        "Score",
        "Flags",
    ]
    rows = []
    for candidate in candidates:
        rows.append([
            candidate.player_name,
            candidate.team,
            candidate.opponent,
            candidate.prop_type,
            candidate.side,
            f"{candidate.line:.1f}",
            "" if candidate.spread is None else f"{candidate.spread:+.1f}",
            "" if candidate.total is None else f"{candidate.total:.1f}",
            "" if candidate.opp_avg is None else f"{candidate.opp_avg:.1f}",
            str(candidate.hits_last_5),
            str(candidate.played_last_5),
            str(candidate.hits_last_10),
            str(candidate.played_last_10),
            f"{candidate.avg_last_5:.1f}",
            f"{candidate.avg_last_10:.1f}",
            f"{candidate.median_last_10:.1f}",
            f"{candidate.season_avg:.1f}",
            f"{candidate.avg_minutes_last_5:.1f}",
            f"{candidate.avg_minutes_last_10:.1f}",
            f"{candidate.delta_avg_last_5:+.1f}",
            str(candidate.score),
            ",".join(_display_flag(flag) for flag in candidate.flags),
        ])

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    output = []
    output.append("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    output.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        output.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))

    if not rows:
        output.append("No props qualified for the current model, score threshold, and available pregame lines.")
    return output


def render_line_board(
    prop_lines: Iterable[PropLine],
    logs_by_player: dict[str, list[PlayerGameLog]],
    limit: int = 40,
) -> str:
    lines = list(prop_lines)
    rows = []
    for line in lines[:limit]:
        logs = logs_by_player.get(line.player_name_norm, [])
        team = line.team or (logs[0].team if logs else "")
        opponent = line.opponent or (logs[0].opponent if logs else "")
        values = [_get_prop_value(log, line.prop_type) for log in logs]
        last_5 = values[:5]
        last_10 = values[:10]
        over_l5 = sum(1 for value in last_5 if value > line.line)
        over_l10 = sum(1 for value in last_10 if value > line.line)
        under_l5 = sum(1 for value in last_5 if value < line.line)
        under_l10 = sum(1 for value in last_10 if value < line.line)
        rows.append(
            [
                line.player_name_raw,
                team,
                opponent,
                line.prop_type,
                f"{line.line:.1f}",
                _hit_cell(over_l5, len(last_5)),
                _hit_cell(over_l10, len(last_10)),
                _hit_cell(under_l5, len(last_5)),
                _hit_cell(under_l10, len(last_10)),
                _avg_cell(last_5),
                _avg_cell(last_10),
                _avg_cell(values),
                _avg_cell([log.minutes for log in logs[:5]]),
                _avg_cell([log.minutes for log in logs[:10]]),
            ]
        )

    headers = [
        "Player",
        "Team",
        "Opp",
        "Prop",
        "Line",
        "O L5",
        "O L10",
        "U L5",
        "U L10",
        "Avg L5",
        "Avg L10",
        "Season",
        "Min L5",
        "Min L10",
    ]
    return "\n".join(_render_rows(headers, rows, empty_message="No prop lines were available for the board."))


def _render_rows(headers: list[str], rows: list[list[str]], empty_message: str) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    output = []
    output.append("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    output.append("  ".join("-" * widths[index] for index in range(len(headers))))
    for row in rows:
        output.append("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)))

    if not rows:
        output.append(empty_message)
    return output


def _hit_cell(hits: int, played: int) -> str:
    return f"{hits}/{played}" if played else ""


def _avg_cell(values: list[float]) -> str:
    return f"{mean(values):.1f}" if values else ""


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


def _render_injury_summary(team_injuries: dict[str, list[TeamInjury]]) -> list[str]:
    lines: list[str] = []
    for team in sorted(team_injuries):
        impactful = [
            injury
            for injury in team_injuries[team]
            if injury.impact_level != "minor" or _is_uncertain_or_out(injury.status)
        ]
        if not impactful:
            continue
        bits = []
        for injury in impactful[:5]:
            impact = f"/{injury.impact_level.upper()}" if injury.impact_level != "minor" else ""
            bits.append(f"{injury.player_name} ({injury.status}{impact})")
        lines.append(f"- {team}: " + ", ".join(bits))
    return lines


def _render_return_summary(return_context: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    for team in sorted(return_context):
        contexts = return_context[team]
        if not contexts:
            continue
        lines.append(f"- {team}: " + ", ".join(contexts[:3]))
    return lines


def _is_uncertain_or_out(status: str) -> bool:
    lowered = status.lower()
    return lowered in {"out", "day-to-day", "questionable", "doubtful", "probable", "game-time decision"}


def _display_flag(flag: str) -> str:
    return FLAG_LABELS.get(flag, flag)
