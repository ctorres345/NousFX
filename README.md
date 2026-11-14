# NousFX

A shareable framework for building software with Claude Code. It holds the agents, skills, hooks and policies I use on every device and project, packaged so my friends can pick them up too.

> **Why "NousFX"?** In Greek philosophy, *nous* (νοῦς) is the intellect, the mind that understands. For Anaxagoras it was the cosmic mind that set the primordial mixture in motion and brought order out of chaos. That is what I want from this repo: a thinking structure around AI-assisted work, so a messy stream of prompts becomes a repeatable process. **FX** stands for framework.

## What you get

| Area | What it does |
|---|---|
| **Delegation** | The main session (Opus) plans and edits. Cheap Haiku subagents do search, docs lookups and noisy test runs. Fable agents give premium second opinions, only after you approve |
| **Spec-to-ticket flow** | Grill the idea, write a spec, split it into Jira tickets, implement, review, open the PR |
| **Quality gates** | A review loop that must pass before `gh pr create` works, and UI review with screenshots for anything users see |
| **Designer (Via)** | A plugin that audits UI, shows prototypes and files tickets with screenshots and specs. See [Meet Via](#meet-via) |
| **Tooling** | Jira board audits, session handoffs, project setup and a token usage report |

```
idea ─► grilling ─► /to-spec ─► /to-tickets (Jira) ─► implement ─► /via check ─► review-loop ─► gate ─► PR
                                                      (tdd, prototype)   (UI only)     (rounds by tier)
```

## Quickstart

```bash
git clone https://github.com/ctorres345/NousFX.git
```

```bash
cd NousFX && ./install.sh
```

On Windows, in PowerShell, run `.\install.ps1` instead. Both scripts only run `scripts/nousfx_sync.py`: they show its dry run, ask, then run it for real. Arguments pass through, so `./install.sh --tools claude,agy` also sets up agy (Antigravity CLI). The modes `--check` and `--restore` go to `scripts/nousfx_sync.py` directly.

Then start a **new** Claude Code session (hooks and agents load at session start) and run `/agents`. You should see `Explore`, `researcher`, `reviewer-light`, `reviewer`, `reviewer-high`, `reviewer-deep`, `architect`, `implementer` and `test-runner`.

The sync writes `~/.claude/CLAUDE.md` (the rules, generated), the agents, skills and hooks, registers the review gate in `~/.claude/settings.json`, adds a deny list there so Claude's file tools cannot read secret files or edit lockfiles, and allows auto mode to delete my merged `cesar/` branches. It backs up every file it changes under `~/.config/nousfx/backups/`, never writes through a symlink it does not own, and skips a skill folder you made by hand (a collision, with a diff) until you pass `--yes-replace NAME`. What it writes and every switch is in [docs/sync.md](docs/sync.md).

If `~/.claude/settings.json` is a symlink (a dotfiles setup), replace it with a regular file first: the sync refuses to write through it. `CLAUDE_HOME` no longer does anything; pass `--home DIR` to aim at another home.

Setting up a new machine, updating after a merge, setting up one project, the smoke checks and rolling back are in [docs/RUNBOOK.md](docs/RUNBOOK.md). If you use agy, my `gemini-orchestrator` plugin decides its delegation and model choice, so install it too.

## Meet Via

Via is my designer skill, named after Octavia, one of my cats. It audits UI, shows before/after prototypes and files tickets with screenshots and specs. It is a plugin in this repo's marketplace, version 0.4.0: `/via setup`, `/via audit` (web projects only) and `/via spec` work so far, and the other modes say they are not built yet. You need Claude Code, Python 3 and git; Chrome or Chromium is needed only for visual checks. Every integration is optional.

### Quickstart

```bash
claude plugin marketplace add ctorres345/NousFX
```

```bash
claude plugin install via@nousfx
```

Then, inside Claude Code, run `/reload-plugins` and:

```text
/via setup
```

`/via setup` checks which integrations you have (Gemini, Jira, Figma, Obsidian, simulators, a browser, design skills), says why each matters and links the fix. It saves the result to `~/.config/nousfx/via.json`, which holds statuses and paths only, never keys. Running it again changes nothing when nothing changed.

### An example run

The repo ships a one-screen sample app, `samples/web`, with two planted flaws: a subtitle at `#BBBBBB` on white, and a 96x28 button below the touch target. Copy it into a folder with `git init` and run `/via audit` there. Via takes light and dark screenshots at phone and desktop width with headless Chrome or Chromium, runs the web design skills, gets a second opinion and saves a findings report in `.via/runs/`, which git ignores.

| Light, desktop | Dark, phone |
|---|---|
| ![The sample app in light mode at desktop width](docs/images/via/sample-light-desktop.png) | ![The sample app in dark mode at phone width](docs/images/via/sample-dark-phone.png) |

The audit should flag both. You approve once which fixes happen now and which become tickets. How I tested this on a clean machine, and what fell back, is in [docs/fresh-user-test.md](docs/fresh-user-test.md).

### Config

`/via setup` writes `~/.config/nousfx/via.json`. It must stay outside any git work tree; the scripts refuse a config kept in a project. The home comes from your system user record, so setting `$HOME` does not move it.

| Key | Default | What it is |
|---|---|---|
| `version` | `1` | Config format. |
| `integrations` | filled by `/via setup` | Status of each integration, never a key. |
| `obsidian.vault_path` | `null` | Your vault. Set only after you say so. |
| `obsidian.argon_log` | `"Argon UI review log.md"` | Note name for the second-opinion log. |
| `figma.state` | `"missing"` | Only `/via export figma` uses it. |
| `jira.site` | `null` | Your Jira site. |
| `outbound_allowlist` | `{}` | A project sends nothing out until it is listed here. Key `host/owner/repo` (for example `github.com/acme/app`), value `{"destinations": ["gemini", "jira", "figma", "artifacts", "obsidian"], "gemini_code_paths": ["src/"]}`. List only the destinations you want; `gemini_code_paths` is the only code Gemini may see. |
| `capture.themes` | `["light", "dark"]` | Themes to shoot. |
| `capture.font_scale` | `"largest"` | Font scale for native shots. |
| `capture.max_second_opinion_shots` | `8` | Cap on shots sent for a second opinion. |

### When something is missing

| Missing | What Via does |
|---|---|
| Gemini key | A fresh-context Claude reviewer gives the second opinion. |
| Atlassian (Jira) | Tickets are written as markdown. |
| Figma | Only `/via export figma` is affected. |
| Obsidian vault | No note is written. |
| Chrome or Chromium | The capture exits 3 and the visual checks are skipped. Set `VIA_CHROME` to point at a browser. |
| A fresh browser profile | The first capture can take a few minutes and stop with exit 4 (`TimeoutExpired`). Run it again, or set `VIA_SHOT_TIMEOUT=180`. |
| Simulator or device | Native visual checks are skipped. |

### Check a change to Via

```bash
python3 -B plugins/via/scripts/via_eval.py
```

[`plugins/via/evals/cases.json`](plugins/via/evals/cases.json) lists each case as a sample prompt and the behavior I expect: routing (fix now, ticket or reject), the fresh-context fallback when Gemini is missing, unattended runs that change nothing, and Via never committing. The runner checks each one on a throwaway git repo, with no model calls and nothing sent out, and prints `ok` or `FAIL` per case. `--only <id>` runs one case and `--json` prints the results as JSON. `scripts/run_checks.py` runs it too, through `test_via_eval.py`. It does not score visual quality.

A plugin can ship hooks that run code on your machine. Via 0.4.0 has none, but read `plugins/via/` before you install a later version. Want to add a skill of your own? See [CONTRIBUTING.md](CONTRIBUTING.md).

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
- The branch is always `cesar/<TICKET-KEY>` (for example `cesar/NFX-12`), cut from the default branch. The gate reads your prefix from `[branch] prefix` in `~/.config/nousfx/local.toml`; set it there to use your own name.
- Claude reads the ticket's `is blocked by` links first and only starts when all of its blockers are Done.
- `tdd` is used when test-first fits, and `prototype` for UI or state questions that a throwaway build answers faster.
- UI tickets: the prototype is the spec. The ticket lists concrete values (colors, sizes, radii, states), and the PR includes screenshots.

### 4. Review and ship

| Step | What happens |
|---|---|
| Via (`/via spec`, `/via check`) | Runs unasked for UI work: `/via spec` at kickoff, `/via check` before the PR. It detects the platform (Compose, Flutter, web), runs the matching design skills and takes light and dark screenshots. `review-loop` then asks Argon for a second opinion on UI changes when the project is allow-listed (schedule: "Gemini checks" in its `SKILL.md`) |
| `review-loop` | `/simplify` (done inline under 100 changed lines), then rounds of a reviewer chosen by the diff's risk tier (`tier.py`): Sonnet `reviewer-light` for tiers 0 and 1 (1 and 3 rounds), Opus `reviewer` for tier 2, `reviewer-high` for tier 3 (5 rounds). It fixes the real high findings and the real med findings at confidence 60 or more (lists meds under 60 as skipped) and records the result and tier for the gate. The gate runs `tier.py` itself and refuses a lower tier than the diff computes unless I give a reason (`--tier-override`). On Tier 3 and on UI changes it can also ask Gemini for a second opinion (`review.py --allow-list`), only for projects on my allow-list in `~/.config/nousfx/via.json` (the `gemini_code_paths` entry says which folders may be sent) |
| PR gate | `gh pr create` is blocked until a review with 0 open high or med (confidence 60+) findings matches the current code and the branch name has a Jira key |
| PR body | Uses `claude/templates/pull_request_template.md` (ticket, what and why, how to test, screenshots, what I checked myself, and the `Review-Gate:` and `Review-Tier:` lines from `review-gate.py attest`) |

### 5. Keep sessions cheap
- `/handoff` writes `.claude/handoff.md` (it asks once to create and gitignore the file if the project has none), so the next session starts from a short note instead of a huge context.
- One task per session. Use `/compact focus on <topic>` at milestones.
- `python3 -I scripts/usage-report.py --days 14` shows where tokens go.

## What's in the repo

| Path | Purpose |
|---|---|
| `harness/models.toml` | Maps each agent's `role:` to a model and effort per tool; sync fills them in |
| `harness/agents/Explore.md` | Haiku code search; replaces the built-in Explore |
| `harness/agents/researcher.md` | Haiku docs and API lookups |
| `harness/agents/test-runner.md` | Haiku build and test runner; returns only failures |
| `harness/agents/implementer.md` | Sonnet builder for ready-for-agent tickets of size S or M, from a plan the main session writes |
| `harness/agents/reviewer.md` | Read-only Opus code reviewer (tier 2) |
| `harness/agents/reviewer-light.md` | Read-only Sonnet reviewer (tiers 0 and 1) |
| `harness/agents/reviewer-high.md` | Read-only Opus reviewer at high effort (tier 3) |
| `harness/agents/reviewer-deep.md` | Fable second-opinion reviewer for large or risky changes, only after asking |
| `harness/agents/architect.md` | Fable architecture and performance consultant, read-only, only after asking |
| `claude/skills/review-loop/` | `/review-loop`: review-fix loop, rounds set by the risk tier (max 5), never commits |
| `claude/skills/gemini-review/` | `/gemini-review`: second-opinion code review through `gemini-api`. `scripts/argon_review.py` is the Argon UI second opinion that `review-loop` calls; the project must be on the outbound allow-list in `~/.config/nousfx/via.json`. `scripts/gemini_common.py` holds the safety checks both Gemini scripts share; they run on macOS and Windows |
| `claude/skills/handoff/` | `/handoff`: session handoff note |
| `claude/skills/after-merge/` | Runs after a confirmed merge: deletes the branches, moves the Jira ticket to Done, runs `/handoff` |
| `claude/skills/jira-sync/` | `/jira-sync <KEY>`: read-only audit of a Jira board against its repo, plus a next-up list. Applies only approved fixes |
| `claude/skills/prototype-fidelity/` | Rules for work based on an approved prototype: tickets, implementing, review, PR |
| `claude/skills/project-setup/` | Sets up or audits a repo with the shared workflow |
| `claude/hooks/review-gate.py` | PreToolUse hook that blocks `gh pr create` until a review with 0 open high or med (confidence 60+) findings (or an approved `--mark --skip`) is recorded. `--mark --findings <count>` records one; `--mark` alone is refused |
| `claude/hooks/git_refs.py` | The one rule for which `origin/` branch is trusted (`origin/HEAD`, else `origin/main`, else `origin/master`, or the PR base `tier.py --base-ref` names). The gate imports it for `--mark`; `tier.py` loads it by path. Keep it next to the gate: without it `--mark` refuses |
| `claude/hooks/pr_create_parser.py` | Shell parser the gate uses to find `gh pr create`, the directory it runs in, and its PR body flags. Keep it next to the gate: without it the gate blocks any command that mentions `gh` and `pr` |
| `claude/hooks/test_review_gate.py` | Gate test suite: `python3 claude/hooks/test_review_gate.py` |
| `claude/templates/pull_request_template.md` | Standard PR body. Copy it to `.github/` per repo |
| `install.sh`, `install.ps1` | Mac and Windows wrappers: dry run of `scripts/nousfx_sync.py`, ask, then the real run |
| `scripts/nousfx_sync.py` | The sync: writes each tool's rules, agents, skills, hooks and MCP servers ([docs/sync.md](docs/sync.md)) |
| `CHANGELOG.md` | What each release changed |
| `scripts/usage-report.py` | Read-only token and cost report |
| `docs/agents/` | This repo's own tracker setup (Jira project `NFX`) |

### Skills this framework uses but doesn't store
Via skips any missing design skill and lists it under "Skipped". Sync installs most of them for me, from a lock file:
- User skills pinned in [`harness/skills.lock.json`](harness/skills.lock.json), each with a commit and a checksum. `python3 -B scripts/nousfx_sync.py` fetches, verifies and links them on every machine ([how it works](docs/sync.md#outside-skills)):
  - `ui-ux-pro-max`
  - the Android set (`adaptive`, `edge-to-edge`, `jetpack-compose-performance`, `navigation-3`, `testing-setup`)
  - `material-3` (if you also have the `material-3@material-3-skill` plugin, remove it so there is one copy)
  - Matt Pocock's workflow skills (`grilling`, `to-spec`, `to-tickets`, `tdd`, `prototype` and others)
- Not pinned yet: `styles`. It only comes with the `android` CLI, because the `android/skills` repo does not have it. Install it by hand.
- Claude plugins: `design:accessibility-review`, `design:design-critique`, `design:ux-copy` and `anthropic-skills:frontend-design`.

## The PR review gate
- Blocks `gh pr create` (including wrappers such as `bash -c`, `eval` and `$(...)`) and create-pull-request MCP tools unless all three hold:
  - the branch is named `cesar/<JIRA-KEY>` (your prefix from `~/.config/nousfx/local.toml`; CI checks the shape `<prefix>/<KEY>`). `review-gate.py --allow-branch "reason"` records an approved exception.
  - a review marker matches the code: a git tree hash of the working tree or `HEAD`.
  - that review has 0 open high or med (confidence 60+) findings (`--mark --findings <count>`), or you approved a skip (`--mark --skip "<reason>"`).
- The marker lives in the git dir, so it is never committed.
- Limits to know:
  - It does not catch every form, for example PRs created from script files, `gh alias`, SDK calls, shell functions, remote shells or `find -exec`. `gh api`, `curl` and `wget` creates are blocked.
  - An error while checking a create blocks it. The call passes only outside a git repo, when the folder is missing, or when git is not installed.
  - A PR opened in the GitHub web UI is not caught.
  - Hooks load at session start, so restart running sessions after installing.

### Hands-off review loop with `/goal`
A `/goal` evaluator reads only the transcript, so paste the procedure as the goal. For a done-condition built on `AC-Proof`, see the template in `docs/review-gate.md`. Start the session in auto mode so test commands don't prompt.

```text
/goal Review-fix loop on everything on this branch since it left the default branch, plus untracked files. Run git merge-base origin/HEAD HEAD || git merge-base origin/main HEAD first; it prints the base hash. If it fails, stop, say so and record nothing. Then use git diff <base hash> and git ls-files --others --exclude-standard, with the literal hash, and give that hash to the reviewer. If both are empty, stop, say so and record nothing. Then run python3 -B <review-loop skill folder>/scripts/tier.py --base <base hash> (`python` on Windows; the skill lives in ~/.claude/skills for Claude Code and ~/.agents/skills for agy) (add --labels with the ticket labels; if the branch has a ticket key and you cannot read them, use at least tier 2 and say "labels unavailable" in the reasons), print the tier and reasons, and use the reviewer for that tier: 0 reviewer-light with effort low and 1 round, 1 reviewer-light and 3 rounds, 2 reviewer and 5 rounds, 3 reviewer-high and 5 rounds. If tier.py fails, use tier 2. Run tier.py again after each round's fixes; the tier only goes up. On a rise, switch to that reviewer and cap; rounds already used count. At tier 0 or 1, when the cap's final round had fixes that no reviewer has seen, raise the tier by one and run another round. Each round, print "Round N/<cap>", run that reviewer subagent with the ticket's goal and acceptance criteria ("no ticket" if the branch has no key, "ticket unavailable" if Jira cannot be read; never your own retelling) and the scope, with no builder summary, verify its findings, fix the real high ones and the real meds at 60 or more, then run tests. Ask the reviewer for the format `ID | severity | confidence | file:line | problem | fix | quoted code` (confidence 0-100). A med with confidence under 60 is listed, not fixed: report it as "skipped: low confidence". Verify every high, and before rejecting a high, ask the same reviewer once for its evidence and decide with that, asking once per finding ID (a fresh call that answers NO FINDINGS is not evidence against the high); check the quoted code is in the file or in the diff, and reject a med whose quote is not found; a rejected finding stays rejected when it returns, unless the reviewer brings new evidence (a corrected quote counts); until then it is not open and is not counted. If SendMessage is not available, call the same reviewer again with the finding. A finding with missing or non-numeric confidence counts as 60 or more. Done when the reviewer's latest output is NO FINDINGS, low-only, or low plus med under 60 (rejected findings do not count), and the passing test output is shown. Stop after the last round of the cap regardless and list what remains. Then run the tests one last time and show the result. If any test fails, say so and record nothing. Otherwise run `python3 <gate> --mark --findings <open high count + open med at 60 or more> --tier <final tier> --reasons "<tier reasons>"` (`python` on Windows), where `<gate>` is the gate path printed in the gate's block message, or the path in the `command` of the review-gate hook in your tool's hook config (`~/.claude/settings.json` for Claude Code, the plugin's `hooks.json` for agy); the gate blocks the PR while the count is above 0. Do not commit.
```

- The cap counts rounds (`Round N/<cap>`), not turns. One round takes several turns.
- "Clean" means no high and no med at confidence 60 or more, other than findings already rejected. Reviewers almost always find something minor.
- For interactive use, just run `/review-loop`.

## Security model

NousFX gives agents real permissions on your machine, so it is built around a few rules:

| Risk | How NousFX handles it |
|---|---|
| Prompt injection from tickets, PRs, web pages, second-opinion output | Treated as data, never instructions. Work comes only from your tracker. Gemini output is verified finding by finding |
| Code or screenshots leaving your machine | Projects not on the `outbound_allowlist` in `~/.config/nousfx/via.json` (keyed `host/owner/repo`, for example `github.com/acme/app`) get local-only output for Gemini, Jira, Figma, Artifacts and Obsidian. Via blurs personal data when it captures web pages, then Claude looks at each screenshot and drops any that still shows personal data, tokens or account details. Code goes to Gemini only through those scripts |
| Secrets | Keys live in the OS keychain, never in this repo or in config files. Project setup includes a secret check |
| Destructive git | Agents never commit, push or stash on their own. A PR needs a recorded review |
| Premium spend | Fable agents run only after you say yes |
| Third-party skills | They run with Claude's permissions. Read a skill's `SKILL.md` and scripts before installing, and pin it to a commit |

Never commit `settings.local.json`, handoffs, memory, session history or anything holding a secret.

## Optional: Fable
`reviewer-deep` and `architect` need Fable access, which is included on Max up to 50% of the weekly limit (terms may change). Without it, remove:
- `harness/agents/reviewer-deep.md` and `harness/agents/architect.md`
- `claude/skills/review-loop/references/fable.md` and the "Fable offers" stub in the review-loop skill, and the sentences that say "see Fable offers"
- their lines in `harness/overlays/claude.overlay.md`

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

| Release | Theme | Status |
|---|---|---|
| v1.0, v1.1 | First releases | Shipped 2026-11-09 |
| **v1.2** | **Hardened harness**: tickets end in `Proof:` lines that `prove.py` runs, the gate reads its check command from the default branch, CI verifies the review attestation, and one source for the agents (`harness/agents`) | **Shipped 2026-11-13.** The Windows live checks moved to v1.3 |
| v1.3 | Via 2: a gate for UI PRs, docs and a fresh-user test | Next |
| v1.4 | Unattended mode and PR evidence | Later |

Details of each release are in [CHANGELOG.md](CHANGELOG.md).

- **Via, the designer** (named after Octavia, one of my cats). The old UI review skill grew into a designer:

  | Command | What it does |
  |---|---|
  | `/via spec <ticket>` | Sets the visual direction and specs before code |
  | `/via check` | Design QA before the PR, enforced by a gate |
  | `/via audit` | Audits the app at any time |

  For each problem it shows before/after prototypes. You approve once which fixes happen now and which become Jira tickets; tickets carry inline screenshots and a spec table. It exports to Obsidian and Figma. It learns from every project, but only saves a lesson after you approve it. Every integration is optional, so it works with only Claude Code installed.
- **Plugin marketplace.** Install Via with `claude plugin marketplace add ctorres345/NousFX`. The rest of the harness comes from the sync; third-party skills are already pinned by commit and checksum.
- **Plugins named after my cats.** Only Via is planned; the rest are ideas for later.

  | Plugin | Role | Would hold |
  |---|---|---|
  | **Via** (Octavia) | Designer | `/via` setup, spec, check, audit and export |
  | Cassie (Cassiopeia) | Planner and navigator | Grilling, spec to tickets, wayfinder, roadmap, `jira-sync` |
  | Rosalia | Guardian | Secret checks, git guardrails, skill checksums, outbound rules |
  | Misty | Memory and context | Handoff, compaction, usage report, graphify |
