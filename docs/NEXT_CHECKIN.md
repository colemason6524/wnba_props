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

**Gate G1 (done):** Aug 3–30 Discord policy holdout 53-49, ROI −10.94%, **no edge**. Policy unchanged. Do not recut. H5/H6 are prospective only.

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
