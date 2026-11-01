# claudio
Set of files and configurations that I share between devices to work with Claude and other AI tools.

## Claude Code orchestration (`claude/`)
Opus/Sonnet main session with Haiku 5.5 subagents for noisy work.

| File | Purpose |
|---|---|
| `claude/agents/Explore.md` | Haiku code search; replaces the built-in Explore |
| `claude/agents/researcher.md` | Haiku docs and API lookups |
| `claude/agents/reviewer.md` | Read-only Opus reviewer |
| `claude/agents/reviewer-deep.md` | Fable second-opinion reviewer; large/risky changes only, and only after asking |
| `claude/agents/architect.md` | Fable architecture consultant; read-only, only after asking |
| `claude/agents/test-runner.md` | Haiku build/test runner, returns only failures |
| `claude/skills/review-loop/SKILL.md` | `/review-loop`: review-fix loop, max 5 rounds, never commits |
| `claude/skills/ui-review/SKILL.md` | `/ui-review`: platform-aware UI/UX review that runs the right design skills (invoked automatically per the policy) |
| `claude/skills/ui-review/scripts/argon-review.sh` | Sends UI files and screenshots to Gemini Argon for a second opinion, with safety stops. Private repos need a line in `argon-repos.txt` |
| `claude/skills/ui-review/assets/flutter_shot_test.dart.txt` | Template for temporary Flutter screenshots |
| `claude/skills/gemini-review/` | `/gemini-review`: second-opinion code review through `gemini-api`. Needs that CLI and a key in the keychain. `scripts/common.sh` holds the safety checks both Gemini scripts share |
| `claude/skills/handoff/SKILL.md` | `/handoff`: writes `.claude/handoff.md` so the next session starts cheap instead of carrying old context |
| `claude/skills/jira-sync/` | `/jira-sync <KEY>`: read-only audit of a Jira board against its repo, plus a next-up list; applies only approved fixes and logs them |
| `claude/skills/project-setup/` | Sets up or audits a repo with the shared workflow: ticket template, work labels, review gate, shared `.claude` files, runbook and secret checks |
| `claude/hooks/review-gate.py` | PreToolUse hook: blocks `gh pr create` until `/review-loop` has recorded a review of the current code with 0 open findings; `--mark` records one |
| `claude/CLAUDE.delegation.md` | Delegation policy appended to `~/.claude/CLAUDE.md` |

### Hands-off review loop with `/goal`
The `review-loop` skill can be invoked by the model, but a `/goal` evaluator only reads the transcript, so paste the procedure as the goal instead (start the session in auto mode so test commands don't prompt):

```text
/goal Review-fix loop on everything on this branch since it left the default branch: git diff $(git merge-base origin/HEAD HEAD), plus untracked files (if origin/HEAD is unset, use origin/main). If that is empty, stop, say so and record nothing. Each round, print "Round N/5", run the reviewer subagent, verify its findings, fix the real high/med ones, then run tests. Done when the reviewer's latest output is NO FINDINGS or low-only, and the passing test output is shown. Stop after Round 5 regardless and list what remains. Then run the tests one last time and show the result. If any test fails, say so and record nothing. Otherwise run `python3 ~/.claude/hooks/review-gate.py --mark --findings <open count>` (`python` on Windows); the gate blocks the PR while the count is above 0. Do not commit.
```

Notes:
- The cap counts rounds (printed as `Round N/5`), not turns, because one round takes several turns.
- "Clean" means no high/med findings; reviewers almost always find something minor.
- The goal evaluator only reads the conversation, so the reviewer output and test result must be shown in it.
- For interactive use, just run `/review-loop`.

### Install
```bash
./install.sh
```
Copies the files into `~/.claude/` (override with `CLAUDE_HOME`), backs up anything it replaces, and keeps the "Delegation policy" section of `CLAUDE.md` current (the rest of that file is untouched). Safe to re-run after pulling updates.

### PR review gate
`install.sh` copies `claude/hooks/review-gate.py` to `~/.claude/hooks/` and registers it in `~/.claude/settings.json` (backed up first; other hooks untouched). It blocks `gh pr create` and create-pull-request MCP tools unless the branch is named `cesar/<JIRA-KEY>` (exception: `review-gate.py --allow-branch "reason"`, with the user's approval) and a review marker matches the code (a git tree hash of the working tree or `HEAD`). The marker lives in the git dir, so it is never committed. Any error lets the call through. Hooks load at session start, so restart running sessions. A PR opened in the GitHub web UI is not caught.

### Fable (optional)
`reviewer-deep` needs Fable access (included on Max up to 50% of the weekly limit; terms may change). If you lose access, delete `claude/agents/reviewer-deep.md` and `claude/agents/architect.md`, the "Optional deep pass" section in the review-loop skill, and their lines in `CLAUDE.delegation.md`.

### UI skills `ui-review` relies on (not stored in this repo)
It skips any that are missing and lists them under "Skipped", so install what you want on each device:
- User skills: `ui-ux-pro-max`, and the Android set (`adaptive`, `edge-to-edge`, `jetpack-compose-performance`, `navigation-3`, `styles`, `testing-setup`), which come from the `android` CLI.
- Plugin: `material-3` (`material-3@material-3-skill`).
- Claude desktop/cloud plugins: `design:accessibility-review`, `design:design-critique`, `design:ux-copy`, and `anthropic-skills:frontend-design`.

### Usage report
```bash
python3 -I scripts/usage-report.py --days 14          # all projects
python3 -I scripts/usage-report.py --project pawly    # one project
```
Read-only. Shows tokens and estimated list-price cost by project and model, where the cost goes (cache reads vs output), the costliest sessions with their average context size, which subagents ran and on which model, the tools returning the most text, and files re-read often. Use it to check that the Haiku/Fable agents are actually being used and where tokens go. Costs are relative weights, not a bill.

### Requirements
- Claude Code 2.1.293 or later (Haiku 5.5). Run `claude update` if older.
- Anthropic API. On Bedrock/Vertex the `haiku` alias resolves to Haiku 4.5; put the full model ID in the agent files.

### Not shared on purpose
`settings.json` (project-specific `autoMode` details and graphify hooks), memory, history, sessions. If you want the same main model, add `"model": "opusplan"` to `~/.claude/settings.json` by hand. To cap context growth (long sessions are the main cost), also add `"autoCompactWindow": 300000` there; the default window compacts at about 967K tokens.

### Check
Run `/agents` in a new session; all four agents should be listed.

### PR template
`claude/templates/pull_request_template.md` is the standard PR body (ticket, what/why, how to test, screenshots or recording for UI changes, checklist). Copy it to `.github/pull_request_template.md` in a project; it is not installed globally because GitHub reads it per repo.
