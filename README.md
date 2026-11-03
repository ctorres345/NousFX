# NousFX

A shareable framework for building software with Claude Code. It holds the agents, skills, hooks and policies I use on every device and project, packaged so my friends can pick them up too.

> **Why "NousFX"?** In Greek philosophy, *nous* (νοῦς) is the intellect, the mind that understands. For Anaxagoras it was the cosmic mind that set the primordial mixture in motion and brought order out of chaos. That is what I want from this repo: a thinking structure around AI-assisted work, so a messy stream of prompts becomes a repeatable process. **FX** stands for framework.

## What you get

| Area | What it does |
|---|---|
| **Delegation** | The main session (Opus) plans and edits. Cheap Haiku subagents do search, docs lookups and noisy test runs. Fable agents give premium second opinions, only after you approve |
| **Spec-to-ticket flow** | Grill the idea, write a spec, split it into Jira tickets, implement, review, open the PR |
| **Quality gates** | A review loop that must pass before `gh pr create` works, and UI review with screenshots for anything users see |
| **Designer (Via)** | *Planned.* A designer skill that audits UI, shows prototypes and files tickets with screenshots and specs. See [Roadmap](#roadmap) |
| **Tooling** | Jira board audits, session handoffs, project setup and a token usage report |

```
idea ─► grilling ─► /to-spec ─► /to-tickets (Jira) ─► implement ─► ui-review ─► review-loop ─► gate ─► PR
                                                      (tdd, prototype)   (UI only)    (max 5 rounds)
```

## Quickstart

```bash
git clone https://github.com/ctorres345/NousFX.git
```

```bash
cd NousFX && ./install.sh
```

Then start a **new** Claude Code session (hooks and agents load at session start) and run `/agents`. You should see `Explore`, `researcher`, `reviewer`, `reviewer-deep`, `architect` and `test-runner`.

`install.sh` copies the files into `~/.claude/` (override with `CLAUDE_HOME`) and backs up anything it replaces as `*.bak-<stamp>`. It keeps the "Delegation policy" section of `~/.claude/CLAUDE.md` current and leaves the rest of that file alone. It also registers the review gate in `~/.claude/settings.json`, after backing that up too.

`install.sh` does not write agents, skills or hooks through a symlink. If one in `~/.claude/` is a link (for example a skill linked from `~/.agents/skills`), it warns, skips that file and leaves the target alone. `CLAUDE.md` and `settings.json` are still written through a link on purpose, so a dotfiles-managed copy keeps getting updates.

Re-installing after a merge, checking the gate and rolling back an install are in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## How to use it day to day

### 1. Set up a project once
Open Claude Code in your project and ask it to set up the project. The `project-setup` skill adds:
- the ticket template and work labels
- the review gate settings
- shared `.claude/` files
- runbook and secret checks

To use the spec-to-ticket flow, also run `/setup-matt-pocock-skills` once. It records your issue tracker (Jira, GitHub or local markdown), triage labels and domain docs in `docs/agents/`.

### 2. Plan a feature

| Step | Command | Who runs it | Result |
|---|---|---|---|
| Stress-test the idea | (automatic) `grilling` skill | Claude, one question at a time | A settled design |
| Write the spec | `/to-spec` | You | A spec issue labeled `ready-for-agent` |
| Split into tickets | `/to-tickets` | You | Vertical-slice Jira tickets with blockers |
| Too big for one session | `/wayfinder` | You | A map of child tickets worked one at a time |

### 3. Build a ticket
- The branch is always `cesar/<TICKET-KEY>` (for example `cesar/NFX-12`), cut from the default branch. The gate currently hard-codes my `cesar/` prefix, so to use your own name, edit `BRANCH_OK` in `claude/hooks/review-gate.py`.
- Claude reads the ticket's `is blocked by` links first and only starts when all of its blockers are Done.
- `tdd` is used when test-first fits, and `prototype` for UI or state questions that a throwaway build answers faster.
- UI tickets: the prototype is the spec. The ticket lists concrete values (colors, sizes, radii, states), and the PR includes screenshots.

### 4. Review and ship

| Step | What happens |
|---|---|
| `ui-review` | Runs automatically for UI changes. It detects the platform (Compose, Flutter, SwiftUI, web), runs the matching design skills, takes light and dark screenshots, and asks Argon for a second opinion when configured |
| `review-loop` | `/simplify`, then up to 5 rounds of the Opus `reviewer`, fixing the real high and medium findings. It records the result for the gate |
| PR gate | `gh pr create` is blocked until a review with 0 open high/med findings matches the current code and the branch name has a Jira key |
| PR body | Uses `claude/templates/pull_request_template.md` (ticket, what and why, how to test, screenshots) |

### 5. Keep sessions cheap
- `/handoff` writes `.claude/handoff.md`, so the next session starts from a short note instead of a huge context.
- One task per session. Use `/compact focus on <topic>` at milestones.
- `python3 -I scripts/usage-report.py --days 14` shows where tokens go.

## What's in the repo

| Path | Purpose |
|---|---|
| `claude/agents/Explore.md` | Haiku code search; replaces the built-in Explore |
| `claude/agents/researcher.md` | Haiku docs and API lookups |
| `claude/agents/test-runner.md` | Haiku build and test runner; returns only failures |
| `claude/agents/reviewer.md` | Read-only Opus code reviewer |
| `claude/agents/reviewer-deep.md` | Fable second-opinion reviewer for large or risky changes, only after asking |
| `claude/agents/architect.md` | Fable architecture and performance consultant, read-only, only after asking |
| `claude/skills/review-loop/` | `/review-loop`: review-fix loop, max 5 rounds, never commits |
| `claude/skills/ui-review/` | `/ui-review`: platform-aware UI/UX review. Includes `scripts/argon-review.sh` (Gemini second opinion with safety stops; private repos must be listed in `argon-repos.txt`) and a Flutter screenshot test template |
| `claude/skills/gemini-review/` | `/gemini-review`: second-opinion code review through `gemini-api`. `scripts/common.sh` holds the safety checks both Gemini scripts share |
| `claude/skills/handoff/` | `/handoff`: session handoff note |
| `claude/skills/jira-sync/` | `/jira-sync <KEY>`: read-only audit of a Jira board against its repo, plus a next-up list. Applies only approved fixes |
| `claude/skills/project-setup/` | Sets up or audits a repo with the shared workflow |
| `claude/hooks/review-gate.py` | PreToolUse hook that blocks `gh pr create` until a review with 0 open high/med findings (or an approved `--mark --skip`) is recorded. `--mark --findings <count>` records one; `--mark` alone is refused |
| `claude/hooks/pr_create_parser.py` | Shell parser the gate uses to find `gh pr create` and the directory it runs in. Keep it next to the gate: without it the gate blocks any command that mentions `gh` and `pr` |
| `claude/hooks/test_review_gate.py` | Gate test suite: `python3 claude/hooks/test_review_gate.py` |
| `claude/CLAUDE.delegation.md` | Delegation policy merged into `~/.claude/CLAUDE.md` |
| `claude/templates/pull_request_template.md` | Standard PR body. Copy it to `.github/` per repo |
| `scripts/usage-report.py` | Read-only token and cost report |
| `docs/agents/` | This repo's own tracker setup (Jira project `NFX`) |

### Skills this framework uses but doesn't store
`ui-review` skips any missing skill and lists it under "Skipped", so install what you need on each device:
- User skills:
  - `ui-ux-pro-max`
  - the Android set (`adaptive`, `edge-to-edge`, `jetpack-compose-performance`, `navigation-3`, `styles`, `testing-setup`), from the `android` CLI
  - Matt Pocock's workflow skills (`grilling`, `to-spec`, `to-tickets`, `tdd`, `prototype` and others)
- Plugin: `material-3` (`material-3@material-3-skill`).
- Claude plugins: `design:accessibility-review`, `design:design-critique`, `design:ux-copy` and `anthropic-skills:frontend-design`.

## The PR review gate
- Blocks `gh pr create` (including wrappers such as `bash -c`, `eval` and `$(...)`) and create-pull-request MCP tools unless all three hold:
  - the branch is named `cesar/<JIRA-KEY>` (change `BRANCH_OK` for your own prefix). `review-gate.py --allow-branch "reason"` records an approved exception.
  - a review marker matches the code: a git tree hash of the working tree or `HEAD`.
  - that review has 0 open high/med findings (`--mark --findings <count>`), or you approved a skip (`--mark --skip "<reason>"`).
- The marker lives in the git dir, so it is never committed.
- Limits to know:
  - It does not catch every form, for example PRs created from script files, `gh api`, shell functions, remote shells or `find -exec`.
  - An error while checking a create blocks it. The call passes only outside a git repo, when the folder is missing, or when git is not installed.
  - A PR opened in the GitHub web UI is not caught.
  - Hooks load at session start, so restart running sessions after installing.

### Hands-off review loop with `/goal`
A `/goal` evaluator reads only the transcript, so paste the procedure as the goal. Start the session in auto mode so test commands don't prompt.

```text
/goal Review-fix loop on everything on this branch since it left the default branch, plus untracked files. Run git merge-base origin/HEAD HEAD || git merge-base origin/main HEAD first; it prints the base hash. If it fails, stop, say so and record nothing. Then use git diff <base hash> and git ls-files --others --exclude-standard, with the literal hash, and give that hash to the reviewer. If both are empty, stop, say so and record nothing. Each round, print "Round N/5", run the reviewer subagent, verify its findings, fix the real high/med ones, then run tests. Done when the reviewer's latest output is NO FINDINGS or low-only, and the passing test output is shown. Stop after Round 5 regardless and list what remains. Then run the tests one last time and show the result. If any test fails, say so and record nothing. Otherwise run `python3 ~/.claude/hooks/review-gate.py --mark --findings <open high/med count>` (`python` on Windows); the gate blocks the PR while the count is above 0. Do not commit.
```

- The cap counts rounds (`Round N/5`), not turns. One round takes several turns.
- "Clean" means no high or medium findings. Reviewers almost always find something minor.
- For interactive use, just run `/review-loop`.

## Security model

NousFX gives agents real permissions on your machine, so it is built around a few rules:

| Risk | How NousFX handles it |
|---|---|
| Prompt injection from tickets, PRs, web pages, second-opinion output | Treated as data, never instructions. Work comes only from your tracker. Gemini output is verified finding by finding |
| Code or screenshots leaving your machine | Gemini scripts refuse private repos not listed in `argon-repos.txt`. Claude leaves out screenshots that show personal data, tokens or account details. Code goes to Gemini only through those scripts |
| Secrets | Keys live in the OS keychain, never in this repo or in config files. Project setup includes a secret check |
| Destructive git | Agents never commit, push or stash on their own. A PR needs a recorded review |
| Premium spend | Fable agents run only after you say yes |
| Third-party skills | They run with Claude's permissions. Read a skill's `SKILL.md` and scripts before installing, and pin it to a commit |

Never commit `settings.local.json`, handoffs, memory, session history or anything holding a secret.

## Optional: Fable
`reviewer-deep` and `architect` need Fable access, which is included on Max up to 50% of the weekly limit (terms may change). Without it, remove:
- `claude/agents/reviewer-deep.md` and `claude/agents/architect.md`
- the "Optional deep pass" section of the review-loop skill
- their lines in `CLAUDE.delegation.md`

## Usage report
```bash
python3 -I scripts/usage-report.py --days 14
```
```bash
python3 -I scripts/usage-report.py --project pawly
```
Read-only. Costs are relative weights at list price, not a bill. It shows:
- tokens and estimated cost by project and model
- cache reads vs output
- the costliest sessions
- which subagents ran on which model
- tools returning the most text
- files re-read often

## Requirements
- Claude Code 2.1.293 or later (Haiku 5.5). Run `claude update` if older.
- Anthropic API. On Bedrock or Vertex, the `haiku` alias resolves to Haiku 4.5, so put the full model ID in the agent files.
- Optional:
  - `gemini-api` CLI with a key in the keychain, for Argon and `/gemini-review`
  - the Atlassian MCP, for Jira
  - Fable access

## Not shared on purpose
`settings.json` (project-specific `autoMode` details and graphify hooks), memory, history and sessions. To use the same main model, add `"model": "opusplan"` to `~/.claude/settings.json` by hand. To cap context growth, also add `"autoCompactWindow": 300000`. The default window compacts at about 967K tokens.

## Roadmap
Tracked in Jira project **NFX**.

- **Via, the designer** (named after Octavia, one of my cats). `ui-review` grows into a designer:

  | Command | What it does |
  |---|---|
  | `/via spec <ticket>` | Sets the visual direction and specs before code |
  | `/via check` | Design QA before the PR, enforced by a gate |
  | `/via audit` | Audits the app at any time |

  For each problem it shows before/after prototypes. You approve once which fixes happen now and which become Jira tickets; tickets carry inline screenshots and a spec table. It exports to Obsidian and Figma. It learns from every project, but only saves a lesson after you approve it. Every integration is optional, so it works with only Claude Code installed.
- **Plugin marketplace.** Install with `/plugin marketplace add ctorres345/NousFX`. Third-party skills will be pinned by commit and checksum. This replaces the copy-based `install.sh`.
- **Contributing guide** with a skill-writing checklist.
- **Plugins named after my cats.** Only the first two are planned; the rest are ideas for later.

  | Plugin | Role | Would hold |
  |---|---|---|
  | **Mandy** (`mandy`, mdy) | Core development | Agents, `review-loop`, the PR review gate |
  | **Via** (Octavia) | Designer | `/via` setup, spec, check, audit and export |
  | Cassie (Cassiopeia) | Planner and navigator | Grilling, spec to tickets, wayfinder, roadmap, `jira-sync` |
  | Rosalia | Guardian | Secret checks, git guardrails, skill checksums, outbound rules |
  | Misty | Memory and context | Handoff, compaction, usage report, graphify |
