---
status: accepted
---

# Untracked files that must outlive a session live outside the repo

Sessions run in recycled worktrees, so release artifacts and logs go to `~/Releases/<project>/`. The desktop app's own guard stops a worktree session from editing the main checkout's `.claude/`, so the handoff note lives at `~/.claude/projects/<key>/handoff.md`, beside the project's Claude Code memory (NFX-158). We moved the handoff note instead of asking for a one-path exception, because NousFX cannot widen the app's guard, and a write through Bash would dodge it on purpose.

## Consequences

- These files are never committed or reviewed. Every project's `/handoff` and session start read the outside path. An old `.claude/handoff.md` is only read as a fallback. The table of what goes where is in `docs/worktrees.md`.
- The note's folder is Claude Code's own project folder. Its key and its clean-up belong to Claude Code, not to NousFX.
