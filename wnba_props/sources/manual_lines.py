from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..config import CONFIG_DIR, SUPPORTED_PROP_TYPES
from ..models import Game, PropLine
from ..utils import normalize_name


class ManualLineSource:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or CONFIG_DIR / "manual_lines.csv"
        self.failures: list[str] = []

    def fetch_prop_lines(self, games: Iterable[Game]) -> list[PropLine]:
        games = list(games)
        if not self.path.exists():
            self.failures.append(f"Manual line file not found: {self.path}")
            return []

        game_by_team_pair = {
            frozenset((game.home_team, game.away_team)): game
            for game in games
        }
        valid_teams = {game.home_team for game in games} | {game.away_team for game in games}
        collected_at = datetime.now(timezone.utc)
        lines: list[PropLine] = []

        with self.path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row_number, row in enumerate(reader, start=2):
                player_name = (row.get("player_name") or "").strip()
                team = (row.get("team") or "").strip().upper()
                opponent = (row.get("opponent") or "").strip().upper()
                prop_type = (row.get("prop_type") or "").strip().upper()
                line_text = (row.get("line") or "").strip()
                bookmaker = (row.get("bookmaker") or "manual").strip() or "manual"

                if not any([player_name, team, opponent, prop_type, line_text]):
                    continue
                if not player_name or not team or not opponent or not prop_type or not line_text:
                    self.failures.append(f"{self.path}:{row_number}: missing required line field")
                    continue
                if prop_type not in SUPPORTED_PROP_TYPES:
                    self.failures.append(f"{self.path}:{row_number}: unsupported prop type {prop_type}")
                    continue
                if team not in valid_teams or opponent not in valid_teams:
                    continue

                game = game_by_team_pair.get(frozenset((team, opponent)))
                if game is None:
                    self.failures.append(f"{self.path}:{row_number}: no matching slate game for {team}@{opponent}")
                    continue
                try:
                    line_value = float(line_text)
                except ValueError:
                    self.failures.append(f"{self.path}:{row_number}: invalid line value {line_text!r}")
                    continue

                lines.append(
                    PropLine(
                        event_id=game.game_id,
                        game_date=game.game_date,
                        player_name_raw=player_name,
                        player_name_norm=normalize_name(player_name),
                        team=team,
                        opponent=opponent,
                        prop_type=prop_type,
                        line=line_value,
                        bookmaker=bookmaker,
                        source="manual_lines",
                        collected_at=collected_at,
                    )
                )

        return sorted(lines, key=lambda line: (line.team, line.player_name_raw, line.prop_type))
