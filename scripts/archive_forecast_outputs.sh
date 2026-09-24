#!/usr/bin/env bash
# Archive a forecast phase's runtime outputs for audit and comparison.
#
# Usage:
#   scripts/archive_forecast_outputs.sh [phase-label]
#
# Example:
#   scripts/archive_forecast_outputs.sh regular-season-2026
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LABEL=${1:-regular-season}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT_DIR="$ROOT/outputs/archive"
ARCHIVE="$OUT_DIR/forecast_${LABEL}_${STAMP}.tar.gz"

mkdir -p "$OUT_DIR"

INCLUDES=()
for sub in forecast_boards ledger grades source_snapshots; do
    if [[ -e "$ROOT/outputs/$sub" ]]; then
        INCLUDES+=("outputs/$sub")
    fi
done

if [[ ${#INCLUDES[@]} -eq 0 ]]; then
    printf 'No forecast outputs found to archive under %s/outputs\n' "$ROOT" >&2
    exit 1
fi

tar -czf "$ARCHIVE" -C "$ROOT" "${INCLUDES[@]}"
printf 'Wrote %s\n' "$ARCHIVE"
