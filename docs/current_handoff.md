# WNBA forecast project handoff

Last verified: 2026-09-12, America/Detroit

## Current topology (Linux only)

- **Mac** (`/Users/colemason/Documents/wnba_props`, `main`): primary working
  copy and bulk store. No scheduled jobs on the Mac.
- **Azure VM** (`azureuser@130.131.0.6`, Ubuntu): the only always-on runner.
  `~/wnba_props` on `main`. Scheduled through systemd **user timers**
  (`scripts/run_linux_task.sh` + `~/.config/systemd/user/sports-wnba-*.timer`).
- Secrets live in `~/.config/wnba_props/env` (mode 600), including
  `WNBA_PROPS_DISCORD_WEBHOOK_URL` and optionally the team/player webhooks.
- Pull VM outputs to the Mac with `scripts/sync_from_vm.sh` (history, health,
  logs, forecast boards, ledger, grades; pull-only, secrets never move).

There is **no Windows or macOS scheduler**. Historical Windows/macOS task
wrappers have been removed; do not recreate them.

## Production system

Prediction-first forecast board (`run_forecast_pipeline.py`). The model projects
every market before any price is attached; prices only classify value and grade
flat-unit ROI.

```text
ESPN slate + point-in-time logs/injuries
        +
Bovada game markets (primary) / Polymarket (fallback reference)
        +
PlayerProps.ai player lines
        -> versioned artifacts (frozen)
        -> price-independent forecasts
        -> board + ROI ledger
        -> Discord (optional) + grading recap
```

- **Game model:** logistic winner + ridge margin/total on team form features.
- **Props:** minutes x rate simulation from joint residual artifacts per
  PTS/REB/AST/3PM, with league-baseline opponent adjustment.
- **Artifacts:** `wnba_props/artifacts/`; load failure stops the run
  (`wnba_props/modeling/registry.py`). Fit with `scripts/fit_game_engine.py`
  and `scripts/fit_props_engine.py`.
- **Publication is fail-closed:** coverage gates in `wnba_props/config.py`
  (`MIN_EVENT_MATCH_RATIO`, `MIN_PLAYER_LOAD_RATIO`, `MIN_EVALUATED_LINES`,
  `MAX_LINE_AGE_MINUTES`). A degraded board is withheld; Discord receives a
  DEGRADED alert instead.
- **Ledger:** one row per `game_date/market/subject`; the later slot supersedes
  the earlier pending snapshot and settled rows are frozen, so each play grades
  once.
- **Grader:** `grade_forecast_board.py` defaults to **yesterday** (the 06:17
  timer runs the morning after), settles flat units, and posts a recap.

## Schedule (America/Detroit)

| Unit | When | Runs |
| --- | --- | --- |
| `sports-wnba-forecast@afternoon.timer` | Sat/Sun 12:36 | `run_forecast_pipeline.py --slot afternoon --send-discord` |
| `sports-wnba-forecast@evening.timer` | daily 18:45 | `run_forecast_pipeline.py --slot evening --send-discord` |
| `sports-wnba-forecast-grade.timer` | daily 06:17 | `grade_forecast_board.py --send-discord` |

Legacy timers (`sports-wnba-daily`, `sports-wnba-shadow-capture`,
`sports-wnba-shadow-grade`) are **disabled**. The cutover helper is
`scripts/disable_legacy_wnba_timers.sh`.

## Source reality on the VM

Bovada blocks the Azure datacenter IP with a 302 redirect loop, so the VM
currently builds game markets from **Polymarket** while Bovada remains primary
where reachable. Discord labels the board `Game prices: Polymarket reference`
in that case. Do not read VM ROI as executable Bovada ROI.

## Season operating plan

Let the system run for the remainder of the regular season as a live
paper-betting experiment. Do not reject the model or add shadow mode.

- Keep the deployed model version **frozen** long enough to measure honestly;
  do not refit after every slate.
- Review weekly: calibration, units/ROI by market, PTS/REB/AST/3PM vs
  ML/spread/total, `health=ok` rate, missing/stale-source counts, and
  Polymarket-primary frequency.
- Iterate in versioned batches: one change, refit, offline compare, deploy only
  after confirming no leakage or pipeline regression.
- Prioritized model improvements: active/DNP probability, minutes/role
  modeling, team opponent adjustment, prop-specific uncertainty calibration,
  source-quality separation (sportsbook vs reference price).

## Historical context (legacy screener, retired)

The prior heuristic screener (`run_nightly.py`) is deprecated and unscheduled.
Its Aug 3-30 Discord-policy holdout graded **53-47-2, -10.94% ROI** (no edge).
The shadow projection challenger (v2) is research-only and not scheduled. Do not
re-enable either; the forecast board replaces them.

## Read next

- `README.md` for commands and current stack.
- `scripts/systemd/README.md` for unit install/update.
- `docs/research_model_roadmap.md` for projection theory and open questions.
