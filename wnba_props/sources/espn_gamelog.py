from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser

from ..cache import JsonCache
from ..config import TEAM_ABBR_TO_ESPN_ID
from ..models import PlayerGameLog
from ..utils import fetch_json, fetch_text, normalize_name, safe_int


class _RosterParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.athletes: list[dict] = []


class _EspnTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.current_title = ""
        self.capture_table = False
        self.capture_section: str | None = None
        self.in_row = False
        self.in_header = False
        self.in_cell = False
        self.current_row: list[str] = []
        self.current_headers: list[str] = []
        self.current_cell = ""
        self.rows_by_section: dict[str, list[list[str]]] = {"regular": [], "postseason": []}
        self.headers_by_section: dict[str, list[str]] = {"regular": [], "postseason": []}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "div" and attrs_dict.get("class") == "Table__Title":
            self.in_title = True
            self.current_title = ""
        elif self.capture_section and tag == "table" and not self.capture_table:
            self.capture_table = True
        elif self.capture_table and tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.capture_table and self.in_row and tag == "th":
            self.in_header = True
            self.current_cell = ""
        elif self.capture_table and self.in_row and tag == "td":
            self.in_cell = True
            self.current_cell = ""

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.current_title += data
        elif self.in_header or self.in_cell:
            self.current_cell += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.in_title:
            title = re.sub(r"\s+", " ", self.current_title).strip()
            if "Regular Season" in title:
                self.capture_section = "regular"
            elif "Postseason" in title or "Playoffs" in title:
                self.capture_section = "postseason"
            else:
                self.capture_section = None
            self.in_title = False
        elif tag == "th" and self.in_header:
            self.current_row.append(re.sub(r"\s+", " ", self.current_cell).strip())
            self.in_header = False
        elif tag == "td" and self.in_cell:
            self.current_row.append(re.sub(r"\s+", " ", self.current_cell).strip())
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                if self.capture_section is None:
                    pass
                elif not self.headers_by_section[self.capture_section]:
                    self.headers_by_section[self.capture_section] = self.current_row
                else:
                    self.rows_by_section[self.capture_section].append(self.current_row)
            self.current_row = []
            self.in_row = False
        elif tag == "table" and self.capture_table:
            self.capture_table = False
            self.capture_section = None


@dataclass
class EspnPlayerLookup:
    athlete_id: str
    player_name_raw: str
    player_name_norm: str


class EspnGameLogSource:
    ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/{team_id}/roster"
    GAMELOG_URL = "https://www.espn.com/wnba/player/gamelog/_/id/{athlete_id}/{slug}"
    PARSED_CACHE_VERSION = "v2"

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_logs(self, player_name: str, team_abbr: str, season_end_year: int) -> list[PlayerGameLog]:
        lookup = self._resolve_player(player_name, team_abbr)
        cache_key = f"espn_parsed_gamelog_{lookup.athlete_id}_{season_end_year}_{self.PARSED_CACHE_VERSION}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [self._deserialize_log(item) for item in cached]

        html = fetch_text(self.GAMELOG_URL.format(athlete_id=lookup.athlete_id, slug=self._slugify(lookup.player_name_raw)))
        self._raise_if_access_challenge(html, lookup.player_name_raw)
        parser = _EspnTableParser()
        parser.feed(html)
        logs = self._parse_rows(
            parser.rows_by_section["regular"] + parser.rows_by_section["postseason"],
            lookup.player_name_raw,
            team_abbr,
            season_end_year,
        )
        if not logs:
            raise RuntimeError(f"ESPN gamelog returned no parsable rows for {lookup.player_name_raw}.")
        logs.sort(key=lambda item: item.game_date, reverse=True)
        self.cache.set(cache_key, [self._serialize_log(log) for log in logs])
        return logs

    def _resolve_player(self, player_name: str, team_abbr: str) -> EspnPlayerLookup:
        normalized = normalize_name(player_name)
        cache_key = f"espn_player_lookup_{team_abbr}_{normalized}"
        cached = self.cache.get(cache_key)
        if cached:
            return EspnPlayerLookup(**cached)

        team_id = TEAM_ABBR_TO_ESPN_ID.get(team_abbr)
        if not team_id:
            raise RuntimeError(f"No ESPN team id mapping for {team_abbr}.")
        roster_cache_key = f"espn_roster_{team_id}"
        roster = self.cache.get(roster_cache_key)
        if roster is None:
            roster = fetch_json(self.ROSTER_URL.format(team_id=team_id))
            self.cache.set(roster_cache_key, roster)

        for athlete in roster.get("athletes", []):
            full_name = athlete.get("fullName", "")
            if normalize_name(full_name) == normalized:
                lookup = EspnPlayerLookup(
                    athlete_id=str(athlete["id"]),
                    player_name_raw=full_name,
                    player_name_norm=normalized,
                )
                self.cache.set(cache_key, lookup.__dict__)
                return lookup
        raise RuntimeError(f"Could not resolve ESPN athlete id for '{player_name}' on {team_abbr}.")

    def _parse_rows(self, rows: list[list[str]], player_name: str, team_abbr: str, season_end_year: int) -> list[PlayerGameLog]:
        logs: list[PlayerGameLog] = []
        for row in rows:
            if len(row) < 17:
                continue
            raw_date = row[0]
            if raw_date.strip().lower() == "date":
                continue
            opp_text = row[1]
            minutes = safe_int(row[3])
            threes = safe_int(row[6].split("-")[0])
            rebounds = safe_int(row[10])
            assists = safe_int(row[11])
            points = safe_int(row[16])
            game_date = self._parse_row_date(raw_date, season_end_year)
            logs.append(
                PlayerGameLog(
                    player_name_raw=player_name,
                    player_name_norm=normalize_name(player_name),
                    game_date=game_date,
                    team=team_abbr,
                    opponent=self._parse_opponent(opp_text),
                    minutes=float(minutes),
                    points=points,
                    rebounds=rebounds,
                    assists=assists,
                    threes_made=threes,
                    did_play=True,
                    source="espn_gamelog",
                )
            )
        return logs

    def _parse_row_date(self, raw_date: str, season_end_year: int) -> date:
        match = re.search(r"(\d{1,2})/(\d{1,2})", raw_date)
        if not match:
            raise RuntimeError(f"Could not parse ESPN gamelog date '{raw_date}'.")
        month = int(match.group(1))
        day = int(match.group(2))
        year = season_end_year
        return date(year, month, day)

    def _parse_opponent(self, opp_text: str) -> str:
        text = opp_text.replace("@", "").replace("vs", "").replace("VS", "").strip()
        return text.split()[-1] if text else ""

    def _slugify(self, name: str) -> str:
        slug = normalize_name(name).replace(" ", "-")
        return slug

    def _raise_if_access_challenge(self, html: str, player_name: str) -> None:
        lowered = html.lower()
        if "challenge.js" in lowered and "awswafintegration" in lowered:
            raise RuntimeError(f"ESPN gamelog blocked by WAF challenge for {player_name}.")
        if "verify that you're not a robot" in lowered:
            raise RuntimeError(f"ESPN gamelog blocked by bot challenge for {player_name}.")

    def _serialize_log(self, log: PlayerGameLog) -> dict:
        payload = dict(log.__dict__)
        payload["game_date"] = log.game_date.isoformat()
        return payload

    def _deserialize_log(self, payload: dict) -> PlayerGameLog:
        item = dict(payload)
        if isinstance(item.get("game_date"), str):
            item["game_date"] = datetime.strptime(item["game_date"], "%Y-%m-%d").date()
        return PlayerGameLog(**item)
