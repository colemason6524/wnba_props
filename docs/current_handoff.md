# WNBA forecast project handoff

Last verified: 2026-09-23, America/Detroit

## Current topology (Linux only)

- **Mac** (`/Users/colemason/Documents/wnba_props`, `main`): primary working
  copy and bulk store. No scheduled jobs on the Mac.
- **Azure VM** (`azureuser@130.131.0.6`, Ubuntu): the only always-on runner.
  `~/wnba_props` on `main`. Scheduled through systemd **user timers**
  (`scripts/run_linux_task.sh` + `~/.config/systemd/user/sports-wnba-*.timer`).
- Secrets live in `~/.config/wnba_props/env` (mode 600), including
  `WNBA_PROPS_DISCORD_WEBHOOK_URL` for the team/operations channel and
  `WNBA_PROPS_PLAYER_DISCORD_WEBHOOK_URL` for player props.
- Pull VM outputs to the Mac with `scripts/sync_from_vm.sh` (history, health,
  logs, forecast boards, ledger, grades; pull-only, secrets never move).

There is **no Windows deployment and no macOS scheduler**. Historical wrappers
have been removed; do not recreate them.

## Production system

Prediction-first forecast board (`run_forecast_pipeline.py`). The model projects
every market before any price is attached; prices only classify value and grade
flat-unit ROI.

```text
ESPN slate + point-in-time logs/injuries
        +
Bovada game markets (preferred when reachable) / Polymarket (fallback reference)
        +
PlayerProps.ai player lines
        -> versioned artifacts (frozen)
        -> price-independent forecasts
        -> board + ROI ledger
        -> Discord (optional) + grading recap
```

- **Game model:** logistic winner + ridge margin/total on team form features,
  with optional no-vig market blending and EV-aware side selection.
- **Props:** minutes x rate simulation from joint residual artifacts per
  PTS/REB/AST/3PM, with league-baseline opponent adjustment, an opponent
  positional (G/F/C) allowance blend, a game-environment (expected total) rate
  factor capped at ±5%, and a role/status DNP probability surfaced as a risk
  flag.
- **Decision layer:** `MARKET_BLEND_WEIGHT` (default 0.0 = off) shrinks final
  probabilities toward the no-vig market; `EV_SIDE_SELECTION` (default false)
  chooses the better-EV side. These are versioned candidate settings: validate
  by replay before enabling, then record them with each comparison.
- **Playoff phase:** set `WNBA_SEASON_PHASE=playoff` to label grading artifacts,
  cumulative `outputs/grades/phase_summary.json`, and Discord recaps separately
  from the regular season. The pipeline continues as one system across phases.
- **Ledger integrity:** each capture carries a `snapshot_id` and `phase`.
  Earlier captures remain for audit; grading and ROI use the latest capture per
  `game_date/market/subject`. A later capture cannot inherit an earlier settled
  result.
- **Position map:** `config/player_positions.json` (regenerate with
  `scripts/fetch_player_positions.py`); unknown positions fall back to
  team-level allowance.
- **Discord:** board and daily recap can split across a team channel
  (`WNBA_PROPS_DISCORD_WEBHOOK_URL`, ML/spread/totals) and a player channel
  (`WNBA_PROPS_PLAYER_DISCORD_WEBHOOK_URL`, PTS/REB/AST/3PM). Row value labels
  are `playable` / `thin` / `no_value`.
- **Artifacts:** `wnba_props/artifacts/`; load failure stops the run
  (`wnba_props/modeling/registry.py`). Fit with `scripts/fit_game_engine.py`
  and `scripts/fit_props_engine.py`. Residual artifacts are fit on the base
  rate; environment/opponent/positional adjustments layer on at projection
  time, so those changes do not require a refit.
- **Publication is fail-closed:** coverage gates in `wnba_props/config.py`
  (`MIN_EVENT_MATCH_RATIO`, `MIN_PLAYER_LOAD_RATIO`, `MIN_EVALUATED_LINES`,
  `MAX_LINE_AGE_MINUTES`). A degraded board is withheld; Discord receives a
  DEGRADED alert instead.
- **Ledger:** every capture is retained with `snapshot_id` and `phase`. The
  latest capture per `game_date/market/subject` is the official evaluation row;
  pending rows can be replaced, and a genuinely later capture of a settled
  identity is appended with the older row marked `superseded_by`.
- **Grader:** `grade_forecast_board.py` defaults to **yesterday** (the 06:17
  timer runs the morning after), settles flat units, and posts a recap.

## Schedule (America/Detroit)

| Unit | When | Runs |
| --- | --- | --- |
| `sports-wnba-forecast@afternoon.timer` | Sat/Sun 12:36 | `run_forecast_pipeline.py --slot afternoon --send-discord` |
| `sports-wnba-forecast@pregame.timer` | daily 10:00 | `run_forecast_pipeline.py --slot pregame --send-discord` (one capture ~75 min before the earliest tip; retries an unhealthy board) |
| `sports-wnba-forecast@evening.timer` | daily 18:45 | `run_forecast_pipeline.py --slot evening --send-discord` |
| `sports-wnba-forecast-grade.timer` | daily 06:17 | `grade_forecast_board.py --send-discord` |

Legacy timers (`sports-wnba-daily`, `sports-wnba-shadow-capture`,
`sports-wnba-shadow-grade`) are **disabled**. The cutover helper is
`scripts/disable_legacy_wnba_timers.sh`.

## Source reality on the VM

Bovada access from the Azure VM has been intermittent: earlier runs hit a 302
redirect loop, while the latest smoke run reached Bovada successfully. The
pipeline falls back to **Polymarket** when Bovada fails and labels the board
`Game prices: Polymarket reference` in that case. Do not read VM ROI as
executable Bovada ROI when Polymarket is the source.

## Season operating plan

The system is one evolving pipeline, not separate regular-season and playoff
models. It continues through the postseason with versioned changes.

- Regular-season review found the ledger was not a faithful board record for
  Sep. 17: 50 preflight rows were captured Sep. 13, and 43 current board rows
  were shadowed by their proposition IDs. The official regular-season view must
  come from the latest board capture per identity, reconciled with
  `scripts/reconcile_forecast_ledger.py`.
- Use `scripts/evaluate_forecast_diagnostics.py` for latest-capture calibration
  and ROI by phase, market, side, and value label.
- Set `WNBA_SEASON_PHASE=playoff` for postseason runs. Grading writes
  `outputs/grades/phase_summary.json` and uses playoff recap titles.
- Iterate in versioned batches: one change, offline/walk-forward replay, deploy
  only after confirming no leakage or pipeline regression. Stamp each snapshot
  with the config changes that produced it.
- Prioritized improvements: minutes/role modeling (per-team and game-script
  context), prop-specific probability calibration (PTS and 3PM first),
  EV-aware side selection with no-vig market blend, 3PM shot-volume distribution,
  and team margin/total uncertainty with lineup/injury context.
- Capture both prices and raw PlayerProps payloads for every slate so future
  market comparisons are exact.

## Playoff operations

- The weekend afternoon timer covers early weekend tips; the daily pregame timer
  fires once at 10:00 ET and the pipeline waits until ~75 minutes before the
  earliest tip. Each capture writes its own `forecast_board_{date}_{slot}_{snapshot}.json`
  file, so retries never overwrite earlier captures. The evening timer remains
  the fallback.
- Set `WNBA_SEASON_PHASE=playoff` in `~/.config/wnba_props/env`.
- Before each round, confirm every scheduled game is captured before tip and
  that player logs include the newest playoff game (a nonempty regular-season
  log must not suppress the ESPN fallback in playoff phase).

## Historical context (legacy screener, retired)

The prior heuristic screener (`run_nightly.py`) is deprecated and unscheduled.
Its Aug 3-30 Discord-policy holdout graded **53-47-2, -10.94% ROI** (no edge).
The shadow projection challenger (v2) is research-only and not scheduled. Do not
re-enable either; the forecast board replaces them.

## Read next

- `README.md` for commands and current stack.
- `scripts/systemd/README.md` for unit install/update.
- `docs/research_model_roadmap.md` for projection theory and open questions.
