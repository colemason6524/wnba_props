"""Bovada WNBA game lines (moneyline / spread / total) from the free coupon API.

Bovada is the primary game-market source. On fetch failure the last good
payload is reused so a transient outage produces a stale-but-diagnosed board
instead of a silent empty one. No player props are read here.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import CACHE_DIR
from ..utils import ensure_dir
from .teams import normalize_team

BOVADA_BASE = "https://www.bovada.lv/services/sports/event/coupon/events/A/description"
BOVADA_WNBA_URL = f"{BOVADA_BASE}/basketball/wnba?marketFilterId=def"
BOVADA_CACHE_NAME = "bovada_wnba.json"
HTTP_TIMEOUT_SECONDS = 30
FETCH_ATTEMPTS = 2


@dataclass
class TwoWayPrice:
    line: Optional[float]
    am_a: int
    am_b: int
    dec_a: float
    dec_b: float


@dataclass
class BovadaGame:
    event_id: str
    away: str
    home: str
    away_name: str
    home_name: str
    start_time_utc: Optional[str]
    link: str
    league_path: str
    moneyline: Optional[TwoWayPrice] = None       # a=home, b=away
    spread: Optional[TwoWayPrice] = None          # line = home handicap; a=home, b=away
    game_total: Optional[TwoWayPrice] = None      # line = total; a=over, b=under
    source: str = "bovada"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class FetchResult:
    payload: Any
    mode: str
    attempts: int
    http_status: Optional[int]
    response_bytes: int
    fetched_at_utc: str
    cache_age_seconds: float
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "attempts": self.attempts,
            "http_status": self.http_status,
            "response_bytes": self.response_bytes,
            "fetched_at_utc": self.fetched_at_utc,
            "cache_age_seconds": round(self.cache_age_seconds, 1),
            "error": self.error,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_age(cache_path: Path) -> float:
    try:
        return max(0.0, time.time() - cache_path.stat().st_mtime)
    except OSError:
        return 0.0


def _load_cache(cache_path: Path) -> Optional[list]:
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return cached if isinstance(cached, list) else None


def _fetch_once(url: str) -> tuple[str, int, int]:
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        text = resp.read().decode("utf-8", "replace")
        status = int(getattr(resp, "status", 200) or 200)
        return text, status, len(text.encode("utf-8", "replace"))


def _fetch_json(url: str, cache_name: str, *, cache_dir: Path, refresh: bool = True) -> FetchResult:
    ensure_dir(cache_dir)
    cache_path = cache_dir / cache_name
    if not refresh:
        cached = _load_cache(cache_path)
        if cached is None:
            raise RuntimeError(f"Invalid cached Bovada payload: {cache_name}")
        return FetchResult(
            cached, "cache", 0, None, cache_path.stat().st_size,
            _now_iso(), _cache_age(cache_path), None,
        )

    last_err: Optional[Exception] = None
    attempts = 0
    for attempt in range(FETCH_ATTEMPTS):
        attempts = attempt + 1
        try:
            text, status, nbytes = _fetch_once(url)
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError(f"unexpected payload type {type(parsed).__name__}")
            cache_path.write_text(text, encoding="utf-8")
            return FetchResult(parsed, "fresh", attempts, status, nbytes, _now_iso(), 0.0, None)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            last_err = exc
            if isinstance(exc, HTTPError) and exc.code != 429 and exc.code < 500:
                break
            if attempt == 0:
                time.sleep(1.0)

    cached = _load_cache(cache_path)
    if cached is not None:
        return FetchResult(
            cached, "cache", attempts, None, cache_path.stat().st_size,
            _now_iso(), _cache_age(cache_path), str(last_err),
        )
    raise RuntimeError(f"Bovada fetch failed: {last_err}") from last_err


def fetch_bovada_payload(*, cache_dir: Path = CACHE_DIR / "lines", refresh: bool = True) -> FetchResult:
    return _fetch_json(BOVADA_WNBA_URL, BOVADA_CACHE_NAME, cache_dir=cache_dir, refresh=refresh)


def _parse_american(value: object) -> int:
    text = str(value).strip().upper()
    if text in ("EVEN", "EV"):
        return 100
    return int(text.replace("+", ""))


def _price(outcome: dict) -> tuple[int, float, Optional[float]]:
    price = outcome["price"]
    am = _parse_american(price["american"])
    dec = float(price.get("decimal") or 0.0)
    handicap = price.get("handicap")
    line = float(handicap) if handicap not in (None, "") else None
    return am, dec, line


def _is_open(market: dict) -> bool:
    return str(market.get("status", "O")).upper() in ("O", "OPEN", "")


def _is_game_period(market: dict) -> bool:
    period = market.get("period", {}) or {}
    desc = str(period.get("description", "")).lower()
    return desc in ("game", "match", "regulation time", "") or bool(period.get("main"))


def _split_teams(description: str) -> Optional[tuple[str, str]]:
    for sep in (" @ ", " vs ", " VS ", " at ", " AT "):
        if sep in description:
            a, b = description.split(sep, 1)
            return a.strip(), b.strip()
    return None


def _extract_main_markets(event: dict, game: BovadaGame) -> None:
    for dg in event.get("displayGroups", []) or []:
        for market in dg.get("markets", []) or []:
            if not (_is_open(market) and _is_game_period(market)):
                continue
            desc = str(market.get("description", "")).lower()
            outcomes = market.get("outcomes") or []
            if len(outcomes) < 2:
                continue
            by_desc = {str(o.get("description", "")).strip().lower(): o for o in outcomes}
            try:
                if desc == "moneyline" and game.moneyline is None:
                    o_h = by_desc.get(game.home_name.lower())
                    o_a = by_desc.get(game.away_name.lower())
                    if not (o_h and o_a):
                        continue
                    am_h, dec_h, _ = _price(o_h)
                    am_a, dec_a, _ = _price(o_a)
                    game.moneyline = TwoWayPrice(None, am_h, am_a, dec_h, dec_a)
                elif desc == "point spread" and game.spread is None:
                    o_h = by_desc.get(game.home_name.lower())
                    o_a = by_desc.get(game.away_name.lower())
                    if not (o_h and o_a):
                        continue
                    am_h, dec_h, line_h = _price(o_h)
                    am_a, dec_a, _ = _price(o_a)
                    if line_h is None:
                        continue
                    game.spread = TwoWayPrice(line_h, am_h, am_a, dec_h, dec_a)
                elif desc == "total" and game.game_total is None:
                    o_o = by_desc.get("over")
                    o_u = by_desc.get("under")
                    if not (o_o and o_u):
                        continue
                    am_o, dec_o, line = _price(o_o)
                    am_u, dec_u, _ = _price(o_u)
                    if line is None:
                        continue
                    game.game_total = TwoWayPrice(line, am_o, am_u, dec_o, dec_u)
            except (KeyError, TypeError, ValueError):
                continue


def parse_games(payload: Any) -> tuple[List[BovadaGame], dict]:
    games: List[BovadaGame] = []
    diags = {"groups": 0, "events_seen": 0, "unparsed_descriptions": [], "unmatched_teams": []}
    for group in payload or []:
        diags["groups"] += 1
        path_parts = [str(p.get("description", "")) for p in group.get("path", [])]
        league_path = " / ".join(p for p in path_parts if p)
        for event in group.get("events", []) or []:
            diags["events_seen"] += 1
            desc = str(event.get("description", ""))
            teams = _split_teams(desc)
            if not teams:
                diags["unparsed_descriptions"].append(desc)
                continue
            away_name, home_name = teams
            away, home = normalize_team(away_name), normalize_team(home_name)
            if not (away and home):
                diags["unmatched_teams"].append(desc)
                continue
            start_ms = event.get("startTime")
            start_iso = None
            if start_ms:
                start_iso = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).isoformat()
            game = BovadaGame(
                event_id=str(event.get("id") or event.get("link") or desc),
                away=away,
                home=home,
                away_name=away_name,
                home_name=home_name,
                start_time_utc=start_iso,
                link=str(event.get("link") or ""),
                league_path=league_path,
            )
            _extract_main_markets(event, game)
            games.append(game)
    return games, diags


def fetch_live_games(
    *,
    cache_dir: Path = CACHE_DIR / "lines",
    refresh: bool = True,
    now: Optional[datetime] = None,
) -> tuple[List[BovadaGame], dict]:
    now = now or datetime.now(timezone.utc)
    coupon = fetch_bovada_payload(cache_dir=cache_dir, refresh=refresh)
    games, diags = parse_games(coupon.payload)
    parsed_count = len(games)

    kept: List[BovadaGame] = []
    stale: List[str] = []
    missing_kickoff: List[str] = []
    for game in games:
        if not game.start_time_utc:
            missing_kickoff.append(f"{game.away} @ {game.home}")
            continue
        try:
            kickoff = datetime.fromisoformat(game.start_time_utc.replace("Z", "+00:00"))
        except ValueError:
            missing_kickoff.append(f"{game.away} @ {game.home}")
            continue
        if kickoff <= now:
            stale.append(f"{game.away} @ {game.home}")
            continue
        kept.append(game)

    diags.update(
        {
            "games_parsed": parsed_count,
            "games_kept": len(kept),
            "with_moneyline": sum(1 for g in kept if g.moneyline),
            "with_spread": sum(1 for g in kept if g.spread),
            "with_game_total": sum(1 for g in kept if g.game_total),
            "coupon_fetch": coupon.as_dict(),
            "stale_games_filtered": len(stale),
            "stale_games": stale,
            "missing_kickoffs_filtered": len(missing_kickoff),
            "missing_kickoffs": missing_kickoff,
        }
    )
    return kept, diags
