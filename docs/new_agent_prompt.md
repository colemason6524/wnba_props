# Copy-ready prompt for the next agent

Use the text below to start the next conversation.

---

I am continuing a WNBA props/forecast project in `/Users/colemason/Documents/wnba_props`. Do not assume the previous conversation is available.

First read these files in order:

1. `docs/current_handoff.md`
2. `docs/NEXT_CHECKIN.md`
3. `README.md`
4. `docs/research_model_roadmap.md`

Topology: the Mac is the working copy; the only always-on runner is the Linux Azure VM at `azureuser@130.131.0.6` (`~/wnba_props` on `main`), scheduled with systemd user timers. There is no Windows or macOS scheduler — those were retired and their wrappers removed. Inspect the VM directly with:

```bash
ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6
```

Production is the prediction-first forecast board (`run_forecast_pipeline.py`): it projects ML/spread/total and PTS/REB/AST/3PM before prices, loads frozen artifacts from `wnba_props/artifacts/`, enforces fail-closed coverage gates, writes a superseding ROI ledger, and optionally posts to Discord. The legacy heuristic screener (`run_nightly.py`) and the shadow challenger are retired and unscheduled. Do not re-enable them.

Current operating plan: let the frozen model run for the rest of the regular season as a live paper-betting experiment. Do not refit after every slate; review weekly. Iterate in versioned batches (one change, refit, offline compare, deploy only after confirming no leakage/regression). Bovada is primary but blocked from the Azure IP, so the VM currently uses Polymarket as the game-price source; Discord labels this.

Before any code edits, tell me what you verified on the VM, what is broken, the exact fix you propose, and how you will prove the pipeline and ledger stayed correct. Verify artifacts, provenance, `health` status, and board/ledger contents — not just timer status. If you find documentation drift, update `docs/current_handoff.md` with a dated note.

---
