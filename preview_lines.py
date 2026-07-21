from __future__ import annotations

import sys
from datetime import datetime, timezone

from wnba_props.cache import JsonCache
from wnba_props.config import CACHE_DIR, load_settings
from wnba_props.sources.draftkings import DraftKingsSource
from wnba_props.sources.espn import EspnSlateSource
from wnba_props.sources.fanduel import FanDuelSource
from wnba_props.sources.manual_lines import ManualLineSource
from wnba_props.sources.playerprops import PlayerPropsSource
from wnba_props.sources.propcruncher import PropCruncherSource


def main() -> int:
    settings = load_settings()
    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    lines_cache = JsonCache(CACHE_DIR / "lines", ttl_hours=settings.lines_cache_ttl_minutes / 60.0)
    slate_source = EspnSlateSource()

    if settings.line_source == "playerprops":
        line_source = PlayerPropsSource(settings, lines_cache)
    elif settings.line_source == "propcruncher":
        line_source = PropCruncherSource(settings, shared_cache)
    elif settings.line_source == "draftkings":
        line_source = DraftKingsSource(settings, lines_cache)
    elif settings.line_source == "fanduel":
        line_source = FanDuelSource(settings, shared_cache, lines_cache)
    elif settings.line_source == "manual":
        line_source = ManualLineSource()
    else:
        print("Supported LINE_SOURCE values: playerprops, draftkings, propcruncher, fanduel, manual.", file=sys.stderr)
        return 1

    games = slate_source.fetch_games(settings.screen_date)
    if settings.pregame_only:
        now_utc = datetime.now(timezone.utc)
        games = [game for game in games if game.game_time > now_utc]

    print(f"Slate games: {len(games)}")
    for game in games:
        print(f"- {game.away_team}@{game.home_team} {game.game_time.isoformat()}")

    lines = line_source.fetch_prop_lines(games)
    print("")
    print(f"Prop lines from {settings.line_source}: {len(lines)}")
    for line in lines:
        print(f"- {line.player_name_raw} {line.team}@{line.opponent} {line.prop_type} {line.line:.1f}")

    failures = getattr(line_source, "failures", [])
    if failures:
        print("")
        print("Line-source issues:")
        for failure in failures[:20]:
            print(f"- {failure}")
        if len(failures) > 20:
            print(f"- ... and {len(failures) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
