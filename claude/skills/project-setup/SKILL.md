---
name: project-setup
description: Set up or audit a repository with Cesar's stack-independent agent workflow: ticket template, work labels, review gate, shared .claude files, runbook and secret checks. Use for a new project, an existing project missing the workflow, or "set up Claude here".
argument-hint: "[path to repo; default current]"
---

Bring one repository up to the shared workflow. `~/.claude` stays the source of truth; the repo gets a copy so cloud runs can use it.

## 1. Audit

Run read-only and fill this table: item | state | action.

- Git repo, default branch, GitHub visibility (public or private), secret scanning and push protection
- Stack and its build, test and lint commands
- `CLAUDE.md`, `docs/agents/`, `docs/RUNBOOK.md` or `docs/engineering/RUNBOOK.md`, PR template, CI workflows
- `.gitignore` rules for `.claude/`, and which `.claude` files are tracked
- Jira project key, and existing labels
- Handoff file: read it if present

Done when every row has a state.

## 2. Agree

Show the table, then ask one question at a time about real choices, each with your recommendation. A public repo defaults to the minimum set. Wait for approval before writing.

## 3. Create the branch

Find or create the Jira ticket, and work on `cesar/<KEY>` in a worktree from the default branch.

## 4. Install

Merge into existing files, and keep what is already there.

| Target | Source |
|---|---|
| `.gitignore` | Allowlist: `.claude/*`, `!.claude/settings.json`, `!.claude/agents/`, `!.claude/skills/`, `!.claude/hooks/`, then `.claude/**/*.bak*`, `.claude/**/.env*`, `CLAUDE.local.md`, `graphify-out/`; replaces any blanket `.claude/` ignore |
| `.claude/settings.json` | [`assets/settings.json`](assets/settings.json), merged |
| `.claude/hooks/review-gate.py` | `~/.claude/hooks/review-gate.py` |
| `.claude/agents/` | `reviewer.md`, `test-runner.md` from `~/.claude/agents/` |
| `.claude/skills/` | `review-loop` from `~/.claude/skills/`, without backups; point its gate command at `"$(git rev-parse --show-toplevel)/.claude/hooks/review-gate.py"` and its prototype reference at `docs/agents/ticket-template.md`; keep its branch scope and when-to-record rules as written. Do not install `pr`: its body format conflicts with the PR template |
| `docs/agents/ticket-template.md` | [`assets/ticket-template.md`](assets/ticket-template.md) |
| `docs/agents/triage-labels.md` | Append [`assets/work-labels.md`](assets/work-labels.md) |
| `CLAUDE.md` | Add build and test commands and pointers to `docs/agents/`, if missing |
| PR template | Add Ticket, What and why, How to test, Screenshots, Checklist, if missing |

If `docs/agents/` is missing, tell Cesar to type `/setup-matt-pocock-skills` first, with Jira as the tracker. If no runbook exists, offer to write one from the repo's docs and config. Add stack-specific skills only when the project uses them.

## 5. Protect

- Run `gitleaks git --staged --redact` and fix every real finding before committing. Report test fixtures as such.
- Run `gitleaks git --redact` on the full history. Report any real finding to Cesar and stop, because rotating a key is Cesar's call.
- On a public repo, confirm that committed files hold no personal paths, infrastructure notes, handoffs or memories. Ask Cesar before enabling secret scanning and push protection.

## 6. Finish

Show the before and after table, then follow the normal PR flow: `review-loop`, then a PR. Done when every audit row is fixed or has a stated reason, and the PR link is reported.
