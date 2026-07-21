from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from ..cache import JsonCache
from ..config import Settings
from ..models import Game, PropLine
from ..utils import fetch_json, normalize_name, safe_float


class DraftKingsSource:
    PAGE_URL = "https://sportsbook.draftkings.com/leagues/basketball/wnba"
    API_BASE = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusmi"
    LEAGUE_ID = "94682"
    MARKET_CONFIGS = {
        "PTS": {
            "seo_id": "points",
            "category_id": "1215",
            "subcategory_id": "16477",
            "title": "Points",
        },
        "3PM": {
            "seo_id": "threes",
            "category_id": "1218",
            "subcategory_id": "16480",
            "title": "Threes",
        },
        "REB": {
            "seo_id": "rebounds",
            "category_id": "1216",
            "subcategory_id": "16479",
            "title": "Rebounds",
        },
        "AST": {
            "seo_id": "assists",
            "category_id": "1217",
            "subcategory_id": "16478",
            "title": "Assists",
        },
    }

    def __init__(self, settings: Settings, lines_cache: JsonCache) -> None:
        self.settings = settings
        self.lines_cache = lines_cache
        self.failures: list[str] = []
        self.diagnostics: dict[str, int] = {}

    def fetch_prop_lines(self, games: Iterable[Game]) -> list[PropLine]:
        games = list(games)
        self.failures = []
        self.diagnostics = {
            "payloads_from_cache": 0,
            "payloads_direct_fetch_success": 0,
            "payloads_browser_fetch_success": 0,
            "payloads_missing": 0,
            "candidate_markets_seen": 0,
            "lines_found": 0,
        }
        lines: list[PropLine] = []
        for prop_type, config in self.MARKET_CONFIGS.items():
            if prop_type not in self.settings.supported_prop_types:
                continue
            payload = self._fetch_market_payload(prop_type, config)
            if payload is None:
                self.diagnostics["payloads_missing"] += 1
                continue
            lines.extend(self._parse_payload(payload, prop_type, games))

        deduped: dict[tuple[str, str], PropLine] = {}
        for line in lines:
            deduped[(line.player_name_norm, line.prop_type)] = line
        self.diagnostics["lines_found"] = len(deduped)
        return sorted(deduped.values(), key=lambda line: (line.team, line.player_name_raw, line.prop_type))

    def _fetch_market_payload(self, prop_type: str, config: dict[str, str]) -> dict | list | None:
        cache_key = f"draftkings_wnba_{prop_type.lower()}_{config['category_id']}_{config['subcategory_id']}"
        cached = self.lines_cache.get(cache_key)
        if cached is not None:
            self.diagnostics["payloads_from_cache"] += 1
            return cached

        url = self._payload_url(config)
        referer = f"{self.PAGE_URL}?category=player-props&subcategory={config['seo_id']}"
        try:
            payload = fetch_json(
                url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://sportsbook.draftkings.com",
                    "Referer": referer,
                },
                timeout=20,
            )
            self.lines_cache.set(cache_key, payload)
            self.diagnostics["payloads_direct_fetch_success"] += 1
            return payload
        except Exception as exc:  # noqa: BLE001
            self.failures.append(f"{prop_type}: direct DraftKings payload fetch failed ({exc})")

        try:
            payload = self._fetch_payload_with_browser(url, referer)
        except Exception as exc:  # noqa: BLE001
            self.failures.append(f"{prop_type}: browser DraftKings payload fetch failed ({exc})")
            return None
        self.lines_cache.set(cache_key, payload)
        self.diagnostics["payloads_browser_fetch_success"] += 1
        return payload

    def _fetch_payload_with_browser(self, url: str, referer: str) -> dict | list:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = context.new_page()
            page.goto(referer, wait_until="domcontentloaded", timeout=45000)
            result = page.evaluate(
                """async (url) => {
                    const response = await fetch(url, {
                        method: "GET",
                        credentials: "include",
                        headers: {
                            "Accept": "application/json, text/plain, */*"
                        }
                    });
                    const text = await response.text();
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}: ${text.slice(0, 300)}`);
                    }
                    return JSON.parse(text);
                }""",
                url,
            )
            browser.close()
            return result

    def _payload_url(self, config: dict[str, str]) -> str:
        return (
            f"{self.API_BASE}/v1/leagues/{self.LEAGUE_ID}"
            f"/categories/{config['category_id']}"
            f"/subcategories/{config['subcategory_id']}"
        )

    def _parse_payload(self, payload: dict | list, prop_type: str, games: list[Game]) -> list[PropLine]:
        candidate_markets = self._candidate_markets(payload)
        self.diagnostics["candidate_markets_seen"] += len(candidate_markets)
        lines: list[PropLine] = []
        collected_at = datetime.now(timezone.utc)
        for market in candidate_markets:
            player_name = self._extract_player_name(market)
            line_value = self._extract_line_value(market)
            if not player_name or line_value is None:
                continue
            game = self._match_game(market, games)
            if game is None:
                game = games[0] if len(games) == 1 else None
            if game is None:
                continue
            team, opponent = self._infer_team_and_opponent(market, game)
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
                    bookmaker="draftkings",
                    source="draftkings_scrape",
                    collected_at=collected_at,
                )
            )
        return lines

    def _candidate_markets(self, payload: dict | list) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                outcomes = node.get("outcomes")
                if isinstance(outcomes, list) and self._has_over_outcome(outcomes):
                    markets.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        return markets

    def _has_over_outcome(self, outcomes: list[Any]) -> bool:
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            text = " ".join(
                str(outcome.get(key) or "")
                for key in ("label", "name", "description", "participant", "selectionLabel")
            ).lower()
            if "over" in text:
                return True
        return False

    def _extract_player_name(self, market: dict[str, Any]) -> str:
        title = self._first_text(market, ("participant", "playerName", "description", "label", "name", "title"))
        cleaned = self._clean_player_from_text(title)
        if cleaned:
            return cleaned
        outcomes = market.get("outcomes")
        if isinstance(outcomes, list):
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                text = self._first_text(outcome, ("participant", "playerName", "description", "label", "name", "title"))
                cleaned = self._clean_player_from_text(text)
                if cleaned:
                    return cleaned
        return ""

    def _clean_player_from_text(self, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return ""
        value = re.split(r"\b(?:Over|Under)\b", value, maxsplit=1, flags=re.I)[0].strip()
        value = re.sub(r"\s+\d+(?:\.\d+)?\+?$", "", value).strip()
        if value.lower() in {"over", "under", "points", "rebounds", "assists", "threes"}:
            return ""
        if not re.search(r"[A-Za-z]", value):
            return ""
        return value

    def _extract_line_value(self, market: dict[str, Any]) -> float | None:
        for key in ("line", "points", "point", "milestoneValue"):
            value = safe_float(market.get(key), default=-1.0)
            if value >= 0:
                return value
        outcomes = market.get("outcomes")
        if isinstance(outcomes, list):
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                text = json.dumps(outcome)
                if "over" not in text.lower():
                    continue
                for key in ("line", "points", "point", "milestoneValue"):
                    value = safe_float(outcome.get(key), default=-1.0)
                    if value >= 0:
                        return value
                match = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
                if match:
                    return float(match.group(1))
        return None

    def _match_game(self, market: dict[str, Any], games: list[Game]) -> Game | None:
        text = json.dumps(market).upper()
        for game in games:
            if game.home_team in text and game.away_team in text:
                return game
        return None

    def _infer_team_and_opponent(self, market: dict[str, Any], game: Game) -> tuple[str, str]:
        text = json.dumps(market).upper()
        home_index = text.find(game.home_team)
        away_index = text.find(game.away_team)
        if home_index >= 0 and (away_index < 0 or home_index < away_index):
            return game.home_team, game.away_team
        if away_index >= 0:
            return game.away_team, game.home_team
        return "", ""

    def _first_text(self, node: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
