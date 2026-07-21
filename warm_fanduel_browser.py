from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from wnba_props.config import CACHE_DIR, TEAM_ABBR_TO_FANDUEL_SLUG, load_settings
from wnba_props.sources.espn import EspnSlateSource


BASE_URL = "https://sportsbook.fanduel.com"
LEAGUE_URL = f"{BASE_URL}/basketball/wnba"
SUPPORTED_TABS = ["player-points", "player-rebounds", "player-assists", "player-threes"]
TAB_MARKERS = {
    "player-points": [" - Points"],
    "player-rebounds": [" - Rebounds"],
    "player-assists": [" - Assists"],
    "player-threes": [" - Made Threes", " - Threes"],
}


def cache_key_for_event_tab(url: str) -> str:
    parsed = urlparse(url)
    safe_path = f"{parsed.path}_{parsed.query}".strip("/").replace("/", "_")
    safe_path = re.sub(r"[^A-Za-z0-9_]+", "_", safe_path)
    return f"fanduel_event_{safe_path}"


def with_tab(url: str, tab: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["tab"] = [tab]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def base_event_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def write_cache(cache_key: str, payload: str, source_url: str) -> Path:
    cache_dir = CACHE_DIR / "lines"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key}.json"
    path.write_text(
        json.dumps(
            {
                "source_url": source_url,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "data": payload,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return path


def game_slug_pairs() -> list[tuple[str, str]]:
    settings = load_settings()
    games = EspnSlateSource().fetch_games(settings.screen_date)
    if settings.pregame_only:
        now_utc = datetime.now(timezone.utc)
        games = [game for game in games if game.game_time > now_utc]
    pairs: list[tuple[str, str]] = []
    for game in games:
        away_slug = TEAM_ABBR_TO_FANDUEL_SLUG.get(game.away_team, "")
        home_slug = TEAM_ABBR_TO_FANDUEL_SLUG.get(game.home_team, "")
        if away_slug and home_slug:
            pairs.append((away_slug, home_slug))
    return pairs


def link_matches_game(url: str, pairs: list[tuple[str, str]]) -> bool:
    lowered = url.lower()
    return any(away_slug in lowered and home_slug in lowered for away_slug, home_slug in pairs)


def discover_event_urls(page, pairs: list[tuple[str, str]]) -> list[str]:
    page.goto(LEAGUE_URL, wait_until="domcontentloaded", timeout=60_000)
    wait_for_fanduel_content(page)
    links = page.eval_on_selector_all(
        "a[href]",
        """anchors => anchors.map(anchor => anchor.href).filter(Boolean)""",
    )
    event_urls = {
        base_event_url(url)
        for url in links
        if "/basketball/wnba/" in url and re.search(r"-\d+(?:\\?|$)", url) and link_matches_game(url, pairs)
    }
    return sorted(event_urls)


def wait_for_fanduel_content(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    try:
        page.wait_for_selector('a[href*="/basketball/wnba/"], [aria-label*="Player Points"], [aria-label*="WNBA"]', timeout=30_000)
    except PlaywrightTimeoutError:
        print("FanDuel content did not fully load yet. If a human check is visible, solve it in the opened browser.")
        page.wait_for_timeout(20_000)


def expand_visible_player_markets(page, tab: str) -> int:
    markers = TAB_MARKERS.get(tab, [])
    return page.evaluate(
        """markers => {
            let clicked = 0;
            const elements = Array.from(document.querySelectorAll('[aria-expanded="false"][aria-label]'));
            for (const element of elements) {
                const label = element.getAttribute('aria-label') || '';
                if (!markers.some(marker => label.includes(marker))) continue;
                element.scrollIntoView({block: 'center'});
                element.click();
                clicked += 1;
            }
            return clicked;
        }""",
        markers,
    )


def collect_tab_html(page, tab_url: str, tab: str) -> str:
    page.goto(tab_url, wait_until="domcontentloaded", timeout=60_000)
    wait_for_fanduel_content(page)
    snapshots: list[str] = []
    last_height = 0
    stable_scrolls = 0
    for _ in range(24):
        expand_visible_player_markets(page, tab)
        page.wait_for_timeout(750)
        snapshots.append(page.evaluate("document.documentElement.outerHTML"))
        height = page.evaluate("Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
        current_y = page.evaluate("window.scrollY")
        page.evaluate("window.scrollBy(0, Math.floor(window.innerHeight * 0.85))")
        page.wait_for_timeout(500)
        next_y = page.evaluate("window.scrollY")
        if height == last_height and next_y == current_y:
            stable_scrolls += 1
        else:
            stable_scrolls = 0
        last_height = height
        if stable_scrolls >= 2:
            break
    expand_visible_player_markets(page, tab)
    snapshots.append(page.evaluate("document.documentElement.outerHTML"))
    return "\n".join(snapshots)


def launch_context(playwright, user_data_dir: Path, headed: bool):
    launch_kwargs = {
        "headless": not headed,
        "viewport": {"width": 1440, "height": 1000},
    }
    try:
        return playwright.chromium.launch_persistent_context(
            str(user_data_dir),
            channel="chrome",
            **launch_kwargs,
        )
    except Exception:
        return playwright.chromium.launch_persistent_context(str(user_data_dir), **launch_kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm FanDuel WNBA event-tab caches through a real browser.")
    parser.add_argument("--headed", action="store_true", help="Show the browser. Use this if FanDuel asks for a human check.")
    parser.add_argument("--tabs", default=",".join(SUPPORTED_TABS), help="Comma-separated FanDuel tabs to cache.")
    parser.add_argument(
        "--profile-dir",
        default=str(CACHE_DIR / "browser_profile"),
        help="Persistent browser profile directory used for FanDuel cookies/session state.",
    )
    args = parser.parse_args()

    tabs = [tab.strip() for tab in args.tabs.split(",") if tab.strip()]
    unsupported = [tab for tab in tabs if tab not in SUPPORTED_TABS]
    if unsupported:
        raise SystemExit(f"Unsupported tab(s): {', '.join(unsupported)}")

    pairs = game_slug_pairs()
    if not pairs:
        print("No eligible WNBA games found for today.")
        return 0

    profile_dir = Path(args.profile_dir).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    with sync_playwright() as playwright:
        context = launch_context(playwright, profile_dir, headed=args.headed)
        page = context.pages[0] if context.pages else context.new_page()
        event_urls = discover_event_urls(page, pairs)
        if not event_urls:
            context.close()
            print("No matching FanDuel WNBA event links were discovered.")
            print("Try again with --headed, solve any visible FanDuel human check, then rerun.")
            return 1

        for event_url in event_urls:
            print(f"FanDuel event: {event_url}")
            for tab in tabs:
                tab_url = with_tab(event_url, tab)
                payload = collect_tab_html(page, tab_url, tab)
                cache_key = cache_key_for_event_tab(tab_url)
                path = write_cache(cache_key, payload, tab_url)
                written.append(path)
                print(f"- cached {tab}: {path}")
        context.close()

    print("FanDuel browser cache warm complete:")
    for path in written:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
