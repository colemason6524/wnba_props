from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..cache import JsonCache
from ..config import Settings, TEAM_ABBR_TO_FANDUEL_SLUG
from ..models import Game, PropLine
from ..utils import fetch_text, normalize_name


PROP_PATTERNS = [
    (re.compile(r"score at least ([0-9]+(?:\.[0-9])?) points\?", re.IGNORECASE), "PTS"),
    (re.compile(r"have at least ([0-9]+(?:\.[0-9])?) rebounds\?", re.IGNORECASE), "REB"),
    (re.compile(r"have at least ([0-9]+(?:\.[0-9])?) assists\?", re.IGNORECASE), "AST"),
    (
        re.compile(r"have at least ([0-9]+(?:\.[0-9])?) points \+ rebounds \+ assists\?", re.IGNORECASE),
        "PRA",
    ),
    (re.compile(r"have at least ([0-9]+(?:\.[0-9])?) points \+ assists\?", re.IGNORECASE), "P+A"),
    (re.compile(r"have at least ([0-9]+(?:\.[0-9])?) points \+ rebounds\?", re.IGNORECASE), "P+R"),
    (re.compile(r"have at least ([0-9]+(?:\.[0-9])?) rebounds \+ assists\?", re.IGNORECASE), "R+A"),
    (
        re.compile(
            r"have at least ([0-9]+(?:\.[0-9])?) (?:three point field goals|three-pointers|made threes)\?",
            re.IGNORECASE,
        ),
        "3PM",
    ),
]

PROP_TYPE_TO_EVENT_TAB = {
    "PTS": "player-points",
    "REB": "player-rebounds",
    "AST": "player-assists",
    "3PM": "player-threes",
}

EVENT_TAB_TO_PROP_TYPE = {value: key for key, value in PROP_TYPE_TO_EVENT_TAB.items()}

MARKET_NAME_TO_PROP_TYPE = [
    ("pts + reb + ast", "PRA"),
    ("points + rebounds + assists", "PRA"),
    ("pts + ast", "P+A"),
    ("points + assists", "P+A"),
    ("pts + reb", "P+R"),
    ("points + rebounds", "P+R"),
    ("reb + ast", "R+A"),
    ("rebounds + assists", "R+A"),
    ("made threes", "3PM"),
    ("three point", "3PM"),
    ("threes", "3PM"),
    ("points", "PTS"),
    ("rebounds", "REB"),
    ("assists", "AST"),
]


class _LinkParser(HTMLParser):
    def __init__(self, prefix: str) -> None:
        super().__init__()
        self.prefix = prefix
        self.current_href = ""
        self.current_text = ""
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        if href and href.startswith(self.prefix):
            self.current_href = href
            self.current_text = ""

    def handle_data(self, data: str) -> None:
        if self.current_href:
            self.current_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_href:
            text = re.sub(r"\s+", " ", self.current_text).strip()
            self.links.append((self.current_href, text))
            self.current_href = ""
            self.current_text = ""


class FanDuelSource:
    BASE_URL = "https://sportsbook.fanduel.com"

    def __init__(self, settings: Settings, roster_cache: JsonCache, lines_cache: JsonCache) -> None:
        self.settings = settings
        self.roster_cache = roster_cache
        self.lines_cache = lines_cache
        self.failures: list[str] = []

    def fetch_prop_lines(self, games: Iterable[Game]) -> list[PropLine]:
        games = list(games)
        lines: list[PropLine] = []
        collected_at = datetime.now(timezone.utc)
        event_urls = self.settings.fanduel_event_urls or self._discover_cached_event_urls()
        if event_urls:
            for game in games:
                lines.extend(self._fetch_event_prop_lines(game, collected_at, event_urls))
            if lines:
                return self._dedupe_lines(lines)

        for game in games:
            for team, opponent in ((game.home_team, game.away_team), (game.away_team, game.home_team)):
                lines.extend(self._fetch_team_prop_lines(game, team, opponent, collected_at))

        return self._dedupe_lines(lines)

    def _dedupe_lines(self, lines: list[PropLine]) -> list[PropLine]:
        deduped: dict[tuple[str, str], PropLine] = {}
        for line in lines:
            key = (line.player_name_norm, line.prop_type)
            deduped[key] = line
        return sorted(deduped.values(), key=lambda item: (item.team, item.player_name_raw, item.prop_type))

    def _fetch_event_prop_lines(
        self,
        game: Game,
        collected_at: datetime,
        event_urls: list[str],
    ) -> list[PropLine]:
        event_url = self._event_url_for_game(game, event_urls)
        if not event_url:
            self.failures.append(f"{game.away_team}@{game.home_team}: no matching FanDuel event URL configured")
            return []

        lines: list[PropLine] = []
        for prop_type, tab in PROP_TYPE_TO_EVENT_TAB.items():
            if prop_type not in self.settings.supported_prop_types:
                continue
            tab_url = self._with_tab(event_url, tab)
            try:
                html = self._fetch_event_tab_html(tab_url)
            except Exception as exc:  # noqa: BLE001
                self.failures.append(f"{game.away_team}@{game.home_team} {tab}: event page unavailable ({exc})")
                continue
            lines.extend(self._parse_event_tab_props(html, tab_url, prop_type, game, collected_at))
        return lines

    def _event_url_for_game(self, game: Game, event_urls: list[str]) -> str:
        home_slug = TEAM_ABBR_TO_FANDUEL_SLUG.get(game.home_team, "")
        away_slug = TEAM_ABBR_TO_FANDUEL_SLUG.get(game.away_team, "")
        for url in event_urls:
            lowered = url.lower()
            if home_slug and away_slug and home_slug in lowered and away_slug in lowered:
                return url
        return ""

    def _discover_cached_event_urls(self) -> list[str]:
        event_urls: set[str] = set()
        for path in sorted(self.lines_cache.root.glob("fanduel_event_*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            source_url = str(payload.get("source_url") or "").strip()
            if source_url:
                event_urls.add(self._base_event_url(source_url))
                continue
            data = str(payload.get("data") or "")
            event_urls.update(self._extract_event_urls(data))
        return sorted(event_urls)

    def _extract_event_urls(self, payload: str) -> set[str]:
        decoded = html_lib.unescape(payload)
        urls = {
            self._base_event_url(url)
            for url in re.findall(r"https://sportsbook\.fanduel\.com/basketball/wnba/[^\"'<>\s]+", decoded)
        }
        urls.update(
            self._base_event_url(f"{self.BASE_URL}{path}")
            for path in re.findall(r'href=["\'](/basketball/wnba/[^"\']+)', decoded)
        )
        return urls

    def _with_tab(self, url: str, tab: str) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        query["tab"] = [tab]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    def _base_event_url(self, url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(parsed._replace(query="", fragment=""))

    def _fetch_event_tab_html(self, url: str) -> str:
        cache_key = self._cache_key_for_event_tab(url)
        cached = self.lines_cache.get(cache_key)
        if cached is not None:
            return cached
        stale = self.lines_cache.get_stale(cache_key)
        try:
            html = fetch_text(url)
            self._raise_if_access_challenge(html, url)
        except Exception:
            if stale is not None:
                return stale
            raise
        self.lines_cache.set(cache_key, html)
        return html

    def _parse_event_tab_props(
        self,
        html: str,
        tab_url: str,
        prop_type: str,
        game: Game,
        collected_at: datetime,
    ) -> list[PropLine]:
        lines = self._parse_structured_market_props(html, prop_type, game, collected_at)
        if lines:
            return lines

        lines = self._parse_event_props_from_aria_labels(html, prop_type, game, collected_at)
        if lines:
            return lines

        text = self._strip_html(html)
        lines = self._parse_event_props_from_text(text, prop_type, game, collected_at)
        if lines:
            return lines
        decoded = self._decode_script_payload_text(html)
        lines = self._parse_event_props_from_text(decoded, prop_type, game, collected_at)
        if not lines:
            if self._looks_like_unhydrated_app_shell(html):
                self.failures.append(
                    f"{game.away_team}@{game.home_team} {tab_url}: cached file looks like FanDuel app shell only; "
                    "save rendered page text/DOM after props load"
                )
            else:
                self.failures.append(f"{game.away_team}@{game.home_team} {tab_url}: no parsable {prop_type} props found")
        return lines

    def _parse_event_props_from_text(
        self,
        text: str,
        prop_type: str,
        game: Game,
        collected_at: datetime,
    ) -> list[PropLine]:
        clean = re.sub(r"\s+", " ", text)
        results: list[PropLine] = []
        seen: set[tuple[str, float]] = set()
        team_by_player = self._player_team_lookup_from_text(clean, game)

        patterns = [
            re.compile(
                r"(?P<player>[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){1,3}).{0,80}?\bOver\b.{0,20}?(?P<line>\d+(?:\.\d)?)",
                re.DOTALL,
            ),
            re.compile(
                r"(?P<player>[A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){1,3}).{0,80}?(?P<line>\d+(?:\.\d)?).{0,20}?\bOver\b",
                re.DOTALL,
            ),
        ]
        for pattern in patterns:
            for match in pattern.finditer(clean):
                player_name = match.group("player").strip()
                if player_name.lower() in {"player points", "player rebounds", "player assists", "golden state", "indiana fever"}:
                    continue
                line_value = float(match.group("line"))
                key = (normalize_name(player_name), line_value)
                if key in seen:
                    continue
                seen.add(key)
                team = team_by_player.get(normalize_name(player_name), "")
                opponent = ""
                if team == game.home_team:
                    opponent = game.away_team
                elif team == game.away_team:
                    opponent = game.home_team
                results.append(
                    PropLine(
                        event_id=game.game_id,
                        game_date=game.game_date,
                        player_name_raw=player_name,
                        player_name_norm=normalize_name(player_name),
                        team=team,
                        opponent=opponent,
                        prop_type=prop_type,
                        line=line_value,
                        bookmaker="fanduel",
                        source="fanduel_event",
                        collected_at=collected_at,
                    )
                )
        return results

    def _parse_event_props_from_aria_labels(
        self,
        html: str,
        prop_type: str,
        game: Game,
        collected_at: datetime,
    ) -> list[PropLine]:
        decoded = html_lib.unescape(html)
        results: list[PropLine] = []
        seen: set[tuple[str, str, float]] = set()
        label_pattern = re.compile(
            r'aria-label="(?P<market>[^"]+? - [^"]+?),\s*(?P<runner>[^"]+?\s+Over),\s*'
            r'(?P<line>\d+(?:\.\d+)?),\s*[+-]\d+"',
            re.IGNORECASE,
        )
        for match in label_pattern.finditer(decoded):
            market_name = match.group("market").strip()
            parsed_prop_type = self._prop_type_from_market({"marketName": market_name})
            if parsed_prop_type != prop_type:
                continue
            player_name = market_name.split(" - ", 1)[0].strip()
            line_value = float(match.group("line"))
            team = self._team_from_event_dom(decoded, match.start(), game)
            opponent = ""
            if team == game.home_team:
                opponent = game.away_team
            elif team == game.away_team:
                opponent = game.home_team
            key = (normalize_name(player_name), prop_type, line_value)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                PropLine(
                    event_id=game.game_id,
                    game_date=game.game_date,
                    player_name_raw=player_name,
                    player_name_norm=normalize_name(player_name),
                    team=team,
                    opponent=opponent,
                    prop_type=prop_type,
                    line=line_value,
                    bookmaker="fanduel",
                    source="fanduel_event",
                    collected_at=collected_at,
                )
            )
        return results

    def _team_from_event_dom(self, decoded_html: str, position: int, game: Game) -> str:
        team_by_image_slug = {
            slug.replace("-", "_"): team
            for team, slug in TEAM_ABBR_TO_FANDUEL_SLUG.items()
            if team in {game.home_team, game.away_team}
        }
        window = decoded_html[max(0, position - 3000) : position + 500]
        matches = re.findall(r"/team/wnba/([a-z0-9_]+)\.png", window, flags=re.IGNORECASE)
        for image_slug in reversed(matches):
            team = team_by_image_slug.get(image_slug.lower())
            if team:
                return team
        return ""

    def _parse_structured_market_props(
        self,
        payload: str,
        desired_prop_type: str,
        game: Game,
        collected_at: datetime,
    ) -> list[PropLine]:
        results: list[PropLine] = []
        seen: set[tuple[str, str, float]] = set()
        for market in self._iter_market_dicts(payload):
            prop_type = self._prop_type_from_market(market)
            if prop_type != desired_prop_type:
                continue
            if prop_type not in self.settings.supported_prop_types:
                continue

            player_name = self._player_name_from_market(market)
            line_value = self._over_handicap_from_market(market)
            if not player_name or line_value is None:
                continue

            key = (normalize_name(player_name), prop_type, line_value)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                PropLine(
                    event_id=game.game_id,
                    game_date=game.game_date,
                    player_name_raw=player_name,
                    player_name_norm=normalize_name(player_name),
                    team="",
                    opponent="",
                    prop_type=prop_type,
                    line=line_value,
                    bookmaker="fanduel",
                    source="fanduel_event",
                    collected_at=collected_at,
                )
            )
        return results

    def _iter_market_dicts(self, payload: str) -> Iterable[dict[str, Any]]:
        for root in self._structured_payload_roots(payload):
            yield from self._walk_market_dicts(root)

    def _structured_payload_roots(self, payload: str) -> list[Any]:
        roots: list[Any] = []
        stripped = payload.strip()
        for candidate in (stripped, html_lib.unescape(stripped)):
            if not candidate or candidate[0] not in "{[":
                continue
            try:
                roots.append(json.loads(candidate))
            except json.JSONDecodeError:
                pass

        script_pattern = re.compile(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(?P<json>.*?)</script>',
            re.DOTALL | re.IGNORECASE,
        )
        for match in script_pattern.finditer(payload):
            script_json = html_lib.unescape(match.group("json")).strip()
            try:
                roots.append(json.loads(script_json))
            except json.JSONDecodeError:
                continue
        return roots

    def _walk_market_dicts(self, value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            selections = value.get("selections")
            if isinstance(selections, list) and (
                value.get("marketName") or value.get("marketType") or value.get("marketTypeName")
            ):
                yield value
            for child in value.values():
                yield from self._walk_market_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_market_dicts(child)

    def _prop_type_from_market(self, market: dict[str, Any]) -> str:
        market_text = " ".join(
            str(market.get(key, ""))
            for key in ("marketName", "marketType", "marketTypeName", "contentText")
        ).lower()
        normalized = market_text.replace("_", " ").replace("-", " ")
        for marker, prop_type in MARKET_NAME_TO_PROP_TYPE:
            if marker in normalized:
                return prop_type
        return ""

    def _player_name_from_market(self, market: dict[str, Any]) -> str:
        market_name = str(market.get("marketName") or "").strip()
        if " - " in market_name:
            return market_name.split(" - ", 1)[0].strip()

        for selection in market.get("selections") or []:
            if not isinstance(selection, dict):
                continue
            runner_name = str(selection.get("runnerName") or "").strip()
            runner_name = re.sub(r"\s+(?:Over|Under)\b.*$", "", runner_name, flags=re.IGNORECASE).strip()
            if runner_name:
                return runner_name
        return ""

    def _over_handicap_from_market(self, market: dict[str, Any]) -> float | None:
        selections = market.get("selections") or []
        if not isinstance(selections, list):
            return None

        fallback: float | None = None
        for selection in selections:
            if not isinstance(selection, dict):
                continue
            handicap = selection.get("handicap")
            if handicap is None:
                continue
            try:
                line_value = float(handicap)
            except (TypeError, ValueError):
                continue
            if fallback is None:
                fallback = line_value
            runner_name = str(selection.get("runnerName") or "")
            if re.search(r"\bOver\b", runner_name, re.IGNORECASE):
                return line_value
        return fallback

    def _player_team_lookup_from_text(self, text: str, game: Game) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for team in (game.home_team, game.away_team):
            slug = TEAM_ABBR_TO_FANDUEL_SLUG.get(team, "")
            if not slug:
                continue
            team_name = slug.replace("-", " ").title()
            for match in re.finditer(rf"([A-Z][A-Za-z.'-]+(?: [A-Z][A-Za-z.'-]+){{1,3}}).{{0,80}}?{re.escape(team_name)}", text):
                lookup[normalize_name(match.group(1))] = team
        return lookup

    def _decode_script_payload_text(self, html: str) -> str:
        text = html
        replacements = {
            r"\u0022": '"',
            r"\u0026": "&",
            r"\u003c": "<",
            r"\u003e": ">",
            r"\/": "/",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        text = re.sub(r'["{}[\],:]', " ", text)
        return re.sub(r"\s+", " ", text)

    def _looks_like_unhydrated_app_shell(self, html: str) -> bool:
        lowered = html.lower()
        has_shell_markers = "fanduel sportsbook" in lowered and "/assets/" in lowered
        has_prop_markers = any(marker in lowered for marker in [" over ", " under ", "caitlin", "player points"])
        return has_shell_markers and not has_prop_markers

    def _fetch_team_prop_lines(
        self,
        game: Game,
        team: str,
        opponent: str,
        collected_at: datetime,
    ) -> list[PropLine]:
        try:
            player_links = self._fetch_team_player_links(team)
        except Exception as exc:  # noqa: BLE001
            self.failures.append(f"{team}: roster/player links unavailable ({exc})")
            return []
        team_lines: list[PropLine] = []
        for player_link, player_name in player_links:
            try:
                props_html = self._fetch_player_props_html(player_link)
            except Exception as exc:  # noqa: BLE001
                self.failures.append(f"{player_name}: prop page unavailable ({exc})")
                continue
            team_lines.extend(
                self._parse_player_props(
                    html=props_html,
                    player_link=player_link,
                    player_name=player_name,
                    team=team,
                    opponent=opponent,
                    game=game,
                    collected_at=collected_at,
                )
            )
        return team_lines

    def _fetch_team_player_links(self, team_abbr: str) -> list[tuple[str, str]]:
        slug = TEAM_ABBR_TO_FANDUEL_SLUG.get(team_abbr)
        if not slug:
            return []
        cache_key = f"fanduel_roster_{slug}"
        cached = self.roster_cache.get(cache_key)
        if cached:
            return cached
        stale = self.roster_cache.get_stale(cache_key)

        url = f"{self.BASE_URL}/teams/wnba/{slug}/roster"
        try:
            html = fetch_text(url)
            self._raise_if_access_challenge(html, url)
        except Exception:
            if stale:
                return stale
            raise
        parser = _LinkParser("/teams/wnba/roster/")
        parser.feed(html)
        unique_links = []
        seen = set()
        for link, text in parser.links:
            if link.count("/") < 4 or link.endswith("/player-props") or not text:
                continue
            key = (link, text)
            if key in seen:
                continue
            seen.add(key)
            unique_links.append(key)
        if not unique_links and stale:
            return stale
        self.roster_cache.set(cache_key, unique_links)
        return unique_links

    def _fetch_player_props_html(self, player_link: str) -> str:
        props_path = player_link.rstrip("/") + "/player-props"
        cache_key = self._cache_key_for_player_props(props_path)
        cached = self.lines_cache.get(cache_key)
        if cached is not None:
            return cached
        stale = self.lines_cache.get_stale(cache_key)
        url = f"{self.BASE_URL}{props_path}"
        try:
            html = fetch_text(url)
            self._raise_if_access_challenge(html, url)
        except Exception:
            if stale is not None:
                return stale
            raise
        self.lines_cache.set(cache_key, html)
        return html

    def _parse_player_props(
        self,
        html: str,
        player_link: str,
        player_name: str,
        team: str,
        opponent: str,
        game: Game,
        collected_at: datetime,
    ) -> list[PropLine]:
        text = self._strip_html(html)
        results: list[PropLine] = []
        for pattern, prop_type in PROP_PATTERNS:
            if prop_type not in self.settings.supported_prop_types:
                continue
            for match in pattern.finditer(text):
                line_value = float(match.group(1))
                results.append(
                    PropLine(
                        event_id=game.game_id,
                        game_date=game.game_date,
                        player_name_raw=player_name,
                        player_name_norm=normalize_name(player_name),
                        team=team,
                        opponent=opponent,
                        prop_type=prop_type,
                        line=line_value,
                        bookmaker="fanduel",
                        source="fanduel",
                        collected_at=collected_at,
                    )
                )
                break
        return results

    def _cache_key_for_player_props(self, props_path: str) -> str:
        safe = props_path.strip("/").replace("/", "_").replace("-", "_")
        return f"fanduel_props_{safe}"

    def _cache_key_for_event_tab(self, url: str) -> str:
        parsed = urlparse(url)
        safe_path = f"{parsed.path}_{parsed.query}".strip("/").replace("/", "_")
        safe_path = re.sub(r"[^A-Za-z0-9_]+", "_", safe_path)
        return f"fanduel_event_{safe_path}"

    def _strip_html(self, html: str) -> str:
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&#39;|&apos;", "'", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"\s+", " ", text)
        return text

    def _raise_if_access_challenge(self, html: str, url: str) -> None:
        lowered = html.lower()
        challenge_markers = [
            "px-captcha",
            "perimeterx",
            "press & hold",
            "access to this page has been denied",
            "verify you are human",
        ]
        if any(marker in lowered for marker in challenge_markers):
            raise RuntimeError(f"FanDuel access challenge for {url}")
