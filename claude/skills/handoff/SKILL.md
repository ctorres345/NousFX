---
name: handoff
description: Write a short handoff note so a fresh session can continue this task without the old context. Use when finishing a task or reaching a stopping point, before /clear, or when the user wants to continue in a new session or thread.
argument-hint: "[optional focus]"
---

Write the project's handoff note (see "Where to save"). Keep it under 30 lines. $ARGUMENTS

Contents, in this order:
1. **Header:** today's date, current branch, short HEAD sha (from git).
2. **Goal:** one or two lines.
3. **Done:** what is finished and verified.
4. **Next:** ordered steps for the next session.
5. **Suggested skills:** one skill per line, each tied to the step it applies to (for example "`tdd` for the URL parsing, before the UI"), naming the next work first. Leave out skills that always apply (`review-loop` before a PR) and do not list a skill twice.
6. **Decisions:** choices made and the reason for each.
7. **Files touched:** paths only.
8. **Verify:** the exact commands that prove the current state (build, tests).
9. **Open questions / risks.**

Rules:
- Use facts from this session and git only. Mark anything unverified as "unverified".
- No secrets, tokens, or credential values. Reference commits, PRs and tickets by link instead of duplicating them.
- Never `git add` or commit the file; tell the user it is local unless they choose otherwise.

## Where to save

One note per repo, in the main checkout, even from a linked worktree (`.claude/worktrees/<name>`). Find its root with:

```bash
common="$(git rev-parse --path-format=absolute --git-common-dir)"
sub="$(git -C "$common" config core.worktree)"  # set only in a submodule
if [ "$(basename "$common")" = .git ]; then root="$(dirname "$common")"
elif [ -n "$sub" ]; then root="$(cd "$common" && cd "$sub" && pwd)"
else root="$(git rev-parse --show-toplevel)"  # a bare repo has no main checkout
fi
```

"The project root" and "the git top level" below mean this folder, and the session-start read of `.claude/handoff.md` uses this same path. A worktree's own `.claude/handoff.md` is a stale copy (the Claude desktop app copies `.claude/` into each new worktree): never read it as current, never update it.

- The project already has `<root>/.claude/handoff.md` (or `<root>/HANDOFF.md`): update that file in place. Keep its structure and replace the stale parts (state, next steps, open risks). Do not create a second copy. Before writing, run `git -C "$root" check-ignore -q --no-index <file>` and `git -C "$root" ls-files --error-unmatch <file>` (no `cd`). If the first exits 1 (no ignore rule) or the second exits 0 (git tracks the file), tell the user before writing.
- Merge, never overwrite: parallel worktree sessions share this one file. Re-read it just before writing, whether or not this session read it before, and keep the Done lines, still-open Next steps and risks that another session wrote; add this session's. The header is this session's; name the other session's branch in its kept lines. Drop another session's lines only once its branch is gone or its ticket is Done; to stay under 30 lines, cut finished items first and shorten, never drop open ones.
- The project has neither and is a git repo: ask the user once per session (in unattended runs do not ask: use the temp folder): "Create `.claude/handoff.md` and add it to `.gitignore`? A note in the temp folder is not found by the next session." On a yes, create the file in `<root>` (so the next session finds it) and, unless `git -C "$root" check-ignore -q --no-index .claude/handoff.md` already succeeds, add `.claude/handoff.md` to that folder's `.gitignore` (no duplicate line). Leave that edit uncommitted and tell the user. Then write the handoff there.
- On a no, outside a git repo, or when writing to `<root>` is refused (a permission prompt declined or blocked): write the note to the OS temp directory and do not ask again that session. Never fall back to the worktree's copy.

Finish by printing the full path of the file written; for the temp folder add that a new session will not find it on its own. Then suggest: run `/clear` (or open a new thread) and start the next session with "continue from <the path just printed>".
