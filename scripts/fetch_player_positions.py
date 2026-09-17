"""Build the committed player position map from ESPN rosters.

Writes ``config/player_positions.json`` mapping normalized player names to
coarse position classes (G/F/C). Both the offline artifact fit and the live
pipeline load this map so opponent positional defense is consistent.

Usage:
    python3 scripts/fetch_player_positions.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wnba_props.cache import JsonCache  # noqa: E402
from wnba_props.config import (  # noqa: E402
    CACHE_DIR,
    PLAYER_POSITIONS_PATH,
    TEAM_ABBR_TO_ESPN_ID,
)
from wnba_props.features.positions import fetch_position_map  # noqa: E402


def main() -> int:
    cache = JsonCache(CACHE_DIR / "shared", ttl_hours=24 * 30)
    positions = fetch_position_map(list(TEAM_ABBR_TO_ESPN_ID), cache)
    if not positions:
        print("no positions fetched", file=sys.stderr)
        return 1
    PLAYER_POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYER_POSITIONS_PATH.write_text(json.dumps(positions, indent=2, sort_keys=True))
    print(f"wrote {len(positions)} positions -> {PLAYER_POSITIONS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
