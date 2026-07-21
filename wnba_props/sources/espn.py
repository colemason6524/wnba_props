from __future__ import annotations

from datetime import date

from ..config import ESPN_TO_TEAM_ABBR
from ..models import Game
from ..utils import parse_iso_datetime, fetch_json


class EspnSlateSource:
    SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"

    def fetch_games(self, screen_date: date) -> list[Game]:
        url = self.SCOREBOARD_URL.format(date_str=screen_date.strftime("%Y%m%d"))
        data = fetch_json(url)
        games: list[Game] = []
        for event in data.get("events", []):
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            competition = competitions[0]
            home_team = ""
            away_team = ""
            for competitor in competition.get("competitors", []):
                team_name = competitor.get("team", {}).get("displayName", "")
                team_abbr = ESPN_TO_TEAM_ABBR.get(team_name, competitor.get("team", {}).get("abbreviation", ""))
                if competitor.get("homeAway") == "home":
                    home_team = team_abbr
                else:
                    away_team = team_abbr
            if not home_team or not away_team:
                continue
            game_time = parse_iso_datetime(event["date"])
            games.append(
                Game(
                    game_id=str(event["id"]),
                    game_date=screen_date,
                    game_time=game_time,
                    home_team=home_team,
                    away_team=away_team,
                    source="espn",
                )
            )
        return games
