# WNBA props project handoff

Last verified: 2026-08-28, America/Detroit (previous full verification: 2026-08-10)

## August 28, 2026 evidence note

Verified directly over `ssh windows` and in the local checkout:

- Windows production (`main` at `ca09a11` plus the ESPN HTTP work, since committed) is healthy: scheduled runs exited 0 with Discord sent and history exported daily through `screen_run_20260827T150653Z.json`. A missing August 24 history file corresponds to one off-schedule 22:45 run that appears to have aborted; every scheduled run since has succeeded. Low priority.
- The 2026 WNBA calendar pauses for the FIBA World Cup from August 30 through September 16; the regular season resumes September 17 and the playoffs start September 27. Production fixes applied for this window:
  - `REGULAR_SEASON_LOG_STALE_DAYS` default raised 14 → 21 in `run_nightly.py`, because 19-day-old logs on the September 17 resumption would have skipped every player as stale (the same failure documented after a prior break).
  - `wnba_props/screener.py` `_is_playoff_window` corrected from the NBA window `{4, 5, 6}` to the WNBA window `{9, 10}`, so `PLAYOFF_ROLE`, the 0.65 playoff recent-weight, and playoff score penalties actually engage during the WNBA postseason. Regression tests added in `tests/test_policy.py`.
- The user-owned uncommitted ESPN HTTP work (browser-safe headers plus curl fallback on 403) was reconciled and committed (`b14a9f6` on `codex/wnba-shadow-collection`, cherry-picked to `main`) and deployed to the Windows production checkout.
- Shadow status in the table below is superseded by `docs/v1_evaluation.md`: prospective collection demonstrably worked by mid-August (graded slates 08-14 through 08-21), and v1 was formally evaluated and **rejected for promotion**. It remains research-only.

This is the canonical starting point for a new conversation. Read this file before changing code, deployment, model policy, or schedules. The project has two deliberately separate systems: a working production screener and a research-only projection challenger.

## Executive state

| System | Purpose | Current state |
| --- | --- | --- |
| Production screener | Create the daily research board, history snapshot, and optional Discord digest | Healthy on Windows as of August 27 |
| PTS projection shadow | Prospectively test a game/player projection model without changing production | Collected through August 21; v1 formally rejected for promotion (research-only) |

The production system must remain unchanged while the shadow defect is repaired and evidence is collected. No shadow output should be promoted into production based on the current evidence.

## Authoritative locations and Git state

- macOS working copy: `/Users/colemason/Documents/wnba_props`
- Windows production: `C:\Users\muski\wnba_props`
- Windows shadow: `C:\Users\muski\wnba_props_shadow`
- Windows production branch/commit verified August 10: `main` at `ca09a11`
- Windows shadow branch/commit verified August 10: `codex/wnba-shadow-collection` at `1aa3a1c`

The local and Windows production checkouts contain uncommitted ESPN HTTP compatibility work in `wnba_props/utils.py`, four ESPN source modules, and `tests/test_espn_http.py`. It adds browser-safe ESPN headers and a curl fallback on HTTP 403. Treat these as active user-owned changes: do not overwrite, discard, or fold them into unrelated work without first reconciling their status.

The local checkout also contains the uncommitted shadow implementation and documentation. The Windows shadow checkout has the initial shadow version committed independently. Runtime artifacts under `.cache/` and `outputs/` are intentionally ignored by Git.

## Production system

The current flow is:

```text
ESPN slate/context/injuries
        +
PlayerProps.ai FanDuel-labeled lines and prices
        +
Basketball-Reference logs with ESPN fallbacks
        -> heuristic screener
        -> terminal board + screen_run history
        -> stricter Discord digest
```

Production is not a full predictive game model. It is a heuristic filter that emphasizes recent hit patterns, trend, opponent context, minutes/role indicators, availability, and other flags. It scores the line opportunity, not the expected value implied by price. Prices are captured where available for retrospective settlement.

Verified Windows evidence from August 10:

- scheduled task `WNBA Props Daily` ran at 11:00:01 a.m. and returned 0;
- 19 of 19 unique players loaded successfully;
- 59 prop lines were evaluated, 15 qualified, and 6 displayed at score 7 or higher;
- Discord sent successfully;
- history was exported to `screen_run_20260810T150715Z.json`;
- daily history files were present for August 3 through August 10.

The task is interactive-only under `colemason41`. A closed laptop does not matter because it runs on the Windows desktop, but that desktop must be powered on and the user logged in.

## Research question and current theory

The motivating question is whether we can move beyond “a player has stayed hot over the last five games” and predict how the coming game will actually play out. The intended direction is a probabilistic player/game model built from role, minutes, per-minute production, opponent and matchup, injuries/rotation, pace and game environment, then compared with the offered line and price.

Shadow v1 is the first narrow step, not the completed vision. It:

- handles PTS only;
- uses only games before the slate date;
- projects minutes and points per minute separately;
- applies conservative total and blowout context when ESPN provides it;
- simulates 10,000 outcomes deterministically;
- outputs a point estimate, uncertainty interval, over probability, fair price, captured market price, and research-only selection;
- never calls Discord or changes production selection.

The leading hypothesis after the engineering smoke test is that minutes and role uncertainty will be a major source of error. That remains a hypothesis, not a tuned conclusion.

## Evidence collected so far

Only one completed-slate engineering snapshot has been graded. It was captured 4.8 minutes after scheduled tip, so it is excluded from prospective evidence. All ten rows came from one game and were strongly correlated.

- 10 resolved projections;
- 8 research selections, 5 wins and 3 losses;
- hypothetical flat-stake result: +1.4576 units;
- points MAE 5.595 and RMSE 6.7628;
- over-probability Brier score 0.2301 versus no-vig market Brier 0.2499;
- line-threshold MAE 5.70;
- 6 of 10 actual results inside the model's 10th–90th percentile interval;
- minutes MAE 3.894;
- actual-minus-projected point bias +1.285 and minute bias +1.024;
- minutes-error/points-error correlation 0.439.

These results prove that capture, grading, price settlement, and metrics can work. They do not establish predictive edge.

## Critical blocker found August 10

Status update August 28: superseded — see the August 28 evidence note and `docs/v1_evaluation.md`. Preserved below for the historical record.

The Windows shadow collector has created **zero** snapshots since deployment.

`WNBA Shadow Capture` runs hourly from 9 a.m. through 11 p.m. and its latest task result often shows 0. However, every observed run that actually entered the 20–90 minute game window aborted at the first progress message, for example:

```text
Shadow capture failed: Loading shadow logs 1/11: Allisha Gray (ATL)
```

The likely root cause is already visible in the code: `run_projection_shadow.py` intentionally writes player-loading progress to stderr, while `scripts/run_wnba_shadow_capture_task.ps1` invokes Python with `2>&1` under `$ErrorActionPreference = "Stop"`. Windows PowerShell can promote native stderr into a terminating `NativeCommandError`. Production previously solved this by capturing native stdout/stderr through `Start-Process` temp files.

Why the dashboard was misleading: a failed eligible-window run was followed by later hourly runs with no eligible games; those returned 0 and replaced Task Scheduler's visible `LastTaskResult`. The grader then also returned 0 because no snapshots existed. Task status alone therefore looked healthy.

The next agent should reproduce and fix this in the isolated shadow wrapper only, add a regression/smoke check for native stderr handling, deploy only to `wnba_props_shadow`, and verify an actual snapshot artifact plus registry entry. Do not modify `run_nightly.py`, production output/Discord code, `WNBA Props Daily`, or production scheduler files as part of that repair.

## Evaluation gate

Strict evidence admits only captures made 20–90 minutes before tip. The rollup stays `COLLECTING` until it has at least:

- 7 distinct slates;
- 20 distinct games;
- 100 strict pregame projections;
- 90% both-side price coverage.

`READY_FOR_REVIEW` means enough data to inspect, not that the model has betting edge. Given correlation and calibration uncertainty, expect to continue beyond the minimum gate—preferably 20–30 slates and several hundred projections—before making a serious model comparison.

## Immediate sequence

1. Preserve and record local/Windows Git state.
2. Repair the isolated shadow PowerShell capture wrapper using the proven production subprocess pattern or another tested native-process capture mechanism.
3. Test the wrapper with a command that writes normal progress to stderr and still exits 0.
4. Run the full local shadow/unit test suite.
5. Deploy only the shadow files to the shadow checkout/branch.
6. During a real 20–90 minute window, verify a new `shadow_projection_*.json`, a capture-registry entry, nonzero projection counts, price coverage, and an honest task exit code.
7. After games finalize, verify grading and rollup artifacts and all unresolved/DNP/price-settlement cases.
8. Let the frozen v1 collect without tuning until the evidence gate is met.

## Guardrails

- Research output is not a recommendation and must not enter Discord.
- Never use post-tip data as prospective evidence.
- Never tune v1 from the one-game smoke result or from partial forward results.
- Keep raw captures immutable; version future model changes and evaluate them on later dates.
- Report by slate and game as well as by player row because props from the same game are correlated.
- Confirm artifacts and log counters; `Ready` and `LastTaskResult: 0` are insufficient operational proof.
- Preserve production and unrelated dirty work.

## Read next

- `docs/project_history_and_lessons.md` for the story of successes, failures, and decisions.
- `docs/research_model_roadmap.md` for the theory, evaluation plan, and open questions.
- `docs/shadow_projection_v1.md` for commands and implementation behavior.
- `docs/new_agent_prompt.md` for a copy-ready continuation prompt.

