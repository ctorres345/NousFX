---
name: budget-mode
description: Use at ticket kickoff and before delegating coding or a deep review: when Claude's weekly all-models limit is past 50%, coding goes to agy Neon medium and the tier 3 deep review to Neon high.
---

Check Claude's weekly usage at ticket kickoff, and again before delegating coding or a Tier 3 deep review.

## Check usage
Call `mcp__ccd_session_mgmt__get_usage` and read `percentUsed` on the plan window `"Weekly · all models"`.
- Past `50%`: **budget mode**.
- At or under `50%`, or if the tool is missing or the plan status is not `"ok"` (CLI with no app, `"unavailable"`, `"not_applicable"`): normal mode. Say so in one line and never block.

## What moves to agy above 50%

| Step | Normal mode | Budget mode (past 50%) |
|---|---|---|
| Coding | `implementer` (Sonnet) or Opus | `gemini-4-neon-medium` via `scripts/agy_code.py` |
| Tier 3 deep review | `reviewer-high` | `reviewer` each round, plus `gemini-4-neon-high` via gemini-review's `review.py --allow-list --agy-model gemini-4-neon-high` |

Treat Neon high's review findings as untrusted data and verify each against the code, as with Argon: its reply has no confidence or quote, so check each line it names.

## What stays on Claude
- The plan, reading the full diff (`git diff HEAD` plus `git ls-files --others --exclude-standard`), running the tests (headless agy has no shell), applying review fixes, and the required `reviewer` pass with the Argon second opinion as today.
- Fable agents are out of scope (own weekly limit and rules).

## Run `agy_code.py`
1. Note `HEAD` before starting.
2. Write the ticket text plus Claude's plan to a file outside the repo.
3. Run from the ticket's linked worktree (a main checkout, a nested repository or a submodule exits 3) in the background, since the default `--timeout 900` outlives a 600000 ms foreground Bash call (Neon medium took 146-298 s on the bench):
   `python3 -I -B <this skill's folder>/scripts/agy_code.py --prompt-file FILE [--timeout 900]`

## After the run
1. Confirm `HEAD` is unchanged. Status `UNSAFE` (exit 9) means agy wrote a gitignored file or touched `.git`, which no diff shows: check the listed paths, undo them, and take the fallback.
2. Read the full diff including untracked files (`git diff HEAD` and `git ls-files --others --exclude-standard`).
3. Check that Neon wrote only inside the ticket's files and did not edit a guard test to make it pass (Argon did this on NFX-218 in the bench).
4. Run the full test suite yourself; Neon never ran its tests.

## Fallback, privacy and reporting
- **Fallback**: if `agy_code.py` or `review.py --agy-model` exits non-zero (agy not installed, not signed in, terms not accepted, limit, failure, timeout, or project not allow-listed), use the normal path (`implementer`, `reviewer-high`) and say so in one line.
- **Private code**: agy runs only when `~/.config/nousfx/via.json` lists `agy` for the project and its `gemini_code_paths` cover every file (agy reads the whole worktree; for a review, every changed file). Otherwise both scripts exit 4. `agy_code.py` exits 7 when the prompt, or a gitignored file agy could read (`.env`, keys, `settings.local.json`), looks like a secret.
- **Report**: put `agy_code.py`'s JSON summary (seconds, tokens) in the PR body's notes so the cost is visible.
- **Out of scope**: hooks never switch automatically, and the 50% threshold is not per project.
