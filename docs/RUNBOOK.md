# Runbook

The steps I repeat by hand for NousFX. Each one changes my own machine only, except [Cut a release](#cut-a-release), which pushes a tag to GitHub and releases a Jira version. If a step changes, update this file in the same PR.

| When | Section |
|---|---|
| Setting up a new machine | [Install on a new device](#install-on-a-new-device) |
| After merging a PR that touches `claude/` or `harness/` | [Update after a merge](#update-after-a-merge) |
| Setting up one repo | [Set up one project](#set-up-one-project) |
| After any install or sync | [Smoke checks](#smoke-checks) |
| Installing or updating Via | [Install or update Via](#install-or-update-via) |
| Something broke after a sync | [Roll back a sync](#roll-back-a-sync) |
| About once a week | [Check token usage](#check-token-usage) |
| Monday, when the weekly skills report says `UPDATES` or `ERRORS` | [Update pinned skills](#update-pinned-skills) |
| Every ticket in a Jira version is Done | [Cut a release](#cut-a-release) |

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

- **Sync** is the only thing that writes those files. `install.sh` (Mac) and `install.ps1` (Windows) copy nothing themselves: they run a dry run, ask, then run the sync for real. Arguments pass through, except the modes `--check` and `--restore`, which conflict with the dry run, and `--diff`, which only goes with one: run those with `scripts/nousfx_sync.py` directly.
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

6. Start a **new** session (hooks and agents load at session start) and run `/agents`. I should see `Explore`, `researcher`, `reviewer-light`, `reviewer`, `reviewer-high`, `reviewer-deep`, `architect`, `implementer` and `test-runner`.
7. Run the [smoke checks](#smoke-checks).

The design skills Via uses are not in this repo. `harness/skills.lock.json` pins most of them and sync installs them (see the README). Missing ones are skipped and reported.

## Update after a merge

1. Update `main`:

   ```bash
   git switch main && git pull --ff-only
   ```

2. See what would change. Every difference should come from merged PRs. The dry run prints one line per file with its `+added -removed` line counts; when a file or a count is more than the merged PRs explain, read the full diffs with `--diff`:

   ```bash
   python3 -B scripts/nousfx_sync.py --dry-run --tools claude,agy
   ```

   ```bash
   python3 -B scripts/nousfx_sync.py --dry-run --diff --tools claude,agy
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

Then turn the pre-push hook on in that repo (sync does not do it). Run `ls .git/hooks | grep -v sample` first. If other hooks are there (graphify's, for example), link it with `common=$(git rev-parse --path-format=absolute --git-common-dir); ln -s "$common/../.githooks/pre-push" "$common/hooks/pre-push"`, because `git config core.hooksPath .githooks` would switch those hooks off. Use `core.hooksPath` only when `.git/hooks` holds nothing else. Each repo that uses the gate needs a `[check] command` in `.nousfx/project.toml`; the `project-setup` skill proposes one. Details are in [docs/sync.md](sync.md#project-mode).

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
   for f in review-gate.py pr_create_parser.py git_refs.py; do cmp -s "claude/hooks/$f" "$HOME/.claude/hooks/$f" && echo "match $f" || echo "DIFFERS $f"; done
   ```

2. The suites pass (the check the CI job runs, about 2 minutes):

   ```bash
   python3 -B scripts/run_checks.py
   ```

   Suites run cheapest first, so a failing fast suite shows within seconds. For a quick local loop, `--fail-fast` stops at the first failure; the gate and CI never pass it and always run the full set.

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

1. Pick the scope. Not sure? Use `user`.

   | Scope | Via works in | Shared with the team |
   |---|---|---|
   | `user` (default) | All my projects | No |
   | `project` | One project | Yes, through that project's `.claude/settings.json` |
   | `local` | One project | No, it stays in `.claude/settings.local.json` |

   For `project` or `local`, I run every command below inside that project's folder and add `--scope project` (or `--scope local`) to `marketplace add`, `install`, `update` and `uninstall`. Update and uninstall must use the same scope as the install. `marketplace update` takes no scope.

2. Add the marketplace (first time only). From GitHub:

   ```bash
   claude plugin marketplace add ctorres345/NousFX
   ```

   Or from a local clone, run this inside it:

   ```bash
   claude plugin marketplace add ./
   ```

3. Install (first time only):

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

4. In a Claude Code session run `/reload-plugins`, then `/via setup`. Check the table lists each integration and ends with the `Via integrations:` line.
5. Run `/via setup` again. The answer must say the config is `unchanged`.
6. To remove it: `claude plugin uninstall via` (with the same scope). `~/.config/nousfx/via.json` stays and is the same for every scope (and so does an old `~/.claude/via.json`, which `/via setup` copied once); delete them by hand if I want them gone. If I once hand-copied `via_outbound.py` into `~/.claude/plugins/cache/nousfx/via/0.4.0/scripts/`, I delete that copy: the Argon script now finds the script in the repo and never reads the cache.

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

## Update pinned skills

The outside skills in `harness/skills.lock.json` and the `android` CLI go stale without a sign. A weekly task runs `check`, which only reads. Applying an update is a normal session under a ticket where I read the diff first. A bump touches `harness/`, a tier 3 path, so nothing here is unattended.

1. Read the weekly report, or run the check by hand:

   ```bash
   cat ~/Releases/NousFX/skills-check/latest.md
   python3 -I -B scripts/nousfx_skills_update.py check
   ```

   The first line says what happened:

   | First line | Meaning |
   |---|---|
   | `NO UPDATES` | every pinned skill is as pinned and the `android` CLI is the latest build |
   | `UPDATES: N changed` | N skills (or the `android` CLI) differ upstream; the names are in the lines below |
   | `ERRORS: N could not be checked (...)` | an upstream was unreachable, a pin was force-pushed away, a folder was removed, an upstream tree was refused (`could not stage`: a symlink or an odd file name, so read it before anything else), or the `android` lookup failed. It is never reported as `NO UPDATES`, so a scheduler that cannot reach GitHub shows up here |

   The last line of the report is `checked: <UTC time>`. If it is older than a week the schedule is not running (a missing script, an interpreter that does not start, the agent not loaded): treat that as an error, whatever line 1 says. A run that dies midway leaves `ERRORS: the check did not finish`.

   Under each changed skill: the diff size, then a `flag:` line for each piece of executable or behavior surface: a non-Markdown file added or changed (`[script]` for code), a `!` command line in `SKILL.md`, a frontmatter change (`description`, `allowed-tools`, `disable-model-invocation` come first) and each new URL.
2. For a skill I want, open a ticket (label `harness`).
3. On `cesar/<KEY>`, bump the skills I picked. It prints the full diff and the same flags, then writes the new commit and sha256 for those skills only, or nothing if any step fails:

   ```bash
   python3 -I -B scripts/nousfx_skills_update.py bump <skill>...
   python3 -I -B scripts/nousfx_skills_update.py bump <skill> --commit <sha>   # an older commit instead of the HEAD
   ```

   It refuses a commit that is not in the upstream HEAD history (another branch, or a fork's commit), and prints `WARNING` when the pinned commit left that history (a force-push), because then the diff may hold more than the last change.

4. Read every flagged line before the PR. A new script, a `!` command, a wider `allowed-tools` or a new URL is a reason to stop and look at the file, not to skim.
5. Check, then PR:

   ```bash
   python3 -B scripts/nousfx_sync.py --check --summary
   python3 -B scripts/run_checks.py
   ```

   Then `review-loop` and the PR. After the merge, [Update after a merge](#update-after-a-merge) installs the new files.
6. The `android` CLI is not in the lock. When the report says a newer build is published, I run `android update` myself.

### Weekly schedule

A macOS LaunchAgent runs `check` every Monday at 09:00 (a run missed while the Mac sleeps happens at wake) and writes the report outside the repo. It never writes the lock and never opens a PR. Save this as `~/Library/LaunchAgents/com.nousfx.skills-check.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.nousfx.skills-check</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>mkdir -p "$HOME/Releases/NousFX/skills-check" &amp;&amp; export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin" &amp;&amp; /usr/bin/python3 -I -B "$HOME/Projects/NousFX/scripts/nousfx_skills_update.py" check --report "$HOME/Releases/NousFX/skills-check/latest.md" &gt;"$HOME/Releases/NousFX/skills-check/last-run.log" 2&gt;&amp;1</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
</dict>
</plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nousfx.skills-check.plist   # turn it on
launchctl kickstart -k gui/$(id -u)/com.nousfx.skills-check                             # run it once now
launchctl print gui/$(id -u)/com.nousfx.skills-check | head -20                         # list it and its last exit code
launchctl bootout gui/$(id -u)/com.nousfx.skills-check                                  # turn it off
```

The job runs the script from the main checkout, so it works only after the PR that added the script is merged and that checkout is updated. An exit code of 1 means updates, 2 means errors; both still write the report. The skill repos are public, so `git` needs no GitHub login; a failure to reach them shows as `ERRORS` in `latest.md`.

## Cut a release

A release is a Jira version (`v1.1`), a dated `CHANGELOG.md` heading (`## 1.1.0 (2026-11-09)`) and a git tag (`v1.1.0`). I trigger it; an agent prepares the PR and the commands.

Each command below uses the version in two forms; set them once in the shell, for example `V=1.2.0` (CHANGELOG and tag) and the Jira name `v1.2`.

1. Every ticket in the version is Done. This JQL, with the Jira name filled in, must return nothing:

   ```text
   project = NFX AND fixVersion = "v1.2" AND statusCategory != Done
   ```

2. `main` passes the suites. The `review-gate / check` job runs only on PRs, never on `main`, so run them in a throwaway worktree of `origin/main`:

   ```bash
   git fetch origin && W=$(mktemp -d) && git worktree add -q --detach "$W" origin/main && (cd "$W" && python3 -B scripts/run_checks.py); rc=$?; git worktree remove --force "$W"; echo "checks exit: $rc"
   ```

   It must end with `checks exit: 0`.

3. Date the heading in a PR on a ticket branch (`cesar/<KEY>`). Tickets write their entries in `changelog.d/<KEY>.md`, not in `CHANGELOG.md` (NFX-292). One command folds every fragment into `## Unreleased`, renames that heading to `## X.Y.Z (YYYY-MM-DD)` with today's date, and deletes the fragments:

   ```bash
   python3 -B scripts/changelog_fragments.py fold --version "$V" --date "$(date +%F)" && python3 -B scripts/changelog_fragments.py check --release
   ```

   The check fails while a fragment is left. A fragment that reaches the release PR after the fold (from merging `main` into it) cannot be folded again, since the version heading exists: move its bullets (and its `###` section line, if the dated block lacks it) into the dated section by hand and delete the fragment. Before the PR, `grep -n "^## " CHANGELOG.md` shows every heading in the dated form. Then I merge the PR.
4. Tag that PR's merge commit, not whatever `main` holds now (later PRs may have merged), and push the tag. With `V` and `N` (the PR number, `N=73`) set, in one shell; it stops if the PR has no merge commit, that commit lacks the dated heading, or it still holds a fragment (a PR that merged while the release PR was open, so its code ships in this version). In that last case, hold other merges to `main` until the tag is pushed, move those fragments' bullets (and a `###` section line the dated block lacks) into the dated section by hand in a PR that also deletes them, then run this step with that PR's number:

   ```bash
   SHA=$(gh pr view "$N" --json mergeCommit --jq .mergeCommit.oid) && [ -n "$SHA" ] && [ "$SHA" != null ] && git fetch origin && git show "$SHA:CHANGELOG.md" | grep -Eq "^## $V \([0-9]{4}-[0-9]{2}-[0-9]{2}\)$" && [ -z "$(git ls-tree --name-only "$SHA" changelog.d/ | grep -vE '/(README\.md|\.gitkeep)$')" ] && git tag -a "v$V" -m "NousFX $V" "$SHA" && git push origin "v$V"
   ```

   Backfill only: when one PR dated two versions (NFX-128 dated 1.0.0 and 1.1.0 together), the older tag goes on the commit that finished that version, which I pick. Its CHANGELOG still says unreleased, so it gets no heading check: `git tag -a v1.0.0 -m "NousFX 1.0.0" <commit> && git push origin v1.0.0`.

5. Mark the version released in Jira with today's date (Releases, the version, Release; or the agent through the Atlassian MCP after I say so).
