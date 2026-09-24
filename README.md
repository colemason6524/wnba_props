# WNBA Forecast Board

Prediction-first WNBA paper-betting forecast system for team markets and player props.

## Current Production State

- **Only runner:** Linux Azure VM at `azureuser@130.131.0.6`, checkout `~/wnba_props`.
- **Working copy:** Mac at `/Users/colemason/Documents/wnba_props`; no scheduled jobs run there.
- **Schedule:** systemd user timers on the VM only. See `scripts/systemd/README.md`.
- **Production commit:** verify the VM directly before operating; do not assume the local checkout is deployed.
- **Markets:** moneyline, spread, total, PTS, REB, AST, and 3PM.
- **Operating mode:** versioned live paper-betting experiment. One pipeline continues through the playoffs; each model/config change is stamped with a snapshot ID and phase. Results are not betting advice.

There is no Windows deployment and no macOS scheduler. The old heuristic screener and shadow collector are retired and unscheduled.

## Production Flow

```text
ESPN slate
  + Bovada game markets when reachable / Polymarket fallback reference
  + PlayerProps.ai player lines
  + Basketball-Reference logs / ESPN fallback
  + ESPN injuries and roster positions
        -> point-in-time features
        -> model forecasts (price-independent)
        -> optional no-vig market blend + EV-aware side selection
        -> board snapshot + versioned ledger
        -> split Discord boards and next-morning grading
```

The model predicts outcomes before prices are used. A configurable market blend
(`MARKET_BLEND_WEIGHT`, default 0.0 = off) can shrink the final decision
probability toward the no-vig market, and `EV_SIDE_SELECTION` (default off)
chooses the side with the better expected value. Both are decision-layer
settings; the underlying feature forecast remains price-independent. They are
available for a validated candidate run but do not change production until
enabled. Ledger rows carry a `paper_play` flag (`value == "playable"`) so the
full forecast record can be graded alongside the positive-value subset. Prices
classify value and determine flat-unit paper ROI.

## Commands

Run a board locally without Discord:

```bash
python3 run_forecast_pipeline.py --date 2026-09-17 --slot evening
```

Run a board and send Discord notifications:

```bash
python3 run_forecast_pipeline.py --slot evening --send-discord
```

Grade the prior day, which is also the default for the morning timer:

```bash
python3 grade_forecast_board.py --date 2026-09-17 --send-discord
```

Reconcile board snapshots against the ledger and produce the latest-capture
regular-season view:

```bash
python3 scripts/reconcile_forecast_ledger.py
python3 scripts/evaluate_forecast_diagnostics.py
```

Replay published board snapshots into the ledger with distinct snapshot IDs
(dry-run first; use `--apply --backup` to write):

```bash
python3 scripts/backfill_board_snapshots.py
```

Archive a phase's boards, ledger, grades, and raw source snapshots:

```bash
scripts/archive_forecast_outputs.sh regular-season-2026
```

Pull VM outputs to the Mac:

```bash
scripts/sync_from_vm.sh
```

## Active VM Schedule

All times are America/Detroit:

| Unit | Schedule | Command |
| --- | --- | --- |
| `sports-wnba-forecast@afternoon.timer` | Sat/Sun 12:36 | `run_forecast_pipeline.py --slot afternoon --send-discord` |
| `sports-wnba-forecast@pregame.timer` | Daily 11,13,15,17 | `run_forecast_pipeline.py --slot pregame --send-discord` |
| `sports-wnba-forecast@evening.timer` | Daily 18:45 | `run_forecast_pipeline.py --slot evening --send-discord` |
| `sports-wnba-forecast-grade.timer` | Daily 06:17 | `grade_forecast_board.py --send-discord` |

Every capture is immutable in the board snapshot and ledger. The official
performance view uses the latest capture per `game_date/market/subject`; earlier
captures remain for audit. A later capture cannot silently inherit an earlier
settled result.

## Discord Output

The existing `WNBA_PROPS_DISCORD_WEBHOOK_URL` is the team/operations channel unless an explicit team webhook is configured.

- `WNBA_PROPS_TEAM_DISCORD_WEBHOOK_URL`: moneylines, spreads, totals, and team recap.
- `WNBA_PROPS_PLAYER_DISCORD_WEBHOOK_URL`: PTS/REB/AST/3PM and player-props recap.
- `WNBA_PROPS_DISCORD_WEBHOOK_URL`: single-channel fallback and operational health alerts.

Board rows use the compact format:

```text
WNBA Player Props - 2026-09-17 (Evening)

== Points (22) ==
- Player Name | Over 18.5 @-110 | p=58% | EV +0.08 [playable]
```

Value labels are `playable`, `thin`, `no_value`, and `unpriced`. A degraded board is withheld and produces a `DEGRADED` alert instead of a picks message. Game-price provenance is labeled when Polymarket is used.

## Model

### Team Markets

- Logistic regression for winner probability.
- Ridge projections for margin and total.
- Team form, opponent allowance, margin, rest, home court, and pace proxies.

### Player Props

- Minutes x per-minute rate simulation for PTS/REB/AST/3PM.
- Frozen joint empirical residual artifacts and calibration shrinkage.
- Opponent team-level allowance adjustment.
- Opponent positional allowance blend for G/F/C classes using `config/player_positions.json`.
- Expected-game-total environment adjustment capped at plus or minus 5%.
- Role/status DNP probability exposed as `DNP_RISK` or `HIGH_DNP_RISK`; DNPs are voided by the grader rather than treated as losses.

- Optional no-vig market blend and EV-aware side selection
  (`MARKET_BLEND_WEIGHT`, `EV_SIDE_SELECTION`).
- Ledger rows persist both prices, over/under/push probabilities, projected
  minutes and rate, percentiles, flags, and DNP/void risk so future losses can
  be attributed without reconstructing mutable caches.

The environment, opponent, and positional adjustments layer on top of residual artifacts fitted on the base rate. Do not refit after every slate. Any future model change requires a versioned offline comparison and prospective evaluation. Playoff runs set `WNBA_SEASON_PHASE=playoff` so grading and recaps separate playoff results.

Regenerate the committed roster position map when needed:

```bash
python3 scripts/fetch_player_positions.py
```

Fit artifacts offline only:

```bash
python3 scripts/fit_game_engine.py --end 2026-09-23 --fetch
python3 scripts/fit_props_engine.py --log-dir .cache/shared --min-latest-date 2026-09-23
```

`fit_game_engine.py` refuses to reuse a cached results file that does not reach
the requested end date unless `--fetch` is supplied. `fit_props_engine.py`
reports the log directory and newest log date and can enforce a minimum with
`--min-latest-date`. Missing or invalid artifacts fail the production run
(`wnba_props/modeling/registry.py`).

## Sources and Fallbacks

- ESPN supplies the slate, injuries, scoreboards, and grading box scores.
- PlayerProps.ai supplies player lines and available prices.
- Basketball-Reference supplies historical player logs, with ESPN gamelog fallback.
- Bovada is the preferred game-market source when reachable.
- Polymarket is a fallback reference source when Bovada fails or is blocked. VM ROI must not be interpreted as executable Bovada ROI when Polymarket is the source.

## Outputs

- `outputs/forecast_boards/`: immutable JSON board snapshots by date and slot.
- `outputs/ledger/forecast_ledger.jsonl`: canonical paper-betting ledger; one row
  per capture identity, with the latest capture used for grading.
- `outputs/grades/`: daily grading artifacts, plus `phase_summary.json` with
  regular-season and playoff totals kept separate.
- `outputs/source_snapshots/`: timestamped raw PlayerProps payloads for audit.
- `outputs/history/`: game-market snapshots and source diagnostics.
- `outputs/logs/`: VM task logs; runtime artifacts are not committed.

## Health Gates

Publication fails closed when configured coverage requirements are not met:

- minimum priced rows;
- minimum evaluated player lines;
- complete game-market coverage;
- minimum player-log coverage;
- stale-line rejection.

Inspect board `summary.health`, `summary.health_reasons`, and `provenance` before interpreting any result.

## Verification

Run the standard tests locally:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

The optional `tests/test_grade_holdout_voids.py` requires `pytest`. The current standard suite is otherwise unittest-based.

Before season operation, verify the VM directly:

```bash
ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6
cd ~/wnba_props
git rev-parse --short HEAD
git status --short
systemctl --user list-timers --all | grep wnba
```

Historical screener, shadow-model, holdout, and research notes remain under `docs/` and `outputs/hunt/` for provenance only. They are not production instructions.
