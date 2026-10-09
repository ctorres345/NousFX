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

### Hands-off review loop with `/goal`
`/review-loop` is user-run only, so a goal can't invoke it. Paste the procedure as the goal instead (start the session in auto mode so test commands don't prompt):

```text
/goal Review-fix loop on git diff HEAD. Each round, print "Round N/5", run the reviewer subagent, verify its findings, fix the real high/med ones, then run tests. Done when the reviewer's latest output is NO FINDINGS or low-only, and the passing test output is shown. Stop after Round 5 regardless and list what remains. Do not commit.
```

Notes:
- The cap counts rounds (printed as `Round N/5`), not turns, because one round takes several turns.
- "Clean" means no high/med findings; reviewers almost always find something minor.
- The goal evaluator only reads the conversation, so the reviewer output and test result must be shown in it.
- For interactive use, just run `/review-loop`.

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
