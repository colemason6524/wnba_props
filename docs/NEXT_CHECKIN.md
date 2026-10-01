# wnba_props — NEXT_CHECKIN

Last updated: 2026-09-30 ET. Canonical operational detail and historical
findings: `docs/current_handoff.md`.

## Where we are

| Surface | Path | State |
| --- | --- | --- |
| Mac primary | `/Users/colemason/Documents/wnba_props` | `main`; working copy and bulk store, no scheduled jobs |
| Azure VM (only runner) | `azureuser@130.131.0.6:~/wnba_props` | `main`; systemd user timers run forecast and grading |

SSH: `ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6`.
Pull VM outputs to the Mac with `scripts/sync_from_vm.sh` (history, health,
logs, forecast boards, ledger, grades; pull-only, secrets never move).

**Playoff phase is board-verified.** Sep. 29 was the first fully verified
playoff capture (pregame + evening, all rows phase=playoff, health=ok) and the
Sep. 30 morning grade settled it cleanly. Cumulative playoff after two slates
(Sep. 27 + Sep. 29): **124–123, −22.042u, −8.92%** (247 settled). Team markets
are running positive (+3.867u, 9–7) while player props bleed (−25.91u, −11.31%):
AST is the standout loser (−16.344u, −32.05% both-ish sided, clustering in
high-minutes guards), PTS/3PM carry the regular season's calibration warnings
forward, REB holds (+4.01u). The playable subset is 65–73, −11.137u. Treat all
of it as two-slate noise-level evidence: keep the model/config frozen and keep
measuring. Full numbers and the freshness-timeline analysis are in
`docs/current_handoff.md`.

Sep. 30 slate (WSH@ATL, GS@DAL) was captured twice (pregame 21:45Z / evening
22:45Z; 99 identities; 45/47 paper plays). Games were in progress at this
check-in; grading lands Oct. 1 at 06:17 ET.

## Next check-back

1. **Oct. 1 morning grade:** confirm the Sep. 30 slate settles (expect ~99
   identities from the evening capture; a pregame-only row grades from pregame
   prices). Verify the cumulative phase_summary splits grow consistently
   (team/player books), and watch whether the AST pattern continues on the
   third slate.
2. **Oct. 1 slate (single game LV@IND, 21:00 ET):** confirm pregame capture
   health and player-log freshness — IND/LV subjects must now include their
   Sep. 29 playoff games via the boxscore fallback; `latest_log_date` should be
   2026-09-29 for most subjects, and `stale_players` should shrink.
3. **Through the series:** rely on scheduled operations; check promptly if a
   timer fails, a board is missing/degraded, scheduled games are uncovered, or
   player logs go stale. Compare team/player and per-market results across
   multiple slates before any model change.

## Operating guardrails

- Do not re-enable legacy timers (`sports-wnba-daily`, `...shadow-capture`,
  `...shadow-grade`) or recreate Windows/macOS task wrappers.
- Do not schedule anything on the Mac (no launchd).
- Do not mix the shadow worktree into the live VM checkout.
- Preserve captured board snapshots and original grades for audit; use corrected
  phase/date reporting rather than overwriting historical artifacts.
- Do not refit production artifacts after every slate; evaluate the frozen
  version prospectively first.

## After several playoff slates

After 5–7 slates, compare minutes error, PTS/3PM calibration, EV-selection
results, and market-blend results, keeping team and player prop records
separate. Treat early results as noisy and do not tune on the same evaluation
sample. First follow-up questions queued from the Sep. 29/30 review: playoff
minutes distribution vs the regularization fit; assist-lineup variance in the
postseason; whether the 2-day staleness guard is the right cadence for
every-other-day rounds (see `docs/current_handoff.md`).

## When Cole says "get to work"

Read `docs/current_handoff.md` → verify VM HEAD (`git rev-parse HEAD` over SSH)
→ `scripts/sync_from_vm.sh` → inspect the latest board/grade → only then begin
implementation.

## Historical gate (legacy screener)

Aug. 3–30 Discord-policy holdout: **53-47-2, -11.15u, ROI -10.94%** (no edge).
Recorded for history; the screen policy is retired and not tuned.
