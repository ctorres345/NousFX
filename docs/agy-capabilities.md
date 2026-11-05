# agy capabilities: what I confirmed by running it

I checked agy 1.5.0 on my Mac before writing any adapter. Each fact is marked **ran** (I saw it happen) or **docs** (only in agy's own docs, not run). Claude Code is the reference column. OpenCode is not covered: I decided on 2026-11-05 to do agy and Claude first, so NFX-35's OpenCode half is still open.

Sources: agy's built-in docs in `~/.gemini/antigravity-cli/builtin/skills/agy-customizations/` (`SKILL.md` and `docs/`), the plugin guide in `builtin/skills/plugin/`, `agy --help`, `agy changelog`, and test runs in a throwaway project (a `.agents/` folder in a temp git repo, never my real `~/.gemini`).

## Summary

| Area | Claude Code | agy | Evidence |
|---|---|---|---|
| Global rules | `~/.claude/CLAUDE.md` | `~/.gemini/config/rules/AGENTS.md` (also `GEMINI.md` or `AGENTS.md` in any folder up to the repo root) | **ran** for the global file (agy quoted its first heading); the `GEMINI.md`/`AGENTS.md` walk-up is **docs** |
| Skills folder | `~/.claude/skills/*` | **not** `~/.agents/skills`. Either declared in `skills.json`, or placed in `~/.gemini/config/skills/<name>/` (see below) | **ran**: 3 skills in `~/.agents/skills`, agy saw none. The `config/skills/` folder is **docs** only |
| Follows symlinked skills | yes | yes | **ran** |
| Claude-only skill fields (`disable-model-invocation`, `argument-hint`) | used | accepted, no warning, skill loads | **ran** |
| Hooks | `settings.json` PreToolUse | `hooks.json`, `PreToolUse` can `deny` | **ran** (project level) |
| Global hooks file | `~/.claude/settings.json` | `~/.gemini/config/hooks.json` | **docs** (`agy changelog`, and `SKILL.md` names `~/.gemini/config/` as the global root); file does not exist yet |
| MCP | `~/.claude.json`, `.mcp.json` | `~/.gemini/config/mcp_config.json`, key `mcpServers` | **docs**; `agy mcp add/list` exists |
| Subagents | `~/.claude/agents/*.md` | `agents/*.md` inside a plugin; a location outside a plugin is unconfirmed | **ran**: `agy agents` lists them. Calling one is untested |

## Rules

- Global file: `~/.gemini/config/rules/AGENTS.md`. Mine already exists and holds my own Gemini orchestration rules (hand-written). **Sync must never overwrite it.** The rule is the one sync already uses for skills: a file sync did not create is left alone unless I pass `--yes-replace`, and it is backed up first.
- Limits (**docs**): 24,000 bytes per file; always-on and global rules share a 20,000-token budget, and over-budget rules are cut to file-path pointers.
- Standalone `GEMINI.md` and `AGENTS.md` files (and a plugin's `rules/AGENTS.md`) take no frontmatter (**docs**). Files in `.agents/rules/` may (not checked).

## Skills

- agy does not read `~/.agents/skills`. The plan assumed it did. It does not.
- `skills.json` works (**ran**). Put it in `~/.gemini/config/` (global, **docs**) or `<repo>/.agents/` (project, **ran**):

```json
{ "entries": [ { "path": "/Users/me/.agents/skills", "include_only": ["tdd", "grilling"] } ] }
```

- `include_only` works (**ran**: `handoff` was left out when not listed). `exclude` is **docs** only, not run.
- `~/` in `path` is **not** expanded, although the docs say it is (**ran**: the same folder worked with the absolute path and failed with `~/`). Sync must write absolute paths. I only tested this in a project-level `skills.json`; the global file is untested.
- A scanned folder is one level deep (**docs**).
- **Second option (docs, not run):** a skill folder placed directly in `~/.gemini/config/skills/<name>/` is a standalone global skill (`plugin/SKILL.md`). It counts as "global discovery", which ranks **above** built-ins. Whether a symlinked folder there works is untested (symlinks were only confirmed at project level).
- **Priority caveat (docs):** agy ranks skill sources: 1 workspace, 2 project `skills.json`, 3 global discovery in `~/.gemini/config/`, 4 built-ins, 5 **global `skills.json` last**. On a name clash, a skill from the global `skills.json` is shadowed by any same-named skill from sources 1 to 4 (workspace, project `skills.json`, loose skills or installed plugins in `~/.gemini/config/`, built-ins).
- A folder in `entries` is scanned one level deep, so no per-skill links are needed. Claude's links stay as they are.

## Hooks

Project-level `.agents/hooks.json`, tested:

```json
{ "spike-gate": { "PreToolUse": [ { "matcher": "run_command",
  "hooks": [ { "type": "command", "command": "/abs/path/hook.sh", "timeout": 10 } ] } ] } }
```

- The hook ran, and `{"decision":"deny","reason":"..."}` blocked the tool. agy told the model: `tool call denied by pre-tool hook: <reason>`.
- Payload on stdin (sample, trimmed): `toolCall.name` is `run_command`, `toolCall.args.CommandLine` is the command, plus `workspacePaths` (list), `conversationId`, `lastUserInput`, `modelName`, `stepIdx`, `transcriptPath`.
- There is **no top-level `cwd` field**. In my run `toolCall.args` held only `CommandLine`, `IsDaemon`, `WaitMsBeforeAsync` and `toolSummary`, so no per-command folder was given. `workspacePaths[0]` is the workspace root, **not proven to be the folder the command runs in**. The hook's own working directory is the folder holding `hooks.json` (**docs**). NFX-44 must decide how to pick the folder to check (prefer a per-command argument if agy ever sends one, else the workspace root) and how to act with several workspace paths. It should fail closed if unsure.
- Output is JSON on stdout. Decisions: `allow`, `deny`, `ask`, `force_ask`.
- A hook that **crashes** (exit 1) makes the tool call error: `JSON hook ... failed: command failed: exit status 1` (**ran**). That fails closed. A hook that **times out** is untested (default 30 s): NFX-44 must check whether a timeout errors the call or falls back to a prompt.
- A hook that exits 0 with **no output** makes no decision. agy falls back to its normal permission prompt (**ran**; headless mode auto-denied). So the adapter must print an explicit `deny` to block, never rely on silence.
- The tool name for shell is `run_command` (**ran**). The matcher is a regex on the tool name (**docs**).
- Hooks run synchronously. Only `type: "command"` exists (**docs**).
- agy's own command guardrails mention `hooks.json` and `~/.gemini/config/` as sensitive targets (seen in strings of the `agy` binary, not in a doc). Expect a permission prompt when sync or an agent edits them.

## MCP (docs only)

```json
{ "mcpServers": { "local": { "command": "node", "args": ["x.js"], "env": {} },
                  "remote": { "url": "https://host/mcp" } } }
```

`serverUrl` is accepted as a legacy key. Legacy SSE servers need a stdio bridge (`mcp-remote`). Plugins can carry their own `mcp_config.json`.

## Plugins and subagents

- A plugin is a folder with `plugin.json` plus optional `rules/`, `skills/`, `agents/`, `hooks.json`, `mcp_config.json`. Installed plugins live in `~/.gemini/config/plugins/` and `agy plugin list` shows them.
- My `gemini-orchestrator` plugin defines subagents (`gemini-executor`, `gemini-scout`) as `agents/*.md` with `name` and `description` frontmatter, and `agy agents` lists them. **The plan said agy has no subagents; agy at least has a subagent concept.** I have not called one, so whether delegating works as the plan needs is untested.
- `agy plugin install` stages Claude Code plugins (`.claude-plugin/plugin.json`) (**docs**, `plugins.md`), and `agy plugin import` appears in `agy --help`. Neither was tried.

## Plugin loading (NFX-42 spike, 2026-11-05)

I built a throwaway plugin `nousfx` (`plugin.json`, `rules/AGENTS.md`, `skills/spikeskill` as a symlink, `mcp_config.json` with a `url` entry) and tried it in a temp git repo and under a temp `HOME`. The real `~/.gemini` was not touched.

| Question | Result | Evidence |
|---|---|---|
| Does a plugin's `rules/AGENTS.md` load? | Yes. agy quoted its heading and followed its instruction | **ran** (`agy -p`, plugin under `<repo>/.agents/plugins/nousfx/`) |
| Is a symlinked skill inside the plugin's `skills/` found? | Yes, under its plain name (`spikeskill`, no `nousfx:` prefix) | **ran** |
| Is a plugin folder found without a `config.json` entry? | Yes. Under a temp `HOME`, `agy plugin list` showed `nousfx (enabled)` with "1 skill, 1 rule, 1 MCP server" | **ran** (`plugin list` only; no model call, login was needed) |
| Does the plugin's `mcp_config.json` register a server? | `plugin list` counts it. `agy mcp list` does **not** show it: that command lists the global `mcp_config.json` only. The agent also did not report the server, but the URL was a dead example host, so a live connection is untested | **ran**, inconclusive for tools |
| Does a global plugin (`~/.gemini/config/plugins/`) load rules in a model call? | Not run: a temp `HOME` has no login | untested; the project-level run is the closest proof |

Effect on NFX-42: the plan (one sync-owned plugin with rules and per-skill links) holds. No `skills.json` fallback is needed. NFX-41 must check the Atlassian entry in a real agy session by hand, because `agy mcp list` cannot show plugin servers.

## What changes in the plan

| Plan said | Now |
|---|---|
| Rules go to `~/.gemini/GEMINI.md` | `~/.gemini/config/rules/AGENTS.md`, and my own file is already there: collision rule applies |
| agy reads `~/.agents/skills` | It does not. Either write a `skills.json` entry with an absolute path (lowest priority in the global file), or link skills into `~/.gemini/config/skills/` (ranks above built-ins; untested). NFX-42 should test both and pick one |
| agy needs hook support checked | Hooks work. The gate adapter is a small `hooks.json` entry plus a script that maps the payload to `check` |
| agy has no subagents, run work inline | agy lists plugin subagents. Keep the inline fallback until a subagent call is tested (NFX-43) |
| MCP via `serverUrl` or a bridge | `url` (Streamable HTTP) or `command`; bridge only for legacy SSE |

Effect on tickets: NFX-42 (skills via `skills.json`, rules collision), NFX-43 (test a real subagent call first), NFX-44 (agy hook adapter; always print an explicit `deny`) and NFX-41 (agy MCP schema) can now be built for agy. OpenCode work waits.

## Repro

1. Make a temp git repo with `.agents/skills/x` (a symlink to a folder holding a `SKILL.md`) and `.agents/hooks.json` as above.
2. `agy -p "Do you have skill x? Then run echo hi with run_command."` shows the skill loaded and the command denied.
3. Add `.agents/skills.json` with an `entries` path. Compare `~/...` and absolute.
