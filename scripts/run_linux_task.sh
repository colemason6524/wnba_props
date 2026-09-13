#!/usr/bin/env bash
set -u

TASK=${1:-}
PROJECT_DIR=${PROJECT_DIR:-"$HOME/wnba_props"}
ENV_FILE=${WNBA_PROPS_ENV_FILE:-"$HOME/.config/wnba_props/env"}
PYTHON_EXE=${WNBA_PROPS_PYTHON_EXE:-"$PROJECT_DIR/.venv/bin/python"}
LOG_DIR="$PROJECT_DIR/outputs/logs"
LOCK_FILE=${WNBA_PROPS_LOCK_FILE:-"$HOME/.local/state/wnba_props/run.lock"}

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_FILE")"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

export TZ=${TZ:-America/Detroit}
export PYTHONUNBUFFERED=1
export PYTHONPYCACHEPREFIX=${PYTHONPYCACHEPREFIX:-.pycache}
ROLLUP_COMMAND=()

if [[ ! -x "$PYTHON_EXE" ]]; then
    printf 'Project Python interpreter is unavailable: %s\n' "$PYTHON_EXE" >&2
    exit 1
fi

case "$TASK" in
    forecast)
        # Prediction-first forecast board. Bovada primary game markets,
        # Polymarket fallback, PlayerProps player lines, versioned artifacts.
        LOG_FILE="$LOG_DIR/wnba_forecast.log"
        TIMEOUT=90m
        FORECAST_SLOT=${WNBA_FORECAST_SLOT:-evening}
        COMMAND=("$PYTHON_EXE" run_forecast_pipeline.py --slot "$FORECAST_SLOT")
        if [[ "${WNBA_SEND_DISCORD:-true}" != "false" ]]; then
            COMMAND+=(--send-discord)
            REQUIRED_SECRET=WNBA_PROPS_DISCORD_WEBHOOK_URL
        else
            REQUIRED_SECRET=
        fi
        ;;
    forecast-grade)
        LOG_FILE="$LOG_DIR/wnba_forecast_grade.log"
        TIMEOUT=30m
        COMMAND=("$PYTHON_EXE" grade_forecast_board.py)
        if [[ "${WNBA_SEND_DISCORD:-true}" != "false" ]]; then
            COMMAND+=(--send-discord)
            REQUIRED_SECRET=WNBA_PROPS_DISCORD_WEBHOOK_URL
        else
            REQUIRED_SECRET=
        fi
        ;;
    daily)
        # DEPRECATED legacy heuristic screener. Kept for rollback only; not
        # scheduled once the forecast timers are enabled.
        LOG_FILE="$LOG_DIR/wnba_props_task.log"
        TIMEOUT=90m
        export LINE_SOURCE=${LINE_SOURCE:-playerprops}
        export PLAYERPROPS_BOOK=${PLAYERPROPS_BOOK:-FANDUEL}
        export SEND_DISCORD=true
        export DISCORD_MIN_SCORE=${DISCORD_MIN_SCORE:-8}
        export DISCORD_LIMIT=${DISCORD_LIMIT:-5}
        COMMAND=("$PYTHON_EXE" run_nightly.py)
        REQUIRED_SECRET=WNBA_PROPS_DISCORD_WEBHOOK_URL
        ;;
    shadow-capture)
        # DEPRECATED legacy shadow capture. Rollback only.
        LOG_FILE="$LOG_DIR/wnba_shadow_capture.log"
        TIMEOUT=90m
        COMMAND=("$PYTHON_EXE" run_projection_shadow.py)
        REQUIRED_SECRET=
        ;;
    shadow-grade)
        # DEPRECATED legacy shadow grading. Rollback only.
        LOG_FILE="$LOG_DIR/wnba_shadow_grade.log"
        TIMEOUT=30m
        COMMAND=("$PYTHON_EXE" grade_projection_shadow.py --all-pending)
        ROLLUP_COMMAND=("$PYTHON_EXE" shadow_rollup.py)
        REQUIRED_SECRET=
        ;;
    *)
        printf 'Unknown task: %s\n' "$TASK" >&2
        exit 2
        ;;
esac

exec >>"$LOG_FILE" 2>&1
printf '%s  Starting %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$TASK"

if [[ -n "$REQUIRED_SECRET" && -z "${!REQUIRED_SECRET:-}" ]]; then
    printf '%s  FAILED: %s is not configured in %s\n' \
        "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$REQUIRED_SECRET" "$ENV_FILE"
    exit 1
fi

cd "$PROJECT_DIR" || exit 1
exec 9>"$LOCK_FILE"
printf '%s  Waiting for the shared task lock\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')"
flock 9
printf '%s  Running: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "${COMMAND[*]}"

timeout --signal=TERM --kill-after=2m "$TIMEOUT" "${COMMAND[@]}"
EXIT_CODE=$?
if [[ $EXIT_CODE -eq 0 && ${#ROLLUP_COMMAND[@]} -gt 0 ]]; then
    printf '%s  Running: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "${ROLLUP_COMMAND[*]}"
    timeout --signal=TERM --kill-after=2m "$TIMEOUT" "${ROLLUP_COMMAND[@]}"
    EXIT_CODE=$?
fi
printf '%s  Finished %s with exit code %s\n' \
    "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$TASK" "$EXIT_CODE"
exit "$EXIT_CODE"
