# WNBA Nightly Props Screener

Numbers-first nightly WNBA prop screener for common over markets.

## Current stack

- Slate: ESPN scoreboard
- Lines: PlayerProps.ai no-key feed with FanDuel/DraftKings book lines; manual ingest fallback; direct sportsbook scrapers remain diagnostic
- Logs: Basketball-Reference, with ESPN boxscore fallback

## What it does

- pulls tonight's WNBA slate
- fetches player prop line values
- fetches recent player game logs from Basketball-Reference
- applies the screening model and context flags
- prints a ranked terminal table

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

Backtest historical screen runs:

```bash
python3 backtest.py
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

Task Scheduler setup:

- Program/script: `C:\Users\muski\wnba_props\scripts\run_wnba_props_task.cmd`
- Start in: `C:\Users\muski\wnba_props`
- Schedule: daily, pregame window such as 5:30 PM local time

PowerShell setup from the terminal:

```powershell
$Action = New-ScheduledTaskAction `
  -Execute "$env:ComSpec" `
  -Argument '/c "C:\Users\muski\wnba_props\scripts\run_wnba_props_task.cmd"' `
  -WorkingDirectory "C:\Users\muski\wnba_props"

$Trigger = New-ScheduledTaskTrigger -Daily -At 5:30PM

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
Start-Sleep -Seconds 10
Get-ScheduledTaskInfo -TaskName "WNBA Props Daily"
Get-Content C:\Users\muski\wnba_props\outputs\logs\wnba_props_cmd_bootstrap.log -Tail 80
Get-Content C:\Users\muski\wnba_props\outputs\logs\wnba_props_task.log -Tail 80
```

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

Install the macOS daily task, scheduled for 5:30 PM local time:

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
export LINE_SOURCE=playerprops
export PLAYERPROPS_BOOK=FANDUEL
export BREF_REQUEST_INTERVAL_SECONDS=6.0
export FANDUEL_EVENT_URLS="https://sportsbook.fanduel.com/basketball/wnba/golden-state-valkyries-@-indiana-fever-35819846?tab=player-points"
```

Use `LINES_CACHE_TTL_MINUTES=0` if you want a full live line refresh every run.

## Notes

- The first run may be slower because it builds local caches and Basketball-Reference player lookup entries.
- WNBA Basketball-Reference player indexes use a different shape than NBA pages; this port handles the WNBA link-based index format.
- The model needs line values, not sportsbook odds. `LINE_SOURCE=playerprops` is the default no-key line path and uses `PLAYERPROPS_BOOK=FANDUEL` unless changed.
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
- first FanDuel runs may be slower because the scraper visits active team roster pages and player prop pages
- Basketball-Reference matching may need manual aliases for certain player names. Add them to `config/player_aliases.json`.
- the board hides qualified props below `MIN_DISPLAY_SCORE` by default, but still evaluates them internally
- availability flags come from ESPN injury feeds for the slate teams and are intended as context, not automatic overrides
- screen runs now write backtest-ready snapshots to `outputs/history/`
- `python3 backtest.py` resolves finished props from stored runs and reports score-band, prop-type, and flag performance

## Current gaps before first live stats run

- broader validation of PlayerProps.ai book-labeled lines against FanDuel/DraftKings screens across several slates
- possible player alias cleanup after the first live line run
- possible source-shape adjustments if FanDuel returns unexpected formats
