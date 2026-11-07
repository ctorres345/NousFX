# Changelog

## 1.1.0 (unreleased)

### Sync and install
- Sync writes a deny list to `~/.claude/settings.json`: Claude's file tools may not read or edit `.env*`, `*.pem`, `*.key` and SSH keys (`id_rsa*`, `id_ed25519*`, `id_ecdsa*`, `id_dsa*`), or edit lockfiles. My own deny rules and settings are kept, and `~/.config/nousfx/deny.toml` leaves a rule out (NFX-74).

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
