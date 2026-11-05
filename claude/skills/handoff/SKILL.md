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

- The project already has `.claude/handoff.md` (or a root `HANDOFF.md`): update that file in place. Keep its structure and replace the stale parts (state, next steps, open risks). Do not create a second copy. Before writing, run from the git top level `git check-ignore -q --no-index <file>` and `git ls-files --error-unmatch <file>`. If the first exits 1 (no ignore rule) or the second exits 0 (git tracks the file), tell the user before writing.
- The project has neither and is a git repo: ask the user once per session (in unattended runs do not ask: use the temp folder): "Create `.claude/handoff.md` and add it to `.gitignore`? A note in the temp folder is not found by the next session." On a yes, create the file in the project root (the git top level, so the next session finds it) and, run from there, unless `git check-ignore -q --no-index .claude/handoff.md` already succeeds, add `.claude/handoff.md` to that folder's `.gitignore` (no duplicate line). Leave that edit uncommitted and tell the user. Then write the handoff there.
- On a no, or outside a git repo: write the note to the OS temp directory and do not ask again that session.

Finish by printing the full path of the file written; for the temp folder add that a new session will not find it on its own. Then suggest: run `/clear` (or open a new thread) and start the next session with "continue from <the path just printed>".
