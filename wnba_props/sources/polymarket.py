"""Polymarket WNBA secondary reference source (best-effort, fail-open).

Bovada is primary. Polymarket is attached only as a comparison reference and
is never averaged with Bovada. WNBA game markets live under the ``wnba`` tag:
moneyline, spreads and totals. Any error returns no references plus a
diagnostic, and the board proceeds.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from .teams import normalize_team

GAMMA_BASE = "https://gamma-api.polymarket.com"
TAG_SLUGS = ("wnba",)
HTTP_TIMEOUT_SECONDS = 25

_SPREAD = re.compile(r"Spread:\s*(.+?)\s*\(([+-]?\d+(?:\.\d+)?)\)", re.IGNORECASE)
_TOTAL = re.compile(r"O/U\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)


def _get(url: str) -> Optional[object]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "wnba_props/1.0 (reference-only)", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _split_matchup(text: str) -> Optional[Tuple[str, str]]:
    for sep in (" vs. ", " vs ", " @ ", " at "):
        if sep in text:
            a, b = text.split(sep, 1)
            away, home = normalize_team(a.strip()), normalize_team(b.strip())
            if away and home:
                return away, home
    return None


def _decimal(price: float) -> Optional[float]:
    if 0.0 < price < 1.0:
        return round(1.0 / price, 4)
    return None


def _parse_event(event: dict) -> Optional[dict]:
    title = str(event.get("title") or event.get("question") or "")
    teams = _split_matchup(title)
    if not teams:
        return None
    away, home = teams

    moneyline: Dict[str, dict] = {}
    best_total: Optional[dict] = None
    best_total_gap = 1.0
    spread: Optional[dict] = None

    for market in event.get("markets") or []:
        mtype = str(market.get("sportsMarketType") or "").lower()
        try:
            outcomes = json.loads(market.get("outcomes") or "[]")
            prices = json.loads(market.get("outcomePrices") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue

        if mtype == "moneyline" and len(outcomes) == 2 and len(prices) == 2:
            for name, price in zip(outcomes, prices):
                canon = normalize_team(name)
                dec = _decimal(float(price))
                if canon and dec is not None:
                    moneyline[canon] = {
                        "decimal": dec,
                        "p": round(float(price), 4),
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    }
        elif mtype == "totals" and len(outcomes) == 2:
            match = _TOTAL.search(str(market.get("question") or ""))
            if not match:
                continue
            price_by_name = {str(o).lower(): float(p) for o, p in zip(outcomes, prices)}
            p_over = price_by_name.get("over")
            if p_over is None or not 0.0 < p_over < 1.0:
                continue
            gap = abs(p_over - 0.5)
            if gap < best_total_gap:
                best_total_gap = gap
                best_total = {
                    "line": float(match.group(1)),
                    "over_decimal": round(1.0 / p_over, 4),
                    "under_decimal": round(1.0 / (1.0 - p_over), 4),
                    "over_p": round(p_over, 4),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
        elif mtype == "spreads" and len(outcomes) == 2 and len(prices) == 2:
            match = _SPREAD.search(str(market.get("question") or ""))
            if not match:
                continue
            favorite = normalize_team(match.group(1))
            line = abs(float(match.group(2)))
            if favorite is None:
                continue
            price_by_team = {}
            for name, price in zip(outcomes, prices):
                canon = normalize_team(name)
                dec = _decimal(float(price))
                if canon and dec is not None:
                    price_by_team[canon] = dec
            if home not in price_by_team or away not in price_by_team:
                continue
            if favorite == home:
                home_spread = -line
            elif favorite == away:
                home_spread = line
            else:
                continue
            spread = {
                "line": home_spread,
                "home_decimal": price_by_team[home],
                "away_decimal": price_by_team[away],
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

    if not moneyline and best_total is None and spread is None:
        return None
    start_iso = (
        event.get("startTime")
        or event.get("gameStartTime")
        or event.get("endDate")
    )
    return {
        "away": away,
        "home": home,
        "start_time_utc": str(start_iso) if start_iso else None,
        "moneyline": moneyline or None,
        "total": best_total,
        "spread": spread,
    }


def fetch_wnba_references(now: Optional[datetime] = None) -> Tuple[Dict[Tuple[str, str], dict], dict]:
    """(parsed_events_by_matchup, diagnostics). Always safe to call."""
    diags = {
        "source": "polymarket",
        "events_seen": 0,
        "matched": 0,
        "tags_tried": [],
        "errors": [],
        "with_moneyline": 0,
        "with_spread": 0,
        "with_total": 0,
    }
    refs: Dict[Tuple[str, str], dict] = {}
    for slug in TAG_SLUGS:
        diags["tags_tried"].append(slug)
        payload = _get(f"{GAMMA_BASE}/events?closed=false&limit=200&tag_slug={slug}")
        if not isinstance(payload, list):
            diags["errors"].append(f"{slug}: no payload")
            continue
        diags["events_seen"] += len(payload)
        for event in payload:
            parsed = _parse_event(event)
            if not parsed:
                continue
            refs[(parsed["away"], parsed["home"])] = parsed
            diags["matched"] += 1
            if parsed["moneyline"]:
                diags["with_moneyline"] += 1
            if parsed["spread"]:
                diags["with_spread"] += 1
            if parsed["total"]:
                diags["with_total"] += 1
        if refs:
            break
    return refs, diags
