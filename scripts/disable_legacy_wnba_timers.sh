#!/usr/bin/env bash
# Disable the legacy WNBA schedulers after the forecast pipeline is validated.
#
# Legacy units (systemd user units on the VM):
#   sports-wnba-daily.timer          -> run_nightly.py (heuristic screener)
# (The shadow capture/grade units and their rollup were deleted on
# 2026-10-22; they never produced a report. See docs/INTEGRITY_AUDIT_2026-10-22.md.)
#
# macOS launchd (local): com.colemason.wnba-props.daily
#
# The new units are the sports-wnba-forecast@*.timer pair and
# sports-wnba-forecast-grade.timer.
set -u

LEGACY_TIMERS=(
    sports-wnba-daily.timer
)
NEW_TIMERS=(
    "sports-wnba-forecast@afternoon.timer"
    "sports-wnba-forecast@evening.timer"
    sports-wnba-forecast-grade.timer
)

if command -v systemctl >/dev/null 2>&1; then
    echo "Disabling legacy WNBA systemd timers..."
    systemctl --user disable --now "${LEGACY_TIMERS[@]}" 2>/dev/null || true
    echo "Enabling forecast WNBA systemd timers..."
    systemctl --user enable --now "${NEW_TIMERS[@]}" 2>/dev/null || true
    echo ""
    echo "Active WNBA timers:"
    systemctl --user list-timers --all 2>/dev/null | grep -i wnba || true
else
    echo "systemctl not available; skipping systemd timer changes."
fi

PLIST="$HOME/Library/LaunchAgents/com.colemason.wnba-props.daily.plist"
if [[ -f "$PLIST" ]]; then
    echo ""
    echo "Unloading legacy macOS launchd job: $PLIST"
    launchctl unload "$PLIST" 2>/dev/null || launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
fi

echo ""
echo "Done. Confirm no legacy timer/service is listed above."
