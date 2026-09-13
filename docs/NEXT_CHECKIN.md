# wnba_props — NEXT_CHECKIN

Last written: 2026-09-12 ET. Canonical detail: `docs/current_handoff.md`.

## Where we are

| Surface | Path | HEAD / branch |
| --- | --- | --- |
| Mac primary | `/Users/colemason/Documents/wnba_props` | `main` (working copy + bulk store; no scheduled jobs) |
| Azure VM (only runner) | `~/wnba_props` | `main` (systemd: forecast weekend 12:36, daily 18:45, grade 06:17 ET) |

SSH: `ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6`.
Pull VM outputs: `scripts/sync_from_vm.sh` (history, health, logs, boards, ledger, grades).

There is no Windows or macOS scheduler; both were retired and their wrappers removed.

**Production system:** prediction-first forecast board (`run_forecast_pipeline.py`).
Legacy heuristic screener (`run_nightly.py`) and the shadow challenger are
retired/unscheduled.

Calendar: World Cup pause through Sep 16; regular season resumes **Sep 17**;
playoffs ~**Sep 27**.

## Do not redo

- Do not re-enable legacy timers (`sports-wnba-daily`, `...shadow-capture`,
  `...shadow-grade`) or recreate Windows/macOS task wrappers.
- Do not schedule anything on the Mac (no launchd).
- Do not mix the shadow worktree into the live VM checkout.
- Do not refit production artifacts after every slate; measure the frozen
  version first.

## Next pop-backs

1. **Wed Sep 17 after the evening run** — first live forecast board after
   resume. Confirm `health=ok`, nonzero priced rows, Discord delivery, and
   which game-price source was used. Then `scripts/sync_from_vm.sh`.
2. **Daily** — read the 06:17 grading recap; watch for DEGRADED alerts instead
   of boards.
3. **Weekly** — calibration and ROI by market; track `health=ok` rate and
   source-staleness counts.
4. **Season end (~mid-Oct)** — `systemctl --user disable --now` the three
   forecast timers; re-enable next season. Unit copies in `scripts/systemd/`.

## When Cole says "get to work"

Read `docs/current_handoff.md` → verify VM HEAD (`git rev-parse HEAD` over ssh)
→ `scripts/sync_from_vm.sh` → check the latest board/grade → only then OpenCode.

## Historical gate (legacy screener)

Aug 3-30 Discord-policy holdout: **53-47-2, -11.15u, ROI -10.94%** (no edge).
Recorded for history; the screen policy is retired and not tuned.
