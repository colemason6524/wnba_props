from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DISCORD_EMBED_LIMIT = 10
DISCORD_MESSAGE_LIMIT = 2000


@dataclass(frozen=True)
class DiscordResult:
    ok: bool
    status_code: int | None = None
    error: str | None = None


def send_discord_message(webhook_url: str, content: str, username: str = "WNBA Props") -> DiscordResult:
    if not webhook_url:
        return DiscordResult(ok=False, error="Missing Discord webhook URL")
    if not content.strip():
        return DiscordResult(ok=False, error="Discord message content is empty")

    payload = {
        "content": _trim_message(content),
        "username": username,
        "allowed_mentions": {"parse": []},
    }
    return _post_payload(webhook_url, payload)


def send_discord_embeds(
    webhook_url: str,
    embeds: list[dict],
    content: str = "",
    username: str = "WNBA Props",
) -> DiscordResult:
    if not webhook_url:
        return DiscordResult(ok=False, error="Missing Discord webhook URL")
    if not embeds and not content.strip():
        return DiscordResult(ok=False, error="Discord payload is empty")

    payload = {
        "content": _trim_message(content) if content else "",
        "username": username,
        "embeds": embeds[:DISCORD_EMBED_LIMIT],
        "allowed_mentions": {"parse": []},
    }
    return _post_payload(webhook_url, payload)


def _post_payload(webhook_url: str, payload: dict) -> DiscordResult:
    request = Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "wnba-props/discord-notifier"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            status_code = getattr(response, "status", None)
            return DiscordResult(ok=200 <= int(status_code or 0) < 300, status_code=status_code)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return DiscordResult(ok=False, status_code=exc.code, error=body[:500])
    except URLError as exc:
        return DiscordResult(ok=False, error=str(exc))


def _trim_message(content: str) -> str:
    if len(content) <= DISCORD_MESSAGE_LIMIT:
        return content
    suffix = "\n... trimmed for Discord"
    return content[: DISCORD_MESSAGE_LIMIT - len(suffix)].rstrip() + suffix
