# wnba_props — NEXT_CHECKIN

Last written: 2026-09-08 ET. Canonical detail: `docs/current_handoff.md` + `docs/research/ledger.md`.

## Where we are

| Surface | Path | HEAD / branch |
| --- | --- | --- |
| Mac primary | `/Users/colemason/Documents/wnba_props` | `main` (working copy + bulk store; no scheduled jobs) |
| Azure VM live | `~/wnba_props` | `main` (systemd: daily 10:56 ET, shadow capture hourly 10–22 ET, shadow grade 06:17 ET) |
| Azure VM shadow | `~/wnba_props_shadow` | frozen `codex/wnba-shadow-v2` worktree — **do not mix with live** |
| Windows | `C:\Users\muski\wnba_props*` | **RETIRED Sep 2026 — do not use** |

SSH: `ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6`.
Pull VM outputs: `scripts/sync_from_vm.sh` (history, health, logs, shadow).

**Gate G1 (done):** Aug 3–30 Discord policy holdout **53-47-2** (W-L-void), −11.15u, ROI −10.94%, **no edge**. Policy unchanged. Do not recut. H5/H6 are prospective only. (The earlier "53-49" was a void-accounting artifact — see 2026-09-08 entry below.)

Calendar: World Cup pause **through Sep 16**; resume **Sep 17**; playoffs **Sep 27**.

## Do not redo

- Do not retune Discord on holdout / SAMPLE_VOL / minutes-leash UNDER (already missed; n=14 leash too small to flip).
- Do not mix the shadow worktree into the live VM checkout.
- Do not promote shadow v1/v2 without a new prospective gate.
- Do not schedule anything on the Mac (no launchd) or reanimate Windows tasks.

## Next pop-backs

1. **Wed Sep 17 after 11:00 ET** — first live board after resume. Confirm players load (staleness 21d), Discord delivery artifact, no SEASON-/stale mass-skip. Then `scripts/sync_from_vm.sh`.
2. **Playoffs ~Sep 27** — confirm WNBA playoff window `{9,10}` behavior.
3. **OpenCode edge hunt (small, after resume):** instrument score-band hit rates (H5) on **new** slates only; OR continue minutes×rate / DNP zero-inflation toward a frozen v3 candidate via `research/walkforward.py` — one ledger experiment, predeclared.
4. **Season end (~mid-Oct, after Finals):** `systemctl --user disable --now` the three WNBA timers on the VM; re-enable next season. Unit copies live in `scripts/systemd/`.

## When Cole says "get to work"

If before Sep 17: leave live alone; only shadow/research if explicitly assigned. If on/after Sep 17: read this + `current_handoff.md` → verify VM live HEAD (`git rev-parse HEAD` over ssh) → `scripts/sync_from_vm.sh` → check that day's board → only then OpenCode.

## Tonight stamp (2026-09-08)

Topology cutover done: Windows retired, Azure VM primary (timers enabled, webhook configured, v2 shadow worktree deployed), Mac holds bulk. Still pause through Sep 16; next real look Sep 17 11:00 ET.

## 2026-09-08 — holdout void-accounting fix (reporting only)

- `outputs/hunt/grade_holdout.py` counted the 2 void (DNP) rows as losses in W-L and in the hit-rate denominator (voids had `profit_units = 0.0` so they entered the priced set, and anything with `outcome != "win"` was a loss). Units/ROI were already correct (0u per void).
- Fixed: new pure `tally_record()` reports W-L-push-void separately; hit rate = wins / (wins + losses). By-side / by-prop / by-price / by-score / per-slate tables use the same tally. `dnp_as_loss` sensitivity still treats voids as losses (consistent with its −1u).
- Corrected primary line: **53-47-2, −11.15u, ROI −10.94%** (CI95 −26.80%..+7.29%), hit 53.00% (was 51.96%). Verdict unchanged: NOT ESTABLISHED. New report `outputs/hunt/holdout_report_20260908T232616Z.txt` (old 20260901 reports left as-is).
- Production paths checked: `wnba_props/shadow/grading.py` and `backtest.py` already keep voids out of W-L — no change needed there.
- Test: `tests/test_grade_holdout_voids.py`. The grader is force-added from the otherwise-ignored `outputs/hunt/` so the test runs on the VM.
