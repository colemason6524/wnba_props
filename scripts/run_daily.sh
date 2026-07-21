#!/bin/zsh
set -u

PROJECT_DIR="/Users/colemason/Documents/wnba_props"
LOG_DIR="$PROJECT_DIR/outputs/logs"
LOG_FILE="$LOG_DIR/daily_run.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR" || exit 1

{
  echo ""
  echo "===== WNBA props daily run: $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  PYTHONPYCACHEPREFIX=.pycache \
  LINE_SOURCE="${LINE_SOURCE:-playerprops}" \
  PLAYERPROPS_BOOK="${PLAYERPROPS_BOOK:-FANDUEL}" \
  SEND_DISCORD="${SEND_DISCORD:-false}" \
  DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}" \
  DISCORD_MIN_SCORE="${DISCORD_MIN_SCORE:-8}" \
  DISCORD_LIMIT="${DISCORD_LIMIT:-8}" \
  /usr/bin/python3 run_nightly.py
  status=$?
  echo "===== Finished with status $status: $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  exit "$status"
} >> "$LOG_FILE" 2>&1
