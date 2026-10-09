from __future__ import annotations

from datetime import date

from ..cache import JsonCache
from ..config import TEAM_ABBR_TO_ESPN_ID
from ..models import TeamInjury
from ..utils import fetch_espn_json, normalize_name


class EspnInjurySource:
    INJURIES_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/wnba/teams/{team_id}/injuries"

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_team_injuries(
        self, team: str, screen_date: date, *, force_refresh: bool = False
    ) -> list[TeamInjury]:
        """Fetch one team's injury list, failing closed on a forced refresh.

        With ``force_refresh`` the cache is skipped for a live ESPN pull
        (the fresh payload still rewrites the cache entry). Fetch failures
        propagate so the caller can fail the board instead of publishing
        from cached injuries.
        """
        team_id = TEAM_ABBR_TO_ESPN_ID.get(team)
        if not team_id:
            return []

        cache_key = f"espn_injuries_{team}_{screen_date.isoformat()}"
        if not force_refresh:
            cached = self.cache.get(cache_key)
            if cached:
                return [TeamInjury(**item) for item in cached]

        payload = fetch_espn_json(self.INJURIES_URL.format(team_id=team_id))
        injuries: list[TeamInjury] = []
        for item in payload.get("items", []):
            ref = item.get("$ref")
            if not ref:
                continue
            injury_payload = fetch_espn_json(ref)
            athlete_ref = injury_payload.get("athlete", {}).get("$ref")
            if not athlete_ref:
                continue
            athlete_payload = fetch_espn_json(athlete_ref)
            player_name = athlete_payload.get("displayName", "").strip()
            if not player_name:
                continue
            details = injury_payload.get("details", {})
            injuries.append(
                TeamInjury(
                    player_name=player_name,
                    player_name_norm=normalize_name(player_name),
                    team=team,
                    status=str(injury_payload.get("status", "")).strip(),
                    note=str(injury_payload.get("shortComment", "")).strip(),
                    injury_type=str(details.get("type", "")).strip(),
                    return_date=details.get("returnDate"),
                )
            )

        self.cache.set(cache_key, [injury.__dict__ for injury in injuries])
        return injuries
