# Ledger integrity audit — 2026-10-22

Scope: `~/wnba_props` (VM), branch `main`, HEAD `ea257e7` at assessment time.
Method: read-only inspection of the live tree, the live ledger
(`outputs/ledger/forecast_ledger.jsonl`), the grade artifacts under
`outputs/grades/`, the writer/reader graph in code, and the live systemd units;
every figure below was recomputed from the ledger bytes on disk (md5
`f12edfd405bf60ec9042786a8e9c436d`, 2060 rows) with the project's own
`wnba_props.ledger` helpers. Nothing in this document was written by changing
ledger data.

Status of this document: sections 1–9 are the assessment (step 1–2). Section 10
lists the structural changes and was appended when they landed (steps 3–4).
Section 11 is the re-check after the changes.

The question this audit answers is the same one the NHL audit answered: **is the
number the project publishes as its performance record the number the ledger
actually supports for the rows the board actually recommended?**

---

## 1. Summary of findings

The three NHL defect classes all reproduce here, in descending severity.

1. **The headline pooled rows the board explicitly did not recommend.**
   `grade_forecast_board.py::_summarize` counted every *priced* latest row and
   emitted it as `overall`. The board's own not-recommended flag
   (`value == "no_value"`, plus `value == "thin"` and the two EV labels) was
   present on 681 of the 1379 settled priced rows, and those 681 rows lost
   **−67.319u**. Pooling them dragged the published headline from
   **−20.781u / −2.98%** to **−88.101u / −6.39%** — a 4.2× overstatement of the
   loss, and a headline that does not describe the record a follower could have
   played. The recap rendered that pool verbatim (`render_recap` printed
   `paper_play` counts over all priced rows). Fixed in `7e84f28`.

2. **A reader pointed at a frozen orphan, printing a clean zero for six weeks.**
   `shadow_rollup.py` globbed `outputs/backtests/shadow_grade_*.json`. That
   directory has never existed in `~/wnba_props`, and the writer that would
   produce those files (`grade_projection_shadow.py:206`) only runs against a
   separate, long-abandoned tree (`~/wnba_props_shadow`), whose own input
   (`outputs/history/shadow_projection_*.json`) is also empty here. The rollup
   therefore found nothing to read, every run, since it was installed, and the
   two systemd units that drove it were gated on
   `ConditionPathExists=%h/wnba_props_shadow/config/shadow_v2_calibration.json`
   — a path that pins them to the dead tree. Retired in `df65992` (rollup
   script, library module, tests, four unit files, and every referring script).
   Detail in §4.

3. **A derived view was indistinguishable from an independent source.**
   `outputs/grades/phase_summary.json` is a cumulative aggregate recomputed
   every grade run from `load_rows(FORECAST_LEDGER)` → `latest_rows()`: it holds
   no data of its own and has **no code consumer** — only its writer, the copy
   embedded in each `forecast_grade_*.json`, and prose in six documents. Because
   it carried no provenance, several docs quote it as if it were a second
   opinion, and it has already drifted from the ledger it derives from
   (`docs/CHECK_2026-10-21.md:88` records 718 playoff plays / −55.262u against
   the ledger's 754; `docs/current_handoff.md:189` quotes a 65–73 playable
   subset). Labelled with a `_provenance` block in `efbab6c`, and the prose
   corrected in `06256fd`. Detail in §9.

Nothing was found that required repairing ledger data. The ledger is
self-consistent (§6, §8) and has not been rewritten by this audit.

---

## 2. Writer map — every writer of the record

There is exactly one record: `outputs/ledger/forecast_ledger.jsonl`, an
append-mostly JSONL of one row per (game_date, market, subject, proposition)
capture, where a re-capture supersedes rather than deletes the prior row.

| Writer | Entry | Mechanism | Trigger |
| --- | --- | --- | --- |
| Board capture | `run_forecast_pipeline.py:607` → `wnba_props/board.py::write_ledger` (656) → `wnba_props/ledger.py::append_rows` (119) | appends one row per proposition with `captured_at`, `run_id`, `phase`, `value`, `paper_play`, `price`, `outcome` | `scripts/run_linux_task.sh` tasks driven by `sports-wnba-forecast@*.timer` / `.service` |
| Grading | `grade_forecast_board.py:340` → `wnba_props/ledger.py::settle_rows` (175) | rewrites the whole file in place; touches only latest-capture rows whose `outcome` ∈ {`PENDING`, `UNPRICED`} | `sports-wnba-forecast-grade.timer` (daily 06:17 Detroit `= 10:17Z`) |
| Snapshot backfill | `scripts/backfill_board_snapshots.py:105` → `append_rows` | appends missing board-capture rows from stored snapshots | manual |
| (deleted) shadow rollup | `shadow_rollup.py` | read-only; wrote `outputs/shadow/rollup_*.json` | `sports-wnba-shadow-{capture,grade}.{service,timer}` — removed in `df65992` |

The append writer and the settle writer are the only two paths that mutate the
record. `settle_rows` is the only in-place rewrite, and it is constrained by
`wnba_props/ledger.py` to latest-capture rows with an unresolved outcome, so an
already-settled row is never re-priced by the normal grade path.

Row identity across the two writers is `(game_date, market, subject,
proposition_id)`; the newest `captured_at` wins, and `latest_rows()`
(`wnba_props/ledger.py:93`) is the single definition of "current state of the
record" used by everything downstream.

## 3. Reader map — every consumer of the record

| Reader | Site | What it claims |
| --- | --- | --- |
| Grade summary | `grade_forecast_board.py::_summarize` (144) | `overall` (recommended only, as of `7e84f28`), `all_rows` (context), `excluded`, `by_market` (recommended only), per-phase PhaseSummary |
| Discord recap | `wnba_props/notifiers/forecast_discord.py::render_recap` (195) | headline `Recommended:` + `Excluded: N of M priced rows…` line |
| Discord board | `grade_forecast_board.py` (via `send_recap` at ~396/409/415) | per-phase board; reads the same summary object |
| Ledger reconciliation | `scripts/reconcile_forecast_ledger.py::_summaries` | `overall` + `all_rows` + board-vs-ledger drift |
| Diagnostics | `scripts/evaluate_forecast_diagnostics.py:165` | per-market accuracy over all rows |
| Injury tracing | `scripts/report_injury_trace.py:79`, `scripts/grade_injury_trace.py:49` | join ledger rows to injury snapshots |
| Snapshot backfill | `scripts/backfill_board_snapshots.py` | reads the record to decide what to append |
| Dashboard | `outputs/run_status.json` + dashboard config | liveness/headline tiles |
| (deleted) shadow rollup | `shadow_rollup.py` | read `outputs/backtests/` — never the record |

Note that `research/walkforward.py::load_rows` is an unrelated local function,
not a ledger reader. `wnba_props/ledger.py` is the only module that parses the
file; every reader above goes through it (or through the grade summary that is
built from it), which is why the step-1 fix is one function rather than a
campaign across call sites.

## 4. Why the shadow rollup froze

Three independent paths had to line up for the rollup to emit a number, and none
of them existed on this host.

1. **The input directory was never created.** `shadow_rollup.py` globbed
   `OUTPUTS_DIR / "backtests"` for `shadow_grade_*.json`. `OUTPUTS_DIR` resolves
   to `~/wnba_props/outputs`, and `~/wnba_props/outputs/backtests` does not
   exist and is not gitignored-and-created either — the directory has no writer
   on this tree.
2. **The writer that would create it runs somewhere else.**
   `grade_projection_shadow.py:206` writes `shadow_grade_*.json` into
   `OUTPUTS_DIR/"backtests"`, but the units that invoked it carried
   `Environment=PROJECT_DIR=%h/wnba_props_shadow`, i.e. a separate tree
   (`~/wnba_props_shadow`, `.git` file dated 2026-09-08,
   `config/shadow_v2_calibration.json` present). Grading in that tree wrote into
   that tree's outputs; the rollup here read this tree's outputs. The two never
   met.
3. **Even in the right tree there was no input.** `run_projection_shadow.py:495`
   writes `OUTPUTS_DIR/"history"/shadow_projection_*.json`, and
   `grade_projection_shadow.py:64` raises
   `no shadow projection snapshots found in outputs/history` when that set is
   empty. **Zero** such files exist. The capture log
   (`outputs/logs/wnba_shadow_capture.log`) runs 2026-09-07 20:13 EDT →
   2026-09-12 21:13 EDT, exit 0 every time, having found no game inside the
   capture window; the grade log ends 2026-09-12 06:17:01 EDT with
   `No pending shadow snapshots require grading.` / `No terminal shadow grading
   reports are available.`

So the failure mode was not a crash — it was a clean, scheduled, exit-0 no-op
that printed nothing, for six weeks, under two service units whose only gating
condition pointed at a tree that this host's tasks never touched. That is
exactly the NHL class-2 signature: a reader whose source is stale-or-absent
while a report still claims to consume it.

`scripts/run_linux_task.sh shadow-grade` now exits 2 (`unknown task`), and the
two `run_projection_shadow.py` / `grade_projection_shadow.py` entry points were
**kept** as research-only: they are unscheduled, `docs/shadow_projection_v1.md`
documents manual use, and `tests/test_shadow_grading.py` imports the latter. What
was retired is the rollup reader, its library module, its tests, its four unit
files, its `run_linux_task.sh` arms, and its `sync_from_vm.sh` pull — nothing
live depended on any of them. Copies of the four unit files installed under
`~/.config/systemd/user/` (dated 2026-09-13) remain on the host, `disabled`; they
are inert — `sports-wnba-shadow-grade.service` still carries
`Environment=PROJECT_DIR=%h/wnba_props_shadow` and
`ConditionPathExists=%h/wnba_props_shadow/config/shadow_v2_calibration.json`, and
its `ExecStart` (`run_linux_task.sh shadow-grade`) now exits 2. They were left in
place rather than removed so the freeze leaves a trace; nothing can arm them
without an explicit `systemctl --user enable`.

## 5. Row-level diff: pooled headline vs recommended headline

Ledger: 2060 captured rows, 1537 latest-capture rows, game dates 2026-09-17 →
2026-10-21. Of the latest rows, 1379 are priced and settled; 158 are
void/pending/unpriced and are excluded from both lines by construction.

### 5.1 The two lines

| Line | n | W-L-P | units | ROI |
| --- | --- | --- | --- | --- |
| **Recommended (official)** | 698 | 359-338-1 | **−20.781u** | **−2.98%** |
| All priced rows (context) | 1379 | 729-648-2 | −88.101u | −6.39% |
| Excluded from the official line | 681 | 370-310-1 | −67.319u | −9.89% |

The excluded rows are not noise: they lost 9.89% against 2.98% for the plays the
board actually recommended, so pooling them nearly triples the reported loss
rate. Every one of them carries an explicit not-recommended label.

### 5.2 Exclusion by label

| `value` | rows | units |
| --- | --- | --- |
| `no_value` | 470 | −62.474u |
| `thin` | 208 | −5.448u |
| `FAVORABLE` | 2 | −0.167u |
| `UNFAVORABLE` | 1 | +0.769u |
| **total excluded** | **681** | **−67.319u** |

### 5.3 Per-date, all priced rows → recommended only

| date | all n | all units | rec n | rec units |
| --- | --- | --- | --- | --- |
| 09-17 | 109 | +0.891 | 60 | +8.102 |
| 09-18 | 72 | −18.978 | 41 | −18.151 |
| 09-19 | 68 | +0.930 | 38 | −1.122 |
| 09-20 | 131 | +2.813 | 63 | +10.463 |
| 09-21 | 53 | −3.836 | 27 | −2.133 |
| 09-22 | 95 | −10.012 | 44 | −12.421 |
| 09-23 | 50 | +0.635 | 21 | +5.066 |
| 09-24 | 36 | −0.967 | 20 | +1.304 |
| 09-27 | 164 | −10.441 | 93 | −5.479 |
| 09-29 | 83 | −11.601 | 45 | −5.658 |
| 09-30 | 81 | −8.834 | 47 | −0.945 |
| 10-01 | 38 | −1.395 | 18 | +0.403 |
| 10-02 | 46 | −3.474 | 29 | −1.153 |
| 10-04 | 92 | −10.810 | 43 | −9.333 |
| 10-07 | 85 | −7.461 | 37 | +4.008 |
| 10-09 | 84 | −0.193 | 36 | +0.376 |
| 10-19 | 45 | −1.053 | 17 | +1.904 |
| 10-21 | 47 | −4.313 | 19 | +3.987 |

The sign, not just the total, differs per date: 09-24, 10-01, 10-07, 10-09 and
10-21 are losses on the pooled line and profits on the recommended line, while
09-19 is a profit pooled and a loss recommended. A pooled headline is therefore
not merely pessimistic — it reorders the days.

### 5.4 By phase

| phase | rec n | rec W-L-P | rec units | rec ROI | all n | all units |
| --- | --- | --- | --- | --- | --- | --- |
| playoff | 384 | 197-187-0 | −11.890u | −3.10% | 765 | −59.575u |
| regular | 312 | 160-151-1 | −10.450u | −3.35% | 609 | −30.687u |
| preflight | 2 | 2-0-0 | +1.559u | +77.9% | 5 | +2.161u |

### 5.5 Recommended-only, by market

| market | n | W-L-P | units | ROI |
| --- | --- | --- | --- | --- |
| 3PM | 124 | 59-65 | −17.498u | −14.11% |
| AST | 122 | 71-51 | +10.348u | +8.48% |
| ML | 18 | 9-9 | +1.585u | +8.80% |
| PTS | 224 | 109-115 | −18.644u | −8.32% |
| REB | 131 | 68-63 | −1.162u | −0.89% |
| SPREAD | 38 | 21-16-1 | +3.181u | +8.37% |
| TOTAL | 41 | 22-19 | +1.408u | +3.43% |

Player props (3PM/AST/PTS/REB) are 601 of the 698 recommended plays and carry
−26.955u; team markets (ML/SPREAD/TOTAL) are 97 plays and +6.174u.

---

## 6. Price basis: the ledger already grades at the displayed price

Every row stores `price` as an integer American price and `units` as the
realized return on a flat 1u stake. Recomputed over the whole latest set:

- every `WIN` row has `units == payout(price)` — **0 violations**;
- every `LOSS` row has `units == −1.0` — **0 violations**;
- every `PUSH` row has `units == 0` — **0 violations**;
- **0 invalid American prices** (no zero, no `|price| < 100`).

The field names are `price` (integer American) and `outcome` (uppercase
`WIN`/`LOSS`/`PUSH`/`VOID`/`UNPRICED`/`PENDING`) — not `decimal`, `american`,
or `win`, which is the naming a cross-project reader would expect from the NHL
tree. Ledger row keys, for reference: `captured_at, ev, game_date, graded,
graded_at, line, market, outcome, phase, pick, pick_label, price, probability,
projection, proposition_id, run_id, source, subject, superseded_by, units,
value`.

So the price basis is clean and requires no change: the official line is
identical whether computed from stored `units` or recomputed from `price`. This
is the one place where the WNBA record is in better shape than the NHL one was.

## 7. Decision required by step 2

**Which rows are the performance record?**

The answer the board already encodes, made explicit:

> The official record is every **latest-capture** row that (a) the board marked
> recommended — `is_recommended(row)`, i.e. `paper_play` when present, else
> `value == "playable"`; (b) is priced (`price` is a valid American price); and
> (c) is settled (`WIN`/`LOSS`/`PUSH`), graded flat 1u at the displayed price.

Everything else — `no_value`, `thin`, `FAVORABLE`/`UNFAVORABLE`, unpriced,
void, pending — is excluded from the headline and reported separately, once,
with its own label. The pooled all-rows figure survives as explicit **context**,
never as the headline.

Label mechanics, verified live:

| label state | count |
| --- | --- |
| `value == "playable"` | 700 |
| `value == "no_value"` | 472 |
| `value == "thin"` | 209 |
| `value == "favorable"` | 4 |
| `value == "unfavorable"` | 3 |
| `value == "unpriced"` | 149 |
| `paper_play` truthy | 404 |
| `paper_play` false | 514 |
| `paper_play` absent | 619 (296 of them `playable`) |

`is_recommended()` and `value == "playable"` disagree on **0** rows; there are
**0** truthy-`paper_play`-but-not-playable rows and **0**
false-`paper_play`-but-playable rows. The two labels are redundant but
consistent, which is why the fix could adopt "`paper_play` when present, else
`value == playable`" without a migration and without changing any row.

## 8. Provably corrupt rows

**None.** The record contains no row whose outcome contradicts its own price,
and no duplicate that changes a total. What it does contain are five classes
that need to be named, because a naive reader will double-count or misread them:

- **(a) 54 settled rows with no price.** `price = null`, `units = null`,
  `value = "unpriced"` — 43 `WIN` and 11 `LOSS`, across 8 dates. Accuracy was
  resolved from the box score; finance was deliberately never invented. Correct
  behaviour: they count in accuracy denominators if you choose, never in units.
- **(b) 4 `VOID` rows.** Games that did not produce a settleable result. 0u.
- **(c) 5 `PENDING` rows.** Latest capture, no result yet at audit time.
- **(d) 95 `UNPRICED` rows.** Not graded; no price to grade against.
- **(e) 555 rows re-graded in a single bulk pass on 2026-09-24T02:00:19–22Z**,
  which overwrote prior outcomes. Ten `proposition_id`s now hold both a `WIN`
  and a `LOSS`:
  - nine are genuine pick flips inside `forecast-2026-09-17-evening`, each of
    which was graded `LOSS` at `2026-09-18T10:17:01.602701+00:00` and then
    re-graded with a different price at `2026-09-24T02:00:19.560441+00:00`;
  - one is a real same-pick, same-price re-grade —
    `AST:georgia amoore:2026-09-20:3.5`, snapshot
    `forecast-2026-09-20-afternoon-20260920T164715Z`, price −106, `LOSS` at
    `2026-09-21T10:17:02.593190+00:00` then `WIN` at
    `2026-09-24T02:00:22.032566+00:00` (+0.9434u).

  Both rows persist; `latest_rows()` resolves to the later one, so **no total is
  double-counted**. This is a provenance caveat, not a corruption: there is no
  box score on disk (`outputs/research/scoreboard_cache/` holds only scoreboard
  summaries), so this audit does **not** adjudicate which grade was right. It
  records that a bulk re-grade overwrote history without a supersession note,
  which is the one place where the append-mostly invariant was violated in
  practice (by an out-of-band script, not by `settle_rows`).

Outcome counts on the latest set, for the record: `WIN` 772, `LOSS` 659,
`PUSH` 2, `VOID` 4, `UNPRICED` 95, `PENDING` 5. Priced 1388; settled 1433.

## 9. The derived view: `outputs/grades/phase_summary.json`

`phase_summary.json` is written by `grade_forecast_board.py` (writer site ~371,
plus an embedded copy inside every `forecast_grade_{date}.json`) from
`load_rows(FORECAST_LEDGER)` → `latest_rows()`. It is a cumulative aggregate: no
independent observation, no separate ingestion, no other writer.

It had **no `_provenance`**, and six documents cite it in prose as though it were
an independent source:

- `README.md:198`
- `docs/current_handoff.md:51`, `:113`
- `docs/CHECK_2026-10-21.md:26`, `:88`
- `docs/NEXT_CHECKIN.md:37`
- `scripts/systemd/README.md:30`

It has already drifted from the ledger it derives from — which is the proof that
treating it as a source is unsafe, not merely untidy:

- `docs/CHECK_2026-10-21.md:88` records **718** playoff plays / 371-346-1 /
  −55.262u; the ledger's playoff set at audit time is **754** latest rows / 765
  priced.
- `docs/current_handoff.md:189` quotes "Playable subset (paper_play): 65–73,
  −11.137u", and `:183` "124–123, −22.042u, −8.92% (247 settled)" — figures that
  match neither the pooled nor the recommended line today.

A derived view that is quoted in prose will drift silently every time the ledger
is re-graded. There is no code consumer that would notice, so nothing can catch
it except a label on the artifact and corrected prose. Both landed:
`_provenance` (`derived`, `derived_from`, `derived_from_sha256`, `ledger_rows`,
`selection`, `row_basis`, `generated_at`, `note`) in `efbab6c`, prose in
`06256fd`. Note `outputs/grades/` is gitignored, so only the code and the docs
could be committed; the artifact itself is labelled the next time grading runs.

---

## 10. Step 3 — structural changes landed

Four commits, each pushed to `main` immediately after its step.

### 10.1 `7e84f28` — the headline counts only recommended rows

`wnba_props/ledger.py` gains the single definition:

- `RECOMMENDED_VALUE = "playable"` (line 16)
- `is_recommended(row)` (68) — `paper_play` when present, else `value == playable`
- `latest_rows()` (93), `recommended_rows()` (114)
- `roi_summary(rows, *, recommended_only=False)` (214)

`grade_forecast_board.py::_summarize` (144) now emits:

- `overall` — recommended-only, with `basis`:
  `"recommended plays only (paper_play when present, else value == playable)"`;
- `all_rows` — the pooled figure, context only;
- `excluded` — count and units of the not-recommended rows;
- `by_market` — recommended-only;
- and **no longer** a `paper_play` key (the old key was the pooled
  pseudo-subset that made the recap wrong).

`wnba_props/notifiers/forecast_discord.py::render_recap` (195) renders
`Recommended:` as the headline and adds
`Excluded: N of M priced rows were not recommended (pooled view in the grade
artifact, not a performance record)`.

`scripts/reconcile_forecast_ledger.py::_summaries` emits `overall` + `all_rows`
so the reconciliation tool and the board agree by construction.

### 10.2 `df65992` — the frozen-orphan reader is retired

Deleted: `shadow_rollup.py`, `wnba_props/shadow/rollup.py`,
`tests/test_shadow_rollup.py`,
`scripts/systemd/sports-wnba-shadow-{capture,grade}.{service,timer}`.
Edited: `scripts/run_linux_task.sh` (dropped the `shadow-capture`/`shadow-grade`
arms and `ROLLUP_COMMAND`), `scripts/sync_from_vm.sh`,
`scripts/systemd/README.md`, `scripts/disable_legacy_wnba_timers.sh`,
`docs/NEXT_CHECKIN.md`, `docs/shadow_projection_v1.md`.

Kept deliberately: `wnba_props/shadow/sources.py` (imported by
`grade_forecast_board.py`), and the two unscheduled research entry points
`run_projection_shadow.py` / `grade_projection_shadow.py`. Verified before
deletion that no live task, timer, or code path referenced the retired pieces;
`run_linux_task.sh shadow-grade` now exits 2. The host's installed unit copies
under `~/.config/systemd/user/` are disabled and were left as inert evidence of
the freeze (§4).

### 10.3 `efbab6c` — the derived view is labelled

`grade_forecast_board.py` gains `import hashlib`, `_display_path()`,
`_phase_summary_provenance()` (258) and `_phase_summary_document()` (288); the
writer site (~352) and the embedded copy (~371) stamp `_provenance` into
`outputs/grades/phase_summary.json` and every `forecast_grade_*.json`. New test
`test_phase_summary_document_labels_its_source` in
`tests/test_board_and_grading.py` asserts the label and the `derived_from`
digest.

### 10.4 `06256fd` — the docs describe the current system

- `README.md`: `paper_play` / `is_recommended` / `all_rows` paragraph;
  `@afternoon` and `@pregame` removed from "Active VM Schedule" and replaced
  with the fact that they are disabled as of 2026-10-22 and that
  `sports-wnba-forecast@finalsg4.timer` is armed for Sat 2026-10-24 16:00 UTC
  (1pm ET, Game 4); `outputs/grades/` now described as a derived view with
  `_provenance`.
- `docs/current_handoff.md`: schedule table corrected the same way;
  `phase_summary.json` described as derived; the "Playoff split ledger (reviewed
  2026-09-30)" section headed with `> **Superseded as a source.**`;
  "All `paper_play` rows are priced" replaced with the `is_recommended()` fact
  (0 of 700 recommended latest rows have `price: null`).
- `scripts/systemd/README.md`: disabled-timer note, install-snippet warning,
  conditional pregame prose.

`docs/CHECK_2026-10-21.md` is a dated check report and was left alone; its
`@afternoon` mentions are historical.

### 10.5 Verification

- `python -m unittest discover -s tests -p "test_*.py"` → `Ran 238 tests`,
  `OK`.
- `scripts/reconcile_forecast_ledger.py` → `ledger_rows=2060 latest_rows=1537
  boards=31 matched=1935 drifted=640 missing=0`; recommended
  `359-338-1 −20.781u over 698 played`; context `729-648-2 −88.101u over 1379`.
  (`drifted` counts rows where a stored board snapshot's
  `_COMPARE_FIELDS` differ from the ledger's latest row — last-capture
  supersession, not corruption; `missing=0` is the meaningful field.)
- Ledger md5 `f12edfd405bf60ec9042786a8e9c436d` before and after every commit —
  unchanged. No ledger row was written, rewritten, or deleted by this audit.
- No Discord message was sent: every run was invoked without `--send-discord`.

## 11. Step 5 — integrity re-check after the changes

Run on the live tree after `a1f730d` (four code/doc commits plus the dashboard
diary commit):

- Tests: `Ran 238 tests` / `OK`.
- Reconciliation: identical totals to §10.5, both lines present and labelled.
- Headline as published by the fixed code path (recap rendered live from the
  ledger through `_summarize` + `render_recap`):
  `Recommended: 359-338-1 | -20.78u | ROI -3.0% (698 priced)` plus
  `Excluded: 681 of 1379 priced rows were not recommended …`.
- Derived-view label: `_phase_summary_document()` emits `_provenance` with
  `derived: true`, `derived_from: outputs/ledger/forecast_ledger.jsonl`,
  `derived_from_sha256`, `ledger_rows: 2060` and the row-basis note.
- Ledger bytes unchanged (same md5); no history file rewritten; `git log` shows
  `7e84f28`, `df65992`, `efbab6c`, `06256fd`, `e4d5e4e`, `5b2d215`, `a1f730d`
  on `main`, working tree clean apart from the untracked `outputs/archive/` and
  `outputs/grades.backup-20260924T020000Z/` directories that predate this audit.

**Verdict: three findings, three fixes, record itself sound.** The WNBA ledger
was not wrong; the number published from it was, by including 681 rows the board
had explicitly declined, and two supporting artifacts (a shadow rollup and a
derived phase summary) were dressed as things they were not. The official
performance record going forward is the recommended, priced, settled, flat-1u
line: **698 plays, 359-338-1, −20.781u, −2.98%**, with the pooled 1379-row
−88.101u figure reported only as labelled context.
