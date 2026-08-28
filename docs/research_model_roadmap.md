# Projection research roadmap

## Objective

Estimate the distribution of a player's result in the upcoming game and compare it with the offered line and price. The goal is not to find who has been hot; it is to determine whether the market-implied probability differs materially from a point-in-time model probability after accounting for uncertainty.

## Conceptual model

```text
availability and rotation
          -> expected minutes distribution

player skill + role + matchup + game environment
          -> per-minute production distribution

minutes x per-minute production + covariance/tails
          -> player outcome distribution

outcome distribution vs line and price
          -> calibrated probability, fair price, and research EV
```

The current v1 implements a simplified form of this for points. It is a challenger baseline, not the endpoint.

## Frozen v1 question

Can a simple, leakage-safe minutes-times-rate simulation produce better calibrated PTS probabilities and point estimates than the offered line/no-vig market across prospectively captured WNBA slates?

Do not change v1 while answering that question. Any meaningful feature, weighting, distribution, or selection change becomes a new model version and must be evaluated on later captures.

## Metrics that matter

- point estimate: MAE, RMSE, bias, and error by minutes/role/context;
- probability: Brier score, calibration buckets, and interval coverage;
- market comparison: absolute error of the line and Brier score of the no-vig market probability;
- operations: eligible games, capture timing, projection coverage, both-side price coverage, unresolved/DNP rates;
- decision layer: selected-side results and flat-stake units at the captured price, always labeled hypothetical;
- dependence: slate-level and game-level summaries, not only row-level totals.

Accuracy alone is not enough. A model can have lower MAE but poor probability calibration, or good calibration without enough edge to overcome price. Units alone are also not enough because a small or correlated sample is volatile.

## Evidence protocol

1. Capture before tip, using only information available at capture time.
2. Save model version, source, collected timestamp, line, both prices where available, game context, features, predictions, and uncertainty.
3. Never overwrite a capture.
4. Grade only after ESPN marks the game final.
5. Keep DNP, push, unresolved, missing-price, and missing-context outcomes explicit.
6. Review the frozen aggregate only after the minimum evidence gate.
7. Form v2 hypotheses from v1 residuals, implement them under a new version, and evaluate prospectively on future dates.

Minimum review gate: 7 slates, 20 games, 100 strict pregame projections, and at least 90% both-side price coverage. A stronger decision sample is 20–30 slates and several hundred projections, spanning teams, roles, injury environments, and game scripts.

## Likely v2 research areas

These are hypotheses to investigate after v1 evidence exists, not changes to make now.

- minutes/rotation: starter probability, bench/closing role, foul risk, injury-return restrictions, teammate availability, depth-chart competition;
- rate context: usage and shot volume, on/off or lineup effects, opponent positional defense, expected defensive assignment;
- game environment: pace, possession count, team total, spread/blowout tails, home/away and rest/travel;
- distribution quality: correlation between minutes and rate, skew/heavy tails, zero/DNP mass, empirical residual simulation rather than convenient independent normals;
- market timing: opener versus captured versus close, line movement, and whether the source is stale relative to the named book;
- calibration: shrinkage by role/sample size and recalibration learned only from prior dates.

## Outward review: what may be wrong or overlooked

### Data and provenance

- PlayerProps is a third-party representation of a named book. Treat it as a source claim until spot checks establish accuracy and latency.
- Cache timestamps are not automatically source timestamps. Record both when available.
- Parsed snapshot retention may not be enough to reproduce a source-shape bug; immutable raw payload retention is worth considering.
- Injuries describe availability but do not fully encode rotation consequences.

### Modeling

- Minutes and per-minute rate are not independent. Players often play more because they are performing well or because the game remains competitive.
- Recent games are selected by schedule and availability, not randomized; opponent and teammate context can distort apparent trends.
- Market lines are strong baselines. Beating a naive last-five rule is not the meaningful standard.
- The current shadow uses limited game context and does not yet simulate possessions, teams, or correlated players jointly.
- A single threshold per player ignores alternate lines and price/line tradeoffs.

### Evaluation

- Many props from one game share the same game script, so 100 rows are not 100 independent observations.
- Selecting only apparent positive EV creates selection bias in win-rate summaries; grade the full projected board too.
- Repeated feature experiments invite multiple-testing and overfitting. Maintain a hypothesis/version ledger.
- Closing-line value may be a useful diagnostic, but it is not currently captured and requires a trustworthy timestamped comparison source.
- Flat one-unit results are research summaries, not bankroll advice. No staking system should be designed before calibration and edge are established.

### Operations

- Interactive Windows tasks require the desktop to be powered on and the user logged in.
- Hourly scheduling provides theoretical coverage but does not guarantee capture after delay, network failure, missing lines, or task overlap.
- The capture registry records success per game/model/source; partial-game capture behavior should be tested so a weak first artifact does not suppress a better eligible retry.
- Monitoring should distinguish no games, outside window, no lines, partial projection coverage, process failure, and successful artifact creation.

## Promotion standard

No model should affect the daily system because it passed the collection gate or produced positive units in a short sample. Promotion requires, at minimum:

- stable prospective calibration and error metrics across multiple slates and teams;
- comparison with both line and no-vig market baselines;
- acceptable source and price coverage;
- robustness after excluding correlated games and high-leverage outliers;
- an untouched future holdout for the exact proposed model version;
- a separately approved integration plan with rollback and shadow parity.

