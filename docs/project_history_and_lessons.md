# Project history and lessons

This document records why the project looks the way it does. It includes failures because many of the current safeguards came directly from them.

## 1. Establishing a separate WNBA system

The project was created as an independent WNBA counterpart to prior NBA/MLB props work. Concepts were reused, but data sources, aliases, thresholds, webhook configuration, caches, and deployment remained WNBA-specific. That separation has been successful: investigation and research can proceed without coupling this system to another sport.

The production workflow became operational on Windows with ESPN for the slate/context, PlayerProps.ai for no-key lines, Basketball-Reference plus ESPN fallbacks for player logs, local history exports, and optional Discord delivery.

## 2. Learning that source health is part of the model

Direct FanDuel and DraftKings collection was unreliable in automated/headless contexts because of bot protection and unavailable payloads. PropCruncher rankings were not a reliable substitute for executable sportsbook lines. The practical free source became PlayerProps.ai's book-labeled feed, with manual CSV as the clean fallback.

That solved the paid-API concern for the current phase. It did not prove that every label and price exactly matches the sportsbook. Point-in-time spot checks remain necessary.

One production failure appeared as “no supported prop lines” even though the feed was populated. PlayerProps used team codes such as `LVA` and `NYL`, while ESPN used `LV` and `NY`. Adding source-boundary aliases and regression tests fixed the matching failure. The broader lesson is to separate:

- source loaded;
- events matched;
- prop rows parsed;
- players loaded;
- lines evaluated;
- candidates qualified;
- candidates displayed or notified.

A zero at one layer should never be described as “the model found no plays” without verifying the earlier layers.

## 3. Learning that recent-data freshness is operational policy

After a league break, the daily system returned no useful board because every player's last game failed a freshness threshold. The data was not absent; the regular-season threshold was too strict for the WNBA calendar. The policy was changed to allow 14 days in the regular season while retaining a tighter two-day playoff default.

This was a success in diagnosis: previewing lines alone would not have found it. The full run counters and skip reasons exposed the real issue.

An unresolved caveat remains: playoff-window logic is not obviously consistent across `screener.py`, `run_nightly.py`, and `backtest.py`. Reconcile it before any seasonal policy change.

## 4. Separating research visibility from action visibility

The terminal board and Discord intentionally diverged. The terminal keeps more candidates visible for research; Discord uses a stricter score threshold and caps each side at five. `SEASON-` and `TEAM_OUT` candidates remain measurable in history but are suppressed from Discord.

This reduced notification noise without deleting evidence. The suppression policy was chosen from a small sample, however, so it is provisional rather than validated truth.

The July 31 retrospective board was encouraging—33 qualified candidates hit 63.6%, and 20 displayed candidates hit 70.0%—but it was one slate. It also showed why correlated same-game props and parlays can make returns look much more convincing than the underlying evidence. Candidate rows, games, and betting days must be reported separately.

## 5. Recognizing the limitation of “last five stayed hot”

The production screener is useful as a disciplined filter, but it is not a generative prediction of the next game. A high recent hit rate can be caused by changing minutes, injury-driven usage, opponent mix, overtime, blowouts, or a line that has not caught up—or it can simply be noise. The sportsbook can also price the hot streak into both the line and the juice.

This led to the current research direction: estimate the player's actual distribution for tonight, then compare that distribution with both the line and the price.

## 6. Building the isolated PTS shadow

Shadow v1 was deliberately narrow and isolated. It predicts minutes and points per minute, includes limited game-environment context, simulates a points distribution, captures prices, grades only finalized games, and produces multi-slate rollups. It has separate caches, outputs, checkout, branch, logs, and scheduled tasks. It does not touch production or Discord.

The first graded engineering slate proved the end-to-end analytics path, including DNP/unresolved handling and price settlement. Its 5–3 selection result and +1.4576 hypothetical units were interesting but unusable as edge evidence because the capture was post-tip and all rows came from one game.

The correct response was to freeze the model and demand prospective collection rather than tune it.

## 7. Deployment success and monitoring failure

The separate Windows checkout and two scheduled tasks were deployed successfully, and initial unit tests/task smoke tests passed. The operational design preserved production exactly as intended.

However, the smoke test happened when no game required player-log loading. In real capture windows, the PowerShell wrapper treated an ordinary Python stderr progress message as a terminating failure. Subsequent no-window runs exited 0, hiding the earlier failure in Task Scheduler. From August 5 through August 10 the shadow collected no Windows snapshots.

This is the most important current failure. It teaches three things:

1. An exit-code smoke outside the critical branch is not an end-to-end smoke.
2. Scheduled-task state and last result are not proof; verify artifact movement and counters.
3. A repeated scheduler can overwrite a meaningful failed status with a later no-op success, so logs or health summaries need to retain the last material attempt.

## 8. Current successes

- Production continues to run daily and export history.
- The free/no-key line-and-price path is adequate for research collection.
- Source matching and stale-log failures now have clearer diagnostics.
- Research candidates and Discord actions are separated.
- The shadow implementation, grader, rollup, evidence gate, and isolation boundaries exist.
- The first completed-game grader validation worked as designed.
- The live audit found the shadow deployment failure before false claims were made about accumulated evidence.

## 9. Current failures and open debts

- Prospective shadow evidence is still zero because the Windows collector wrapper fails in the meaningful execution branch.
- Shadow v1 is not yet the full matchup/game simulator originally envisioned.
- Production selection is not price-aware.
- PlayerProps book labels and timestamps have not been externally validated at scale.
- There is no strong shadow health alert for a game day with eligible windows but no artifact.
- The evidence minimum is a collection gate, not a statistically sufficient edge threshold.
- Local and Windows production both contain uncommitted ESPN HTTP work that needs deliberate reconciliation.
- Raw parsed snapshots are immutable, but the project should decide whether immutable raw source payloads are also required for auditability.

