from __future__ import annotations

import unittest
from datetime import date

from wnba_props.game_markets import (
    MarketQuote,
    build_snapshot,
    bovada_quote,
    fetch_game_markets,
    polymarket_quote,
    to_game_market,
)
from wnba_props.sources.bovada import BovadaGame, TwoWayPrice, parse_games
from wnba_props.sources.polymarket import _parse_event
from wnba_props.sources.teams import normalize_team


def _bovada_event(desc: str, home: str = "Atlanta Dream", away: str = "Connecticut Sun") -> dict:
    return {
        "id": "evt-1",
        "description": desc,
        "startTime": 1789687800000,
        "link": "/basketball/wnba/x",
        "displayGroups": [
            {
                "markets": [
                    {
                        "description": "Point Spread",
                        "status": "O",
                        "period": {"description": "Game"},
                        "outcomes": [
                            {"description": away, "price": {"handicap": "14.5", "american": "-110", "decimal": "1.909091"}},
                            {"description": home, "price": {"handicap": "-14.5", "american": "-110", "decimal": "1.909091"}},
                        ],
                    },
                    {
                        "description": "Moneyline",
                        "status": "O",
                        "period": {"description": "Game"},
                        "outcomes": [
                            {"description": away, "price": {"american": "+750", "decimal": "8.5"}},
                            {"description": home, "price": {"american": "-1400", "decimal": "1.071429"}},
                        ],
                    },
                    {
                        "description": "Total",
                        "status": "O",
                        "period": {"description": "Game"},
                        "outcomes": [
                            {"description": "Over", "price": {"handicap": "169.5", "american": "-110", "decimal": "1.909091"}},
                            {"description": "Under", "price": {"handicap": "169.5", "american": "-110", "decimal": "1.909091"}},
                        ],
                    },
                ]
            }
        ],
    }


class TeamNormalizationTests(unittest.TestCase):
    def test_full_names_and_aliases(self) -> None:
        self.assertEqual(normalize_team("Connecticut Sun"), "CON")
        self.assertEqual(normalize_team("Atlanta Dream"), "ATL")
        self.assertEqual(normalize_team("Las Vegas Aces"), "LV")
        self.assertEqual(normalize_team("Golden State Valkyries"), "GS")
        self.assertEqual(normalize_team("Los Angeles Sparks"), "LA")
        self.assertEqual(normalize_team("LVA"), "LV")
        self.assertEqual(normalize_team("Aces"), "LV")

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(normalize_team("Nowhere Nonexistent"))


class BovadaParseTests(unittest.TestCase):
    def test_parse_main_markets(self) -> None:
        payload = [{"path": [{"description": "Basketball"}], "events": [_bovada_event("Connecticut Sun @ Atlanta Dream")]}]
        games, diags = parse_games(payload)
        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual((game.away, game.home), ("CON", "ATL"))
        self.assertIsNotNone(game.moneyline)
        self.assertIsNotNone(game.spread)
        self.assertIsNotNone(game.game_total)
        self.assertEqual(game.spread.line, -14.5)
        self.assertEqual(game.moneyline.am_a, -1400)
        self.assertEqual(game.game_total.line, 169.5)

    def test_unmatched_team_reported(self) -> None:
        payload = [{"events": [_bovada_event("Foo @ Bar")]}]
        games, diags = parse_games(payload)
        self.assertEqual(games, [])
        self.assertTrue(diags["unmatched_teams"])


class PolymarketParseTests(unittest.TestCase):
    def test_parse_game_markets(self) -> None:
        event = {
            "title": "Connecticut Sun vs. Atlanta Dream",
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "outcomes": '["Connecticut Sun", "Atlanta Dream"]',
                    "outcomePrices": '["0.125", "0.875"]',
                },
                {
                    "sportsMarketType": "totals",
                    "question": "Connecticut Sun vs. Atlanta Dream: O/U 169.5",
                    "outcomes": '["Over", "Under"]',
                    "outcomePrices": '["0.5", "0.5"]',
                },
                {
                    "sportsMarketType": "spreads",
                    "question": "Spread: Atlanta Dream (-14.5)",
                    "outcomes": '["Atlanta Dream", "Connecticut Sun"]',
                    "outcomePrices": '["0.285", "0.715"]',
                },
            ],
        }
        parsed = _parse_event(event)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed["away"], parsed["home"]), ("CON", "ATL"))
        self.assertIn("ATL", parsed["moneyline"], parsed["moneyline"])
        self.assertEqual(parsed["total"]["line"], 169.5)
        self.assertEqual(parsed["spread"]["line"], -14.5)

    def test_quote_maps_moneyline_by_team_key(self) -> None:
        event = {
            "title": "Connecticut Sun vs. Atlanta Dream",
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "outcomes": '["Connecticut Sun", "Atlanta Dream"]',
                    "outcomePrices": '["0.125", "0.875"]',
                }
            ],
        }
        parsed = _parse_event(event)
        assert parsed is not None
        quote = polymarket_quote(parsed)
        self.assertIsNotNone(quote.moneyline)
        assert quote.moneyline is not None
        self.assertEqual(quote.moneyline["home_am"], -700)
        self.assertEqual(quote.moneyline["away_am"], 700)


class HierarchyTests(unittest.TestCase):
    def _bovada(self, total_line: float = 169.5) -> MarketQuote:
        return MarketQuote(
            source="bovada",
            away="CON",
            home="ATL",
            start_time_utc="2026-09-17T23:30:00+00:00",
            moneyline={"home_am": -1400, "away_am": 750, "home_dec": 1.0714, "away_dec": 8.5},
            spread={"line": -14.5, "home_am": -110, "away_am": -110, "home_dec": 1.909, "away_dec": 1.909},
            total={"line": total_line, "over_am": -110, "under_am": -110, "over_dec": 1.909, "under_dec": 1.909},
        )

    def _poly(self) -> MarketQuote:
        return MarketQuote(
            source="polymarket",
            away="CON",
            home="ATL",
            start_time_utc=None,
            moneyline={"home_am": -700, "away_am": 500, "home_dec": 1.14, "away_dec": 6.0},
            total={"line": 169.5, "over_am": -105, "under_am": -115, "over_dec": 1.95, "under_dec": 1.87},
        )

    def test_primary_used_when_complete(self) -> None:
        snapshot = build_snapshot(self._bovada(), self._poly())
        self.assertEqual(snapshot.moneyline_source, "bovada")
        self.assertEqual(snapshot.spread_source, "bovada")
        self.assertEqual(snapshot.total_source, "bovada")

    def test_nonwhole_total_preferred_from_cross_check(self) -> None:
        snapshot = build_snapshot(self._bovada(total_line=169.0), self._poly())
        self.assertEqual(snapshot.total_source, "polymarket")
        self.assertEqual(snapshot.total["line"], 169.5)
        self.assertEqual(
            snapshot.total_selection_reason, "cross_check_nonwhole_total_preferred"
        )
        # prices stayed with the selected line's source; no mixing
        self.assertEqual(snapshot.total["over_am"], -105)

    def test_fallback_when_primary_missing_family(self) -> None:
        primary = self._bovada()
        primary.spread = None
        cross = self._poly()
        cross.spread = {
            "line": -14.5, "home_am": -108, "away_am": -112,
            "home_dec": 1.926, "away_dec": 1.893,
        }
        snapshot = build_snapshot(primary, cross)
        self.assertEqual(snapshot.spread_source, "polymarket")

    def test_to_game_market(self) -> None:
        snapshot = build_snapshot(self._bovada(), self._poly())
        market = to_game_market(snapshot)
        self.assertEqual(market.home_moneyline, -1400)
        self.assertEqual(market.home_spread, -14.5)
        self.assertEqual(market.total_line, 169.5)


class StaleMarketTests(unittest.TestCase):
    def test_stale_bovada_prices_are_discarded(self) -> None:
        from unittest.mock import patch

        game = BovadaGame(
            event_id="1",
            away="CON",
            home="ATL",
            away_name="Connecticut Sun",
            home_name="Atlanta Dream",
            start_time_utc="2026-09-17T23:30:00+00:00",
            link="/x",
            league_path="Basketball / WNBA",
            moneyline=TwoWayPrice(None, -1400, 750, 1.07, 8.5),
            spread=TwoWayPrice(-14.5, -110, -110, 1.9, 1.9),
            game_total=TwoWayPrice(169.5, -110, -110, 1.9, 1.9),
        )
        diags = {"coupon_fetch": {"mode": "cache", "cache_age_seconds": 999_999}}
        with patch(
            "wnba_props.game_markets.fetch_bovada_games", return_value=([game], diags)
        ), patch(
            "wnba_props.game_markets.fetch_wnba_references", return_value=({}, {})
        ):
            snapshots, out = fetch_game_markets(
                screen_date=date(2026, 9, 17), max_cache_age_seconds=3600.0
            )
        self.assertTrue(out["bovada_stale"])
        snapshot = snapshots[("CON", "ATL")]
        self.assertIsNone(snapshot.moneyline)
        self.assertIsNone(snapshot.spread)
        self.assertIsNone(snapshot.total)


if __name__ == "__main__":
    unittest.main()
