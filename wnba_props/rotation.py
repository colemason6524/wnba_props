"""WNBA rotation redistribution: reassign vacated OUT minutes to teammates.

When a rotation player is ruled OUT (out / injured reserve / suspended),
a share of their projected minutes is redistributed to healthy teammates on
the same team. Day-to-day (and other uncertain statuses such as questionable
or probable) are deliberately untouched: those players may still suit up, so
their minutes are not treated as vacated.

Method
------
* Candidate pool: same team as the OUT player, excluding OUT players.
* Ranking (deterministic order only): same position class as the OUT player
  first, then bench-role players, then rotation depth.
* Weight: ``w_i = base_min_i / sum(base_min(pool))``.
* Bump: ``bump_i = REDIST_FACTOR (0.8) * vacated_min * w_i``.
* Clamps: no player may gain more than 50% of their own base minutes, and
  total bumps never exceed total vacated minutes.

Only statuses ``out``, ``injured reserve`` and ``suspended`` (plus explicit
out-variants such as "out for season") vacate minutes. Everything else --
including day-to-day -- is ignored.
"""

from __future__ import annotations

from typing import Iterable, Mapping

#: Share of each OUT player's minutes redistributed to teammates.
REDIST_FACTOR = 0.8

#: Maximum bump for one player, as a fraction of their own base minutes.
MAX_BUMP_RATIO = 0.5

_OUT_STATUSES = frozenset({"out", "out for season", "out indefinitely"})
_IR_STATUSES = frozenset({"injured reserve", "ir"})
_SUSPENDED_STATUSES = frozenset({"suspended"})

#: Statuses that vacate minutes. Day-to-day and all uncertain statuses are
#: intentionally absent.
QUALIFYING_OUT_STATUSES = _OUT_STATUSES | _IR_STATUSES | _SUSPENDED_STATUSES


def normalize_status(status: object) -> str:
    """Normalize an injury status string for comparison."""
    text = str(status or "").strip().lower().replace("-", " ")
    return " ".join(text.split())


def is_redistributable_out(status: object) -> bool:
    """True when ``status`` vacates minutes (out / IR / suspended)."""
    return normalize_status(status) in QUALIFYING_OUT_STATUSES


def redistribute_out_minutes(
    out_players: Iterable[Mapping[str, object]],
    roster_minutes: Mapping[str, float],
    meta: Mapping[str, Mapping[str, object]],
) -> dict:
    """Compute per-player minute bumps from OUT players' vacated minutes.

    Args:
        out_players: one mapping per unavailable player with keys
            ``player_name_norm``, ``team``, ``status``, ``base_minutes``
            (projected minutes now vacated) and optional ``position``.
            Entries whose status is not out / injured reserve / suspended
            (e.g. day-to-day) are ignored.
        roster_minutes: base projected minutes by player norm for the
            full roster (healthy teammates and OUT players alike).
        meta: per-player info by norm with keys ``team``, ``position``,
            ``role`` (``"starter"``/``"bench"``) and ``depth`` (lower
            number = higher in the rotation).

    Returns:
        Mapping of player norm to bump minutes (>= 0). Empty when there is
        nothing to redistribute or no eligible teammate exists.
    """
    excluded: set = set()
    vacated: list = []
    for entry in out_players or ():
        if not is_redistributable_out(entry.get("status", "")):
            continue
        norm = entry.get("player_name_norm", "")
        base = roster_minutes.get(norm, entry.get("base_minutes", 0.0)) or 0.0
        try:
            base = float(base)
        except (TypeError, ValueError):
            continue
        if not norm or base <= 0.0:
            continue
        excluded.add(norm)
        vacated.append(
            {
                "norm": norm,
                "team": (meta.get(norm, {}).get("team", "") if meta else "")
                or entry.get("team", ""),
                "position": (meta.get(norm, {}).get("position", "") if meta else "")
                or entry.get("position", ""),
                "minutes": base,
            }
        )

    if not vacated:
        return {}

    bumps: dict = {}
    total_vacated = 0.0
    for out in vacated:
        team = out["team"]
        if not team:
            continue
        pool = []
        for norm, base in roster_minutes.items():
            if norm in excluded:
                continue
            info = (meta or {}).get(norm, {})
            if info.get("team", "") != team:
                continue
            try:
                base = float(base)
            except (TypeError, ValueError):
                continue
            if base <= 0.0:
                continue
            pool.append(
                {
                    "norm": norm,
                    "base": base,
                    "position": info.get("position", ""),
                    "role": info.get("role", ""),
                    "depth": info.get("depth", 0),
                }
            )
        if not pool:
            continue
        pool_base = sum(item["base"] for item in pool)
        if pool_base <= 0.0:
            continue
        # Deterministic ranking: same position class as the OUT player,
        # then bench role (absorbs vacated run), then rotation depth.
        pool.sort(
            key=lambda item: (
                0 if item["position"] == out["position"] else 1,
                0 if str(item["role"]).lower() == "bench" else 1,
                item["depth"],
                item["norm"],
            )
        )
        total_vacated += out["minutes"]
        for item in pool:
            weight = item["base"] / pool_base
            bumps[item["norm"]] = bumps.get(item["norm"], 0.0) + (
                REDIST_FACTOR * out["minutes"] * weight
            )

    if not bumps:
        return {}

    # Clamp: no player gains more than 50% of their own base minutes.
    for norm in list(bumps):
        cap = MAX_BUMP_RATIO * float(roster_minutes.get(norm, 0.0))
        bumps[norm] = max(0.0, min(bumps[norm], cap))

    # Clamp: total bumps never exceed total vacated minutes.
    total_bump = sum(bumps.values())
    if total_bump > total_vacated > 0.0:
        scale = total_vacated / total_bump
        for norm in bumps:
            bumps[norm] *= scale

    return {norm: bump for norm, bump in bumps.items() if bump > 0.0}
