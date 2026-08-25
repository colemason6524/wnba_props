# WNBA points projection shadow v2

Research-only successor to the rejected v1. It never touches the production
screener, `run_nightly.py`, Discord delivery, or any scheduled production task.

## Frozen identity

- Model: `wnba-points-shadow-v2`
- Config hash: derived from `config_signature` over every `ProjectionConfig`
  field (canonical, includes `calibration_lambda`)
- Residual artifact: `config/shadow_v2_calibration.json`
  - sha256: `da5052abf0b7542c8e023f45e8d0d6af2daf64109c5529226a9e767746d88344`
  - 209 centered `(minutes_z, rate_error)` pairs sampled jointly
  - trained only on the clean v1 cohort (8 slates, 22 games, 209 primary rows)
  - source: `wnba-points-shadow-v1` @ `3184273`, config `b3096ccb93b6f4d1`
  - training-row digest: `projection_ids_sha256 = 68f9b2a8f17f0c11…`

The collector fails fast when the artifact is missing or invalid, and records
the artifact id and hash in every snapshot and health record.

## What changed from v1

1. **Joint residual simulation.** The independent truncated-normal minutes/rate
   draws are replaced by sampling one *pair* `(minutes_z, rate_error)` from the
   frozen artifact, preserving observed minutes/rate error dependence. The
   artificial 40-minute cap is removed; overtime tails are representable.
2. **Optional symmetric calibration.** `calibration_lambda` shrinks conditional
   over probability toward 0.5 (`0.5 + λ(p−0.5)`). For the frozen launch it is
   **off** (`None`): leave-one-slate-out fitting chose a tiny λ whose OOF Brier
   was worse than no shrinkage.
3. **Price-gated selection.** A side is selected only when both prices are
   valid, its expected value is strictly positive, and it strictly beats the
   other side's EV. Incomplete pricing and exact EV ties are `PASS`. The v1
   unpriced 0.55 fallback is gone.

Unchanged from v1: central minutes formula (65/35 recent/season blend), PPM
recency blend, game-total elasticity, blowout adjustment, self availability
exclusion/uncertainty handling, deterministic per-row seeding (now including
config signature and artifact hash), 10,000 simulations, strict 20–90 minute
pregame capture window.

## Why C1 was frozen despite an MAE near-miss

Leave-one-slate-out results against the official clean v1 metrics:

| Candidate | MAE pts | Brier | Coverage |
|---|---:|---:|---:|
| v1 official | 4.8462 | 0.2529 | 71.29% |
| C1 joint residual | 4.8688 | 0.2516 | 80.86% |
| C1 + λ=0.1 | 4.8811 | 0.2535 | 80.38% |

C1 decisively fixes the two failure modes that drove the v1 rejection
(under-dispersion and probability quality) while point MAE moved 0.02 points,
inside simulation noise. Per the predeclared ambiguity fallback, the
distribution-only candidate was frozen with the exception recorded in the
artifact's `selection_rule` field. λ remains available but stays off unless
future evidence supports it. ROI played no part in this choice.

## Runtime behavior

```bash
python3 run_projection_shadow.py            # uses config/shadow_v2_calibration.json
python3 grade_projection_shadow.py --all-pending
python3 shadow_rollup.py --model-version wnba-points-shadow-v2
```

- Missing/invalid artifact → run exits non-zero before any capture.
- Snapshots record a `residual_model` block (id, schema, row count, sha256,
  source identity, training-row digest) plus per-projection raw vs calibrated
  probabilities, normalized availability status, and injury-source health.
- Grade reports carry both `over_brier_score` (primary) and
  `over_brier_score_raw`.
- `shadow_rollup.py --model-version wnba-points-shadow-v2` keeps archived v1
  reports from forcing `MIXED_MODELS`; the v2 gate starts empty.
- Registry scope already keys on model version, so v2 collection starts fresh
  without touching v1 history.

## Evidence gates

Collection review (unchanged thresholds): 7 slates, 20 games, 100 strict
pregame projections, ≥90% both-side price coverage, one clean identity →
`READY_FOR_REVIEW`.

Promotion consideration additionally requires prospective (not development)
evidence: MAE better than the line, Brier better than the no-vig market,
10th–90th coverage inside a predeclared 75–85% band, gains not concentrated in
one side or a few games, and a larger sample (~20 slates / ~50 games /
~300 projections). Positive ROI alone never promotes.

## Non-goals for v2.x

No UNDER-specific rule, no threshold tuned on v1 ROI or the profitable 2–3
point bucket, no per-player/side calibration, no lineup/matchup/pace/rest
features without new point-in-time instrumentation, no other prop types, no
staking, no Discord delivery, no changes to production code paths, and no
retroactive edits to immutable v1 artifacts.

## Offline tooling

`research/evaluate_shadow_v2.py` (runs against the archived artifacts, stdlib
only) rebuilds the exact 209-row dataset, replays the candidate ladder under
leave-one-slate-out validation, applies the predeclared selection rules, and
emits the frozen artifact. Deterministic seeds make every reported number
reproducible.
