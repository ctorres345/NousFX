# /via setup

Doctor mode: show what works, what is missing, why it matters and how to fix it.

## 1. Collect what only you can see
Look at your loaded tools and skill list. Decide:
- `--atlassian present` if Atlassian Jira tools are loaded, else `missing`.
- `--figma authorized` if real Figma tools are loaded, `needs-auth` if only Figma authenticate tools exist, else `missing`.
- `--browser chrome` or `builtin` if you can drive a browser, else `none`.
- `--skills` (leave it out if none are installed) as a comma list of the design skills in your skill listing (for example `ui-ux-pro-max,design:design-critique,material-3:material-3`).

## 2. Run the doctor
```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/via_setup.py" --atlassian <present|missing> --figma <authorized|needs-auth|missing> --browser <chrome|builtin|none> [--skills <comma list>]
```
Substitute the real plugin path for `${CLAUDE_PLUGIN_ROOT}` in the command, because the variable is not set inside Bash. The script detects Gemini (key source only, never the key), the Obsidian vault, simulators and emulators, then creates or refreshes `~/.config/nousfx/via.json`.

## 3. Report
- Print a table: integration | status | why it matters | fix link. Use the script's JSON.
- Outbound allow-list: tell the user that a project sends nothing to Gemini, Jira, Figma, Artifacts or Obsidian until it is listed in `outbound_allowlist` in `~/.config/nousfx/via.json` (key `owner/repo`, value `{"destinations": [...]}`, plus `gemini_code_paths` for the legacy Argon code review). Add a project only when the user says so in chat. The file must stay inside `~/.config/nousfx`, outside any git work tree: the scripts refuse a config kept in a project. The home comes from the system user record, so no variable moves it. An old `~/.claude/via.json` is copied there once and still read for one release.
- If an Obsidian vault shows as `detected`, tell the user the path and ask before setting `obsidian.vault_path`.
- Print the script's `summary` line as the last line.
- Say whether the config was `written` or `unchanged`.

## Do not
- Do not read, print or store any API key.
- Do not install anything. Pinned installs come in a later release.
