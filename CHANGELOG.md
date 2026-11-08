# Changelog

## 1.1.0 (unreleased)

### Review gate
- The gate no longer passes by being slow or by a common alternative command. Sync writes one budget into every gate hook (`--budget 55`, timeout 60) and the gate blocks with "gate timed out; retry or review at Tier 3" when it runs out; each tree is hashed once per run; a payload it cannot read blocks unless the tool cannot open a PR; and `gh api`, `curl` and `wget` PR creates (plus a GraphQL `createPullRequest`) block, while GETs pass (NFX-102).
- An agent can no longer waive its own review as easily. Sync denies Claude's file tools the gate's marker, pass stamp, stamp key and branch-name exception (by name, in any git dir; `deny.toml` cannot drop these), adds `ask` rules for `review-gate.py --skip`, `--tier-override` and `--allow-branch`, and the gate's messages say to ask me instead of printing a waiver command (NFX-101).
- `gh pr create` (and a create-PR MCP tool) now also needs a `Review-Gate: <HEAD tree>` line in the PR body. No body, `--fill`, a stale hash, a body built at run time or a body file without the line blocks, and the message prints the exact lines to paste. `attest` refuses with "commit or clean first" when the working tree differs from HEAD (NFX-85).
- The tests no longer run twice before a PR. review-loop's last test run is `review-gate.py run-check --stamp`, which writes a signed pass stamp for the tree; `--mark` on the same tree, branch and command uses it instead of running the check again. Any changed file still forces a run. `scripts/run_checks.py` runs the suites in parallel (`-j N`, `-j 1` for one by one). A root `HANDOFF.md` is gitignored, and `via.example.json` lists `obsidian.argon_log` (NFX-97).

### Sync and install
- Sync writes two auto-mode allow rules, `Bash(git branch -D cesar/*)` and `Bash(git push origin --delete cesar/*)`, to `autoMode.allow` in `~/.claude/settings.json`, so the post-merge cleanup is no longer blocked as Git Destructive. `"$defaults"`, my own entries and `autoMode.environment` are kept; a missing list starts with `"$defaults"`. The shared rules now say: after a PR merges, delete its branch (local, remote) and any screenshot branches the agent made, without asking, and only its own (NFX-117).
- The deny rules and the Gemini excludes now come from one list of secret file-name patterns (`secret_patterns.py`), so they can't drift. Content scanning keeps its own regexes. Sync newly denies Claude's file tools Android and iOS secrets (`*.jks`, `*.keystore`, `*.ks`, `*.p12`, `*.p8`, `*.pfx`, `key.properties`, `google-services.json`, `GoogleService-Info.plist`) and credential files (`.npmrc`, `.netrc`, `.pypirc`, `.pgpass`, `.dockercfg`, `kubeconfig`, `.git-credentials`, gcloud's `application_default_credentials.json`, `*.env`). Broad globs such as `*secret*` and `local.properties` stay Gemini-only, and sync stops with code 2 if the list can't be loaded (NFX-103).
- Project mode keeps its MCP ownership record in the repo (`.nousfx/mcp-owned.json`, committed with `.mcp.json`), so a fresh worktree, clone or cloud checkout no longer stops with "not mine" on its own committed servers. The first run moves the old entry out of `~/.config/nousfx/mcp-owned.json`, and `--restore` handles both records (NFX-99).
- Project MCP record edge cases: an old state entry merges into an existing repo record, an empty record still takes the migration, a repo record without hashes is refused, `--restore` keeps `.mcp.json`'s mode, and an unusable global record no longer blocks a project that has its own (NFX-100).
- `nousfx_sync.py --project` checks the repo's copies of the gate, agents and review-loop: `--check` reports a stale one as drift and a real run refreshes it, never adding one. project-setup no longer points review-loop at the repo's gate copy; that copy is only the cloud fallback, and the project hook now skips it when `~/.claude/hooks/review-gate.py` exists; existing repos merge that hook again, and `--project` prints a note until they do (NFX-98).
- Sync writes a deny list to `~/.claude/settings.json`: Claude's file tools may not read or edit `.env*`, `*.pem`, `*.key` and SSH keys (`id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`), or edit lockfiles. My own deny rules and settings are kept, and `~/.config/nousfx/deny.toml` leaves a rule out (NFX-74).

### Via
- Every finding gets a route (fix now, ticket or reject) and I approve the batch once. `via_run.py route` rejects into the ledger, writes one markdown ticket per screen or theme to `docs/design/tickets/` with escaped finding text, and lists the fix-now findings (NFX-13).
- `/via check` checks the screens a branch changed against the ticket's spec and target mock, routes the findings and records a pass. `via_spec.py compare` lists theme values that drifted from the spec. With `VIA_UNATTENDED=1`, Via decides nothing: `route` prints the pending decisions and changes nothing (NFX-17).
- The old `ui-review` skill is retired: a one-line stub points to Via for one release. The rules now run `/via spec` at kickoff and `/via check` before the PR on UI work, and `/via audit` on review requests. `argon_review.py` moved to `gemini-review/scripts/`, and the Argon log goes to the vault set in `via.json` (`obsidian.vault_path`, `obsidian.argon_log`), or nowhere (NFX-18).
- Via has an eval set: `plugins/via/evals/cases.json` pairs sample prompts with the expected behavior (routing, the second-opinion fallback without Gemini, unattended runs, never committing), and `python3 -B plugins/via/scripts/via_eval.py` checks each case and reports pass or fail. `run_checks.py` runs it (NFX-19).

## 1.0.0 (unreleased)

The daily-use harness: Claude Code and agy on Mac and Windows, one sync command, a gate that runs the tests, trimmed rules, and a review loop that scales with risk. Proven on Animus.

### Sync and install
- One command, `scripts/nousfx_sync.py`, writes the global rules, agents, skills, hooks and MCP servers for Claude Code and agy, with a dry run, a drift check and a restore (NFX-29, 30, 35, 38, 41, 42).
- Project mode sets up one repo for every tool (NFX-46). Animus is migrated (NFX-47).
- Outside skills are pinned by commit and checksum in `harness/skills.lock.json` and fetched by sync (NFX-55).
- A session-start warning tells me when this machine is behind `main` (NFX-57).
- `install.sh` (Mac) and `install.ps1` (Windows) are now thin wrappers: dry run, confirm, then the sync (NFX-48). Two behaviors of the old installer are gone: it no longer writes through a symlinked `settings.json` (the sync stops and asks me to replace the link with a file), and `CLAUDE_HOME` is ignored (use `--home`).

### Rules
- The always-loaded rules shrank from about 9 KB to 4 KB, with one copy of each rule (NFX-58).

### Review gate
- The gate runs the project's check command before it records a review (NFX-54).
- An agy adapter blocks `gh pr create` without a review (NFX-44).
- `--mark --tier N` runs `tier.py` and refuses a lower tier unless I give a reason (NFX-68).

### Review loop
- `tier.py` picks a review tier from the diff and the ticket labels (NFX-62).
- Reviewers by tier: `reviewer-light`, `reviewer`, `reviewer-high`; the PR body gets a `Review-Tier:` line (NFX-63).
- Findings carry a confidence score, and every high is verified before it is fixed or rejected (NFX-65).
- Fable (`architect`, `reviewer-deep`) is offered only where it pays off, and only after I say yes (NFX-66).
- Gemini checks run inside the loop: Argon on UI changes, a code check on Tier 3, only for allow-listed projects (NFX-64).
- The Gemini scripts run on Windows (NFX-56).

### Docs and templates
- A runbook with a smoke-check table, updated README and sync docs (NFX-48).
- The PR template has a "What I checked myself" section (NFX-48).
- The planned `mandy` plugin is dropped from the roadmap.

### Not in 1.0
- OpenCode (NFX-50, NFX-43), a lean profile (NFX-45) and the cross-tool eval (NFX-49).
- Live checks on a real Mac session and on Windows are tracked in NFX-53.
