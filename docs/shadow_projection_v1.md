# WNBA points projection shadow v1

This is a research-only challenger to the existing daily screener. It does not replace or modify the nightly runner, heuristic screener, Discord policy, scheduled-task scripts, or normal `screen_run_*.json` exports.

## Initial scope

- PTS props only
- FanDuel-labeled PlayerProps lines and available over/under prices by default
- point-in-time game logs restricted to `game_date < screen_date`
- separately projected minutes and points per minute
- conservative game-total and blowout adjustments when ESPN context is available
- 10,000 deterministic simulations per prop
- fair prices, break-even probabilities, and flat-stake expected value
- explicit missing-context flags when a spread or total is unavailable
- every result labeled `RESEARCH_ONLY`

The first version deliberately does not use the existing four-of-five qualification rule. Every PTS line with at least five eligible prior games can receive a projection.

## Isolation guarantees

The shadow command:

- uses `.cache/shadow/` instead of the production cache directories
- uses an isolated ESPN slate client and reads spread/total from the same scoreboard response, so research compatibility changes cannot affect production
- writes only `outputs/history/shadow_projection_*.json`
- grades snapshots separately into `outputs/backtests/shadow_grade_*.json` and `.txt`
- never imports or calls a Discord notifier
- is not referenced by `run_nightly.py` or any scheduler script
- does not alter current candidate scoring or daily output

It reuses the existing read-only log-loading helper so source fallbacks and stale-log protection remain consistent without changing the production runner.

## Run manually

The default collector is strict: it only captures games 20–90 minutes before scheduled tip and records each game once per model version and line source.

```bash
python3 run_projection_shadow.py
```

For a deliberate second capture while a game remains inside the window:

```bash
python3 run_projection_shadow.py --force-recapture
```

Use a smaller deterministic simulation count for a smoke test:

```bash
python3 run_projection_shadow.py --simulations 1000
```

The separate manual-line fallback is available with:

```bash
SHADOW_LINE_SOURCE=manual python3 run_projection_shadow.py
```

`--include-started` remains available only for engineering smoke tests. Those snapshots retain their capture timestamp but are excluded from the strict pregame rollup.

The command is intentionally not part of the production Windows scheduled task. It was deployed on August 5, 2026 to a separate shadow checkout and separate shadow tasks. Discord delivery is not part of the collection design.

## Grade a completed snapshot

The grader checks ESPN game state first and resolves a projection only after the game is marked final. A player with a matched zero-minute boxscore row is voided as a DNP. A missing player match remains unresolved for inspection rather than being silently treated as a DNP or loss.

```bash
python3 grade_projection_shadow.py
```

By default, it grades the newest shadow snapshot. A specific point-in-time snapshot can be supplied explicitly:

```bash
python3 grade_projection_shadow.py --snapshot outputs/history/shadow_projection_YYYYMMDDTHHMMSSZ.json
```

The report includes points and minutes error, over-probability Brier score and calibration buckets, research-selection outcomes, and flat-stake units only where the original snapshot captured the selected side's price. Pending, DNP, unresolved, unpriced, and push records remain explicit.

Grade every snapshot that does not yet have a terminal final-game report:

```bash
python3 grade_projection_shadow.py --all-pending
```

The batch grader fetches the scoreboard once per slate date and each final boxscore once per game. Final boxscores remain in the isolated shadow cache.

## Multi-slate evidence rollup

```bash
python3 shadow_rollup.py
```

The primary report admits only projections captured 20–90 minutes before scheduled tip. If deliberate duplicate snapshots exist, it selects the closest eligible pregame capture for each player, game, prop, bookmaker, and model version. Later or overly early snapshots remain in diagnostics but cannot enter the primary evidence set.

The rollup compares model MAE and Brier score with the sportsbook line and no-vig market probabilities. It also reports interval coverage, minutes error, context and price coverage, results by slate/game, and research-only flat-stake units.

The collection gate remains `COLLECTING` until it has at least 7 slates, 20 games, 100 strict pregame projections, and 90% both-side price coverage. `READY_FOR_REVIEW` means only that the sample is large enough for review; it does not establish betting edge.

## Windows deployment and current blocker

The isolated deployment is:

- checkout: `C:\Users\muski\wnba_props_shadow`
- branch: `codex/wnba-shadow-collection`
- initial deployment commit: `1aa3a1c`
- `WNBA Shadow Capture`: hourly 9 a.m.–11 p.m. Eastern
- `WNBA Shadow Grade`: daily at 6 a.m. Eastern

Two shadow-only PowerShell scripts back those tasks:

- `scripts/run_wnba_shadow_capture_task.ps1` runs only the strict pregame collector and writes `outputs/logs/wnba_shadow_capture.log`.
- `scripts/run_wnba_shadow_grade_task.ps1` grades all pending snapshots, rebuilds the rollup, and writes `outputs/logs/wnba_shadow_grade.log`.

The deployment is isolated from `WNBA Props Daily`.

## Evidence audit record (August 21, 2026)

The collection, grading, and rollup pipeline is healthy and producing evidence. The first evidence-gate transition occurred on August 21 at 06:00 ET:

```
Evidence gate: READY_FOR_REVIEW
Progress: 8/7 slates, 21/20 games, 200/100 projections
Both-side price coverage: 100.00%
```

That first `READY_FOR_REVIEW` transition was influenced by six snapshots marked `code_dirty: true`, caused by an untracked read-only audit helper (`shadow_summary.py`) left in the Windows shadow checkout on August 17. The helper was never imported or executed by the model. Model code, commit `3184273`, and config hash `b3096ccb93b6f4d1` never changed. The helper was removed on August 21 and the checkout verified clean again.

### Audit-tainted snapshots (operationally valid, excluded from strict gate)

| Snapshot | Screen date | Games | Projections |
|---|---|---|---|
| `shadow_projection_20260818T220446Z.json` | 2026-08-18 | 2 | 17 |
| `shadow_projection_20260819T000419Z.json` | 2026-08-18 | 2 | 20 |
| `shadow_projection_20260819T220135Z.json` | 2026-08-19 | 1 | 9 |
| `shadow_projection_20260820T010247Z.json` | 2026-08-19 | 1 | 11 |
| `shadow_projection_20260820T230220Z.json` | 2026-08-20 | 1 | 10 |
| `shadow_projection_20260821T010536Z.json` | 2026-08-20 | 2 | 19 |

### Clean baseline preserved

The strict evidence set excludes the tainted cohort above plus the pre-freeze diagnostic snapshot `shadow_projection_20260813T030154Z.json` (non-canonical config `6cfcfae3020ea5e8`, commit `1aa3a1c`).

Clean baseline from the 11 frozen-canonical snapshots (Aug 13–17):

- 5 slates
- 12 games
- 117 projections
- 100% both-side price coverage

Remaining to close the clean-only gate: 2 slates and 8 games. The collector remains frozen at commit `3184273` until that clean-only gate closes. Snapshots, grade reports, and rollups are never deleted or rewritten.

## Audit-aware rollup tooling (August 21, 2026)

Developed in the local macOS repo and verified in the throwaway Windows analysis workspace at `C:\Users\muski\wnba_props_shadow_analysis`. Not yet deployed to the frozen collector checkout.

Changes:

- `grade_projection_shadow.py` records `source_code_commit` and `source_code_dirty` on new grade reports.
- `shadow_rollup.py` backfills `code_commit` and `code_dirty` onto graded rows from their source snapshot, or from report-level source fields when the snapshot file is gone.
- `wnba_props/shadow/rollup.py`:
  - excludes `code_dirty: true` rows from primary evidence (`code_dirty` reason)
  - excludes rows missing a commit (`code_commit_missing`) or missing the dirty flag (`code_state_missing`) from strict evidence
  - includes `code_commit` in the model identity and dedup key; multiple commits now report `MIXED_MODELS`
  - keeps tainted rows in diagnostics (`all_resolved_diagnostics`) but separate from `primary_pregame`

Verification on the copied historical artifacts: the audit-aware rollup excludes 83 `code_dirty` + 29 `code_state_missing` rows and reports exactly the clean baseline above (5/7 slates, 12/20 games, 117/100 projections, gate `COLLECTING`). All 43 local tests and 32 Windows shadow tests pass.

## Preliminary v1 evaluation from the clean cohort (August 21, 2026)

Based on the 117 clean strict-pregame projections (5 slates, 12 games, Aug 13–17):

- Model points MAE `4.9347` vs sportsbook-line MAE `4.6966` — the model predicts points slightly worse than the closing-style line.
- Model over Brier `0.2672` vs market no-vig over Brier `0.2480` — the model probabilities are less accurate than the market's.
- 10th–90th interval coverage `69.23%` vs nominal `80%` — uncertainty is too narrow.
- Selected hit rate `40/86` (46.5%), flat-stake ROI `-12.99%` (research diagnostics only).
- Calibration degrades above 50%: buckets with predicted over probability 0.5–0.6 and 0.6–0.7 hit only 41.3% and 42.1%; 0.7–0.9 buckets all went under.

This is a preliminary read, not the formal closure. The formal v1 decision is made only after the clean-only cohort reaches 7 slates and 20 games with the audit-aware rollup.
