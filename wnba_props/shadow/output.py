from __future__ import annotations

from .models import ShadowProjection


def render_shadow_board(projections: list[ShadowProjection]) -> str:
    if not projections:
        return "No PTS shadow projections were produced."

    headers = ["Player", "Game", "Line", "Price O/U", "Proj", "O%", "U%", "Side", "Status"]
    rows = []
    for item in sorted(projections, key=_sort_key):
        rows.append(
            [
                item.player_name,
                f"{item.team}-{item.opponent}",
                f"{item.line:.1f}",
                f"{_price(item.over_odds)}/{_price(item.under_odds)}",
                f"{item.projected_mean:.1f}",
                f"{item.over_probability * 100:.1f}",
                f"{item.under_probability * 100:.1f}",
                item.model_side,
                "RESEARCH",
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = min(28, max(widths[index], len(value)))

    def format_row(row: list[str]) -> str:
        clipped = [value if len(value) <= widths[index] else value[: widths[index] - 1] + "…" for index, value in enumerate(row)]
        return "  ".join(value.ljust(widths[index]) for index, value in enumerate(clipped))

    divider = "  ".join("-" * width for width in widths)
    return "\n".join(["WNBA PTS Shadow Projections (research only)", format_row(headers), divider, *[format_row(row) for row in rows]])


def _sort_key(item: ShadowProjection) -> tuple[float, str]:
    edge = max(
        item.over_expected_value if item.over_expected_value is not None else -1.0,
        item.under_expected_value if item.under_expected_value is not None else -1.0,
    )
    return (-edge, item.player_name)


def _price(value: int | None) -> str:
    if value is None:
        return "-"
    return f"+{value}" if value > 0 else str(value)
