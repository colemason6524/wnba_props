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

The command is intentionally not part of the Windows scheduled task. A separate shadow checkout and separate scheduled tasks require future approval. Discord delivery is not part of the collection design.

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

## Prepared Windows entrypoints

Two shadow-only PowerShell scripts are prepared but are not registered with Task Scheduler:

- `scripts/run_wnba_shadow_capture_task.ps1` runs only the strict pregame collector and writes `outputs/logs/wnba_shadow_capture.log`.
- `scripts/run_wnba_shadow_grade_task.ps1` grades all pending snapshots, rebuilds the rollup, and writes `outputs/logs/wnba_shadow_grade.log`.

They are intended for a separate `wnba_props_shadow` checkout. Creating that checkout or changing Windows Task Scheduler remains a separate, explicit deployment action.
