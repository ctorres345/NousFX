# Runbook

The steps I repeat by hand for NousFX. Each one installs into my own `~/.claude/` only; nothing here goes live for anyone else. If a step changes, update this file in the same PR.

| When | Section |
|---|---|
| Setting up a new machine | [Install on a new device](#install-on-a-new-device) |
| After merging a PR that touches `claude/` | [Re-install after a merge](#re-install-after-a-merge) |
| Installing or updating Via | [Install or update Via](#install-or-update-via) |
| Something broke after an install | [Roll back an install](#roll-back-an-install) |
| About once a week | [Check token usage](#check-token-usage) |

## Install on a new device

1. Check Claude Code is 2.1.293 or later (Haiku 5.5 needs it):

   ```bash
   claude --version
   ```

2. Clone and install:

   ```bash
   git clone https://github.com/ctorres345/NousFX.git && cd NousFX && ./install.sh
   ```

3. Add these two keys to `~/.claude/settings.json` by hand. Do not copy a whole settings file from another machine; mine holds machine-specific `autoMode` and graphify hooks.

   ```json
   "model": "opusplan",
   "autoCompactWindow": 300000
   ```

4. Start a **new** session (hooks and agents load at session start) and run `/agents`. You should see `Explore`, `researcher`, `reviewer`, `reviewer-deep`, `architect` and `test-runner`.
5. [Verify the gate](#verify-the-gate).

UI skills that `ui-review` uses are not in this repo (see the README). Missing ones are skipped and reported, so install them separately if you want them.

## Re-install after a merge

1. Update `main`:

   ```bash
   git switch main && git pull --ff-only
   ```

2. See what the install would change. Every difference should come from merged PRs. If `~/.claude/` has a newer edit that never made it into the repo, copy it into the repo first, or the install replaces it (it keeps a `.bak-<stamp>` copy).

   ```bash
   for d in agents skills hooks; do diff -rq "claude/$d" "$HOME/.claude/$d"; done 2>&1 | grep -v -E "^Only in $HOME|\.bak-|__pycache__"
   ```

   Symlinked skills such as `handoff` can show up as different. That is fine: the install skips them.

   The install also replaces the "Delegation policy" section of `~/.claude/CLAUDE.md` with `claude/CLAUDE.delegation.md`. No output here means they already match. This assumes the section is the last one in the file, where the install always leaves it:

   ```bash
   awk '$0 == "# Delegation policy" { s = 1 } s && /^# / && $0 != "# Delegation policy" { s = 0 } s' ~/.claude/CLAUDE.md | diff - claude/CLAUDE.delegation.md
   ```

3. Run the install:

   ```bash
   ./install.sh
   ```

   - `backed up <file>`: the old copy is saved as `<file>.bak-<stamp>`.
   - `warning: skipped <file> (... is a symlink)`: expected for skills linked from `~/.agents/skills`, such as `handoff`. Update those at the link target.
   - A skipped `hooks/pr_create_parser.py` is not fine: the gate then blocks every `gh pr` command. Replace the link with the real file.

4. [Verify the gate](#verify-the-gate), then start a new session so the hooks reload.

## Verify the gate

1. The installed hook files match the repo:

   ```bash
   for f in review-gate.py pr_create_parser.py test_review_gate.py; do cmp -s "claude/hooks/$f" "$HOME/.claude/hooks/$f" && echo "match $f" || echo "DIFFERS $f"; done
   ```

2. The test suite passes from the installed copy (about 30 seconds):

   ```bash
   python3 -I ~/.claude/hooks/test_review_gate.py
   ```

3. A live check: `gh pr create` blocks (exit 2) and other commands pass (exit 0). Run it from this repo on `main`, where the branch-name rule blocks it. On a ticket branch whose code a review already recorded, exit 0 is correct.

   ```bash
   for c in 'gh pr create' 'ls'; do printf '{"tool_name":"Bash","cwd":"%s","tool_input":{"command":"%s"}}' "$PWD" "$c" | python3 ~/.claude/hooks/review-gate.py >/dev/null 2>&1; echo "$c -> exit $?"; done
   ```

4. `settings.json` registers the gate exactly once:

   ```bash
   grep -c 'review-gate.py' ~/.claude/settings.json
   ```

## Install or update Via

Via comes from the marketplace in this repo, not from `install.sh`.

1. Add the marketplace (first time only), using `./` from a local clone or `ctorres345/NousFX` from GitHub:

   ```bash
   claude plugin marketplace add ctorres345/NousFX
   ```

2. Install, or update after a merge to `main`:

   ```bash
   claude plugin install via@nousfx
   ```

   ```bash
   claude plugin marketplace update nousfx
   ```

3. In a Claude Code session run `/reload-plugins`, then `/via setup`. Check the table lists each integration and ends with the `Via integrations:` line.
4. Run `/via setup` again. The answer must say the config is `unchanged`.
5. To remove it: `claude plugin uninstall via`. `~/.claude/via.json` stays; delete it by hand if you want it gone.

Before releasing a change, run `python3 plugins/via/scripts/test_via_setup.py` and `claude plugin validate . --strict`.

## Roll back an install

Every replaced file has a `.bak-<stamp>` copy, and all files from one run share the same stamp.

1. Find the stamp of the run to undo. Every run backs up `settings.json`, so its backups list every run, newest last. Undo runs newest first, one stamp at a time: an older stamp brings back the state from before that run and drops every later install.

   ```bash
   ls ~/.claude/settings.json.bak-2* | sed 's/.*\.bak-//' | tail -5
   ```

2. Restore that run's agents, skills and hooks (replace `<stamp>` in both places, for example `20261103-160825`). `find` does not follow symlinked skills, so linked targets stay untouched:

   ```bash
   find ~/.claude/agents ~/.claude/skills ~/.claude/hooks -name '*.bak-<stamp>' | while read -r f; do cp "$f" "${f%.bak-<stamp>}"; done
   ```

3. Do not restore `settings.json` or `CLAUDE.md` blindly. Their backup is from before that run, so restoring it drops every later edit, such as the `model` keys or hooks other tools added. Diff first, then copy back only what the install changed:

   ```bash
   diff ~/.claude/settings.json.bak-<stamp> ~/.claude/settings.json
   ```

   ```bash
   diff ~/.claude/CLAUDE.md.bak-<stamp> ~/.claude/CLAUDE.md
   ```

   A `CLAUDE.md` backup exists only if that run changed the delegation section. If there is no `settings.json` backup, it was a first install: remove the `review-gate.py` entry under `hooks.PreToolUse` in `settings.json` by hand.

4. Start a new session. Files the install added for the first time are not removed; delete them by hand if they cause trouble.

## Check token usage

About a week after a change to the delegation policy or the agents, compare against the last report:

```bash
python3 -I scripts/usage-report.py --days 14
```

Look at average context per session and Fable cost. The baseline from 2026-10-19 was about $277 over 14 days, with cache reads at 60–66% of it.
