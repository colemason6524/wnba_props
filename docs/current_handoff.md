# WNBA forecast project handoff

Last verified: 2026-09-30, America/Detroit

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

- The regular-season headline is published-board-only; see the dated results
  section below for its record and the five preflight-only settled rows excluded
  from that headline. Reconcile boards against latest ledger identities with
  `scripts/reconcile_forecast_ledger.py` before changing these totals.
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

## Season results and phase baseline (reviewed 2026-09-28)

Keep player props and team markets as separate views as well as a combined
record: they share a forecast pipeline, but can show different trends.

Regular-season published-board population (Sep. 17–24), latest ledger row per
`game_date/market/subject`: 676 identities, 609 settled, 3 pending, 64 unpriced.
ROI is settled flat-stake units divided by settled selections; pending/unpriced
rows are not losses.

- **Combined:** 330–278–1, −30.687u, −5.04% ROI (609 settled).
- **Player props (PTS/REB/AST/3PM):** 282–237, −19.573u, −3.77% (519).
  PTS −20.411u/−11.94% (171); 3PM −11.368u/−10.24% (111);
  AST +8.862u/+8.44% (105); REB +3.344u/+2.53% (132).
- **Team markets (ML/SPREAD/TOTAL):** 48–41–1, −11.114u, −12.35% (90).
  ML −5.949u/−19.83% (30); SPREAD −4.091u/−13.64% (30; one push);
  TOTAL −1.073u/−3.58% (30).
- **Playable subset:** 11–9, +1.304u/+6.52% overall (20): player props 6–5,
  +0.687u/+6.25% (11); team markets 5–4, +0.617u/+6.85% (9). All 20 came
  from Sep. 24 only, so this is not prospective evidence of an edge.

The Sep. 24 grade's cumulative ledger summary was 334–279–1, −28.525u,
−4.65%. It includes five settled preflight selections absent from published
boards (+2.161u). Use the published-board result above as the regular-season
headline and keep the cumulative-grade figure as a reconciliation caveat. The
676 published identities match tagged regular-season ledger identities. Some
older board captures show value drift against a later capture; drift is not by
itself evidence of missing identities.

Regular-season findings: AST and REB were positive; PTS and 3PM were the largest
player-prop losses; all team-market groups were negative, especially ML/spread.
Selected-side average probability versus win rate was 57.5% vs 48.0% for PTS and
61.3% vs 52.3% for 3PM. Treat these as calibration warnings, not sufficient
evidence for a same-sample fix. Offline minutes × rate / recency work improved
predictive diagnostics but did not prove a betting edge. Market blend and
EV-side selection were off.

First playoff slate (Sep. 27), split by game date because its original phase
label was incorrect: **combined 85–79, −10.441u, −6.37%** (164 settled; 25
unpriced); **player props 77–75, −14.142u, −9.30%** (152); **team markets 8–4,
+3.700u, +30.84%** (12, across four games). Player-market units: PTS −4.870u,
3PM −3.262u, AST −12.131u, REB +6.121u. Playable subset: player 39–45,
−9.274u/−11.04% (84); team 6–3, +3.795u/+42.17% (9). This is one slate with
correlated picks, not a new established trend. First-slate game prices were
Bovada; market blend (0.0) and EV-side selection (false) were off. Eleven of 30
regular-season TOTAL rows used Polymarket reference prices, not executable
Bovada prices.

Track phase, player/team group, market, side, source and calibration/minutes
signals over several playoff slates before model changes. Preserve original
snapshots and the original Sep. 27 grade for audit; use corrected date/phase
reporting rather than overwriting those artifacts.

## Playoff split ledger (reviewed 2026-09-30)

Sep. 29 was the first fully verified playoff capture: pregame (21:15Z, 87 rows)
+ evening (22:45Z), all rows phase=playoff, and the Sep. 30 morning timer graded
83 settled rows cleanly (39–44, −11.601u; 4 unpriced remain pending). After two
slates (Sep. 27 + Sep. 29), cumulative playoff: **124–123, −22.042u, −8.92%**
(247 settled). Splits from `phase_summary.json`:

- Player props: 113–116, −25.91u, −11.31% (229). AST −16.344u/−32.05% (51);
  PTS −7.857u/−11.6% (68); 3PM −5.72u/−11.4% (50); REB +4.01u/+6.7% (60).
- Team markets: 9–7, +3.867u (ML 4–2 +2.505u; SPREAD 3–3 −0.273u;
  TOTAL 4–2 +1.636u).
- Playable subset (paper_play): 65–73, −11.137u, −8.07%. Across the settled
  playoff book only `thin` rows were profitable (17–12, +0.26u); `playable`
  and `no_value` labels each lost ≈ −11.1u.

Research observations (no change made — policy keeps the model frozen):
- AST bleeds on BOTH sides (OVER 6–12 at 0.568 avg prob; UNDER 14–19 at 0.605)
  and concentrates in the 28–34 projected-minutes bucket (10–22, 0.31 win rate
  vs 0.568 avg prob), while the REB 28–34 bucket holds (18–14, 0.56). Price
  level and per-row |ev| do not separate wins from losses in any market.
- A bootstrap over the 51 settled AST plays: observed ROI −32.0%, 95% CI
  [−54.9%, −8.1%]; 96.6% of resamples fall below −10% ROI, so the AST bleed is
  a consistent two-slate effect, not one blowout slate. Statistical power to
  detect a true −10% ROI at n=51 is only ~20%, so treat the PTS/3PM −11%
  readings as calibration warnings, not proof.
- Candidate explanations for the follow-up review (after 5–7 slates):
  playoff rotation compression (minutes distribution narrower than the
  artifacts trained in the regular season), five-out/small-ball lineups
  lifting assist variance, and minutes-telemetry quality (see below).

Player-log freshness, board-verified: every capture now carries
`player_logs_freshness` telemetry with a 2-day playoff staleness guard
(`PLAYOFF_LOG_STALENESS_DAYS=2`); it separates "loaded," "stale players," and
the bounded ESPN boxscore-fallback top-up in the board provenance. On the
Sep. 30 captures, 27/27 subjects loaded with latest logs from Sep. 27 and 27
top-up rows added — correct, because the Sep. 30 subjects (ATL/WSH/DAL/GS)
had no Sep. 29 games (IND/LV and NY/MIN played that night). The fallback is
bounded per-day (per-date scoreboard cache, 6h TTL, one boxscore cache per
event id shared between teams), point-in-time, and DNP-safe; no fix pending.
Keep watching `latest_log_date` vs the guard on every capture: a capture whose
subjects' most recent game is older than the 2-day guard but whose health=ok
matches a real gap (e.g. an off-day round break), not missing data.

Sep. 30 slate: 99 identities in both captures (45/47 paper plays pregame vs
evening — evening adds two rows at evening lines). Latest-capture grading
takes the evening rows for shared identities and grades a pregame-only row
from pregame prices; no double-count. Bovada reachable on both captures
(only one TOTAL cross-check fell back to Polymarket reference).


## Playoff operations

- The weekend afternoon timer covers early weekend tips; the daily pregame timer
  fires once at 10:00 ET and the pipeline waits until ~75 minutes before the
  earliest tip. Each capture writes its own `forecast_board_{date}_{slot}_{snapshot}.json`
  file, so retries never overwrite earlier captures. The evening timer remains
  the fallback.
- On 2026-09-28, `WNBA_SEASON_PHASE=playoff` was set in
  `~/.config/wnba_props/env` (mode 600); the 189 Sep. 27 ledger rows were
  reclassified as playoff and 113 previously untagged Sep. 17 preflight rows
  were explicitly classified as preflight. `phase_summary.json` now reports
  regular 330–278–1/−30.687u, playoff 85–79/−10.441u, and preflight
  4–1/+2.161u. Timestamped backups of the ledger and phase summary were saved
  on the VM before each edit. The original Sep. 27 board snapshots and grade
  remain unchanged and retain their `regular` label. **Board verification of
  the phase transition is complete (Sep. 29 capture + Sep. 30 grade; all rows
  phase=playoff).** Sep. 28 had no games.
- Before each round, confirm every scheduled game is captured before tip and
  that player logs include the newest playoff game (a nonempty regular-season
  log must not suppress the ESPN fallback in playoff phase).

## Pre-capture readiness checklist

Run this before the first capture of a round, on the VM, with the production
env file sourced so `season_phase` matches the timers:

```bash
cd ~/wnba_props && set -a && source ~/.config/wnba_props/env && set +a
export SCREEN_DATE=YYYY-MM-DD WNBA_SEND_DISCORD=false
.venv/bin/python run_forecast_pipeline.py --slot pregame --date YYYY-MM-DD \
    --no-pregame-wait --no-refresh --coverage-report
```

`--coverage-report` builds the board, prints the publish health gate, and exits
without writing a board, ledger row, or snapshot. It exists so the fail-closed
coverage check can be exercised before a timer fires instead of discovering a
degraded board after publication. A healthy result looks like
`health=ok reasons=none` plus `player_logs: loaded=23/25 latest=<newest game>
boxscore_fallback=<n> stale=<n>`. Always source the env file: running without it
silently falls back to `regular` phase, which disables the playoff log fallback
and makes the check meaningless.

Known-benign residuals, verified 2026-09-28: `outputs/health/run_status.jsonl`
is written only by the retired `run_nightly.py` and read by nothing; it is inert.
FanDuel sometimes publishes only the over side. Because side selection is
forecast-probability-first when `EV_SIDE_SELECTION=false`, a row can select
UNDER even when only `over_odds` is present. Such a row is labeled `UNPRICED`
(`price is None`): it must not enter monetary ROI, but after the game the grader
records its WIN/LOSS/PUSH/VOID outcome with `units=null`. Grade summaries report
these separately under `unpriced` (evaluated/wins/losses/pushes/voids), distinct
from still-pending rows; this preserves forecast-accuracy evidence without
inventing a payout. All `paper_play` rows are priced, so the paper ROI record is
not affected. Historical rows previously left `UNPRICED` can be resolved by
rerunning that date's grader after this code is deployed; no historical price
or units are fabricated.

## Historical context (legacy screener, retired)

The prior heuristic screener (`run_nightly.py`) is deprecated and unscheduled.
Its Aug 3-30 Discord-policy holdout graded **53-47-2, -10.94% ROI** (no edge).
The shadow projection challenger (v2) is research-only and not scheduled. Do not
re-enable either; the forecast board replaces them.

## Read next

- `README.md` for commands and current stack.
- `scripts/systemd/README.md` for unit install/update.
- `docs/research_model_roadmap.md` for projection theory and open questions.
