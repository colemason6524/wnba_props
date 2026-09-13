"""WNBA game-market collection and source hierarchy.

Bovada is primary for moneyline, spread and total. Polymarket is the fallback
reference when Bovada lacks a family, or a cross-check when it does. ESPN odds
provide a numeric cross-check only (no prices). A line and its prices always
come from the same source; sources are never averaged.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import CACHE_DIR
from .modeling.game_forecast import GameMarket
from .modeling.value import american_to_decimal, decimal_to_american
from .sources.bovada import BovadaGame, fetch_live_games as fetch_bovada_games
from .sources.polymarket import fetch_wnba_references

SOURCE_BOVADA = "bovada"
SOURCE_POLYMARKET = "polymarket"
SOURCE_ESPN_CROSS = "espn_odds_cross_check"

GAME_MARKETS_HISTORY_SCHEMA_VERSION = 1
GAME_MARKETS_SOURCE_POLICY_VERSION = "bovada-primary-polymarket-fallback-espn-crosscheck-v1"


@dataclass
class MarketQuote:
    source: str
    away: str
    home: str
    start_time_utc: Optional[str]
    moneyline: Optional[dict] = None
    spread: Optional[dict] = None
    total: Optional[dict] = None
    captured_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class MarketSnapshot:
    away: str
    home: str
    start_time_utc: Optional[str]
    moneyline: Optional[dict]
    spread: Optional[dict]
    total: Optional[dict]
    moneyline_source: Optional[str]
    spread_source: Optional[str]
    total_source: Optional[str]
    total_selection_reason: str = ""
    cross_check_total: Optional[dict] = None
    cross_check_spread: Optional[dict] = None
    primary_source: str = SOURCE_BOVADA
    sources: List[str] = field(default_factory=list)
    captured_at: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def bovada_quote(game: BovadaGame) -> MarketQuote:
    moneyline = None
    if game.moneyline is not None:
        moneyline = {
            "home_am": game.moneyline.am_a,
            "away_am": game.moneyline.am_b,
            "home_dec": round(game.moneyline.dec_a, 4),
            "away_dec": round(game.moneyline.dec_b, 4),
        }
    spread = None
    if game.spread is not None:
        spread = {
            "line": game.spread.line,
            "home_am": game.spread.am_a,
            "away_am": game.spread.am_b,
            "home_dec": round(game.spread.dec_a, 4),
            "away_dec": round(game.spread.dec_b, 4),
        }
    total = None
    if game.game_total is not None:
        total = {
            "line": game.game_total.line,
            "over_am": game.game_total.am_a,
            "under_am": game.game_total.am_b,
            "over_dec": round(game.game_total.dec_a, 4),
            "under_dec": round(game.game_total.dec_b, 4),
        }
    return MarketQuote(
        source=SOURCE_BOVADA,
        away=game.away,
        home=game.home,
        start_time_utc=game.start_time_utc,
        moneyline=moneyline,
        spread=spread,
        total=total,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def polymarket_quote(ref: dict) -> MarketQuote:
    moneyline = None
    ml = ref.get("moneyline") or {}
    home_key = ref.get("home")
    away_key = ref.get("away")
    home_entry = ml.get(home_key) if home_key else None
    away_entry = ml.get(away_key) if away_key else None
    if home_entry and away_entry:
        moneyline = {
            "home_am": decimal_to_american(home_entry["decimal"]),
            "away_am": decimal_to_american(away_entry["decimal"]),
            "home_dec": home_entry["decimal"],
            "away_dec": away_entry["decimal"],
        }
    spread = None
    if ref.get("spread"):
        sp = ref["spread"]
        spread = {
            "line": sp["line"],
            "home_am": decimal_to_american(sp["home_decimal"]),
            "away_am": decimal_to_american(sp["away_decimal"]),
            "home_dec": sp["home_decimal"],
            "away_dec": sp["away_decimal"],
        }
    total = None
    if ref.get("total"):
        tp = ref["total"]
        total = {
            "line": tp["line"],
            "over_am": decimal_to_american(tp["over_decimal"]),
            "under_am": decimal_to_american(tp["under_decimal"]),
            "over_dec": tp["over_decimal"],
            "under_dec": tp["under_decimal"],
        }
    return MarketQuote(
        source=SOURCE_POLYMARKET,
        away=ref["away"],
        home=ref["home"],
        start_time_utc=None,
        moneyline=moneyline,
        spread=spread,
        total=total,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def _is_whole(line: Optional[float]) -> bool:
    return line is not None and abs(line - round(line)) < 1e-9


def build_snapshot(
    primary: MarketQuote,
    cross_check: Optional[MarketQuote] = None,
) -> MarketSnapshot:
    """Select each family from the primary source, falling back per-family."""
    moneyline = primary.moneyline or (cross_check.moneyline if cross_check else None)
    moneyline_source = (
        primary.source if primary.moneyline else (cross_check.source if cross_check and cross_check.moneyline else None)
    )
    spread = primary.spread or (cross_check.spread if cross_check else None)
    spread_source = (
        primary.source if primary.spread else (cross_check.source if cross_check and cross_check.spread else None)
    )

    total = primary.total
    total_source = primary.source if primary.total else None
    reason = "primary_total_selected" if primary.total else ""
    if total is None and cross_check and cross_check.total:
        total = cross_check.total
        total_source = cross_check.source
        reason = "cross_check_total_fallback"
    elif total is not None and cross_check and cross_check.total:
        if _is_whole(total.get("line")) and not _is_whole(cross_check.total.get("line")):
            total = cross_check.total
            total_source = cross_check.source
            reason = "cross_check_nonwhole_total_preferred"
        else:
            reason = "primary_total_selected"

    sources: List[str] = []
    for source in (moneyline_source, spread_source, total_source):
        if source and source not in sources:
            sources.append(source)

    return MarketSnapshot(
        away=primary.away,
        home=primary.home,
        start_time_utc=primary.start_time_utc,
        moneyline=moneyline,
        spread=spread,
        total=total,
        moneyline_source=moneyline_source,
        spread_source=spread_source,
        total_source=total_source,
        total_selection_reason=reason,
        cross_check_total=(cross_check.total if cross_check else None),
        cross_check_spread=(cross_check.spread if cross_check else None),
        primary_source=primary.source,
        sources=sources,
        captured_at=primary.captured_at,
    )


def to_game_market(snapshot: MarketSnapshot) -> GameMarket:
    ml = snapshot.moneyline or {}
    sp = snapshot.spread or {}
    total = snapshot.total or {}
    return GameMarket(
        home_moneyline=ml.get("home_am"),
        away_moneyline=ml.get("away_am"),
        home_spread=sp.get("line"),
        home_spread_price=sp.get("home_am"),
        away_spread_price=sp.get("away_am"),
        total_line=total.get("line"),
        over_price=total.get("over_am"),
        under_price=total.get("under_am"),
    )


def _et_date(iso: Optional[str]) -> Optional[date]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("America/Detroit")).date()
    except Exception:  # noqa: BLE001 - tzdata may be absent on some hosts
        return dt.date()


def fetch_game_markets(
    *,
    screen_date: date,
    cache_dir: Path = CACHE_DIR / "lines",
    refresh: bool = True,
    max_cache_age_seconds: Optional[float] = None,
) -> Tuple[Dict[Tuple[str, str], MarketSnapshot], dict]:
    """Collect and combine Bovada + Polymarket into per-matchup snapshots.

    When ``max_cache_age_seconds`` is set and the Bovada coupon came from a
    cache older than that, Bovada pricing is discarded (the event list is kept
    only as a matchup skeleton) and Polymarket fills in families it can.
    """
    diagnostics: Dict[str, Any] = {
        "source_policy_version": GAME_MARKETS_SOURCE_POLICY_VERSION,
        "screen_date": screen_date.isoformat(),
        "bovada": {},
        "polymarket": {},
        "espn_cross_check": {},
        "coverage": {},
        "unmatched_bovada": [],
        "bovada_stale": False,
    }

    bovada_games, bovada_diags = fetch_bovada_games(cache_dir=cache_dir, refresh=refresh)
    diagnostics["bovada"] = bovada_diags
    bovada_games = [g for g in bovada_games if _et_date(g.start_time_utc) == screen_date]

    coupon = bovada_diags.get("coupon_fetch", {}) if isinstance(bovada_diags, dict) else {}
    if max_cache_age_seconds is not None and coupon.get("mode") == "cache":
        age = float(coupon.get("cache_age_seconds") or 0.0)
        if age > max_cache_age_seconds:
            diagnostics["bovada_stale"] = True
            diagnostics["bovada_cache_age_seconds"] = round(age, 1)
            for game in bovada_games:
                game.moneyline = None
                game.spread = None
                game.game_total = None

    try:
        poly_refs, poly_diags = fetch_wnba_references()
    except Exception as exc:  # noqa: BLE001 - secondary source never blocks
        poly_refs, poly_diags = {}, {"source": "polymarket", "error": str(exc)}
    diagnostics["polymarket"] = poly_diags

    snapshots: Dict[Tuple[str, str], MarketSnapshot] = {}
    unmatched: List[str] = []
    for game in bovada_games:
        primary = bovada_quote(game)
        cross_ref = poly_refs.get((game.away, game.home))
        cross_check = polymarket_quote(cross_ref) if cross_ref else None
        if cross_check is None:
            unmatched.append(f"{game.away} @ {game.home}")
        snapshots[(game.away, game.home)] = build_snapshot(primary, cross_check)
    diagnostics["unmatched_bovada"] = unmatched

    coverage = {
        "bovada_games": len(bovada_games),
        "snapshots": len(snapshots),
        "with_moneyline": sum(1 for s in snapshots.values() if s.moneyline),
        "with_spread": sum(1 for s in snapshots.values() if s.spread),
        "with_total": sum(1 for s in snapshots.values() if s.total),
        "moneyline_source_bovada": sum(1 for s in snapshots.values() if s.moneyline_source == SOURCE_BOVADA),
        "moneyline_source_polymarket": sum(1 for s in snapshots.values() if s.moneyline_source == SOURCE_POLYMARKET),
        "spread_source_bovada": sum(1 for s in snapshots.values() if s.spread_source == SOURCE_BOVADA),
        "spread_source_polymarket": sum(1 for s in snapshots.values() if s.spread_source == SOURCE_POLYMARKET),
        "total_source_bovada": sum(1 for s in snapshots.values() if s.total_source == SOURCE_BOVADA),
        "total_source_polymarket": sum(1 for s in snapshots.values() if s.total_source == SOURCE_POLYMARKET),
        "cross_check_nonwhole_total_preferred": sum(
            1 for s in snapshots.values() if s.total_selection_reason == "cross_check_nonwhole_total_preferred"
        ),
    }
    diagnostics["coverage"] = coverage
    return snapshots, diagnostics


def export_game_markets(
    path: Path,
    *,
    screen_date: date,
    snapshots: Dict[Tuple[str, str], MarketSnapshot],
    diagnostics: dict,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "history_schema_version": GAME_MARKETS_HISTORY_SCHEMA_VERSION,
        "source_policy_version": GAME_MARKETS_SOURCE_POLICY_VERSION,
        "screen_date": screen_date.isoformat(),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "observation_only": True,
        "diagnostics": diagnostics,
        "games": [snapshot.as_dict() for snapshot in snapshots.values()],
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)
    return path
