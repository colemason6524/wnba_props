# wnba_props — NEXT_CHECKIN

Last written: 2026-09-23 ET. Canonical detail: `docs/current_handoff.md`.

## Where we are

| Surface | Path | HEAD / branch |
| --- | --- | --- |
| Mac primary | `/Users/colemason/Documents/wnba_props` | `main` (working copy + bulk store; no scheduled jobs) |
| Azure VM (only runner) | `~/wnba_props` | `main` (systemd: forecast weekend 12:36, daily 18:45, grade 06:17 ET) |

SSH: `ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6`.
Pull VM outputs: `scripts/sync_from_vm.sh` (history, health, logs, boards, ledger, grades).

There is no Windows deployment and no macOS scheduler; both were retired and their wrappers removed.

**Production system:** prediction-first forecast board (`run_forecast_pipeline.py`).
Legacy heuristic screener (`run_nightly.py`) and the shadow challenger are
retired/unscheduled.

The regular season resumes **Sep 17**. The next objective is prospective
paper-betting measurement, not another pre-opening model change.

## Do not redo

- Do not re-enable legacy timers (`sports-wnba-daily`, `...shadow-capture`,
  `...shadow-grade`) or recreate Windows/macOS task wrappers.
- Do not schedule anything on the Mac (no launchd).
- Do not mix the shadow worktree into the live VM checkout.
- Do not refit production artifacts after every slate; measure the frozen
  version first.

## Next pop-backs

1. **Last regular-season slate** — confirm `health=ok`, snapshot IDs, rich
   ledger fields, and that pregame/evening captures are distinct.
2. **Before playoffs** — set `WNBA_SEASON_PHASE=playoff`; enable the pregame
   timer; run `scripts/reconcile_forecast_ledger.py` against the regular-season
   boards and archive the reconciliation output.
3. **Playoff game day** — verify every scheduled game is captured before tip and
   that player logs include the newest playoff game.
4. **Morning after** — confirm the 06:17 grader settles the slate, writes
   `phase_summary.json`, and posts playoff-labeled recaps. Then run
   `scripts/sync_from_vm.sh`.
5. **After 5-7 playoff slates** — compare minutes error, PTS/3PM calibration,
   EV-selection results, and market blend; do not update on the same sample.
6. **Season end** — disable all four forecast timers; re-enable next season.

## When Cole says "get to work"

Read `docs/current_handoff.md` → verify VM HEAD (`git rev-parse HEAD` over ssh)
→ `scripts/sync_from_vm.sh` → check the latest board/grade → only then OpenCode.

## Historical gate (legacy screener)

Aug 3-30 Discord-policy holdout: **53-47-2, -11.15u, ROI -10.94%** (no edge).
Recorded for history; the screen policy is retired and not tuned.
