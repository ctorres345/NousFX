---
name: review-loop
description: Simplify the current changes, then run an independent Opus review-fix loop (max 5 rounds) using the reviewer subagent.
disable-model-invocation: true
argument-hint: "[scope: staged | branch | path]"
---

Run a bounded review-fix loop on: $ARGUMENTS (default: all uncommitted changes, `git diff HEAD`).

You orchestrate from the main thread. Do not change models or effort levels; if you think a different model or effort is needed, ask the user first.

## Step 0: simplify (once)

Invoke the `simplify` skill via the Skill tool on the same scope, so the reviewer sees the final code. If the skill cannot be invoked, say so and continue. Re-run the tests afterwards; if simplify broke them, fix or revert that change before starting the loop. Simplify does not count as a round.

## Loop (hard cap: 5 rounds)

For round N = 1..5:

1. Print `Round N/5`. Get the diff for the scope. If it is empty, stop and say so.
2. Call the `reviewer` subagent with: the objective (find real bugs and security issues), the scope or diff, and the required output format (`ID | severity | file:line | problem | suggested fix`, or `NO FINDINGS`). Show the reviewer's output in the conversation.
3. If the result is `NO FINDINGS`, or contains only `low` findings, stop and report success. Clean means no `high` or `med` findings; list any `low` items in the final report.
4. For each `high` and `med` finding, verify it yourself against the code. The reviewer can be wrong.
   - Real: fix it in the main thread.
   - Not real: record it as rejected with a one-line reason.
5. Run the project's tests/lint and show the result in the conversation. Use the `test-runner` agent if it exists, otherwise run them inline.
6. Stop early if any of these holds:
   - The same finding ID returns after you tried to fix it (no progress).
   - Tests break and you cannot repair them within this round.
   - Only `low` findings remain.

After round 5, stop regardless of remaining findings.

## Rules
- Never commit, push, or stash. Leave all changes in the working tree.
- Do not widen scope beyond the findings.
- Re-read any file yourself before editing it; do not rely on a summary alone.

## Final report
Print a table: rounds used, findings fixed, findings rejected (with reason), findings still open, and test status.
