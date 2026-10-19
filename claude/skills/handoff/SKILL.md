---
name: handoff
description: Write a short handoff note so a fresh session can continue this task without the old context. Use when finishing a task or reaching a stopping point, before /clear, or when the user wants to continue in a new session or thread.
argument-hint: "[optional focus]"
---

Write `.claude/handoff.md` in the project root (overwrite any existing one). Keep it under 30 lines. $ARGUMENTS

Contents, in this order:
1. **Header:** today's date, current branch, short HEAD sha (from git).
2. **Goal:** one or two lines.
3. **Done:** what is finished and verified.
4. **Next:** ordered steps for the next session.
5. **Decisions:** choices made and the reason for each.
6. **Files touched:** paths only.
7. **Verify:** the exact commands that prove the current state (build, tests).
8. **Open questions / risks.**

Rules:
- Use facts from this session and git only. Mark anything unverified as "unverified".
- No secrets, tokens, or credential values.
- Do not `git add` or commit the file; tell the user it is local unless they choose otherwise.

Then tell the user the file path and suggest: run `/clear` (or open a new thread) and start the next session with "continue from .claude/handoff.md".
