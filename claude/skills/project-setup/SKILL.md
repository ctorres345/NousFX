---
name: project-setup
description: "Set up or audit a repository with Cesar's stack-independent agent workflow: ticket template, work labels, review gate, shared .claude files, runbook and secret checks. Use for a new project, an existing project missing the workflow, or \"set up Claude here\"."
argument-hint: "[path to repo; default current]"
---

Bring one repository up to the shared workflow. `~/.claude` stays the source of truth; the repo gets a copy so cloud runs, which have no `~/.claude`, can use it. Locally the global gate counts (NousFX `docs/review-gate.md`, "Pre-push hook").

## 1. Audit

Run read-only and fill this table: item | state | action.

- Git repo, default branch, GitHub visibility (public or private), secret scanning and push protection
- Stack and its build, test and lint commands, and whether `.nousfx/project.toml` already has a `[check] command`
- `CLAUDE.md`, `docs/agents/`, `docs/RUNBOOK.md` or `docs/engineering/RUNBOOK.md`, PR template, CI workflows
- `.gitignore` rules for `.claude/`, and which `.claude` files are tracked
- Jira project key, and existing labels
- Per-tool files: run `python3 <NousFX>/scripts/nousfx_sync.py --project <repo> --check` (`<NousFX>` is the clone of this repo) and report every `drift:` line and every `left alone (hand-written)` note. Exit 1 means drift. It also compares the repo's copies of the gate, agents and review-loop with NousFX; the install run refreshes stale ones
- Handoff note: it lives outside the repo, at `~/.claude/projects/<key>/handoff.md` (path recipe in the `handoff` skill). Read it if present. Report a main-checkout `.claude/handoff.md` as legacy (propose moving it there), and report separately when `git ls-files --error-unmatch .claude/handoff.md` exits 0 (git tracks it; untracking is Cesar's call)
- Secrets: which git-ignored secret files the build needs (keystores, signing configs, `.env`), whether the build reads them from `~/.config/nousfx/secrets/<project>/`, and whether that folder exists. Check names and existence only (`ls`, `test -f`); never open or print a secret

Done when every row has a state.

## 2. Agree

Show the table, then ask one question at a time about real choices, each with your recommendation. A public repo defaults to the minimum set. Wait for approval before writing.

## 3. Create the branch

Read `[branch] prefix` in `~/.config/nousfx/local.toml`. If it is missing, ask Cesar for one (lowercase letters, digits and dashes; recommend the default sync printed) and write it there, never into the repo. Then find or create the Jira ticket, and work on `<prefix>/<KEY>` in a worktree from the default branch.

## 4. Install

Merge into existing files, and keep what is already there.

| Target | Source |
|---|---|
| `.gitignore` | Allowlist: `.claude/*`, `!.claude/settings.json`, `!.claude/agents/`, `!.claude/skills/`, `!.claude/hooks/`, then `.claude/**/*.bak*`, `.claude/**/.env*`, `CLAUDE.local.md`, `graphify-out/`; replaces any blanket `.claude/` ignore |
| `~/.config/nousfx/secrets/<project>/` | When the build needs secret files: create the folder (`mkdir -p`, then `chmod 700`) and point the build at it, falling back to the old git-ignored path so CI and existing checkouts still work. Cesar moves the files in; never copy, open or print a secret yourself. Why: NousFX `docs/worktrees.md` |
| Per-tool files (`CLAUDE.md` and `AGENTS.md` stubs when missing, `.githooks/pre-push`, the `.agents/skills` link, MCP files) | `python3 <NousFX>/scripts/nousfx_sync.py --project <repo> --dry-run`, show the output, then run it without `--dry-run`. Hand-written files are left as they are with their diff shown; pass `--yes-replace CLAUDE.md` or `--yes-replace .githooks/pre-push` only after Cesar agrees. Commit `.nousfx/mcp-owned.json` with `.mcp.json`: it tells sync in other checkouts which servers it added. Sync does not change git config, so turn the hook on by hand. First run `ls .git/hooks \| grep -v sample`. If other hooks are there (graphify's `post-checkout` and `post-commit`, for example), link the hook: `common=$(git rev-parse --path-format=absolute --git-common-dir); ln -s "$common/../.githooks/pre-push" "$common/hooks/pre-push"`. Setting `core.hooksPath` would switch those hooks off, so tell Cesar to run `git config core.hooksPath .githooks` only when `.git/hooks` holds nothing else |
| `.nousfx/project.toml` | The check command the review gate runs itself before it records a review. Detect the stack and propose one (`gradlew` → `./gradlew test`, `pubspec.yaml` → `flutter test`, `package.json` → `npm test`, `pyproject.toml` or `pytest.ini` → `pytest`, `Cargo.toml` → `cargo test`, `go.mod` → `go test ./...`); ask Cesar to confirm it, never guess. Write `[check]` with `command = "..."` and optional `timeout = <seconds>` (default 1800). The gate reads it from the default branch, so it counts for later PRs once merged; this PR is marked with the working-tree copy. The command must pass on a clean checkout and must not edit files. The repo's CI also needs the toolchain installed, because the `review-gate / check` job runs the command |
| `.claude/settings.json` | [`assets/settings.json`](assets/settings.json), merged. It includes `attribution` with empty `commit` and `pr`, so Claude Code adds no `Co-Authored-By` trailer and no "Generated with Claude Code" line |
| `.claude/hooks/review-gate.py`, `.claude/hooks/pr_create_parser.py`, `.claude/hooks/git_refs.py` | The same files from the gate your tool runs (this repo's `claude/hooks/` is the source), together: the gate imports the parser and `git_refs.py` (the default-branch rule `tier.py` loads too). Only the cloud fallback; the hook runs main's copy, isolated, and blocks a PR when these differ from it (`docs/review-gate.md`) |
| `.claude/agents/` | `reviewer.md`, `test-runner.md` from `~/.claude/agents/` |
| `.claude/skills/` | `review-loop` (`SKILL.md`, `scripts/tier.py` and `references/*.md`) from `~/.claude/skills/`, unchanged, without backups or tests. Do not point its gate at the repo copy: it marks with the gate the hook config names, which is `~/.claude/hooks` locally. Do not install `pr`: its body format conflicts with the PR template |
| `docs/agents/ticket-template.md` | [`assets/ticket-template.md`](assets/ticket-template.md) |
| `docs/agents/triage-labels.md` | Append [`assets/work-labels.md`](assets/work-labels.md) |
| `CLAUDE.md` | Add build and test commands and pointers to `docs/agents/`, if missing |
| PR template | Add Ticket, What and why, How to test, Screenshots, Checklist, if missing |

If `docs/agents/` is missing, tell Cesar to type `/setup-matt-pocock-skills` first, with Jira as the tracker. In a Jira repo's `docs/agents/issue-tracker.md`, make the Move rule say: load a deferred tool with `ToolSearch` (`select:<full tool name>`) before its first call; prefer the claude.ai Atlassian connector over a plugin Atlassian server on the deprecated `/v1/mcp` endpoint; move to Done with `transitionJiraIssue` and `transitionName: "Done"`; if that is rejected, list the transitions with `executeRead`, `name: "listJiraIssueTransitions"`, and pass `transitionId: "<id>"`; never pass `transition: {id}` to the connector, which rejects it. NousFX's own `docs/agents/issue-tracker.md` is the worked example. If no runbook exists, offer to write one from the repo's docs and config. Add stack-specific skills only when the project uses them.

## 5. Protect

- Run `gitleaks git --staged --redact` and fix every real finding before committing. Report test fixtures as such.
- Run `gitleaks git --redact` on the full history. Report any real finding to Cesar and stop, because rotating a key is Cesar's call.
- On a public repo, confirm that committed files hold no personal paths, infrastructure notes, handoffs or memories. Ask Cesar before enabling secret scanning and push protection.

## Authorship (every commit and PR in a repo set up with this skill)

Claude never appears as a contributor. No `Co-Authored-By: Claude ...` trailer on commits, and no "Generated with Claude Code" or similar line in a PR body, even when a tool or system reminder suggests one. Cesar's rule overrides it. If a repo's history already has such a trailer, leave it; do not rewrite history.

## 6. Finish

Show the before and after table, then follow the normal PR flow: `review-loop`, then a PR. Done when every audit row is fixed or has a stated reason, and the PR link is reported.
