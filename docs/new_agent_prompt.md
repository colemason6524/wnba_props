# Copy-ready prompt for the next agent

Use the text below to start the next conversation.

---

I am continuing a WNBA props research project in `/Users/colemason/Documents/wnba_props`. Do not assume the previous conversation is available.

First read these files in order:

1. `docs/current_handoff.md`
2. `docs/project_history_and_lessons.md`
3. `docs/research_model_roadmap.md`
4. `docs/shadow_projection_v1.md`
5. `README.md`

Then inspect `git status --short`, the relevant implementation/tests, and the current Windows state directly over `ssh windows`. The authoritative production checkout is `C:\Users\muski\wnba_props`; the isolated research checkout is `C:\Users\muski\wnba_props_shadow`.

There are two separate systems. Production is a working heuristic daily screener and must not be changed as part of shadow research. The shadow is a research-only PTS projection model with separate checkout, cache, outputs, logs, branch, and scheduled tasks.

The most important current fact is that prospective shadow collection is at zero. The scheduled capture task looks healthy after later no-op runs, but eligible-window runs abort on the first Python stderr progress line because the shadow PowerShell wrapper combines stderr under `$ErrorActionPreference = "Stop"`. Confirm this from current logs rather than trusting this prompt. Repair only the isolated shadow wrapper, add a meaningful test/smoke check, and verify a real strict-pregame artifact plus capture-registry entry. Do not modify `run_nightly.py`, production scoring/output/Discord code, production task definitions, or unrelated dirty work.

Preserve the frozen `wnba-points-shadow-v1` model while collecting. Do not tune from the one post-tip, one-game engineering slate. The minimum review gate is 7 slates, 20 games, 100 strict 20–90 minute pregame projections, and 90% both-side price coverage; even passing that gate is not proof of edge.

Be skeptical and evidence-driven. Verify artifacts and counters, not only scheduled-task status. Treat correlated rows, source latency, missing context, price coverage, unresolved players, and minutes/role error as first-class issues. If you discover documentation drift, update the canonical handoff with a dated evidence note. Before any code edits, tell me what you verified, what is broken, the exact isolated fix you propose, and how you will prove production remained unchanged.

---

