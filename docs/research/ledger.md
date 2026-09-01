# Research hypothesis ledger

One row per hypothesis. Rules:

1. Every hypothesis is written down with its protocol and metric **before**
   outcomes are computed.
2. Experiments run through `research/walkforward.py` (walk-forward by date;
   any fitted parameter is fit only on the training period).
3. Nothing is promoted to production or shadow on the basis of a ledger
   experiment alone. Ledger results only decide what becomes a frozen
   candidate for prospective shadow collection.
4. The August holdout (Gate G1) may not be re-cut to tune any entry.

Status values: `proposed` -> `running` -> `evaluated` -> `frozen-candidate` /
`rejected` / `needs-data`.

## H1 — Market blend (shrink model probability toward no-vig)

- Rationale: shadow v1 and v2 both trail the no-vig market on Brier; the
  cheapest probability upgrade is shrinking toward the market.
- Protocol: needs archived v1/v2 rows with both-side prices (not present in
  the local checkout — blocked on the `v1_final_20260824` archive).
- Metric: paired Brier diff vs no-vig baseline, walk-forward λ fit on prior
  slates only; slate-clustered CI.
- Status: `needs-data`.

## H2 — Minutes/opportunity model

- Rationale: formal v1 residuals blamed minutes most (minutes MAE 4.45);
  minutes drive both the mean and the uncertainty of player outcomes.
- Protocol (offline diagnostic, this break): compare next-game minutes
  predictors walk-forward — L5 mean vs recency-weighted mean (half-life 6)
  vs season mean; blend weight fit on the training period only. Clustered
  MAE diff CI by game date.
- Data: cached 2026 gamelogs (151 players, through Aug 28).
- Status: `evaluated` 2026-08-31 (`outputs/research/walkforward_20260831.txt`):
  recency-weighted minutes (half-life 6) MAE **4.375** vs L5 mean **4.480**
  vs season mean **4.642** on 996 walk-forward player-games (eval Aug 1–28).
  Recency weighting wins; supports keeping it in v2/v3 minutes work.

## H3 — Points via minutes x rate

- Rationale: separate opportunity from production rate; test whether the
  v1-style structure beats direct points averages offline before investing
  in a fuller v3.
- Protocol: points target; baselines = L5 mean, season mean; challenger =
  minutes blend x ppm blend with train-period-fitted weights.
- Status: `evaluated` 2026-08-31 (`outputs/research/walkforward_20260831.txt`):
  minutes x rate blend (w=0.30 fit on train) MAE **4.500** vs points L5
  **4.791** vs season mean **4.550**. The structure beats the hot-streak
  baseline; it is NOT yet comparable to the sportsbook line (no line data
  for the full player universe). Market comparison must happen in shadow.

## H4 — DNP / zero-inflation mass

- Rationale: both v1 and production treat every row as a played game;
  zero-inflated outcomes widen tails and shift under-probabilities.
- Protocol: infer team games from the pooled cache; measure next-team-game
  played rate conditional on recent availability; report by bucket.
- Status: `evaluated` 2026-08-31 (`outputs/research/walkforward_20260831.txt`):
  regulars (10 of last 10 rows) played **95.4%** of their team's next games
  (958 opportunities) — a ~4.6% absence mass that the current simulation
  ignores entirely. Low-availability buckets had too few opportunities to
  measure. Zero-inflation belongs in the v3 distribution.

## H5 — Score inversion (production holdout observation)

- Observation: in the Aug 3–30 holdout, flat-stake ROI fell monotonically
  with candidate score (8: +18.3%, 9: -27.7%, 10: -35.7%, 11: -64.6%).
  Hot-streak persistence may be priced into lines/juice.
- Constraint: this is NOT a tuning signal; the Discord policy stays as-is
  per the holdout protocol. Recorded as a prospective hypothesis: instrument
  score-band hit rates when games resume; revisit only with fresh evidence.
- Status: `proposed` (prospective instrumentation only).

## H6 — Suppression inversion (production holdout observation)

- Observation: suppressed `SEASON-`/`TEAM_OUT` rows hit 81.25% (+51.5% ROI,
  n=16) in August, the opposite sign of the July sample (29.4%). Both are
  small and inconsistent — almost certainly noise.
- Constraint: do not flip the suppression policy. Continue logging
  suppressed-group outcomes; re-examine at n >= 100 with clustered CIs.
- Status: `proposed` (prospective instrumentation only).

## H7 — WNBA-scale total context

- Implemented this break: `HIGH_TOT`/`LOW_TOT` thresholds moved from the
  NBA-inherited 218/232 to WNBA-scale 172/156 (anchored to the frozen v1
  league-total baseline 164 +/- 5%), env-overridable
  (`TOTAL_CONTEXT_HIGH` / `TOTAL_CONTEXT_LOW`). Flag outcomes should be
  re-measured prospectively; do not re-tune thresholds on holdout outcomes.
- Status: `frozen-candidate` (behavioral fix, no accuracy claim).

## H8 — Opponent allowance table

- Rationale: production opponent averages come only from tonight's loaded
  player universe and are minutes/role unadjusted; `MATCHUP_PLUS`
  underperformed in July history.
- Protocol: needs a point-in-time league-wide gamelog table (all players,
  all games, pre-date). Blocked on a bulk backfill of 2026 gamelogs.
- Status: `needs-data`.
