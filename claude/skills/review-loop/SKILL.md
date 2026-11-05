---
name: review-loop
description: Simplify the current changes, then run an independent Opus review-fix loop (max 5 rounds) using the reviewer subagent.
argument-hint: "[scope: staged | branch | path]"
---

Run a bounded review-fix loop on: $ARGUMENTS (default: everything on this branch since it left the default branch, plus untracked files. Run `git merge-base origin/HEAD HEAD || git merge-base origin/main HEAD` first; it prints the base hash. If it fails, stop and record nothing: a bare `git diff` would review only part of the branch, so run `git remote set-head origin -a` or `git fetch --unshallow`, then retry. Then use `git diff <base hash>` and `git ls-files --others --exclude-standard`, with the literal hash, and give that hash to the reviewer).

You orchestrate from the main thread. Do not change models or effort levels; if you think a different model or effort is needed, ask the user first.

Run it once per feature, not per edit, and do not wait for the user to type `/review-loop`. It replaces running `/simplify` and `/code-review` by hand, and it is the review for unattended `/goal` runs.

## Step 0: simplify (once)

Invoke the `simplify` skill via the Skill tool on the same scope, so the reviewer sees the final code. If the skill cannot be invoked, say so and continue. Re-run the tests afterwards; if simplify broke them, fix or revert that change before starting the loop. Simplify does not count as a round.

## Loop (hard cap: 5 rounds)

For round N = 1..5:

1. Print `Round N/5`. Get the diff for the scope. If it is empty and there are no untracked files, stop and say so.
2. Call the `reviewer` subagent with: the objective (find real bugs and security issues), the scope or diff, and the required output format (`ID | severity | file:line | problem | suggested fix`, or `NO FINDINGS`). Show the reviewer's output in the conversation. If the work is based on a prototype or design (see the `prototype-fidelity` skill), also give the reviewer the prototype (link or the saved source file path) and ask it to list visual deviations (colors, sizes, radii, spacing, icons, states, light and dark) as findings, severity `med` unless purely cosmetic; skip this only if there is no prototype.
3. If the result is `NO FINDINGS`, or contains only `low` findings, stop the loop and report success. Clean means no `high` or `med` findings; list any `low` items in the final report.
4. For each `high` and `med` finding, verify it yourself against the code. The reviewer can be wrong.
   - Real: fix it in the main thread.
   - Not real: record it as rejected with a one-line reason.
5. Run the project's tests/lint and show the result in the conversation. Use the `test-runner` agent if it exists, otherwise run them inline.
6. Stop early if any of these holds:
   - The same finding ID returns after you tried to fix it (no progress).
   - Tests break and you cannot repair them within this round.
   - Only `low` findings remain.

After round 5, stop regardless of remaining findings.

## Optional deep pass (interactive only)
When the loop ends clean and the change is large or risky (security/auth, data migrations, concurrency, public API changes), tell the user why and ask whether to run `reviewer-deep` once. Run it only after they approve. Treat its findings like any others (verify, fix real high/med ones, run tests), then stop; do not start another loop. Skip this step in unattended or `/goal` runs.

## Record the review (last step)
When the loop ends, run the project's tests one last time and show the result. Also run the ticket's "Test or command that proves it" line, if the ticket has one, and show its result for the PR body. Record nothing if there was nothing to review or any test fails: say so, and leave the gate blocking. Otherwise, run `python3 <gate> --mark --findings <open high/med count>` (`python` on Windows), where `<gate>` is the gate path printed in the gate's block message, or the path in the `command` of the review-gate hook in your tool's hook config (`~/.claude/settings.json` for Claude Code, the plugin's `hooks.json` for agy). The mark runs the project's check command itself and refuses to record when it fails, times out, edits files or is missing (rules: `docs/review-gate.md`, "The check command"). Show its output. If it refuses, fix the cause and mark again; do not report the findings count as if the review had been recorded. Without a mark, and with a mark that has open findings, the PR gate hook blocks `gh pr create`. Run it after the final fix and test run, since any later edit invalidates it. If the user chose to skip the review, use `--skip "<their reason>"` instead; only that gets a PR past open findings or failing tests, and it runs no check.

## Rules
- Never commit, push, or stash. Leave all changes in the working tree.
- Do not widen scope beyond the findings.

## Final report
After recording, or deciding not to, print a table: rounds used, findings fixed, findings rejected (with reason), findings still open, and test status.
