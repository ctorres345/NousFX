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
- The note lives outside the repo, so it is never committed and needs no `.gitignore` line.

## Where to save

One note per repo, outside it, beside the project's Claude Code memory: `~/.claude/projects/<key>/handoff.md`. In Claude Code that folder is the parent of your auto-memory folder (`.../memory/`). Every session of the repo, in the main checkout or in any worktree, uses this one file. It moved here because the Claude desktop app blocks worktree sessions from editing the main checkout's `.claude/` (NFX-158; NousFX `docs/worktrees.md`).

`<key>` is the main checkout's real path with every character that is not a letter or digit turned into `-` (`/Users/me/Projects/App` gives `-Users-me-Projects-App`). Without the memory path, find it with:

```bash
common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
sub="$(git -C "$common" config core.worktree 2>/dev/null)"  # set only in a submodule
if [ -z "$common" ]; then root="$(pwd -P)"  # not a git repo
elif [ "$(basename "$common")" = .git ]; then root="$(dirname "$common")"
elif [ -n "$sub" ]; then root="$(cd "$common" && cd "$sub" && pwd -P)"
else root="$(git rev-parse --show-toplevel)"  # a bare repo has no main checkout
fi
note="$HOME/.claude/projects/$(cd "$root" && pwd -P | LC_ALL=C sed 's/[^A-Za-z0-9]/-/g')/handoff.md"
```

Prefer the memory folder's parent whenever you know it: the recipe can differ from Claude Code's key on Windows, for non-ASCII paths and for very long paths. If the recipe's folder does not exist but a memory folder does, use the memory folder's parent.

- The note exists: update it in place. Keep its structure and replace the stale parts (state, next steps, open risks).
- Merge, never overwrite: parallel worktree sessions share this one file. Re-read it just before writing, whether or not this session read it before, and keep the Done lines, still-open Next steps and risks that another session wrote; add this session's. The header is this session's; name the other session's branch in its kept lines. Drop another session's lines only once its branch is gone or its ticket is Done; to stay under 30 lines, cut finished items first and shorten, never drop open ones.
- The note is missing but the main checkout (`<root>`) has an older `.claude/handoff.md` or root `HANDOFF.md`: read that as the other sessions' lines, merge as above, and write the result to the new path. Leave the old file alone; tell the user it can be deleted. A worktree's own `.claude/handoff.md` is a stale copy (the app copies `.claude/` into each new worktree): never read it as current, never update it.
- Neither exists: create the folder if needed and write the note there; no question to ask.
- Writing there is refused (a permission prompt declined or blocked): write the note to the OS temp directory and do not try again that session. Never fall back to a worktree path.

Finish by printing the full path of the file written; for the temp folder add that a new session will not find it on its own. Then suggest: run `/clear` (or open a new thread) and start the next session with "continue from <the path just printed>".
