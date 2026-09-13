# WNBA Daily Props Screener

Numbers-first daily WNBA prop screener for common player prop markets. The goal is to mirror the NBA/MLB props workflow in a separate WNBA repo: collect the slate, load no-key line values, evaluate the current model for overs and unders, save history for backtesting, and optionally send only stronger plays to Discord.

## Start here for a continuation

- [`docs/current_handoff.md`](docs/current_handoff.md) is the canonical verified state and next action.
- [`docs/project_history_and_lessons.md`](docs/project_history_and_lessons.md) explains the successes, failures, and decisions that produced the current architecture.
- [`docs/research_model_roadmap.md`](docs/research_model_roadmap.md) defines the projection theory, evidence rules, and skeptical review questions.
- [`docs/shadow_projection_v1.md`](docs/shadow_projection_v1.md) documents the isolated PTS challenger.
- [`docs/new_agent_prompt.md`](docs/new_agent_prompt.md) contains a copy-ready introduction for a new conversation.

As of September 12, 2026, production is migrating to a **prediction-first forecast board** (`run_forecast_pipeline.py`): the model projects moneylines, spreads, totals and player props before any price is attached, then prices are used only for value classification and flat-unit ROI grading. Bovada is the primary game-market source, Polymarket is the fallback reference, and PlayerProps.ai supplies player lines. The legacy heuristic screener (`run_nightly.py`) is deprecated and its timers are disabled.

## Forecast board (prediction-first)

The evening run builds the board daily at 18:45 ET. A weekend-only early run
fires weekends at 12:36 ET (off-peak) for early tip-offs. The later slot
supersedes the earlier pending line, so each play is logged and graded once.
The grader runs the next morning and defaults to the prior calendar day.

```bash
# Afternoon board (early games)
python3 run_forecast_pipeline.py --slot afternoon --send-discord

# Evening board (majority of games; refreshes lines)
python3 run_forecast_pipeline.py --slot evening --send-discord

# Grade a completed board and post the recap
python3 grade_forecast_board.py --date 2026-09-17 --send-discord
```

Board publication fails closed: if game-market coverage, player coverage, or
priced-row minimums fall below the configured thresholds in
`wnba_props/config.py`, the board is marked `degraded` and Discord is blocked.

Discord output can be split across two channels for readability by setting
`WNBA_PROPS_TEAM_DISCORD_WEBHOOK_URL` (moneyline/spread/totals) and
`WNBA_PROPS_PLAYER_DISCORD_WEBHOOK_URL` (PTS/REB/AST/3PM). If either is unset,
the single `WNBA_PROPS_DISCORD_WEBHOOK_URL` is used for all sections.

Model artifacts are fitted offline and loaded as frozen production inputs:

```bash
python3 scripts/fit_game_engine.py            # winner / margin / total
python3 scripts/fit_props_engine.py           # PTS/REB/AST/3PM residual + calibration
```

A missing or invalid artifact fails the run (see `wnba_props/modeling/registry.py`).
Outputs: `outputs/forecast_boards/`, `outputs/ledger/forecast_ledger.jsonl`,
`outputs/history/game_markets_*.json`, `outputs/grades/`.

## Legacy screener (deprecated)

The commands below document the retired heuristic screener. Its systemd timers
(`sports-wnba-daily`, `sports-wnba-shadow-capture`, `sports-wnba-shadow-grade`)
are disabled; do not re-enable them. See
[`scripts/systemd/README.md`](scripts/systemd/README.md).

## Current project state

- Independent repo: primary working copy and bulk store on macOS at `/Users/colemason/Documents/wnba_props`; always-on runner is a lightweight Azure VM (`~/wnba_props` on `main`, plus a `~/wnba_props_shadow` worktree of `codex/wnba-shadow-v2`). The Windows box (`C:\Users\muski\wnba_props`) is retired as of September 2026.
- Default daily flow is operational: ESPN slate, PlayerProps.ai line values, Basketball-Reference/ESPN logs, ESPN injuries and odds context, terminal board, JSON history export, and optional Discord notification.
- Azure VM systemd user timers are the primary deployment target (`scripts/run_linux_task.sh` + `~/.config/systemd/user/sports-wnba-*.timer`). The Windows Task Scheduler wrapper remains in `scripts/` for reference only.
- Pull VM runtime outputs to the Mac with `scripts/sync_from_vm.sh` (history, health, logs, shadow outputs; pull-only, secrets never move).
- Saved caches, logs, history exports, and backtest reports are local runtime artifacts under `.cache/` and `outputs/`; they are intentionally ignored by git.

## Current stack

- Slate: ESPN scoreboard
- Lines: PlayerProps.ai no-key feed with FanDuel/DraftKings book lines and available over/under prices; manual ingest fallback; direct sportsbook scrapers remain diagnostic
- Logs: Basketball-Reference, with ESPN boxscore fallback
- Context: ESPN injuries plus ESPN odds when available
- Notifications: Discord webhook embeds, gated separately from the terminal board

## What it does

- pulls tonight's WNBA slate
- fetches player prop line values
- fetches recent player game logs from Basketball-Reference
- applies the screening model and context flags
- prints a ranked terminal table
- writes a backtest-ready `screen_run_*.json` history snapshot
- optionally sends a Discord digest for picks above a stricter notification cutoff

## Requirements

- Python 3.9+

## Quick start

```bash
python3 run_nightly.py
```

Run with the default FanDuel-labeled PlayerProps line source:

```bash
python3 run_nightly.py
```

Preview today's default line source without loading player stats:

```bash
python3 preview_lines.py
```

Preview the experimental PropCruncher ranking source without loading player stats:

```bash
LINE_SOURCE=propcruncher python3 preview_lines.py
```

Preview DraftKings-labeled lines from the same no-key feed:

```bash
PLAYERPROPS_BOOK=DRAFTKINGS python3 preview_lines.py
```

Run from a simple line-value CSV if the source needs a manual override:

```bash
LINE_SOURCE=manual python3 run_nightly.py
```

Manual rows live in `config/manual_lines.csv`:

```csv
player_name,team,opponent,prop_type,line,bookmaker
Sonia Citron,WSH,POR,PTS,16.5,manual
Carla Leite,POR,WSH,PTS,15.5,manual
```

Warm cache without screening:

```bash
python3 run_nightly.py --warm-cache
```

Backtest the latest completed historical screen run:

```bash
python3 backtest.py
```

Backtest the latest saved screen for every historical slate date:

```bash
python3 backtest.py --all-history
```

Backtest reports are also exported automatically to `outputs/backtests/` with one file per slate date.

Show cache summary:

```bash
python3 run_nightly.py --cache-report
```

Remove legacy top-level cache files:

```bash
python3 run_nightly.py --cache-clean
```

Send the daily picks digest to Discord:

```bash
SEND_DISCORD=true WNBA_PROPS_DISCORD_WEBHOOK_URL=your_discord_webhook_url python3 run_nightly.py
```

Discord defaults to `DISCORD_MIN_SCORE=8` and `DISCORD_LIMIT=5` per side (10 plays maximum), while the terminal board still uses `MIN_DISPLAY_SCORE=7` unless changed. Candidates flagged `SEASON-` or `TEAM_OUT` remain on the research board and in history but are suppressed from Discord by default.

Inspect the full board without sending Discord:

```bash
SEND_DISCORD=false MIN_DISPLAY_SCORE=0 python3 run_nightly.py
```

If Discord says there are no plays, use the full-board command to distinguish between "no model-qualified props" and "qualified props exist but none cleared the Discord cutoff."

## Architecture decisions

- **Separate WNBA repo:** this project copies lessons from NBA/MLB projects but stays independent so WNBA-specific data quirks, aliases, thresholds, and model tuning can evolve without disturbing the NBA project.
- **No paid odds API dependency:** the default line source is the no-key PlayerProps.ai feed. The screener still scores line value rather than price, but it now saves available PlayerProps over/under prices so future resolved runs can report flat-stake units and ROI.
- **PlayerProps.ai is default; FanDuel/DraftKings direct sources are diagnostic:** FanDuel WNBA pages can return bot/captcha/CORS-challenged content, and DraftKings direct/headless paths have been unreliable. PlayerProps.ai book-labeled lines have been the most practical no-key source so far, but still need continued validation against sportsbook screens.
- **Manual line fallback stays simple:** `LINE_SOURCE=manual` reads `config/manual_lines.csv` when the automated line feed is wrong, unavailable, or needs spot validation.
- **Terminal board and Discord cutoff are intentionally different:** `MIN_DISPLAY_SCORE` controls what a user can inspect locally; `DISCORD_MIN_SCORE` controls what gets pushed as a notification. Discord defaults stricter to reduce noise.
- **Risk-flagged props stay measurable:** `SEASON-` now carries a real score penalty, `TEAM_OUT` is treated as uncertainty rather than an automatic usage boost, and both flags are suppressed from Discord. They remain in saved candidate history as a shadow group for forward validation.
- **Line-source failure is not a no-plays result:** a populated feed that cannot match the ESPN slate records structured diagnostics, writes a `screen_failure_*.json` snapshot, exits nonzero, and optionally sends a distinct Discord data-failure message.
- **WNBA-specific Discord webhook variable:** use `WNBA_PROPS_DISCORD_WEBHOOK_URL` so this project does not hijack MLB tasks that may already use `DISCORD_WEBHOOK_URL`.
- **Pregame-only by default:** `PREGAME_ONLY=true` drops games that have already started. Late-day manual runs may show a smaller slate or no eligible games.
- **Regular-season log freshness allows league breaks:** `REGULAR_SEASON_LOG_STALE_DAYS=21` prevents the board from zeroing out after WNBA breaks, including the multi-week World Cup pause. Playoff freshness remains tighter by default with `PLAYOFF_LOG_STALE_DAYS=2`.
- **PowerShell wrapper avoids native stderr failure:** Python progress messages are written to stderr. The Windows task wrapper captures stdout/stderr through `Start-Process` temp files so normal progress output does not become a PowerShell `NativeCommandError`.

## Daily Automation

The project is ready for daily collection once the normal run completes and writes a history file.

### Linux (Azure VM) systemd timers — primary

On the VM, `scripts/run_linux_task.sh` runs the three jobs with a shared
flock lock, per-task logs under `outputs/logs/`, and timeouts. Secrets live
in `~/.config/wnba_props/env` (mode 600, never synced). The user timers are
`~/.config/systemd/user/sports-wnba-daily.timer` (daily 10:56 ET),
`sports-wnba-shadow-capture.timer` (hourly 10:00–22:00 ET), and
`sports-wnba-shadow-grade.timer` (06:17 ET); the shadow services run from the
`~/wnba_props_shadow` v2 worktree via `PROJECT_DIR`/`WNBA_PROPS_PYTHON_EXE`
overrides while sharing the main checkout's venv and lock.

After changing a unit file: `systemctl --user daemon-reload`. Check status
with `systemctl --user list-timers` and per-task logs in `outputs/logs/`.
The VM checkout must stay on a clean `main` or scheduled Discord is blocked
by `WNBA_REQUIRE_CLEAN_TREE`.

### Windows Task Scheduler (forecast pipeline)

The Windows desktop can run the prediction-first forecast pipeline. This is
useful when the Azure VM cannot reach Bovada (datacenter IPs get a 302 redirect
loop), because a residential connection can pull live Bovada prices.

From an elevated PowerShell on the Windows box:

```powershell
cd C:\Users\muski\wnba_props
git pull --ff-only origin main
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_forecast_tasks.ps1
```

That registers three interactive tasks and disables the legacy ones:

| Task | Schedule (local) | Runs |
| --- | --- | --- |
| `WNBA Forecast Weekend` | Sat/Sun 12:36 | `run_forecast_pipeline.py --slot afternoon --send-discord` |
| `WNBA Forecast Daily` | daily 18:45 | `run_forecast_pipeline.py --slot evening --send-discord` |
| `WNBA Forecast Grade` | daily 06:17 | `grade_forecast_board.py --send-discord` (grades yesterday) |

Secrets are read from `%USERPROFILE%\.config\wnba_props\env` (KEY=VALUE per
line). At minimum set:

```text
WNBA_PROPS_DISCORD_WEBHOOK_URL=...
```

The wrapper logs to `outputs\logs\wnba_forecast.log` and
`outputs\logs\wnba_forecast_grade.log`. Test manually before trusting the
schedule:

```powershell
.\scripts\run_wnba_forecast_task.ps1 -ProjectDir "C:\Users\muski\wnba_props" -Slot evening -NoDiscord
.\scripts\run_wnba_forecast_grade_task.ps1 -ProjectDir "C:\Users\muski\wnba_props" -NoDiscord
```

Windows uses the legacy daily screen (below) only for rollback.

### Windows legacy screener (retired September 2026)

After cloning the repo on Windows, test the exact scheduled command manually from PowerShell:

```powershell
cd C:\Users\muski\wnba_props
.\scripts\run_wnba_props_task.ps1 -ProjectDir "C:\Users\muski\wnba_props" -PythonExe "python"
```

That appends terminal output to:

```powershell
outputs\logs\wnba_props_task.log
```

Every successful nightly screen writes the backtest-ready JSON snapshot to:

```powershell
outputs\history\
```

Store the Discord webhook once for the Windows user that runs the scheduled task:

```powershell
setx WNBA_PROPS_DISCORD_WEBHOOK_URL "your_discord_webhook_url"
```

Open a new PowerShell window after `setx` before testing. The scheduled wrapper opts into Discord with `SEND_DISCORD=true`; if the webhook is missing, the run still completes and logs a notification failure. `DISCORD_WEBHOOK_URL` is still supported as a fallback for manual runs, but the WNBA-specific variable avoids interfering with MLB tasks that may use the generic name.

Task Scheduler setup:

- Program/script: `C:\Users\muski\wnba_props\scripts\run_wnba_props_task.cmd`
- Start in: `C:\Users\muski\wnba_props`
- Schedule: daily, pregame window such as 11:00 AM local time

PowerShell setup from the terminal:

```powershell
$Action = New-ScheduledTaskAction `
  -Execute "$env:ComSpec" `
  -Argument '/c "C:\Users\muski\wnba_props\scripts\run_wnba_props_task.cmd"' `
  -WorkingDirectory "C:\Users\muski\wnba_props"

$Trigger = New-ScheduledTaskTrigger -Daily -At 11:00AM

$Settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries

Register-ScheduledTask `
  -TaskName "WNBA Props Daily" `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Runs the WNBA props screener daily and logs history snapshots." `
  -Force
```

Smoke test the scheduled task:

```powershell
Start-ScheduledTask -TaskName "WNBA Props Daily"
Start-Sleep -Seconds 60
Get-ScheduledTaskInfo -TaskName "WNBA Props Daily"
Get-Content C:\Users\muski\wnba_props\outputs\logs\wnba_props_cmd_bootstrap.log -Tail 80
Get-Content C:\Users\muski\wnba_props\outputs\logs\wnba_props_task.log -Tail 80
```

Expected success signal:

```powershell
LastTaskResult : 0
```

and the task log should end with either `Finished WNBA props with exit code 0` or a clear runner-level failure. Old failure entries may remain in the log; evaluate the newest timestamped run.

If the repo is not at `C:\Users\muski\wnba_props`, edit `PROJECT_DIR` in `scripts\run_wnba_props_task.cmd` or pass the correct `-ProjectDir` when testing the PowerShell script.

### macOS launchd (manual use only — not scheduled)

Test the exact scheduled command manually:

```bash
scripts/run_daily.sh
```

That appends terminal output to:

```bash
outputs/logs/daily_run.log
```

Every successful nightly screen still writes the backtest-ready JSON snapshot to:

```bash
outputs/history/
```

Install the macOS daily task only for manual/local runs — nothing is scheduled on the Mac; the VM timers are the production schedule. The checked-in plist is currently scheduled for 5:30 PM local time; WNBA slates can start earlier, so VM production automation uses a 10:56 AM pregame run.

```bash
mkdir -p ~/Library/LaunchAgents
cp scripts/com.colemason.wnba-props.daily.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.colemason.wnba-props.daily.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.colemason.wnba-props.daily.plist
```

Run the scheduled job immediately for a smoke test:

```bash
launchctl start com.colemason.wnba-props.daily
```

Check scheduled logs:

```bash
tail -n 80 outputs/logs/daily_run.log
```

Uninstall the scheduled task:

```bash
launchctl unload ~/Library/LaunchAgents/com.colemason.wnba-props.daily.plist
rm ~/Library/LaunchAgents/com.colemason.wnba-props.daily.plist
```

Optional environment variables:

```bash
export SCREEN_DATE=2026-04-01
export SCREEN_PROP_TYPES=PTS,REB,AST
export CACHE_TTL_HOURS=24
export LINES_CACHE_TTL_MINUTES=10
export INJURIES_CACHE_TTL_MINUTES=10
export INCLUDE_UNDERS=true
export PREGAME_ONLY=true
export STICKY_DAILY_LOG_CACHE=true
export EXPORT_HISTORY=false
export MIN_DISPLAY_SCORE=7
export SEND_DISCORD=false
export WNBA_PROPS_DISCORD_WEBHOOK_URL=your_discord_webhook_url
export DISCORD_MIN_SCORE=8
export DISCORD_LIMIT=5
export LINE_SOURCE=playerprops
export PLAYERPROPS_BOOK=FANDUEL
export BREF_REQUEST_INTERVAL_SECONDS=6.0
export REGULAR_SEASON_LOG_STALE_DAYS=21
export PLAYOFF_LOG_STALE_DAYS=2
export FANDUEL_EVENT_URLS="https://sportsbook.fanduel.com/basketball/wnba/golden-state-valkyries-@-indiana-fever-35819846?tab=player-points"
```

Use `LINES_CACHE_TTL_MINUTES=0` if you want a full live line refresh every run.

## Testing and validation

Before pushing code changes, run at least:

```bash
PYTHONPYCACHEPREFIX=.pycache python3 -m py_compile run_nightly.py backtest.py wnba_props/config.py wnba_props/output.py
PYTHONPYCACHEPREFIX=.pycache python3 run_nightly.py --cache-report
python3 preview_lines.py
python3 -m unittest discover -s tests -v
```

For a slate sanity check:

```bash
SEND_DISCORD=false MIN_DISPLAY_SCORE=0 python3 run_nightly.py
```

For Windows task validation:

```powershell
Start-ScheduledTask -TaskName "WNBA Props Daily"
Start-Sleep -Seconds 60
Get-ScheduledTaskInfo -TaskName "WNBA Props Daily"
Get-Content C:\Users\muski\wnba_props\outputs\logs\wnba_props_task.log -Tail 120
```

Important summary fields:

- `Players loaded successfully` should be near `Unique players with lines`; if it is zero, inspect skipped player reasons before interpreting the board.
- `Prop lines evaluated` should be greater than zero on a real slate with available lines.
- `Prop lines that qualified` is the full model-qualified set.
- `Prop lines displayed` depends on `MIN_DISPLAY_SCORE`.
- Discord only sends candidates at or above `DISCORD_MIN_SCORE`.
- Discord also suppresses candidates carrying `SEASON-` or `TEAM_OUT`; backtest reports show the eligible group and suppressed shadow group separately.

## Notes

- The first run may be slower because it builds local caches and Basketball-Reference player lookup entries.
- WNBA Basketball-Reference player indexes use a different shape than NBA pages; this port handles the WNBA link-based index format.
- The model scores line values rather than sportsbook prices. `LINE_SOURCE=playerprops` is the default no-key line path and uses `PLAYERPROPS_BOOK=FANDUEL` unless changed; available over/under prices are saved for later ROI reporting.
- Use `PLAYERPROPS_BOOK=DRAFTKINGS` to switch the same feed to DraftKings-labeled lines.
- PropCruncher ranking pages are not reliable sportsbook line inputs; they can be useful for source investigation only.
- `LINE_SOURCE=manual` reads `config/manual_lines.csv` and is the cleanest source-independent fallback.
- `LINE_SOURCE=draftkings` is an experimental direct DraftKings browser/API scraper. DraftKings currently returns no available bets in headless browser context and blocks direct market payloads, so it is not the default.
- FanDuel WNBA pages may return bot/captcha challenges from simple network clients; FanDuel should be treated as a fallback/cache source unless a reliable line feed is added.
- WNBA FanDuel lines can optionally be warmed through `warm_fanduel_browser.py`, which uses a persistent local browser profile to discover today's WNBA event pages, expand player prop rows, and write `.cache/lines` files.
- injury feeds are cached separately with a short TTL by default
- repeated runs within the short line-cache window reuse scraped player prop pages
- slate and game-log data can stay cached longer because they change much less often
- Basketball-Reference requests are rate-limited by `BREF_REQUEST_INTERVAL_SECONDS` to reduce 429s while building first-time caches
- regular-season logs can be up to `REGULAR_SEASON_LOG_STALE_DAYS` old by default so league breaks do not wipe the board
- first FanDuel runs may be slower because the scraper visits active team roster pages and player prop pages
- Basketball-Reference matching may need manual aliases for certain player names. Add them to `config/player_aliases.json`.
- the board hides qualified props below `MIN_DISPLAY_SCORE` by default, but still evaluates them internally
- availability flags come from ESPN injury feeds for the slate teams and are intended as context, not automatic overrides
- screen runs now write backtest-ready snapshots to `outputs/history/`
- `python3 backtest.py` resolves finished props from stored runs and reports score-band, prop-type, and flag performance
- new priced snapshots also report flat-stake units and ROI; older snapshots remain hit-rate-only because they did not store prices

## Debugging lessons learned

- If Discord reports no plays, first check whether the run evaluated any props. After the WNBA break, the board initially showed no Discord plays because every player was skipped as `stale recent logs`; increasing regular-season log freshness fixed the actual issue.
- If the line feed contains events but no lines match the slate, inspect the reported raw and normalized team pairs. The August 1, 2026 run lost an entire populated slate because PlayerProps used `LVA`/`NYL` while ESPN used `LV`/`NY`; those aliases are now covered by regression tests.
- A line-source failure now sends a distinct operational message when Discord is enabled. It should not be interpreted as a normal model-qualified no-plays day.
- `preview_lines.py` only proves line coverage. It does not load stats or prove the model evaluated candidates.
- `MIN_DISPLAY_SCORE=0` is the safest manual inspection mode because it shows every model-qualified candidate without changing scoring.
- `SEND_DISCORD=false` should be set for manual investigations to avoid duplicate notifications.
- `setx` writes future Windows environment variables but does not update the current PowerShell session. Open a new PowerShell window after setting webhook variables.
- A clean scheduled task can still produce `No eligible WNBA games found` if the run happens after games have started or there is no remaining pregame slate.
- A first run after a break or cache miss can be slow because Basketball-Reference fetches are rate-limited intentionally.

## Known limitations and future work

- broader validation of PlayerProps.ai book-labeled lines against FanDuel/DraftKings screens across several slates
- possible player alias cleanup after the first live line run
- possible source-shape adjustments if FanDuel returns unexpected formats
- WNBA-specific model tuning is still unresolved; current scoring is intentionally conservative and inherited from prior props workflows
- combined props such as PRA, P+A, P+R, and R+A are supported in code but depend on line-source coverage and have not been validated as deeply as PTS, REB, AST, and 3PM
- direct FanDuel/DraftKings scraping remains unreliable enough that it should not be considered the production line path
- backtesting depends on saved `outputs/history/screen_run_*.json` files from the runtime machine; those files are not committed
- injury and availability flags are context signals for teammates; the candidate player's own `OUT`/`IR`/`SUSPENDED` status is now a hard exclusion
- prices are captured when PlayerProps supplies them, but pricing does not yet affect candidate scoring and older snapshots cannot produce ROI
- the `SEASON-`/`TEAM_OUT` suppression policy was selected from a small historical sample and must be evaluated prospectively through the saved shadow group

## Break-sprint hardening (2026-08-31)

Applied before the September 17 season resumption:

- **Point-in-time safety:** production features use only games strictly before `SCREEN_DATE`; DNP/zero-minute rows are excluded everywhere (parity with the shadow model).
- **Availability:** a player listed `OUT`, `Out For Season`, `Out Indefinitely`, `IR`, or `Suspended` is excluded from screening and counted in the run summary (`excluded unavailable`); injury-source failures now mark the run degraded instead of pretending there are no injuries.
- **Run health:** every run is classified `healthy` / `degraded` / `failed` / `no_slate` using event-match, player-load, and evaluated-line gates (`MIN_EVENT_MATCH_RATIO`, `MIN_PLAYER_LOAD_RATIO`, `MIN_EVALUATED_LINES`). Discord is blocked on degraded runs unless `WNBA_ALLOW_DEGRADED_DISCORD=true`.
- **Artifact before notify:** history is written atomically (temp file + readback validation + rename) before any Discord send; delivery outcome is recorded in a companion `screen_run_<ts>.delivery.json` and `outputs/health/run_status.jsonl`.
- **Pregame guard:** games that start mid-run and candidates whose captured line is older than `MAX_LINE_AGE_MINUTES` (default 240) are dropped before output.
- **Provenance:** every snapshot stores policy version, git commit, dirty flag, python version, config fingerprint, and thresholds. Scheduled Discord also requires a clean tree (`WNBA_REQUIRE_CLEAN_TREE=false` to override).
- **WNBA total context:** the NBA-inherited 218/232 `HIGH_TOT`/`LOW_TOT` thresholds are replaced with WNBA-scale 172/156 (anchored to the frozen v1 league-total baseline 164 ± 5%); override with `TOTAL_CONTEXT_HIGH` / `TOTAL_CONTEXT_LOW`.
- **Statistics:** `wnba_props/stats.py` provides slate-clustered bootstrap CIs, paired cluster diffs, and leave-one-cluster-out for any correlated-row evaluation.
- **Backtests:** slates exported after tip are excluded; the Discord policy threshold is taken from snapshot provenance when present.
- **Research:** August 3–30 holdout verdict (exact capped Discord policy): 53-47-2 (W-L-void; earlier "53-49" counted the 2 DNP voids as losses), ROI **-10.94%** (CI95 -26.8%..+7.3%) vs 59.4% break-even — no edge; score is inversely related to ROI. See `outputs/hunt/holdout_report_*.txt`, protocol in `outputs/hunt/HOLDOUT_PROTOCOL.md`, hypothesis ledger in `docs/research/ledger.md`, walk-forward diagnostics in `research/walkforward.py`.
