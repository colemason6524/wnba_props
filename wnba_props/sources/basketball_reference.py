from __future__ import annotations

import re
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError
from typing import Iterable

from ..cache import JsonCache
from ..models import PlayerGameLog
from ..utils import fetch_text, normalize_name, safe_int


TEAM_ABBR_FIXES = {
    "GSV": "GS",
    "LAS": "LA",
    "LVA": "LV",
    "NYL": "NY",
    "PHO": "PHX",
    "WAS": "WSH",
}


class _TableParser(HTMLParser):
    def __init__(self, target_table_id: str) -> None:
        super().__init__()
        self.target_table_id = target_table_id
        self.in_target = False
        self.in_row = False
        self.current_tag = None
        self.current_header = ""
        self.current_row: dict[str, str] = {}
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and attrs_dict.get("id") == self.target_table_id:
            self.in_target = True
        elif self.in_target and tag == "tr":
            self.in_row = True
            self.current_row = {}
        elif self.in_target and self.in_row and tag in {"th", "td"}:
            self.current_tag = tag
            self.current_header = attrs_dict.get("data-stat", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_target:
            self.in_target = False
        elif tag == "tr" and self.in_row:
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
            self.current_row = {}
        elif tag in {"th", "td"}:
            self.current_tag = None

    def handle_data(self, data: str) -> None:
        if self.in_target and self.in_row and self.current_tag and self.current_header:
            text = self.current_row.get(self.current_header, "")
            self.current_row[self.current_header] = f"{text}{data}"


class _PlayerIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_players_table = False
        self.current_code = ""
        self.capture_text = False
        self.current_name = ""
        self.players: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and attrs_dict.get("id") == "players":
            self.in_players_table = True
            return
        if not self.in_players_table:
            return
        if tag == "th" and attrs_dict.get("data-stat") == "player":
            self.current_code = attrs_dict.get("data-append-csv", "") or ""
        elif tag == "a" and self.current_code:
            self.capture_text = True
            self.current_name = ""

    def handle_data(self, data: str) -> None:
        if self.capture_text:
            self.current_name += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self.in_players_table:
            self.in_players_table = False
        elif tag == "a" and self.capture_text:
            name = self.current_name.strip()
            if self.current_code and name:
                self.players.append((self.current_code, name))
            self.capture_text = False
            self.current_name = ""
        elif tag == "th" and self.current_code and not self.capture_text:
            self.current_code = ""


@dataclass
class PlayerLookup:
    player_name_norm: str
    player_name_raw: str
    player_code: str


@dataclass
class FetchStats:
    same_day_cache_hits: int = 0
    ttl_cache_hits: int = 0
    stale_cache_fallbacks: int = 0
    fresh_fetches: int = 0


class BasketballReferenceSource:
    PLAYER_INDEX_URL = "https://www.basketball-reference.com/wnba/players/{letter}/"
    GAMELOG_URL = "https://www.basketball-reference.com/wnba/players/{letter}/{code}/gamelog/{season}"
    PLAYOFF_GAMELOG_URL = "https://www.basketball-reference.com/wnba/players/{letter}/{code}/gamelog-playoffs/"
    PARSED_CACHE_VERSION = "v2"
    _last_request_at = 0.0

    def __init__(self, cache: JsonCache, sticky_daily_cache: bool = True) -> None:
        self.cache = cache
        self.sticky_daily_cache = sticky_daily_cache
        self.stats = FetchStats()

    def fetch_logs(self, player_name: str, team_hint: str, season_end_year: int) -> list[PlayerGameLog]:
        lookup = self._resolve_player(player_name)
        parsed_cache_key = f"bref_parsed_gamelog_{lookup.player_code}_{season_end_year}_{self.PARSED_CACHE_VERSION}"
        cached_payload = self.cache.get_payload(parsed_cache_key)
        if cached_payload is not None:
            cached_logs = cached_payload["data"]
            if self.sticky_daily_cache and self._payload_saved_on_current_day(cached_payload):
                self.stats.same_day_cache_hits += 1
                return [self._deserialize_log(item) for item in cached_logs]
            fresh_logs = self.cache.get(parsed_cache_key)
            if fresh_logs is not None:
                self.stats.ttl_cache_hits += 1
                return [self._deserialize_log(item) for item in fresh_logs]

        stale_logs = self.cache.get_stale(parsed_cache_key)
        try:
            html = self._get_gamelog_html(lookup.player_code, season_end_year)
            rows = self._extract_first_available_table_rows(html, ["player_game_log_reg", "wnba_pgl_basic"])
            regular_logs = self._parse_logs(rows, lookup.player_name_raw, team_hint)
            playoff_logs = self._fetch_playoff_logs(lookup.player_code, lookup.player_name_raw, team_hint, season_end_year)
            parsed_logs = self._merge_logs(regular_logs, playoff_logs)
            self.cache.set(parsed_cache_key, [self._serialize_log(log) for log in parsed_logs])
            self.stats.fresh_fetches += 1
            return parsed_logs
        except Exception as exc:  # noqa: BLE001
            if stale_logs is not None and "HTTP 429" in str(exc):
                self.stats.stale_cache_fallbacks += 1
                return [self._deserialize_log(item) for item in stale_logs]
            raise

    def _resolve_player(self, player_name: str) -> PlayerLookup:
        normalized = normalize_name(player_name)
        cache_key = f"bref_player_lookup_{normalized}"
        cached = self.cache.get(cache_key)
        if cached:
            return PlayerLookup(**cached)

        name_parts = [part for part in normalized.split(" ") if part]
        letter = name_parts[-1][0] if name_parts else normalized[0]
        lookup = self._lookup_from_letter(normalized, letter)
        if lookup:
            self.cache.set(cache_key, lookup.__dict__)
            return lookup

        for fallback_letter in "abcdefghijklmnopqrstuvwxyz":
            if fallback_letter == letter:
                continue
            lookup = self._lookup_from_letter(normalized, fallback_letter)
            if lookup:
                self.cache.set(cache_key, lookup.__dict__)
                return lookup
        raise RuntimeError(f"Could not resolve Basketball-Reference player code for '{player_name}'.")

    def _lookup_from_letter(self, normalized_name: str, letter: str) -> PlayerLookup | None:
        index_html = self._get_player_index_html(letter)
        parser = _PlayerIndexParser()
        parser.feed(index_html)
        for code, raw_name in parser.players:
            clean_name = normalize_name(unescape(raw_name))
            if clean_name == normalized_name:
                return PlayerLookup(
                    player_name_norm=normalized_name,
                    player_name_raw=unescape(raw_name),
                    player_code=code,
                )
        for code, raw_name in self._extract_wnba_index_players(index_html, letter):
            clean_name = normalize_name(unescape(raw_name))
            if clean_name == normalized_name:
                return PlayerLookup(
                    player_name_norm=normalized_name,
                    player_name_raw=unescape(raw_name),
                    player_code=code,
                )
        return None

    def _extract_wnba_index_players(self, html: str, letter: str) -> list[tuple[str, str]]:
        pattern = re.compile(
            rf'href="/wnba/players/{re.escape(letter)}/([^"/]+)\.html">(.*?)</a>',
            flags=re.IGNORECASE | re.DOTALL,
        )
        players: list[tuple[str, str]] = []
        for code, raw_html_name in pattern.findall(html):
            raw_name = re.sub(r"<[^>]+>", "", raw_html_name)
            raw_name = re.sub(r"\s+", " ", raw_name).strip()
            if code and raw_name:
                players.append((code, raw_name))
        return players

    def _get_player_index_html(self, letter: str) -> str:
        cache_key = f"bref_player_index_{letter}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        stale = self.cache.get_stale(cache_key)
        try:
            html = self._fetch_bref_text(self.PLAYER_INDEX_URL.format(letter=letter))
            self.cache.set(cache_key, html)
            return html
        except Exception as exc:  # noqa: BLE001
            if stale is not None and "HTTP 429" in str(exc):
                return stale
            raise

    def _get_gamelog_html(self, player_code: str, season_end_year: int) -> str:
        cache_key = f"bref_gamelog_{player_code}_{season_end_year}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        stale = self.cache.get_stale(cache_key)
        try:
            html = self._fetch_bref_text(
                self.GAMELOG_URL.format(letter=player_code[0], code=player_code, season=season_end_year)
            )
            self.cache.set(cache_key, html)
            return html
        except Exception as exc:  # noqa: BLE001
            if stale is not None and "HTTP 429" in str(exc):
                return stale
            raise

    def _get_playoff_gamelog_html(self, player_code: str) -> str | None:
        cache_key = f"bref_gamelog_playoffs_{player_code}"
        cached = self.cache.get(cache_key)
        if cached:
            return cached
        stale = self.cache.get_stale(cache_key)
        try:
            html = self._fetch_bref_text(
                self.PLAYOFF_GAMELOG_URL.format(letter=player_code[0], code=player_code)
            )
            self.cache.set(cache_key, html)
            return html
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if stale is not None and exc.code == 429:
                return stale
            raise
        except Exception as exc:  # noqa: BLE001
            if stale is not None and "HTTP 429" in str(exc):
                return stale
            raise

    def _fetch_bref_text(self, url: str) -> str:
        self._wait_for_rate_limit()
        try:
            return fetch_text(url)
        except Exception:
            # Make the next attempted BRef request wait too; repeated 429s usually
            # mean the server wants a quiet period, not an immediate fallback burst.
            self.__class__._last_request_at = time.monotonic()
            raise

    def _wait_for_rate_limit(self) -> None:
        interval = float(os.environ.get("BREF_REQUEST_INTERVAL_SECONDS", "6.0"))
        if interval <= 0:
            return
        elapsed = time.monotonic() - self.__class__._last_request_at
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self.__class__._last_request_at = time.monotonic()

    def _fetch_playoff_logs(
        self,
        player_code: str,
        player_name: str,
        team_hint: str,
        season_end_year: int,
    ) -> list[PlayerGameLog]:
        try:
            html = self._get_playoff_gamelog_html(player_code)
        except Exception:
            return []
        if not html:
            return []
        try:
            rows = self._extract_first_available_table_rows(html, ["player_game_log_post", "wnba_pgl_basic"])
        except RuntimeError:
            return []
        playoff_logs = self._parse_logs(rows, player_name, team_hint)
        return [log for log in playoff_logs if log.game_date.year == season_end_year]

    def _merge_logs(
        self,
        regular_logs: list[PlayerGameLog],
        playoff_logs: list[PlayerGameLog],
    ) -> list[PlayerGameLog]:
        merged: dict[tuple[date, str, str], PlayerGameLog] = {}
        for log in playoff_logs + regular_logs:
            key = (log.game_date, log.team, log.opponent)
            if key not in merged:
                merged[key] = log
        return sorted(merged.values(), key=lambda item: item.game_date, reverse=True)

    def _extract_table_rows(self, html: str, table_id: str) -> list[dict[str, str]]:
        parser = _TableParser(table_id)
        parser.feed(html)
        if parser.rows:
            return parser.rows

        comments = re.findall(r"<!--(.*?)-->", html, flags=re.DOTALL)
        for comment in comments:
            parser = _TableParser(table_id)
            parser.feed(comment)
            if parser.rows:
                return parser.rows
        raise RuntimeError(f"Could not find Basketball-Reference table '{table_id}'.")

    def _extract_first_available_table_rows(self, html: str, table_ids: list[str]) -> list[dict[str, str]]:
        errors: list[str] = []
        for table_id in table_ids:
            try:
                return self._extract_table_rows(html, table_id)
            except RuntimeError as exc:
                errors.append(str(exc))
        raise RuntimeError("; ".join(errors))

    def _parse_logs(self, rows: Iterable[dict[str, str]], player_name: str, team_hint: str) -> list[PlayerGameLog]:
        results: list[PlayerGameLog] = []
        for row in rows:
            raw_date = row.get("date", "").strip() or row.get("date_game", "").strip()
            if not raw_date or raw_date in {"Date", "date"}:
                continue
            if row.get("reason", "").strip():
                continue
            if not row.get("mp", "").strip():
                continue
            game_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            team_key = row.get("team_name_abbr", "").strip() or row.get("team_id", "").strip()
            opp_key = row.get("opp_name_abbr", "").strip() or row.get("opp_id", "").strip()
            team = TEAM_ABBR_FIXES.get(team_key, team_key)
            opponent = TEAM_ABBR_FIXES.get(opp_key, opp_key)
            minutes = self._parse_minutes(row.get("mp", "0:00"))
            results.append(
                PlayerGameLog(
                    player_name_raw=player_name,
                    player_name_norm=normalize_name(player_name),
                    game_date=game_date,
                    team=team or team_hint,
                    opponent=opponent,
                    minutes=minutes,
                    points=safe_int(row.get("pts")),
                    rebounds=safe_int(row.get("trb")),
                    assists=safe_int(row.get("ast")),
                    threes_made=safe_int(row.get("fg3")),
                    did_play=True,
                    source="basketball_reference",
                )
            )
        results.sort(key=lambda item: item.game_date, reverse=True)
        return results

    def _parse_minutes(self, value: str) -> float:
        if ":" not in value:
            return 0.0
        minutes, seconds = value.split(":", 1)
        return int(minutes) + (int(seconds) / 60.0)

    def _payload_saved_on_current_day(self, payload: dict) -> bool:
        saved_at = datetime.fromisoformat(payload["saved_at"])
        return saved_at.date() == datetime.now(saved_at.tzinfo).date()

    def _serialize_log(self, log: PlayerGameLog) -> dict:
        payload = dict(log.__dict__)
        payload["game_date"] = log.game_date.isoformat()
        return payload

    def _deserialize_log(self, payload: dict) -> PlayerGameLog:
        item = dict(payload)
        if isinstance(item.get("game_date"), str):
            item["game_date"] = datetime.strptime(item["game_date"], "%Y-%m-%d").date()
        return PlayerGameLog(**item)
