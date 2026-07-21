from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Iterable

from ..cache import JsonCache
from ..config import Settings
from ..models import Game, PropLine
from ..utils import fetch_text, normalize_name


PROP_TYPE_TO_PATH = {
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "3PM": "3-pt-made",
    "PRA": "ptsrebsasts",
    "P+A": "ptsasts",
    "P+R": "ptsrebs",
    "R+A": "rebsasts",
}

TEAM_ALIASES = {
    "GSV": "GS",
    "LAS": "LA",
    "PDX": "POR",
    "PHO": "PHX",
    "WAS": "WSH",
}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data).strip()
        if text:
            self.parts.append(text)


class PropCruncherSource:
    BASE_URL = "https://propcruncher.com/props/wnba/stats"

    def __init__(self, settings: Settings, cache: JsonCache) -> None:
        self.settings = settings
        self.cache = cache
        self.failures: list[str] = []

    def fetch_prop_lines(self, games: Iterable[Game]) -> list[PropLine]:
        games = list(games)
        game_by_pair = {frozenset((game.home_team, game.away_team)): game for game in games}
        lines: list[PropLine] = []
        collected_at = datetime.now(timezone.utc)
        for prop_type, path in PROP_TYPE_TO_PATH.items():
            if prop_type not in self.settings.supported_prop_types:
                continue
            try:
                html = self._fetch_page(path)
            except Exception as exc:  # noqa: BLE001
                self.failures.append(f"{prop_type}: PropCruncher page unavailable ({exc})")
                continue
            parsed_rows = self._parse_page(html, prop_type)
            for player_name, team, opponent, line_value in parsed_rows:
                game = game_by_pair.get(frozenset((team, opponent)))
                if game is None:
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
                        bookmaker="propcruncher_rankings",
                        source="propcruncher_rankings",
                        collected_at=collected_at,
                    )
                )
        return self._dedupe(lines)

    def _fetch_page(self, path: str) -> str:
        cache_key = f"propcruncher_wnba_{path}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        stale = self.cache.get_stale(cache_key)
        url = f"{self.BASE_URL}/{path}"
        try:
            html = fetch_text(url)
        except Exception:
            if stale is not None:
                return stale
            raise
        self.cache.set(cache_key, html)
        return html

    def _parse_page(self, html: str, prop_type: str) -> list[tuple[str, str, str, float]]:
        parts = self._text_parts_from_html(html)
        rows: list[tuple[str, str, str, float]] = []
        seen: set[tuple[str, str, str, float]] = set()

        for index in range(len(parts) - 7):
            if parts[index + 2].lower() != "vs":
                continue
            if parts[index + 5].lower() not in {"over", "under"}:
                continue
            if not re.fullmatch(r"[A-FS]", parts[index + 6]):
                continue
            if not re.fullmatch(r"\d+%", parts[index + 7]):
                continue
            try:
                line_value = float(parts[index + 4])
            except ValueError:
                continue
            row = (
                self._clean_player_name(parts[index]),
                self._normalize_team(parts[index + 1]),
                self._normalize_team(parts[index + 3]),
                line_value,
            )
            if row not in seen:
                seen.add(row)
                rows.append(row)
        return rows

    def _text_parts_from_html(self, html: str) -> list[str]:
        parser = _TextParser()
        parser.feed(html)
        return parser.parts

    def _clean_player_name(self, value: str) -> str:
        return value.replace("’", "'").strip()

    def _normalize_team(self, value: str) -> str:
        cleaned = re.sub(r"[^A-Z]", "", value.upper())
        return TEAM_ALIASES.get(cleaned, cleaned)

    def _dedupe(self, lines: list[PropLine]) -> list[PropLine]:
        deduped: dict[tuple[str, str, str], PropLine] = {}
        for line in lines:
            key = (line.player_name_norm, line.team, line.prop_type)
            deduped[key] = line
        return sorted(deduped.values(), key=lambda line: (line.team, line.player_name_raw, line.prop_type))
