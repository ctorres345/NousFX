---
name: review-loop
description: Simplify the current changes, then run an independent review-fix loop with the reviewer subagent the diff's risk tier calls for (max 5 rounds).
argument-hint: "[scope: staged | branch | path]"
---

Run a bounded review-fix loop on: $ARGUMENTS (default: everything on this branch since it left the default branch, plus untracked files. Run `git merge-base origin/HEAD HEAD || git merge-base origin/main HEAD` first; it prints the base hash. If it fails, stop and record nothing: a bare `git diff` would review only part of the branch, so run `git remote set-head origin -a` or `git fetch --unshallow`, then retry. Then use `git diff <base hash>` and `git ls-files --others --exclude-standard`, with the literal hash, and give that hash to the reviewer).

You orchestrate from the main thread. The tier (below) picks the reviewer, effort and round cap; use exactly those. Raise the tier when you judge the risk higher and say why. Lowering it needs the user's yes, also in unattended runs (there: keep the tier).

Run it once per feature, not per edit, and do not wait for the user to type `/review-loop`. It replaces running `/simplify` and `/code-review` by hand, and it is the review for unattended `/goal` runs.

## Step 0: simplify (once)

Invoke the `simplify` skill via the Skill tool on the same scope, so the reviewer sees the final code. If the skill cannot be invoked, say so and continue. Re-run the tests afterwards; if simplify broke them, fix or revert that change before starting the loop. Simplify does not count as a round.

## Step 1: pick the tier

Run `python3 -B <this skill's folder>/scripts/tier.py --base <base hash> --labels <ticket labels>` (`python` on Windows). The ticket is the one in the branch name `cesar/<KEY>`; read its labels from Jira. If the branch has no key, leave out `--labels` and say so. If it has a key but Jira cannot be read, labels are unknown: use at least Tier 2 and put "labels unavailable" in the reasons. Print the tier and its reasons. The merge base was checked above, so any failure of `tier.py` (including exit 2): use Tier 2. Without the Agent tool (agy): the tier sets only the round cap; review inline.

| Tier | Reviewer (Agent call) | Rounds |
|---|---|---|
| 0 | `reviewer-light`, pass `effort: low` | 1 |
| 1 | `reviewer-light` | 3 |
| 2 | `reviewer` | 5 |
| 3 | `reviewer-high` | 5 |

The first time the tier is 3, whether Step 1 picks it or a later rise does, see Fable offers (plan check) before the next round.

After each round's fixes, run `tier.py` again. The tier only goes up (keep the higher of old and new). On a rise, switch to the new reviewer and cap; rounds already used count. The loop below says "the cap" for this number. At Tier 0 or 1, when the cap's final round had fixes that no reviewer has seen, raise the tier by one and run another round.

## Loop (hard cap: the tier's rounds)

For round N = 1..cap:

1. Print `Round N/<cap>`. Get the diff for the scope. If it is empty and there are no untracked files, stop and say so.
2. Call the tier's reviewer subagent with: the objective (find real bugs and security issues), the scope or diff, and the required output format (`ID | severity | confidence | file:line | problem | fix | quoted code`, or `NO FINDINGS`). Show the reviewer's output in the conversation. If the work is based on a prototype or design (see the `prototype-fidelity` skill), also give the reviewer the prototype (link or the saved source file path) and ask it to list visual deviations (colors, sizes, radii, spacing, icons, states, light and dark) as findings, severity `med` unless purely cosmetic. Check each against the prototype whatever its confidence, as for a `high`: the prototype is the spec. Skip this only if there is no prototype.
3. If the result is `NO FINDINGS`, or contains only `low` findings and `med` findings with confidence under 60, stop the loop and report success. A finding with a missing or non-numeric confidence counts as 60 or more; ask the reviewer to re-emit it in the format. Clean means no `high` and no `med` at 60 or more, other than findings you already rejected. A `med` under 60 is listed, not fixed: put it in the final report as "skipped: low confidence", with its quoted code.
4. For each `high` and each `med` at 60 or more, verify it yourself against the code. The reviewer can be wrong. Check the quoted code is really in the file or in `git diff <base hash>` (removed lines); if it is not, the finding is unverified: reject a `med` with the reason "quote not found", and for a `high` ask for evidence. Verify every `high`, whatever its confidence.
   - Real: fix it in the main thread.
   - Not real: record it as rejected with a one-line reason. Before rejecting a `high`, ask the same reviewer once for its evidence (SendMessage to that agent; where SendMessage is missing, call the same reviewer again with the finding and ask for the evidence). Decide with that answer. One ask per finding ID for the whole loop. If you still disagree with the reviewer after that answer on a `high`, see Fable offers (tie-breaker). A rejected finding of any severity stays rejected when it returns under the same ID, unless the reviewer brings new evidence (a corrected quote that is in the file or the diff counts); it does not block the clean stop and is not an open finding or counted in `--findings`. Ask for the code path and reasoning in plain text. If a fresh call answers `NO FINDINGS`, that is not evidence against the `high`.
5. Run the project's tests/lint and show the result in the conversation. Use the `test-runner` agent if it exists, otherwise run them inline.
6. Stop early if any of these holds:
   - The same finding ID returns after you tried to fix it (no progress). Stop, then see Fable offers (tie-breaker).
   - Tests break and you cannot repair them within this round.
   - Only `low` findings, `med` findings under 60 and findings you already rejected remain.

After the last round (the cap after any Tier 0/1 raise), stop regardless of remaining findings.

## Fable offers (interactive only)
Fable is offered at three points, never every round. Each offer is one line with the reason, and the agent runs only after the user says yes. Make each offer at most once per loop, except the tie-breaker, which is once per finding ID. No offer at Tier 0 or 1. No offer in unattended or `/goal` runs: there, the stop rules above apply as written.

1. **Plan check.** The first time the tier is 3, offer `architect` to check the plan and the diff's design before the next round (round 1 if Step 1 picked Tier 3). Say what it would look at. Skip this if you already offered `architect` for this ticket at kickoff.
2. **Tie-breaker.** `reviewer-deep` acts as the tie-breaker. When the same finding ID returns after your fix, stop the loop and offer it. When you still disagree with the reviewer on a `high` after the evidence ask, pause that finding (not the round) and offer it. Give it the finding, the quoted code and both positions. Use its answer for that finding only; any other finding it reports is handled like any others (see below). Never offer `reviewer-deep` to settle a disagreement with `reviewer-deep` itself: decide it with your own reason and tell the user. If the user says no: for a paused finding, decide as before (reject with your reason, or fix) and continue the round; for a returning finding ID, the loop stays stopped. A second tie on another finding ID: offer again.
3. **Final pass.** When the loop ends clean at Tier 3, offer one final pass of `reviewer-deep` over the whole diff.

Treat the findings of `reviewer-deep` like any others (verify, fix real `high` and `med` at 60 or more, list `med` under 60 as skipped: low confidence, run tests). After a paused tie-breaker, apply the answer and continue the round. After a no-progress tie-breaker or the final pass, stop; a Fable pass never starts another loop. A fix made after a tie-breaker or final pass has no reviewer yet: if rounds remain, run one more round with the tier's reviewer, else count it as open in `--findings` and say so in the report. Ask `reviewer-deep` for evidence with SendMessage only; if SendMessage is missing, ask the user before calling it again. Summarize the `architect` result for the user to decide.

## Record the review (last step)
When the loop ends, run the project's tests one last time and show the result. Also run the ticket's "Test or command that proves it" line, if the ticket has one, and show its result for the PR body. Record nothing if there was nothing to review or any test fails: say so, and leave the gate blocking. Otherwise, run `python3 <gate> --mark --findings <open high count + open med at 60 or more> --tier <final tier> --reasons "<reasons for the final tier, including your own reason for a raise>"` (`python` on Windows), where `<gate>` is the gate path printed in the gate's block message, or the path in the `command` of the review-gate hook in your tool's hook config (`~/.claude/settings.json` for Claude Code, the plugin's `hooks.json` for agy). The mark runs the project's check command itself and refuses to record when it fails, times out, edits files or is missing (rules: `docs/review-gate.md`, "The check command"). Show its output. If it refuses, fix the cause and mark again; do not report the findings count as if the review had been recorded. Without a mark, and with a mark that has open findings, the PR gate hook blocks `gh pr create`. Run it after the final fix and test run, since any later edit invalidates it. If the user chose to skip the review, use `--skip "<their reason>"` instead; only that gets a PR past open findings or failing tests, and it runs no check.

## Why the main model stays
The main model stays `opusplan`. A skill `model:` line would bypass `harness/models.toml` and break the local profile (NFX-45). The evidence ask gives a strong finding a second look without changing the model.

## Rules
- Never commit, push, or stash. Leave all changes in the working tree.
- Do not widen scope beyond the findings.

## Final report
After recording, or deciding not to, print a table: tier and reviewer, rounds used, findings fixed, findings rejected (with reason), findings skipped: low confidence, findings still open, and test status.
