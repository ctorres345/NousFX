# Worktrees and durable files

Claude Code sessions in the desktop app run in git worktrees under `<repo>/.claude/worktrees/<name>`. The app keeps a small pool of them per repo (three to five in practice) and hands a free one to each new session. A worktree is a workspace, not storage: assume anything in it is gone when the session ends.

## Why worktrees

- **Parallel sessions stay apart.** Each session has its own branch and files, so two tickets never edit the same checkout.
- **My main checkout stays mine.** I keep working in `~/Projects/<repo>` while agents work beside it.
- **One `.git`.** Worktrees share the object store, refs and stash with the main checkout, so a new one costs seconds, not a clone.
- **Warm caches.** A recycled worktree keeps its build output (Gradle, `node_modules`, DerivedData), so the next build is incremental.

The app removes a worktree only after about 30 days idle, so no clean-up rule is needed.

## The trade-off

- **Git-ignored files and build output don't survive.** The next session that gets the folder may run `git clean`, switch branches or rebuild over them. A signed `.aab` left in `app/build/` is gone by the next release (ANI-198).
- **The main checkout's `.claude/` is blocked.** The app's own PreToolUse guard stops a worktree session from editing files in the main checkout's `.claude/`, so a note there cannot be updated from a worktree.
- **The worktree's `.claude/` is a copy.** The app copies `.claude/` into each new worktree, so files there are stale and local to that folder.
- **Untracked secrets are missing.** A git-ignored `keystore.properties` or `.env` in the main checkout is not in the worktree.

## Where durable files go

| What | Where | Rule |
|---|---|---|
| Release artifacts (`.aab`, `.ipa`, signed builds) | `~/Releases/<project>/<version>-code<n>/` | Copy them there before the session ends; never hand over a worktree path. |
| Logs worth keeping | `~/Releases/<project>/<version>-code<n>/` or another fixed folder outside the repo | Same. |
| Secrets (keystores, signing configs, API keys) | `~/.config/nousfx/secrets/<project>/` | Builds read them from there (ANI-199). Agents only check that a file exists; they never open or print it. |
| Handoff note | `~/.claude/projects/<key>/handoff.md`, beside the project's Claude Code memory | Every session of the repo, main checkout or worktree, reads and writes this one file (NFX-158); the app's guard does not cover it (checked from a worktree session in auto mode with Write and Edit, 2026-11-16: no block, no prompt; other permission modes not checked). The `handoff` skill has the path recipe. |

## The main checkout

From a worktree, agents treat my main checkout as read-only: no edits, no git writes (switch, pull, commit, stash, reset) and no builds there. They may read it (for example an older handoff note). A session that runs in the main checkout itself (a thread) works there as usual.

## Rules

`harness/AGENTS.md` ("Durable files") carries the short form of this page into every session's rules. The `project-setup` skill creates the secrets folder for a new project.
