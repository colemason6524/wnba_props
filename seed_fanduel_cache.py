from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from wnba_props.config import CACHE_DIR


SUPPORTED_TABS = ["player-points", "player-rebounds", "player-assists", "player-threes"]


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


def infer_event_url(payload: str) -> str:
    decoded = unescape(payload)
    absolute = re.search(r"https://sportsbook\.fanduel\.com/basketball/wnba/[^\"'<>\s]+", decoded)
    if absolute:
        return base_event_url(absolute.group(0))
    relative = re.search(r'href=["\'](/basketball/wnba/[^"\']+)', decoded)
    if relative:
        return base_event_url(f"https://sportsbook.fanduel.com{relative.group(1)}")
    return ""


def looks_like_unhydrated_app_shell(payload: str) -> bool:
    lowered = payload.lower()
    has_shell_markers = "fanduel sportsbook" in lowered and "/assets/" in lowered
    has_prop_markers = any(marker in lowered for marker in [" over ", " under ", "caitlin", "player points"])
    return has_shell_markers and not has_prop_markers


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed FanDuel WNBA event-tab HTML into the line cache.")
    parser.add_argument(
        "--event-url",
        default="",
        help="FanDuel WNBA event URL. Any tab is okay. If omitted, the script tries to infer it from the HTML.",
    )
    parser.add_argument(
        "--html",
        action="append",
        default=[],
        help="One tab=file pair, for example player-points=/tmp/points.html. Repeat for each tab.",
    )
    args = parser.parse_args()

    if not args.html:
        raise SystemExit("Provide at least one --html tab=/path/to/file pair.")

    written: list[Path] = []
    for item in args.html:
        if "=" not in item:
            raise SystemExit(f"Expected tab=/path/to/file, got: {item}")
        tab, file_path = item.split("=", 1)
        tab = tab.strip()
        if tab not in SUPPORTED_TABS:
            raise SystemExit(f"Unsupported tab '{tab}'. Use one of: {', '.join(SUPPORTED_TABS)}")
        html_path = Path(file_path).expanduser()
        if not html_path.exists():
            raise SystemExit(f"HTML file does not exist: {html_path}")

        payload = html_path.read_text(errors="replace")
        if looks_like_unhydrated_app_shell(payload):
            print(
                f"Warning: {html_path} looks like the FanDuel app shell, not rendered prop content. "
                "Use browser console copy(document.documentElement.outerHTML) after props load."
            )

        event_url = args.event_url.strip() or infer_event_url(payload)
        if not event_url:
            raise SystemExit(f"Could not infer FanDuel event URL from {html_path}; pass --event-url explicitly.")
        tab_url = with_tab(event_url, tab)
        cache_key = cache_key_for_event_tab(tab_url)
        written.append(write_cache(cache_key, payload, tab_url))

    print("Seeded FanDuel cache files:")
    for path in written:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
