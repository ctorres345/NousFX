# Review gate for every tool

My review gate asks one question: was this code reviewed before a PR opens? Each tool asks it in its own way.

```
Claude Code hook ──┐
pre-push hook   ───┼──► review-gate.py (evaluate) ──► exit 0 allow / exit 2 block
CI on the PR    ───┘
```

## The plain entry

```
python3 claude/hooks/review-gate.py check --cwd DIR --command "gh pr create"
```

- Exit 0 allows. Exit 2 blocks, with the reason on stderr.
- It judges the repo in `DIR`: branch named `cesar/<KEY>`, and a review mark for this exact code.
- Bad input blocks. A missing option, a missing folder or a folder that is not a git repo all exit 2.
- The Claude hook parses its JSON payload and then calls the same `evaluate()` function. Behavior of the hook does not change.
- Marks stay per worktree, in the git dir, keyed by tree hash. They do not move.

## Pre-push hook: advisory

- Template: `claude/githooks/pre-push`. Install it with `cp claude/githooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`, or point `core.hooksPath` at the folder.
- It fails closed. A missing gate script, missing `python3` or any git error blocks the push.
- It checks every branch, not only `cesar/*`.
- It is advisory. `git push --no-verify` skips it, and nothing on the client can stop that. I never treat it as the only control.

## CI: the real gate for tools without hooks

- Workflow: `.github/workflows/review-gate.yml`. It runs the gate tests and `check` on every pull request.
- Tools without hooks (agy, OpenCode, plain git) cannot be blocked on my machine, so the server-side check is what counts.
- Making it required is a manual step in GitHub: Settings, Branches, branch protection, require the `review-gate / check` status.

## Known limit of CI

- Review marks live in the git dir and are never committed, so a fresh CI checkout has no mark.
- Until I pick a durable attestation (for example a trailer or a PR label the check can read), the CI `check` fails on a PR that passes locally. Do not mark it required before that is decided.
