# Dashboard checkpoint

This repository participates in the local sports-model dashboard
(`/Users/colemason/dashboard`). The dashboard is a read-only viewer: it shows
what is being tested, when to check back, and the latest verified run/report
from the Azure VM.

## The file you must keep current

`.dashboard/journal.jsonl` is an append-only diary. **Each line is one
checkpoint entry** (one JSON object). The last line is the "current"
checkpoint that drives the dashboard. Update it whenever you finish a
meaningful change to this project (code, model, deployment, or an observation
that changes what we are watching). Do not edit older lines; append new ones.

`.dashboard/checkpoint.json` mirrors the current entry for tools that only
read that file. Keep it equal to the last journal line.

## How to update it

In a coding conversation, run the dashboard command and it will append the
entry for you from the conversation plus the project docs:

- OpenCode: `/dashboard`
- CodeWhale: `/sportsboard`

Or append it yourself with the helper (it validates before writing):

    cd /Users/colemason/dashboard
    python3 -m dashboard checkpoint-update \
      --project <project_id> \
      --summary "what changed or what is being tested" \
      --kind game_days --target 7 --label "after a week of games" \
      --watching-for "the evidence that will judge this" \
      --next-action "what to do when the check-back is due"

Add `--deployed` only once the change is verified on the VM. Add
`--baseline <ISO-8601>` only when the count should start at a specific time.

## Required fields

- `updated_at` — ISO-8601 timestamp with offset (when this entry was written).
- `status` — `observing` (change is live, evidence accumulating),
  `awaiting_deployment` (merged but not yet running on the VM), `blocked`
  (needs a human decision), or `idle` (nothing scheduled).
- `summary` — one or two sentences: what changed or what is being tested.
- `check_back.kind` — `calendar` (number of days), `game_days` (number of
  distinct dates with games, counted from an authoritative slate source), or
  `grade_event` (a named grading artifact after the baseline).
- `check_back.label` — a human phrase like "after next Sunday grade" or "after
  a week of games".
- `check_back.target_date` — optional, `grade_event` only (YYYY-MM-DD): the
  rule fires only when the grade's date is on/after this date. Use it when a
  specific slate matters, e.g. "after Sep 27 is graded" -> `--target-date
  2026-09-27`. Without it, any new grade after the baseline satisfies the rule.

For `game_days`, a sport that does not play near-daily is usually better
expressed as `calendar` or `grade_event`; the dashboard only counts dates with
actual games.

## When to append an entry

- After you change code or a model that is or will be observed: set the new
  `summary`, `experiment`/`watching_for`, `status`, and `check_back` rule.
- After you deploy to the VM: set `deployed=true` and, if the observation
  starts now, reset `check_back.baseline`.
- After you check back and decide what happens next: append the new
  checkpoint.

A later deployment or observation may be a separate entry from the code commit.
The dashboard flags a project as "code changed after checkpoint" when
substantive code moves ahead of the last entry; that is a prompt to append an
entry, not an invented summary.

The dashboard never writes or edits these files for you and never guesses
their contents from git history. Keeping them truthful is what makes the
overview trustworthy.

## Validating

    python3 -m dashboard validate-checkpoints     # from /Users/colemason/dashboard
    python3 -m dashboard checkpoint-template --project <project_id>
