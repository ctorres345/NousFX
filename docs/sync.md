# Sync: one command writes Claude's global rules

I keep my rules in three layers and let one command assemble Claude Code's global rules file.

```
harness/AGENTS.md                  neutral rules, models named by role (fast / strong / deep)
harness/overlays/claude.overlay.md Claude only: Skill tool, goal runs, subagent names
~/.config/nousfx/local.md          mine alone, never committed (copy harness/local.md.example)
        |
        v   python3 -B scripts/nousfx_sync.py
~/.claude/CLAUDE.md                generated, header holds a hash of the sources
~/.claude/settings.json            review gate registered (other settings kept)
~/.claude/hooks/                   review-gate.py and pr_create_parser.py
```

## Switches

| Switch | What it does |
|---|---|
| none | back up, write, then self-test the gate hook |
| `--dry-run` | print a diff, write nothing |
| `--check` | list drifted files, exit 1 on drift, write nothing |
| `--restore` | put back the last backup (files that did not exist are removed) |
| `--clean-backups` | also move old `install.sh` backups into the backup (otherwise they are only listed) |
| `--yes-replace NAME` | let my skill NAME replace a folder sync does not own (moved into the backup first) |
| `--home`, `--state-home`, `--repo` | override locations; tests always use these |

## Rules I keep

- The home dir comes from the user record, never the `HOME` variable.
- The generated header holds a short hash of the sources that made the file (rules: neutral file, overlay, local file; agents: the agent file and its role entry in `models.toml`), never the repo commit. `--check` flags a real source change or a hand edit, not every commit.
- The installed hook runs `python3 -I -B "<gate>"` (`python -I -B` on Windows). `-B` leaves no bytecode behind. `-I` ignores `PYTHONPATH` and user site, so a file named like a standard module cannot make the gate allow a PR.
- Sync refuses a symlinked target file or folder under `~/.claude` instead of replacing it or writing through it. Nothing is changed when it refuses.
- A backup goes to `<state home>/backups/<stamp>/` before every write. A run with nothing to change writes and backs up nothing.
- A secret scan runs over the generated rules and the hook files. A hit fails the run before anything is written.
- After writing, the installed gate runs twice: a PR create on a wrong branch must block, a harmless command must pass. If not, the run fails and `--restore` undoes it.
- On Windows the gate path goes through `cygpath -m`, the interpreter is `python`, and the hook runs in PowerShell. Hooks are copied, not linked, so no junction is needed here.
- Nothing from Claude's auto-mode allow list is copied anywhere.

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

## Real-machine check

`python3 -B scripts/real_machine_regression.py --out DIR` builds the rules from the repo and my local file and compares them line by line with my real `~/.claude/CLAUDE.md`. The report in `DIR/regression.txt` classes each of today's lines as unchanged, reordered, reworded or dropped. `--state-home` tries a proposed local file, and `--fail-on-dropped` exits 1 when a rule has no home. It only reads the real home and is not part of CI. A fixture test runs the same guard.

## Tests

`python3 -B scripts/test_nousfx_sync.py` and `python3 -B scripts/test_nousfx_skills.py`. They run the CLI as a subprocess against a temp home. The golden test builds `scripts/fixtures/sync/expected_CLAUDE.md` from generic fixtures.

## Agents by role

My agents name a role, not a model. Sync turns that into Claude's agent files.

```
harness/agents/*.md     frontmatter says role: fast | strong | deep (no model line)
harness/models.toml     per tool, each role -> model + effort (claude: haiku / opus / fable)
        |
        v
~/.claude/agents/*.md   model and effort filled in, generated header after the frontmatter
```

- Change one model in `models.toml` and only the agents with that role change.
- An agent can set its own `effort:` and that wins over the role default (test-runner uses `low`).
- An unknown or missing role fails the run with the agent and role named. Nothing is written.
- `--dry-run`, `--check`, `--restore` and the backup cover these files like the rest.
- Sync never deletes an agent file I removed from the sources.
- The one intended difference from `claude/agents/*.md` is the generated header. The golden test in `scripts/test_nousfx_agents.py` enforces it, so the old files stay as the reference while `install.sh` still uses them.
