# Runbook

The steps I repeat by hand for NousFX. Each one changes my own machine only; nothing here goes live for anyone else. If a step changes, update this file in the same PR.

| When | Section |
|---|---|
| Setting up a new machine | [Install on a new device](#install-on-a-new-device) |
| After merging a PR that touches `claude/` or `harness/` | [Update after a merge](#update-after-a-merge) |
| Setting up one repo | [Set up one project](#set-up-one-project) |
| After any install or sync | [Smoke checks](#smoke-checks) |
| Installing or updating Via | [Install or update Via](#install-or-update-via) |
| Something broke after a sync | [Roll back a sync](#roll-back-a-sync) |
| About once a week | [Check token usage](#check-token-usage) |

## How it fits together

```
~/Projects/NousFX (this clone)
   │  python3 -B scripts/nousfx_sync.py        (./install.sh or .\install.ps1 run it for me)
   ▼
~/.claude/            rules (CLAUDE.md), agents, skills, hooks, settings.json
~/.gemini/config/plugins/nousfx/     (agy) rules, skills, gate hook, MCP file
~/.agents/skills/     one shared skills folder; each tool links to it
~/.config/nousfx/     state home: my local rules, via.json allow-list, backups, pinned outside skills
```

- **Sync** is the only thing that writes those files. `install.sh` (Mac) and `install.ps1` (Windows) copy nothing themselves: they run a dry run, ask, then run the sync for real. Arguments pass through, except the modes `--check` and `--restore`, which conflict with the dry run: run those with `scripts/nousfx_sync.py` directly.
- **Models** are mapped per role in `harness/models.toml` (`fast`, `balanced`, `strong`, `deep`). Change a model there and only the agents with that role change.
- **State home** is `~/.config/nousfx/`. It holds `local.md`, `local.claude.md` and `local.agy.md` (mine alone, never committed), `via.json` (the outbound allow-list), `mcp-owned.json` and `backups/<stamp>/`.
- **agy delegation and model choice** belong to my `gemini-orchestrator` plugin, not to sync. Install that plugin on each machine that runs agy.
- The full switch list and file layout is in [docs/sync.md](sync.md).

## Install on a new device

1. Check Claude Code is 2.1.293 or later (Haiku 5.5 needs it):

   ```bash
   claude --version
   ```

2. Clone:

   ```bash
   git clone https://github.com/ctorres345/NousFX.git && cd NousFX
   ```

3. Put my personal rules in the state home (copy `harness/local.md.example`, or bring `local.md` from another machine). Skip this on a friend's machine.

   ```bash
   mkdir -p ~/.config/nousfx && cp -n harness/local.md.example ~/.config/nousfx/local.md
   ```

4. Run the installer (it needs Python 3 on PATH as `python3` or `python`). It shows the dry run, then asks before writing. Add `agy` only on a machine that has agy:

   ```bash
   ./install.sh --tools claude,agy
   ```

   On Windows, in PowerShell (the same arguments work). If PowerShell refuses to run the script, allow it for this one run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install.ps1 --tools claude,agy
   ```

   Read the dry run first. A skill folder I made by hand shows up as a collision and is skipped; look at it, then re-run with `--yes-replace NAME`.

   Two things the old copy installer did that the sync does not:
   - The sync refuses to write through a symlink, including a symlinked `~/.claude/settings.json` (a dotfiles-managed one). It stops with "is a symlink" and writes nothing. Replace the link with a regular file first.
   - `CLAUDE_HOME` is ignored. To aim at another home, pass `--home DIR` (and `--state-home DIR`); the default is the home in the user record.

5. Add these two keys to `~/.claude/settings.json` by hand. Do not copy a whole settings file from another machine; mine holds machine-specific `autoMode` and graphify hooks.

   ```json
   "model": "opusplan",
   "autoCompactWindow": 300000
   ```

6. Start a **new** session (hooks and agents load at session start) and run `/agents`. I should see `Explore`, `researcher`, `reviewer-light`, `reviewer`, `reviewer-high`, `reviewer-deep`, `architect` and `test-runner`.
7. Run the [smoke checks](#smoke-checks).

UI skills that `ui-review` uses are not in this repo. `harness/skills.lock.json` pins most of them and sync installs them (see the README). Missing ones are skipped and reported.

## Update after a merge

1. Update `main`:

   ```bash
   git switch main && git pull --ff-only
   ```

2. See what would change. Every difference should come from merged PRs:

   ```bash
   python3 -B scripts/nousfx_sync.py --dry-run --tools claude,agy
   ```

3. Apply it (the installer asks first, or run the sync directly):

   ```bash
   ./install.sh --tools claude,agy
   ```

4. Confirm nothing drifted, then start a new session so hooks reload:

   ```bash
   python3 -B scripts/nousfx_sync.py --check --tools claude,agy
   ```

   It prints `no drift` and exits 0. A Claude session also warns me at start when this machine is behind `origin/main` (the drift hook).

## Set up one project

For a repo other than this one. Project mode writes `AGENTS.md` and `CLAUDE.md` stubs when missing, `.githooks/pre-push`, the `.agents/skills` link and the MCP files from `.nousfx/mcp.toml`. It skips hand-written files and shows a diff.

```bash
python3 -B scripts/nousfx_sync.py --project ~/Projects/my-app --tools claude,agy --dry-run
```

```bash
python3 -B scripts/nousfx_sync.py --project ~/Projects/my-app --tools claude,agy
```

Then `git config core.hooksPath .githooks` in that repo (sync does not set it). Each repo that uses the gate needs a `[check] command` in `.nousfx/project.toml`; the `project-setup` skill proposes one. Details are in [docs/sync.md](sync.md#project-mode).

## Smoke checks

Run after a first install and after anything that touches the gate, rules or skills. The commands below use a temp home so a real machine is never touched; on a real machine, drop `--home` and `--state-home`.

```bash
T=$(mktemp -d)
python3 -B scripts/nousfx_sync.py --home "$T" --state-home "$T/state" --tools claude,agy
python3 -B scripts/nousfx_sync.py --check --home "$T" --state-home "$T/state" --tools claude,agy
head -c 90 "$T/.claude/CLAUDE.md"
ls "$T/.claude/skills" | wc -l
```

The sync run ends with `hook self-test passed` and `agy gate self-test passed`. `--check` prints `no drift`. The first line of `CLAUDE.md` starts `<!-- generated by nousfx_sync`.

The gate must block a PR with no review and let other commands through. Use a scratch repo on a ticket-named branch (a branch with no commits yet inherits the last merged review marker, so do not test on a fresh branch of a real repo):

```bash
R=$(mktemp -d) && cd "$R" && git init -q -b cesar/NFX-1 && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
for c in 'gh pr create' 'ls'; do printf '{"tool_name":"Bash","cwd":"%s","tool_input":{"command":"%s"}}' "$PWD" "$c" | python3 -B "$T/.claude/hooks/review-gate.py" >/dev/null 2>&1; echo "$c -> exit $?"; done
```

Expected: `gh pr create -> exit 2` and `ls -> exit 0`.

For agy and for a real machine, also check by hand in a live session: rules show up (ask it for its rules), skills are listed, the Atlassian server answers, and `gh pr create` is denied without a review. agy has no Skill tool and no session-start event, so its rules carry the instructions.

Results (a dash means not run yet; each cell gets a date when it is):

| Tool and machine | Rules load | Skills visible | MCP works | Gate blocks a PR |
|---|---|---|---|---|
| Claude Code, Mac (temp home) | ok 2026-11-06 (4,192 B file, generated header) | ok 2026-11-06 (45 linked) | n/a: Claude uses its own Atlassian connector; `.claude.json` gets only servers listed for it | ok 2026-11-06 (exit 2; `--check` clean) |
| Claude Code, Mac (live session) | ok 2026-11-06 (global rules in the session are the generated file; `--check` exit 0, no drift) | ok 2026-11-06 (18 replaced skills are links into `~/.agents/skills`; skill list shows them; the other skills were not re-counted) | n/a: Claude uses its own Atlassian connector; it answered in the live session 2026-11-06 | ok 2026-11-06 (live hook denied `gh pr create` on an unreviewed `cesar/NFX-1` scratch branch; a `cd` to a variable path is also denied). `/agents` and `review-loop` end to end: pending (NFX-53) |
| agy, Mac (temp home) | ok 2026-11-06 (plugin `rules` written) | ok 2026-11-06 (45 linked) | file written 2026-11-06 (`mcp_config.json` has atlassian); live answer pending (NFX-53) | ok 2026-11-06 (`agy gate self-test passed`); live deny pending (NFX-53) |
| agy, Mac (live session) | pending (NFX-53) | pending (NFX-53) | pending (NFX-53) | pending (NFX-53) |
| Claude Code, Windows | pending (NFX-53) | pending (NFX-53) | pending (NFX-53) | pending (NFX-53) |
| agy, Windows | pending (NFX-53) | pending (NFX-53) | pending (NFX-53) | pending (NFX-53) |
| OpenCode | not checked ([NFX-50](https://cjthdev.atlassian.net/browse/NFX-50)) | not checked | not checked | not checked |

## Check the review gate

1. The gate files in a synced home match the repo:

   ```bash
   for f in review-gate.py pr_create_parser.py; do cmp -s "claude/hooks/$f" "$HOME/.claude/hooks/$f" && echo "match $f" || echo "DIFFERS $f"; done
   ```

2. The suites pass (the check the CI job runs, about 2 minutes):

   ```bash
   python3 -B scripts/run_checks.py
   ```

3. `settings.json` registers the gate exactly once:

   ```bash
   grep -c 'review-gate.py' ~/.claude/settings.json
   ```

4. In CI, the `review-gate / check` job requires a `Review-Gate:` line in the PR body (from `review-gate.py attest`), using the gate from the base branch, then runs the project's check command on the PR head. It is the gate for tools that cannot block a PR themselves, so it must be a required check on `main` (GitHub: Settings, Branches, rule for `main`, "Require status checks", add `check`). To confirm:

   ```bash
   gh api repos/ctorres345/NousFX/branches/main/protection/required_status_checks --jq '.contexts'
   ```

   The job does not re-check the `Review-Tier:` line; the gate checks the tier when it records the review (`--mark --tier`).

## Install or update Via

Via comes from the marketplace in this repo; sync does not install it.

1. Add the marketplace (first time only). From GitHub:

   ```bash
   claude plugin marketplace add ctorres345/NousFX
   ```

   Or from a local clone, run this inside it:

   ```bash
   claude plugin marketplace add ./
   ```

2. Install (first time only):

   ```bash
   claude plugin install via@nousfx
   ```

   To update after a merge to `main`, refresh the marketplace and then update the plugin. Installing again does not upgrade it:

   ```bash
   claude plugin marketplace update nousfx
   ```

   ```bash
   claude plugin update via@nousfx
   ```

3. In a Claude Code session run `/reload-plugins`, then `/via setup`. Check the table lists each integration and ends with the `Via integrations:` line.
4. Run `/via setup` again. The answer must say the config is `unchanged`.
5. To remove it: `claude plugin uninstall via`. `~/.config/nousfx/via.json` stays (and so does an old `~/.claude/via.json`, which `/via setup` copied once); delete them by hand if I want them gone. If I once hand-copied `via_outbound.py` into `~/.claude/plugins/cache/nousfx/via/0.4.0/scripts/`, I delete that copy: the Argon script now finds the script in the repo and never reads the cache.

Before releasing a change, run `python3 plugins/via/scripts/test_via_setup.py`, `python3 plugins/via/scripts/test_via_run.py` and `claude plugin validate . --strict`.

## Roll back a sync

Every sync backs up what it changes under `~/.config/nousfx/backups/<stamp>/`, and `--restore` puts the last backup back. Files the run created are removed.

1. See what the last run did:

   ```bash
   ls ~/.config/nousfx/backups | tail -3
   ```

2. Undo it:

   ```bash
   python3 -B scripts/nousfx_sync.py --restore --tools claude,agy
   ```

3. Start a new session. Run `--restore` again to go one run further back.

Old `.bak-<stamp>` files from the retired copy-based installer are only listed. `--clean-backups` moves them into the run's backup.

## Check token usage

About a week after a change to the rules or the agents, compare against the last report:

```bash
python3 -I scripts/usage-report.py --days 14
```

Look at average context per session and Fable cost. The baseline from 2026-10-19 was about $277 over 14 days, with cache reads at 60–66% of it.
