# WNBA Daily Props Screener

Numbers-first daily WNBA prop screener for common player prop markets. The goal is to mirror the NBA/MLB props workflow in a separate WNBA repo: collect the slate, load no-key line values, evaluate the current model for overs and unders, save history for backtesting, and optionally send only stronger plays to Discord.

## Current project state

- Independent repo intended to live at `C:\Users\muski\wnba_props` on the Windows automation box and `/Users/colemason/Documents/wnba_props` on macOS.
- Default daily flow is operational: ESPN slate, PlayerProps.ai line values, Basketball-Reference/ESPN logs, ESPN injuries and odds context, terminal board, JSON history export, and optional Discord notification.
- Windows Task Scheduler is the primary deployment target. The checked-in Windows wrapper assumes `C:\Users\muski\wnba_props`, `python`, and Windows PowerShell 5.1.
- The model is intentionally still close to the NBA-style heuristic model. Feature engineering and WNBA-specific model tuning are future work, not current behavior.
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
- **Regular-season log freshness allows league breaks:** `REGULAR_SEASON_LOG_STALE_DAYS=14` prevents the board from zeroing out after WNBA breaks. Playoff freshness remains tighter by default with `PLAYOFF_LOG_STALE_DAYS=2`.
- **PowerShell wrapper avoids native stderr failure:** Python progress messages are written to stderr. The Windows task wrapper captures stdout/stderr through `Start-Process` temp files so normal progress output does not become a PowerShell `NativeCommandError`.

## Daily Automation

The project is ready for daily collection once the normal run completes and writes a history file.

### Windows Task Scheduler

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

### macOS launchd

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

Install the macOS daily task. The checked-in plist is currently scheduled for 5:30 PM local time; WNBA slates can start earlier, so Windows production automation currently uses an 11:00 AM pregame run.

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
export REGULAR_SEASON_LOG_STALE_DAYS=14
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
- injury and availability flags are context signals, not automatic hard excludes
- prices are captured when PlayerProps supplies them, but pricing does not yet affect candidate scoring and older snapshots cannot produce ROI
- the `SEASON-`/`TEAM_OUT` suppression policy was selected from a small historical sample and must be evaluated prospectively through the saved shadow group
