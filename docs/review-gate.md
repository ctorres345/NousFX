# Review gate for every tool

My review gate asks one question: was this code reviewed before a PR opens? Each tool asks it in its own way.

```
Claude Code hook ──┐
pre-push hook   ───┼──► review-gate.py (evaluate) ──► exit 0 allow / exit 2 block
CI on the PR    ───┘
```

## The plain entry

```
python3 -B claude/hooks/review-gate.py check --cwd DIR --command "gh pr create"
```

- Exit 0 allows. Exit 2 blocks, with the reason on stderr.
- It judges the repo in `DIR`: branch named `cesar/<KEY>`, and a review mark for this exact code.
- Bad input blocks. A missing option, a missing folder or a folder that is not a git repo all exit 2.
- The Claude hook parses its JSON payload and then calls the same `evaluate()` function. Behavior of the hook does not change.
- Marks stay per worktree, in the git dir, keyed by tree hash. They do not move.

## Pre-push hook: advisory

- Template: `claude/githooks/pre-push`. Install it with `cp claude/githooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`, or point `core.hooksPath` at the folder.
- The gate script runs from outside the repo: `<home>/.claude/hooks/review-gate.py`, with `pr_create_parser.py` beside it. Copy both there from this repo's `claude/hooks/`. `<home>` comes from the passwd record, not `$HOME`. A copy inside the repo is code under review, and a branch could edit it to pass.
- `REVIEW_GATE_DIR` overrides that folder. It is for tests and for my own shell. The hook refuses it, and blocks the push, when the folder is inside any git work tree (or git cannot say), because code in a work tree is code under review.
- It fails closed. A missing gate script, missing `python3` or any git error blocks the push.
- It reads the ref list git sends on stdin. Deletes, tag pushes and pushes of the default branch (`main`, `master`, or `origin/HEAD`) pass. Any other branch push is judged on the tree of the pushed commit (`local_sha`), not the checked-out branch. That tree needs a valid review mark, and the pushed branch name must be `cesar/<KEY>`. A sha that is not a commit blocks. It calls `review-gate.py check ... --tree TREE --branch NAME`.
- It is advisory. `git push --no-verify` skips it, and nothing on the client can stop that. I never treat it as the only control.

## CI: the real gate for tools without hooks

- Workflow: `.github/workflows/review-gate.yml`. It runs on open, push, reopen and description edit.
- It takes `review-gate.py` and `pr_create_parser.py` from the base branch (`git show origin/<base>:...`), never from the PR. A PR cannot edit the gate to approve itself.
- The PR body reaches the job through an env var and a file. It is never interpolated into a shell command.
- The job passes only when the PR description or the last commit message holds a `Review-Gate: <tree hash>` line whose hash equals the tree of the PR head commit.
- A skip attestation passes too, and the log shows `skipped: <reason>`.
- Making it required is a manual step in GitHub: Settings, Branches, branch protection, require the `review-gate / check` status.
- First PR that introduces the gate: the base branch has no gate (or none with `verify-attestation`). The job fails with a clear message, it does not pass. I merge that one by hand as the owner.

## Attestation: how CI knows a review happened

Review marks live in the git dir and are never committed, so CI cannot see them. Instead:

```
review-loop ──► mark ──► review-gate.py attest ──► "Review-Gate: <tree>" ──► PR body
                                                                              │
CI: git rev-parse HEAD^{tree} == hash in the line? ◄──────────────────────────┘
```

1. Finish the code and run the review loop. It records the mark.
2. Run `python3 -B claude/hooks/review-gate.py attest`. It prints one line and exits 0 only when a clean mark (or a recorded skip) covers the current code. Otherwise it exits 2 and prints nothing.
3. Paste the line into the PR body, as its own line. A commit trailer on the last commit works too.
4. Any later change to the tree (a new commit, an amended file) changes the hash. Run the review again, run `attest` again, edit the body.

Line formats:

- `Review-Gate: <40 or 64 hex chars>`
- `Review-Gate: <hash> skipped: <reason>` for a recorded skip. The reason is one line.

`verify-attestation --tree HASH --text-file FILE [--text-file FILE]` is what CI runs. The line must start its own line. A mention inside a sentence does not count.

### What it does and does not guarantee

- It is an honor-system claim, not proof of a review.
- It proves one thing: the claim was written for exactly this code. Change the code and the claim goes stale.
- It does not prove the review ran. Anyone who can edit the PR body can type a line with the right hash.
- It does not prove the review was good, or that findings were fixed beyond the mark's count.
- A skip passes. The log shows the reason so a human can see it.
- The gate script comes from the base branch, so a PR cannot weaken the check on itself. A change to the gate takes effect only after it merges.
- Real enforcement needs a human reviewer or a trusted runner that does the review. Branch protection plus this check only makes skipping visible.
