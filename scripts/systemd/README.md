# systemd units (Azure VM) — reference copies

These are copies of the live user units on the Azure VM
(`~/.config/systemd/user/sports-wnba-*`). The VM files are authoritative;
if you change a unit on the VM, copy it back here, and vice versa.

## Active: prediction-first forecast board

| Unit | Schedule (America/Detroit) | Command |
| --- | --- | --- |
| `sports-wnba-forecast@afternoon.timer` | weekends, random 10:00–13:00 | `run_forecast_pipeline.py --slot afternoon --send-discord` |
| `sports-wnba-forecast@evening.timer` | daily 18:45 | `run_forecast_pipeline.py --slot evening --send-discord` |
| `sports-wnba-forecast-grade.timer` | daily 06:17 | `grade_forecast_board.py --send-discord` |

The afternoon run covers weekend early tip-offs and fires once at a random
time between 10:00 and 13:00 ET (weekends only). The evening run is daily at
18:45 ET. The ledger supersedes the earlier slot's pending line with the later
snapshot, so the same play is graded once.

The forecast pipeline collects Bovada game markets (primary), Polymarket
(fallback reference), PlayerProps player lines, point-in-time player logs and
injuries, then loads versioned artifacts and publishes the board + ledgers.

## Legacy (disabled after cutover)

| Unit | Was | Status |
| --- | --- | --- |
| `sports-wnba-daily.timer` | `run_nightly.py` heuristic screener | disabled |
| `sports-wnba-shadow-capture.timer` | `run_projection_shadow.py` | disabled |
| `sports-wnba-shadow-grade.timer` | `grade_projection_shadow.py` + rollup | disabled |

Legacy code remains in the repo for rollback and historical grading, but the
legacy timers must stay disabled so the old board never competes with the
forecast ledger or posts to the active Discord webhook.

## Prerequisites on the VM

- checkout at `~/wnba_props` on clean `main` (+ stdlib venv at `~/wnba_props/.venv`)
- production artifacts present under `~/wnba_props/wnba_props/artifacts/`
  (`game_engine_artifact.json`, `pts_engine_artifact.json`,
  `reb_engine_artifact.json`, `ast_engine_artifact.json`,
  `3pm_engine_artifact.json`) — fit offline with
  `scripts/fit_game_engine.py` and `scripts/fit_props_engine.py`
- secrets in `~/.config/wnba_props/env` (mode 600), including
  `WNBA_PROPS_DISCORD_WEBHOOK_URL`
- user lingering enabled (`loginctl enable-linger azureuser`)

## Install / update

```bash
cp scripts/systemd/sports-wnba-* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now "sports-wnba-forecast@afternoon.timer" "sports-wnba-forecast@evening.timer" sports-wnba-forecast-grade.timer
systemctl --user disable --now sports-wnba-daily.timer sports-wnba-shadow-capture.timer sports-wnba-shadow-grade.timer
systemctl --user list-timers | grep wnba
```

Or run the cutover helper (Linux + macOS):

```bash
scripts/disable_legacy_wnba_timers.sh
```

## Season end

```bash
systemctl --user disable --now "sports-wnba-forecast@afternoon.timer" "sports-wnba-forecast@evening.timer" sports-wnba-forecast-grade.timer
```

Re-enable next season. Do not re-enable the legacy timers.
