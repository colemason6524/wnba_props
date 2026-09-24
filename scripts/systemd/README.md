# systemd units (Azure VM) — reference copies

These are copies of the live user units on the Azure VM
(`~/.config/systemd/user/sports-wnba-*`). The VM files are authoritative;
if you change a unit on the VM, copy it back here, and vice versa.

## Active: prediction-first forecast board

| Unit | Schedule (America/Detroit) | Command |
| --- | --- | --- |
| `sports-wnba-forecast@afternoon.timer` | weekends 12:36 | `run_forecast_pipeline.py --slot afternoon --send-discord` |
| `sports-wnba-forecast@pregame.timer` | daily 10:00 | `run_forecast_pipeline.py --slot pregame --send-discord` (one schedule-aware capture ~75 min before the earliest tip) |
| `sports-wnba-forecast@evening.timer` | daily 18:45 | `run_forecast_pipeline.py --slot evening --send-discord` |
| `sports-wnba-forecast-grade.timer` | daily 06:17 | `grade_forecast_board.py --send-discord` |

The afternoon run covers weekend early tip-offs and fires once at 12:36 ET on
Saturdays and Sundays (off-peak, to avoid load spikes). The evening run is daily
at 18:45 ET. The ledger supersedes the earlier slot's pending line with the later
snapshot, so the same play is graded once. The grader defaults to the prior
calendar day because it runs the next morning.

The forecast pipeline prefers Bovada game markets when reachable, falls back to
Polymarket as a reference source when needed, collects PlayerProps player
lines, point-in-time player logs, injuries, and roster positions, then loads
versioned artifacts and publishes the board + ledgers. Bovada access from the
VM has been intermittent, so always inspect board provenance before interpreting
game-market ROI.

Set `WNBA_SEASON_PHASE=playoff` in `~/.config/wnba_props/env` for the postseason
so grading artifacts, cumulative `outputs/grades/phase_summary.json`, and Discord
recaps are labeled separately. The pregame timer fires once daily at 10:00 ET;
the pipeline then waits until ~75 minutes before the earliest untipped game,
captures once, and retries an unhealthy board (same `pregame` slot, so Discord
delivery stays idempotent). The evening timer remains the daily fallback.

Snapshot-versioned model settings live in the environment file as well:
`MARKET_BLEND_WEIGHT` (default 0.0 = off) and `EV_SIDE_SELECTION` (default
false). Validate them by replay before enabling for a live candidate run.


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
  `WNBA_PROPS_DISCORD_WEBHOOK_URL` and
  `WNBA_PROPS_PLAYER_DISCORD_WEBHOOK_URL`
- user lingering enabled (`loginctl enable-linger azureuser`)

## Install / update

```bash
cp scripts/systemd/sports-wnba-* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now "sports-wnba-forecast@afternoon.timer" "sports-wnba-forecast@pregame.timer" "sports-wnba-forecast@evening.timer" sports-wnba-forecast-grade.timer
systemctl --user disable --now sports-wnba-daily.timer sports-wnba-shadow-capture.timer sports-wnba-shadow-grade.timer
systemctl --user list-timers | grep wnba
```

Or run the cutover helper (Linux + macOS):

```bash
scripts/disable_legacy_wnba_timers.sh
```

## Season end

```bash
systemctl --user disable --now "sports-wnba-forecast@afternoon.timer" "sports-wnba-forecast@pregame.timer" "sports-wnba-forecast@evening.timer" sports-wnba-forecast-grade.timer
```

Re-enable next season. Do not re-enable the legacy timers.
