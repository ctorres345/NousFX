# Sync: one command writes Claude's global rules

I keep my rules in three layers and let one command assemble Claude Code's global rules file.

```
harness/AGENTS.md                  neutral rules, models named by role (fast / strong / deep)
harness/overlays/claude.overlay.md Claude only: Skill tool, goal runs, subagent names
~/.config/nousfx/local.md          mine alone, never committed (copy harness/local.md.example)
        |
        v   python3 scripts/nousfx_sync.py
~/.claude/CLAUDE.md                generated, header names the commit
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
| `--home`, `--state-home`, `--repo` | override locations; tests always use these |

## Rules I keep

- The home dir comes from the user record, never the `HOME` variable.
- A backup goes to `<state home>/backups/<stamp>/` before every write. A run with nothing to change writes and backs up nothing.
- A secret scan runs over the generated rules and the hook files. A hit fails the run before anything is written.
- After writing, the installed gate runs twice: a PR create on a wrong branch must block, a harmless command must pass. If not, the run fails and `--restore` undoes it.
- On Windows the gate path goes through `cygpath -m`, the interpreter is `python`, and the hook runs in PowerShell. Hooks are copied, not linked, so no junction is needed here.
- Nothing from Claude's auto-mode allow list is copied anywhere.

## Real-machine check

`python3 -B scripts/real_machine_regression.py --out DIR` generates the rules into a throwaway home and writes the diff against my real `~/.claude/CLAUDE.md` into `DIR`. It only reads the real home. It is not part of CI.

## Tests

`python3 -B scripts/test_nousfx_sync.py`. They run the CLI as a subprocess against a temp home. The golden test builds `scripts/fixtures/sync/expected_CLAUDE.md` from generic fixtures.
