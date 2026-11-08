# Sync: one command writes each tool's global rules

I keep my rules in layers and let one command assemble the global rules for Claude Code and, when I ask for it, for agy (Antigravity CLI).

```
harness/AGENTS.md                  neutral rules, models named by role (fast / balanced / strong / deep)
harness/overlays/claude.overlay.md Claude only: Skill tool, goal runs, subagent names
harness/overlays/agy.overlay.md    agy only: no Skill tool, review before PR by hand
~/.config/nousfx/local.md          mine alone, never committed, tool-neutral (copy harness/local.md.example)
~/.config/nousfx/local.claude.md   mine, Claude only (optional)
~/.config/nousfx/local.agy.md      mine, agy only (optional)
        |
        v   python3 -B scripts/nousfx_sync.py [--tools claude,agy]
~/.claude/CLAUDE.md                generated, header holds a hash of the sources and names them by path
~/.claude/settings.json            review gate, drift warning and deny list (other settings kept)
~/.claude/hooks/                   review-gate.py, pr_create_parser.py and nousfx-drift.py
~/.gemini/config/plugins/nousfx/   (agy) hooks.json, and hooks/ with agy_gate.py and its own copy of the gate
```

## Switches

| Switch | What it does |
|---|---|
| none | back up, write, then self-test the gate hook |
| `--dry-run` | print a diff, write nothing |
| `--check` | list drifted files, exit 1 on drift, write nothing |
| `--check --summary` | the same exit code, but one JSON line of counts (`files`, `skills`, `outside`) and a `rules` object (`bytes`, `budget`, `repeats`). The drift warning reads it |
| `--restore` | put back the last backup (files that did not exist are removed) |
| `--clean-backups` | also move the `.bak-<stamp>` files the old copy-based `install.sh` left behind into the backup (otherwise they are only listed) |
| `--yes-replace NAME` | let my skill NAME replace a folder sync does not own (moved into the backup first) |
| `--tools claude,agy` | which tools to write. The default is `claude` only, so nothing touches `~/.gemini` until I name `agy` |
| `--project DIR` | set up one repo (see Project mode below) |
| `--home`, `--state-home`, `--repo` | override locations; tests always use these |

## Drift warning

When I change NousFX on one machine, the other one tells me at the start of a Claude Code session. Sync registers a `SessionStart` hook, `~/.claude/hooks/nousfx-drift.py`, with the clone's path and the `--tools` list written into its command. It runs on a fresh start (`matcher: startup`), not on resume, clear or compact.

What it checks:
- **This machine is out of date.** It runs `nousfx_sync.py --check --summary` in the clone. Any exit 1 with a valid summary counts, so a skill collision I have not decided yet also warns.
- **The clone is behind `origin/main`.** It counts `HEAD..origin/main` from the last fetch. The hook starts `git fetch` first, in the background, at most every 10 minutes (no password prompts, no window on Windows), so the next session sees fresh data. The warning can be one session late, and it never waits for the network.

- **The rules are too long or repeat the harness.** It reads the summary's `rules` object, also on exit 0 (the budget only warns). See Rules budget below.

What it prints: nothing when all is well. Otherwise one line, for example `NousFX on this machine is out of date (2 files, 13 skills need a decision). Run python3 -B scripts/nousfx_sync.py in <clone>.` On Windows the command says `python`. The same text goes to me and to the model.

What it never does: sync, pull or write a file of mine. The background fetch is the only write, and it only moves `origin/main` in the clone. Any error, a missing clone or more than 2 seconds means it says nothing and exits 0, so it cannot break a session start.

A rules warning is one more line, for example `NousFX rules: 9492 bytes, over the 6144 byte budget. Run python3 -B scripts/nousfx_sync.py --dry-run in <clone> to see which, then trim the local files (docs/sync.md).`

To turn it off, remove the `SessionStart` entry that names `nousfx-drift.py` from `~/.claude/settings.json`. The next sync puts it back.

agy has no session-start hook (only `PreToolUse` is proven), so its rules carry a line that asks the model to run `--check --summary --tools agy` at the start of a session and report drift. Sync fills the clone's path into that line.

## Rules budget

Longer instruction files lower adherence, so the rules a tool loads stay short on every machine, not just in the repo. Every run measures each tool's generated rules without the header, local layers included (`--check --summary` reports the numbers in its `rules` object). Every other run warns on stderr:

- `warning: rules budget: .claude/CLAUDE.md is 9492 bytes without its header, over the 6144 byte budget. ...` when the body is over budget.
- `warning: repeated rule: "<sentence>" is in <local file> and <harness file>` for each sentence of 6 words or more that a local layer shares with `harness/AGENTS.md` or the tool's overlay. It uses the same matcher as `scripts/test_rules_single_copy.py`.

The default budget is 6144 bytes. `NOUSFX_RULES_BUDGET=<n>` overrides it for one run; a `rules-budget: <n>` line in `local.md` overrides it for good. That line is a setting, read only from `local.md`, so it stays out of the generated rules. The variable wins over the line.

It only warns: the exit code stays the same, and `--check` still says `no drift`. The repo's own 4096-byte budget for `AGENTS.md` plus the Claude overlay (`test_rules_single_copy.py`) is separate and still fails the tests.

Local files hold only what is personal. A shared rule goes in the harness, once.

### Using /doctor with NousFX

Claude Code's `/doctor` may suggest edits to `~/.claude/CLAUDE.md`. Apply them to the sources (`harness/`, or `local.md` and `local.claude.md` in the state home), then re-run sync. A direct edit to `CLAUDE.md` is overwritten by the next sync, and `--check` flags it as drift until then.

## Rules I keep

- The home dir comes from the user record, never the `HOME` variable.
- The generated header holds a short hash of the sources that made the file (rules: neutral file, overlay, local file; agents: the agent file and its role entry in `models.toml`), never the repo commit. `--check` flags a real source change or a hand edit, not every commit. The rules header also names its source files by path, as `~/...` when they are under my home (personal, so only in my home, never in `--project` stubs); the hash still comes from the texts alone.
- The installed hook runs `python3 -I -B "<gate>"` (`python -I -B` on Windows). `-B` leaves no bytecode behind. `-I` ignores `PYTHONPATH` and user site, so a file named like a standard module cannot make the gate allow a PR.
- Sync refuses a symlinked target file or folder under `~/.claude` instead of replacing it or writing through it. Nothing is changed when it refuses.
- A backup goes to `<state home>/backups/<stamp>/` before every write. A run with nothing to change writes and backs up nothing.
- A secret scan runs over the generated rules and the hook files. A hit fails the run before anything is written.
- After writing, the installed gate runs twice: a PR create on a wrong branch must block, a harmless command must pass. If not, the run fails and `--restore` undoes it.
- On Windows the gate path goes through `cygpath -m`, the interpreter is `python`, and the hook runs in PowerShell. Hooks are copied, not linked, so no junction is needed here.
- Nothing from Claude's auto-mode allow list is copied anywhere.

## Deny list

Claude's file tools may not read or edit secret files, and may not edit lockfiles. Sync writes these rules to `permissions.deny` in `~/.claude/settings.json`. They are plain Claude Code rules: no model decides, and they work the same on Mac and Windows.

| Files | Rule | Blocks |
|---|---|---|
| `.env*` (also `.envrc`, `.env.example`) | `Read(//**/.env*)`, `Edit(//**/.env*)` | read and edit |
| `*.pem` | `Read(//**/*.pem)`, `Edit(//**/*.pem)` | read and edit |
| `*.key` (also Keynote files) | `Read(//**/*.key)`, `Edit(//**/*.key)` | read and edit |
| SSH keys: `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*` (also their `.pub`) | `Read(//**/id_rsa*)`, `Edit(//**/id_rsa*)`, and the same for each | read and edit |
| `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `pubspec.lock`, `Podfile.lock`, `gradle.lockfile`, `gradle/dependency-locks/*.lockfile` | `Edit(//**/<name>)` | edit only, reading is fine |

How the rules read:
- `//` means "from the root of the disk", so a rule matches anywhere, not only inside the folder the session started in. A bare `**/.env*` would only cover that folder. On Windows, Claude Code turns `C:\Users\me` into `/c/Users/me` before it matches, so `//**/` covers every drive. The rules are the same text on both machines.
- `Read` also covers Grep and Glob (Claude Code calls this best effort). `Edit` covers every built-in tool that changes a file: Write, MultiEdit and NotebookEdit. A `Write(...)` rule would never match, so there is none.
- A deny rule wins over every allow, in every settings file. A project cannot undo it.

What it does not stop (the Bash gap):

| Command | Blocked? |
|---|---|
| `cat .env`, `head`, `tail`, `sed`, `tee`, `> file`, `< file` | yes, Claude Code checks the files these name |
| `grep -r KEY .` (reads files without naming them) | no |
| a script: `python x.py`, `node x.js` that opens `.env` | no |
| `npm install`, `flutter pub get`, `pod install` rewriting a lockfile | no, and that is wanted |

For a hard stop on everything, Claude Code's sandbox is the tool, not these rules. agy has no matching setting yet (its gate guards only its own files); that is a follow-up ticket.

### Keeping my own rules

Sync owns only the rules in the table. Any other deny rule I wrote by hand stays where it is, and so does every other setting (`allow`, `defaultMode`, hooks, model). If I write one of the table's rules by hand, sync treats it as its own.

- `--check` reports `settings.json` as drift when one of the rules is missing or edited. The next run puts it back.
- A second run changes nothing.
- `--restore` puts back the whole `settings.json` from before the last run.

### Leaving a rule out

A project cannot allow a denied file, so exceptions go in one file of mine, `~/.config/nousfx/deny.toml` (never committed):

```toml
# a bare pattern drops both rules for it (Read and Edit); a full rule drops just that one
exclude = ["//**/*.key", "Edit(//**/pubspec.lock)"]
```

- An entry must match a rule in the table exactly. One that matches nothing (a typo, or `**/.env.example`) stops the run before anything is written, and the message lists the rules.
- A rule cannot carve one file out of a pattern. To allow `.env.example` but not `.env`, I exclude `//**/.env*` and add my own narrower rules by hand, for example `Read(//**/.env)` and `Read(//**/.env.local)`.
- After I change `deny.toml`, `--check` shows drift until I run sync.

## Skills

My skills live in one shared folder that other tools can read. Sync links them from the repo, so an edit is live.

```
claude/skills/*, plugins/via/skills/via   sources
~/.agents/skills/<name>                   link to the source
~/.claude/skills/<name>                   link to the source
<state home>/skills-manifest.json         every link sync made
```

- Sync owns only what the manifest lists. It never deletes or overwrites anything else.
- A folder of the same name that is not in the manifest is a collision. Sync reports it and skips that skill. I re-run with `--yes-replace NAME` once I have looked at it; the old folder is backed up first.
- Removing a skill from the repo removes only its manifest-owned link.
- `--check` exits 1 on a collision, a broken link or a missing link. `--dry-run` and `--restore` cover the links, the manifest and the moved folders.
- Old `install.sh` backups (`*.bak-YYYYMMDD-HHMMSS` of `CLAUDE.md`, `settings.json` and the files under `agents/`, `skills/`, `hooks/` in `~/.claude`) are listed in every run and never moved on their own. `--clean-backups` moves them into the run's backup, so `--restore` brings them back. `projects/`, `plugins/` and everything else are out of scope.
- On Windows a failed symlink falls back to a junction. The command is fixed (`cmd /c mklink /J`); nothing in the environment can replace it. `NOUSFX_NO_SYMLINK=1` only forces that fallback and is test-only: tests pass `--platform windows` with a stand-in `cmd` first on PATH.

### Outside skills

Skills I use but do not write (Matt Pocock's workflow set, `ui-ux-pro-max`, the Android set, `material-3`) are pinned in `harness/skills.lock.json`. It has the same format as Via's `plugins/via/skills.lock.json`: a repo, a path, a full commit and a sha256 for each skill.

- A real run fetches each skill that is missing, checks the sha256, and keeps the files in `<state home>/outside-skills/<name>-<12 characters of the sha256>/`. After that it links them like my own skills, so the manifest, collisions, backups and `--restore` all work the same. A skill already in the cache is not fetched again.
- A skill that fails (the fetch fails, the checksum differs, or the tree is unsafe) is reported on stderr and left out. Nothing is written for it, and its existing links stay. Everything else is applied, and the run exits 1.
- A hand-installed copy that holds exactly the pinned files is replaced without `--yes-replace`; the old folder is backed up first, so `--restore` brings it back. A copy that differs (an older version, or one I edited) is a collision as before.
- `--check` exits 1 when a pinned skill is missing from the cache or its files were edited. `--dry-run` prints `would fetch NAME@commit`. Neither fetches anything.
- The fetch and checksum code is Via's (`via_skills.py`): https only, a full commit, the tree read from git objects, size limits, files written as plain 0644, and nothing run.
- A name that `claude/skills` or Via already ships cannot be in the lock. Old cache folders are never deleted, because `--restore` may link back to them. `.DS_Store`, `__pycache__` and `*.pyc` do not count as edits (a skill's own scripts write them), so the edit check guards the fetch and not the local disk: anyone who can write in the state folder can change a skill's scripts anyway. If I edit a skill through its link, the next run moves my edited folder to `<state home>/outside-skills/.edited-*/` before it puts the pinned files back.
- Not pinned on purpose: Vercel's `web-design-guidelines` skill (NFX-23, NFX-51). Its `SKILL.md` tells the agent to fetch the rules from the `main` branch of `vercel-labs/web-interface-guidelines` before every review, so pinning the skill folder would pin nothing that matters. Nothing here uses it: Via does not call it, and no install of it exists on my machines. Do not add it to the lock unless a tool starts to need it. If that happens, pin the rules repo at a commit and ship a patched copy that reads the pinned file, then check that the installed `SKILL.md` has no runtime fetch.
- To add or update a pin, use `python3 -I -B plugins/via/scripts/via_skills.py --lock harness/skills.lock.json add NAME --repo URL --path PATH --commit SHA` (or `update NAME`). It shows the files and the scripts the skill ships, and writes the lock only with `--approve` and the checksum it showed.
- `NOUSFX_ALLOW_FILE_URLS=1` accepts `file://` repos. It is test-only.

## Real-machine check

`python3 -B scripts/real_machine_regression.py --out DIR` builds the rules from the repo and my local file and compares them line by line with my real `~/.claude/CLAUDE.md`. The report in `DIR/regression.txt` classes each of today's lines as unchanged, reordered, reworded or dropped. `--state-home` tries a proposed local file, and `--fail-on-dropped` exits 1 when a rule has no home. It only reads the real home and is not part of CI. A fixture test runs the same guard.

## Tests

`python3 -B scripts/test_nousfx_sync.py`, `test_nousfx_sync_tools.py` (agy, MCP and `--project`) and `test_nousfx_skills.py`. They run the CLI as a subprocess against a temp home. `test_nousfx_agy.py` and `test_nousfx_mcp.py` test the two modules directly; run them as `python3 -B -m unittest test_nousfx_mcp` from `scripts/`. The golden test builds `scripts/fixtures/sync/expected_CLAUDE.md` from generic fixtures.

## Agents by role

My agents name a role, not a model. Sync turns that into Claude's agent files.

```
harness/agents/*.md     frontmatter says role: fast | balanced | strong | deep (no model line)
harness/models.toml     per tool, each role -> model + effort (claude: haiku / sonnet / opus / fable)
        |
        v
~/.claude/agents/*.md   model and effort filled in, generated header after the frontmatter
```

- Change one model in `models.toml` and only the agents with that role change.
- An agent can set its own `effort:` and that wins over the role default (test-runner uses `low`).
- An unknown or missing role fails the run with the agent and role named. Nothing is written.
- `--dry-run`, `--check`, `--restore` and the backup cover these files like the rest.
- Sync never deletes an agent file I removed from the sources.
- The one intended difference from `claude/agents/*.md` is the generated header. The golden test in `scripts/test_nousfx_agents.py` enforces it, so the old files stay as the reference for that test.

## agy

I keep agy in one folder that sync owns, and I leave my own agy files alone.

```
harness/AGENTS.md + agy.overlay.md + local.md + local.agy.md
        |
        v   python3 -B scripts/nousfx_sync.py --tools claude,agy
~/.gemini/config/plugins/nousfx/plugin.json
~/.gemini/config/plugins/nousfx/rules/AGENTS.md     generated, same header style as CLAUDE.md
~/.gemini/config/plugins/nousfx/skills/<name>       link to ~/.agents/skills/<name>
~/.gemini/config/plugins/nousfx/mcp_config.json     from the MCP list (below)
~/.gemini/config/plugins/nousfx/hooks.json          the review gate on run_command
~/.gemini/config/plugins/nousfx/hooks/              agy_gate.py, review-gate.py, pr_create_parser.py (copies)
```

- My own `~/.gemini/config/rules/AGENTS.md` and the global `mcp_config.json` are never read or written. To turn the plugin off I run `agy plugin disable nousfx`.
- I checked that agy loads a plugin's rules and a symlinked skill without any `config.json` entry (`docs/agy-capabilities.md`, "Plugin loading").
- The agy rules have no Claude-only terms. A test checks the generated file, and sync warns when my `local.md` holds one.
- The rules file must stay under agy's 24,000 byte cap, or the run fails before writing.
- The skill links use the same manifest, collision rule and `--restore` journal as the other skill links.
- The review gate is wired too (`docs/review-gate.md`, "agy adapter"), with the hashes of its three files in `hooks.json`. After writing, sync runs the adapter once: a PR create in a throwaway repo must be denied and a harmless command must get no answer. If not, the run fails and `--restore` undoes it.

## Project mode

`--project DIR` sets up one repo for every tool. It never touches my home folder's rules, skills or settings.

| File in the repo | What sync does |
|---|---|
| `AGENTS.md` | Writes a short stub only when the repo has none. After that it is the repo's. |
| `CLAUDE.md` | Writes a stub with `@AGENTS.md` only when the repo has none. A file that already imports `AGENTS.md` is never touched. |
| `.githooks/pre-push` | The advisory pre-push gate (`claude/githooks/pre-push`) with a generator line. Hand edits are drift. |
| `.agents/skills` | A link to `../.claude/skills`, only when the repo has that folder and nothing at the link path. |
| `.mcp.json`, `.agents/plugins/nousfx/` | MCP files from `.nousfx/mcp.toml` (see MCP). |
| `.claude/hooks/review-gate.py`, `pr_create_parser.py`, `.claude/agents/<name>.md`, `.claude/skills/review-loop/` (`SKILL.md`, `scripts/tier.py`) | Copies `project-setup` installed for cloud sessions. Refreshed from their NousFX sources (agents rendered as for `~/.claude/agents`) only when the repo already has them; never created. A stale copy is drift, and a real run replaces it (backed up) even when edited by hand. An agent NousFX does not ship is left alone. |
| `.claude/settings.json` | Never written (merged by hand from project-setup's `assets/settings.json`). Sync prints a note when its review-gate hook runs the repo copy without checking for `$HOME/.claude/hooks/review-gate.py`. |
| `.nousfx/project.toml` | Never written. Sync prints a note when it has no `[check] command`, because the review gate cannot record a review without one (see `docs/review-gate.md`). |

- A file I wrote by hand is left alone (shared copies excepted, above). A `CLAUDE.md` without the import, or a `.githooks/pre-push` that is not generated, is shown as a diff with the line to confirm it: `--yes-replace CLAUDE.md` adds the import line at the top and keeps the rest, `--yes-replace .githooks/pre-push` replaces the hook. The run goes on with everything else, and `--check` does not count these as drift.
- `--check` exits 1 for a missing or hand-edited generated file, a stale shared copy, a missing skills link, or a hook without the executable bit. `--dry-run` prints diffs and writes nothing. `--restore` removes what a run created and puts back what it changed.
- Sync never adds the review gate to a repo: a copy there is code under review, and a branch could edit it to pass. The pre-push hook and review-loop use the gate in `~/.claude/hooks`, and agy uses the gate in the global plugin. A repo copy is only the cloud fallback (`docs/review-gate.md`, "Pre-push hook").
- `--restore` does not undo a mode change on a hook whose text was already right (sync only made it executable).
- Sync does not change git config. To turn the hook on I check `ls .git/hooks | grep -v sample` once per clone. If other hooks are there (graphify's, for example), I link it: `common=$(git rev-parse --path-format=absolute --git-common-dir); ln -s "$common/../.githooks/pre-push" "$common/hooks/pre-push"`. `git config core.hooksPath .githooks` switches those other hooks off, so I use it only when `.git/hooks` holds nothing else.
- `project-setup` runs `--dry-run` and then the real run when it sets a repo up, and `--check` in its audit.

## MCP

I add a server once and every tool that should have it gets it.

```
harness/mcp.toml                shared list (committed)
~/.config/nousfx/mcp.toml       my own servers (optional, same name wins)
<project>/.nousfx/mcp.toml      one project's servers (only with --project)
```

```toml
[servers.atlassian]
tools = ["agy"]        # required: claude, agy, or both
url = "https://mcp.atlassian.com/v1/mcp"
```

- Claude gets the entry in `~/.claude.json` (only the `mcpServers` key, only names sync added). agy gets it in the plugin's `mcp_config.json`. Atlassian is agy only, because Claude has its own connector.
- A value is either a URL or an environment variable written as `${NAME}`. A literal token anywhere fails the run before anything is written, and the message names the server, never the value.
- Sync never overwrites a server it did not add. It stops and tells me. The ownership record keeps a hash of what sync wrote for each name, so a server I changed by hand, or one Claude rewrote, under a name sync owns is also left alone.
- `harness/mcp.toml` is required. A missing one stops the run, because an empty list would delete every server sync added.
- A backup of `~/.claude.json` keeps only its `mcpServers` part, and the backup folder is private (mode 0700). `--restore` puts back only the servers sync changed, with the JSON reformatted.
- I run sync with no Claude Code session open. A session that is already running can rewrite `~/.claude.json` from its own state later and drop sync's servers. `--check` then reports drift, and a normal run adds them back.
- With `--project DIR`, only `DIR/.nousfx/mcp.toml` is read, and the servers go to `DIR/.mcp.json` (Claude) and `DIR/.agents/plugins/nousfx/mcp_config.json` (agy). Global servers never go there.
- `agy mcp list` shows only the global file, not plugin servers, so I check a plugin server by running agy and asking for it.
