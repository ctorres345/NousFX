# Review replay results (NFX-130, 2026-11-10)

Six past tier-2 round-1 reviews (five NousFX, one private Animus UI case that returned no findings), replayed with today's review-loop brief. Rule: `README.md`.

| | opus-medium | sonnet-high | sonnet-medium |
|---|---|---|---|
| real high caught, all past | 2/3 | 2/3 | 2/3 |
| real med caught, all past | 7/10 | 7/10 | 5/10 |
| real high caught, comparable | 2/2 | 2/2 | 2/2 |
| real med caught, comparable | 7/7 | 5/7 | 4/7 |
| new findings judged | 0 | 0 | 0 |
| unjudged new findings | 15 | 14 | 11 |
| tokens | 1,736,619 | 1,263,573 | 1,195,611 |
| cost ($, from the CLI) | 2.64 | 1.19 | 0.98 |

Without nfx-28 and the clean case ani-172 (see Limits), Sonnet high catches 4 of 6 comparable mediums and Opus 6 of 6; cost $0.88 against $2.00.

Comparable = past real findings that the Opus-medium replay also caught on the rebuilt tree.

## Decision

**Keep Opus.** Sonnet high catches every comparable real high, but only 5 of 7 comparable real mediums (4 of 6 without nfx-28); the rule needs at least 90%. Sonnet is cheaper ($1.19 against $2.64), which the rule's cost condition allows, but it does not make up for the misses.

## Limits

- The new findings were not judged one by one (40 unjudged across the three runs), so the false-positive condition is not measured. It does not change the result, because the medium condition fails first.
- One Opus run: its run-to-run spread is not measured. Opus catches all comparable findings by construction.
- No tree is proven to equal the round-1 tree: all five NousFX cases are marked inexact. Files made through Bash (`cp`, `sed -i`, heredocs) are not in the transcript's Edit/Write calls, so each rebuilt tree differs from the merged PR's file list (some of the gap is files added in later rounds). The comparable numbers drop findings the Opus replay could not reproduce, and one past high (of 3) was not reproducible.
- nfx-28 is the worst case: its rebuilt tree holds none of the ticket's change (only `.claude/handoff.md`). An earlier capture carried NFX-7's edits into it, because `git checkout -q main` was not seen as a reset; that is fixed, and nfx-28 was recaptured and rerun. Its numbers are kept in the table but are weak evidence, and the Opus "catch" there rests on a line match with a "change is missing" finding.
- The replayed reviewer works in a git worktree that shares the repo's refs, and `Read` can open the main checkout. It could therefore read later commits, the merged PR or the saved cases, which hold the answers. Nothing shows that any run did, but a clean clone at the base would close it. This could inflate catches for every reviewer alike.
- The three runs used `Bash(git:*)`. The script now allows only these git commands (prefix rules, so some flags such as `--output` still write; the worktree also shares the repo's refs) (`diff`, `show`, `log`, `blame`, `grep`, `ls-files`, `ls-tree`, `status`); only nfx-28 was rerun under that list.
- Only 6 cases and 2 comparable highs.
- The ticket text of each case is the original round-1 prompt minus its round and output-format lines. It still carries that prompt's own scope sentence (an old base hash, a repo path, an older output format) next to the replay brief's. All three reviewers got the same text, but the brief is less clean than the README says. Fixing it means re-running all three reviewers.
- Matching is by file and line (within 10) only. Only the misses were read side by side; the catches were not.
