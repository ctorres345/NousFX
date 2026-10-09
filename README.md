# claudio
Set of files and configurations that I share between devices to work with Claude and other AI tools.

## Claude Code orchestration (`claude/`)
Opus/Sonnet main session with Haiku 5.5 subagents for noisy work.

| File | Purpose |
|---|---|
| `claude/agents/Explore.md` | Haiku code search; replaces the built-in Explore |
| `claude/agents/researcher.md` | Haiku docs and API lookups |
| `claude/agents/reviewer.md` | Read-only Opus reviewer |
| `claude/agents/test-runner.md` | Haiku build/test runner, returns only failures |
| `claude/skills/review-loop/SKILL.md` | `/review-loop`: review-fix loop, max 5 rounds, never commits |
| `claude/CLAUDE.delegation.md` | Delegation policy appended to `~/.claude/CLAUDE.md` |

### Install
```bash
./install.sh
```
Copies the files into `~/.claude/` (override with `CLAUDE_HOME`), backs up anything it replaces, and appends the delegation policy to `CLAUDE.md` only once.

### Requirements
- Claude Code 2.1.293 or later (Haiku 5.5). Run `claude update` if older.
- Anthropic API. On Bedrock/Vertex the `haiku` alias resolves to Haiku 4.5; put the full model ID in the agent files.

### Not shared on purpose
`settings.json` (project-specific `autoMode` details and graphify hooks), memory, history, sessions. If you want the same main model, add `"model": "opusplan"` to `~/.claude/settings.json` by hand.

### Check
Run `/agents` in a new session; all four agents should be listed.
