#!/bin/zsh
# WNBA prediction-first forecast board (macOS daily wrapper).
# Replaces the legacy heuristic run_nightly.py path.
set -u

PROJECT_DIR="/Users/colemason/Documents/wnba_props"
LOG_DIR="$PROJECT_DIR/outputs/logs"
FORECAST_LOG="$LOG_DIR/forecast_run.log"
GRADE_LOG="$LOG_DIR/forecast_grade.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

export PYTHONPYCACHEPREFIX=.pycache
export WNBA_FORECAST_SLOT="${WNBA_FORECAST_SLOT:-evening}"

discord_flag=()
if [[ "${SEND_DISCORD:-false}" == "true" ]]; then
  discord_flag=(--send-discord)
fi

{
  echo ""
  echo "===== WNBA forecast board (${WNBA_FORECAST_SLOT}): $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  /usr/bin/python3 run_forecast_pipeline.py --slot "$WNBA_FORECAST_SLOT" "${discord_flag[@]}"
  forecast_status=$?
  echo "===== Forecast finished with status $forecast_status: $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
} >> "$FORECAST_LOG" 2>&1

if [[ $forecast_status -ne 0 ]]; then
  echo "Forecast failed (status $forecast_status); skipping grade." >> "$FORECAST_LOG"
  exit $forecast_status
fi

{
  echo ""
  echo "===== WNBA forecast grade: $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  /usr/bin/python3 grade_forecast_board.py "${discord_flag[@]}"
  grade_status=$?
  echo "===== Grade finished with status $grade_status: $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
} >> "$GRADE_LOG" 2>&1

exit $grade_status
