# agy capabilities: what I confirmed by running it

I checked agy 1.5.0 on my Mac before writing any adapter. Each fact is marked **ran** (I saw it happen) or **docs** (only in agy's own docs, not run). Claude Code is the reference column. OpenCode is not covered: I decided on 2026-11-05 to do agy and Claude first, so NFX-35's OpenCode half is still open.

Sources: agy's built-in docs in `~/.gemini/antigravity-cli/builtin/skills/agy-customizations/docs/` and test runs in a throwaway project (a `.agents/` folder in a temp git repo, never my real `~/.gemini`).

## Summary

| Area | Claude Code | agy | Evidence |
|---|---|---|---|
| Global rules | `~/.claude/CLAUDE.md` | `~/.gemini/config/rules/AGENTS.md` (also `GEMINI.md` or `AGENTS.md` in any folder up to the repo root) | **ran**: agy quoted the first heading of my global file |
| Skills folder | `~/.claude/skills/*` | **not** `~/.agents/skills`. Declared in `skills.json` (see below) | **ran**: 3 skills in `~/.agents/skills`, agy saw none |
| Follows symlinked skills | yes | yes | **ran** |
| Claude-only skill fields (`disable-model-invocation`, `argument-hint`) | used | accepted, no warning, skill loads | **ran** |
| Hooks | `settings.json` PreToolUse | `hooks.json`, `PreToolUse` can `deny` | **ran** (project level) |
| Global hooks file | `~/.claude/settings.json` | `~/.gemini/config/hooks.json` | **docs** (changelog); file does not exist yet |
| MCP | `~/.claude.json`, `.mcp.json` | `~/.gemini/config/mcp_config.json`, key `mcpServers` | **docs**; `agy mcp add/list` exists |
| Subagents | `~/.claude/agents/*.md` | `agents/*.md` inside a plugin | **ran** (`agy agents` lists them), format not tested |

## Rules

- Global file: `~/.gemini/config/rules/AGENTS.md`. Mine already exists and holds my own Gemini orchestration rules (hand-written). **Sync must never overwrite it.** It follows the same collision rule as skills.
- Limits (**docs**): 24,000 bytes per file; always-on and global rules share a 20,000-token budget, and over-budget rules are cut to file-path pointers.
- Rules files take no frontmatter.

## Skills

- agy does not read `~/.agents/skills`. The plan assumed it did. It does not.
- `skills.json` works (**ran**). Put it in `~/.gemini/config/` (global, **docs**) or `<repo>/.agents/` (project, **ran**):

```json
{ "entries": [ { "path": "/Users/me/.agents/skills", "include_only": ["tdd", "grilling"] } ] }
```

- `include_only` and `exclude` work (**ran**: `handoff` was left out when not listed).
- `~/` in `path` is **not** expanded, although the docs say it is (**ran**: the same folder worked with the absolute path and failed with `~/`). Sync must write absolute paths.
- A folder in `entries` is scanned one level deep, so no per-skill links are needed. Claude's links stay as they are.

## Hooks

Project-level `.agents/hooks.json`, tested:

```json
{ "spike-gate": { "PreToolUse": [ { "matcher": "run_command",
  "hooks": [ { "type": "command", "command": "/abs/path/hook.sh", "timeout": 10 } ] } ] } }
```

- The hook ran, and `{"decision":"deny","reason":"..."}` blocked the tool. agy told the model: `tool call denied by pre-tool hook: <reason>`.
- Payload on stdin (sample, trimmed): `toolCall.name` is `run_command`, `toolCall.args.CommandLine` is the command, plus `workspacePaths` (list), `conversationId`, `lastUserInput`, `modelName`, `stepIdx`, `transcriptPath`.
- There is **no `cwd` field**: the working folder is `workspacePaths[0]`. The gate adapter must map that to `--cwd`.
- Output is JSON on stdout. Decisions: `allow`, `deny`, `ask`, `force_ask`.
- A hook that **crashes** (exit 1) makes the tool call error: `JSON hook ... failed: command failed: exit status 1` (**ran**). That fails closed.
- A hook that exits 0 with **no output** makes no decision. agy falls back to its normal permission prompt (**ran**; headless mode auto-denied). So the adapter must print an explicit `deny` to block, never rely on silence.
- The tool name for shell is `run_command` (the matcher is a regex on it).
- Hooks run synchronously. Only `type: "command"` exists.
- Hook files are on agy's list of protected configs: its own guardrails treat edits to `hooks.json` and `~/.gemini/config/` as sensitive.

## MCP (docs only)

```json
{ "mcpServers": { "local": { "command": "node", "args": ["x.js"], "env": {} },
                  "remote": { "url": "https://host/mcp" } } }
```

`serverUrl` is accepted as a legacy key. Legacy SSE servers need a stdio bridge (`mcp-remote`). Plugins can carry their own `mcp_config.json`.

## Plugins and subagents

- A plugin is a folder with `plugin.json` plus optional `rules/`, `skills/`, `agents/`, `hooks.json`, `mcp_config.json`. Installed plugins live in `~/.gemini/config/plugins/` and `agy plugin list` shows them.
- My `gemini-orchestrator` plugin defines subagents (`gemini-executor`, `gemini-scout`) as `agents/*.md` with `name` and `description` frontmatter. **The plan said agy has no subagents. That is wrong.**
- `agy plugin import` can import from a Claude Code plugin (not tried).

## What changes in the plan

| Plan said | Now |
|---|---|
| Rules go to `~/.gemini/GEMINI.md` | `~/.gemini/config/rules/AGENTS.md`, and my own file is already there: collision rule applies |
| agy reads `~/.agents/skills` | It does not. Write a `skills.json` entry with an absolute path |
| agy needs hook support checked | Hooks work. The gate adapter is a small `hooks.json` entry plus a script that maps the payload to `check` |
| agy has no subagents, run work inline | agy has subagents. NFX-43's "inline fallback" is no longer needed for agy |
| MCP via `serverUrl` or a bridge | `url` (Streamable HTTP) or `command`; bridge only for legacy SSE |

Effect on tickets: NFX-42 (skills via `skills.json`, rules collision), NFX-43 (agy agents become real), NFX-44 (agy hook adapter; always print an explicit `deny`) and NFX-41 (agy MCP schema) can now be built for agy. OpenCode work waits.

## Repro

1. Make a temp git repo with `.agents/skills/x` (a symlink to a folder holding a `SKILL.md`) and `.agents/hooks.json` as above.
2. `agy -p "Do you have skill x? Then run echo hi with run_command."` shows the skill loaded and the command denied.
3. Add `.agents/skills.json` with an `entries` path. Compare `~/...` and absolute.
