# WNBA points projection shadow v1 — formal evaluation

Decision date: August 24, 2026 (archive `v1_final_20260824`).

## Decision

**REJECT v1 for production promotion.** v1 remains `RESEARCH_ONLY` and is never delivered to Discord. A minor positive flat-stake ROI is noted but is not evidence of edge given the model trails both predictive baselines and its uncertainty is under-dispersed.

## Immutable evaluation cutoff

- Collection ended at the August 23 slate; both shadow tasks disabled on August 24.
- Frozen collector commit: `3184273` (`codex/wnba-shadow-collection`)
- Model: `wnba-points-shadow-v1`
- Config hash: `b3096ccb93b6f4d1`
- Source snapshots are immutable and archived; the official rollup is `shadow_rollup_20260825T032143Z.json`.
- Audit-aware tooling deployed at Windows commit `07c13d8` (backed up to GitHub `codex/wnba-shadow-audit-deploy`).

## Evidence gate

The audit-aware rollup admits only clean primary rows (single commit, `code_dirty: false`, canonical config).

| Metric | Clean v1 | Target |
|---|---:|---:|
| Slates | 8 | 7 |
| Games | 22 | 20 |
| Strict pregame projections | 209 | 100 |
| Both-side price coverage | 100.00% | 90% |
| Model identity | 1 (@ 3184273) | 1 |
| Gate status | READY_FOR_REVIEW | — |

Excluded from primary evidence: 83 `code_dirty` rows (audit taint Aug 18–20) + 29 `code_state_missing` rows (pre-freeze diagnostic) = 112.

## Headline metrics

- Model points MAE: `4.8462`
- Sportsbook-line MAE: `4.7632` — model trails the line
- Model over Brier: `0.2529`
- Market no-vig over Brier: `0.2496` — model probabilities trail the market
- 10th–90th interval coverage: `71.29%` (nominal 80%) — uncertainty too narrow
- Minutes MAE: `4.4475`; points MAE: `4.8462`
- Selections: `162` (OVER 92, UNDER 70)
- Record: `88-74` (121 wins / 110 losses incl. pushes=0); hit rate `52.4%`
- Flat-stake units: `+3.2494`
- Flat-stake ROI: `+2.01%`

## Side split

| Side | n | W-L | Units | Brier |
|---|---:|---:|---:|---:|
| OVER | 92 | 46-46 | -5.431 | 0.2687 |
| UNDER | 70 | 42-28 | +8.681 | 0.2337 |

Profit came almost entirely from UNDERs. OVER is a losing side at nearly 50/50, i.e., no directional edge.

## Confidence buckets (|model over prob - 0.5|)

| Bucket | n | Decided | Win rate | Units |
|---|---:|---:|---:|---:|
| 0.00-0.05 | 71 | 28 | 0.464 | -2.759 |
| 0.05-0.10 | 59 | 55 | 0.527 | -1.137 |
| 0.10-0.15 | 41 | 41 | 0.585 | +3.843 |
| 0.15-0.20 | 16 | 16 | 0.562 | +0.842 |
| 0.20-1.00 | 22 | 22 | 0.591 | +2.461 |

Higher-confidence buckets are small-sample and their profit is not distinguishable from noise.

## Projected vs line edge buckets (abs diff)

| Bucket | n | Decided | Win rate | Units |
|---|---:|---:|---:|---:|
| 0-1 | 106 | 59 | 0.525 | -0.358 |
| 1-2 | 68 | 68 | 0.500 | -4.297 |
| 2-3 | 23 | 23 | 0.739 | +8.310 |
| 3+ | 12 | 12 | 0.500 | -0.405 |

The profitable 2-3 bucket has only 23 rows — insufficient to claim a stable edge.

## Calibration (over probability)

| Bucket | Pred | Actual | n |
|---|---:|---:|---:|
| 0.0-0.1 | 0.072 | 0.000 | 1 |
| 0.1-0.2 | 0.188 | 0.000 | 2 |
| 0.2-0.3 | 0.261 | 0.400 | 10 |
| 0.3-0.4 | 0.364 | 0.318 | 22 |
| 0.4-0.5 | 0.454 | 0.483 | 58 |
| 0.5-0.6 | 0.550 | 0.472 | 72 |
| 0.6-0.7 | 0.639 | 0.514 | 35 |
| 0.7-0.8 | 0.732 | 0.500 | 8 |
| 0.8-0.9 | 0.887 | 0.000 | 1 |

Calibration degrades above 0.5: the model overstates over probability in the 0.5-0.7 range. This is the core reason Brier trails the market.

## By slate

| Slate | n | Games | MAE | Brier | ROI | Win rate |
|---|---:|---:|---:|---:|---:|---:|
| 08-13 | 30 | 3 | 6.6213 | 0.2801 | -0.3954 | 0.3182 |
| 08-14 | 20 | 2 | 4.3685 | 0.2338 | +0.1175 | 0.6000 |
| 08-15 | 29 | 3 | 4.2234 | 0.3046 | -0.5056 | 0.2632 |
| 08-16 | 28 | 3 | 4.7179 | 0.2429 | +0.1279 | 0.6087 |
| 08-17 | 10 | 1 | 3.6770 | 0.2549 | +0.3466 | 0.7143 |
| 08-21 | 26 | 3 | 5.3612 | 0.2511 | +0.1429 | 0.5909 |
| 08-22 | 30 | 3 | 3.6040 | 0.2349 | +0.1181 | 0.6000 |
| 08-23 | 36 | 4 | 5.2219 | 0.2229 | +0.2872 | 0.6897 |

Slate variance is large (ROI from -0.51 to +0.35); the aggregate +2.01% is within sampling noise.

## Why reject despite +2.01% ROI

1. The model's point projection is slightly worse than the sportsbook line (MAE 4.85 vs 4.76).
2. The model's probabilities are worse than the market's no-vig probabilities (Brier 0.253 vs 0.250).
3. Uncertainty is under-dispersed: 71% vs 80% nominal 10th-90th coverage.
4. Calibration is miscalibrated exactly in the 50-70% region where edges would be found.
5. The positive ROI is small (+2.01% over 162 bets, ~+3.25 units), concentrated in UNDERs and thin high-confidence buckets, and varies wildly by slate.
6. There is no evidence the model identifies prices with positive expected value that the market has missed.

## Record-keeping

- The `+2.01%` ROI is documented here as a minor positive but explicitly not treated as evidence of edge.
- All artifacts archived with SHA-256 manifest (`manifest.json`, 165 files).
- No artifacts were deleted or rewritten.
- v1 selections were never sent to Discord and production (`WNBA Props Daily`) was never modified.
- v2 will use a new model version, new config hash, and a fresh evidence gate; v1 data is development evidence only.

## Next steps

1. Begin v2 (`wnba-points-shadow-v2`) with a new identity and empty gate.
2. Prioritize minutes/opportunity forecasting, wider uncertainty, and probability calibration.
3. Re-enable shadow capture and grading only after v2 code, tests, and configuration are frozen.
