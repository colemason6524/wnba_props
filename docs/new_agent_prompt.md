# Copy-ready prompt for the next agent

Use the text below to start the next conversation.

---

I am continuing a WNBA props/forecast project in `/Users/colemason/Documents/wnba_props`. Do not assume the previous conversation is available.

First read these files in order:

1. `docs/current_handoff.md`
2. `docs/NEXT_CHECKIN.md`
3. `README.md`
4. `docs/research_model_roadmap.md`

Topology: the Mac is the working copy; the only always-on runner is the Linux Azure VM at `azureuser@130.131.0.6` (`~/wnba_props` on `main`), scheduled with systemd user timers. There is no Windows deployment and no macOS scheduler — both were retired and their wrappers removed. Inspect the VM directly with:

```bash
ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6
```

Production is the prediction-first forecast board (`run_forecast_pipeline.py`): it projects ML/spread/total and PTS/REB/AST/3PM before prices, loads frozen artifacts from `wnba_props/artifacts/`, applies game-environment, role/status DNP-risk, and G/F/C positional-defense adjustments, enforces fail-closed coverage gates, writes a superseding ROI ledger, and posts split team/player Discord boards when configured. The daily grader posts split team/player recaps. The legacy heuristic screener (`run_nightly.py`) and the shadow challenger are retired and unscheduled. Do not re-enable them.

Current operating plan: let the current frozen model run for the rest of the regular season as a live paper-betting experiment. Do not refit or change model logic before the first 5-7 slate review unless the pipeline is broken. Review health and grading daily, then iterate in versioned batches with offline comparison and prospective evaluation. Bovada is preferred when reachable but has been intermittent from the Azure VM; Polymarket is the fallback reference source and Discord labels it when used.

Before any code edits, tell me what you verified on the VM, what is broken, the exact fix you propose, and how you will prove the pipeline and ledger stayed correct. Verify artifacts, provenance, `health` status, and board/ledger contents — not just timer status. If you find documentation drift, update `docs/current_handoff.md` with a dated note.

---
