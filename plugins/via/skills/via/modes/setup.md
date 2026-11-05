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
python3 -I -B "${CLAUDE_PLUGIN_ROOT}/scripts/via_setup.py" --atlassian <present|missing> --figma <authorized|needs-auth|missing> --browser <chrome|builtin|none> [--skills <comma list>]
```
Substitute the real plugin path for `${CLAUDE_PLUGIN_ROOT}` in the command, because the variable is not set inside Bash. The script detects Gemini (key source only, never the key), the Obsidian vault, simulators and emulators, then creates or refreshes `~/.config/nousfx/via.json`.

## 3. Locked skills
Third-party skills are installed only from the lock (`${CLAUDE_PLUGIN_ROOT}/skills.lock.json`): a pinned commit and a checksum for each, verified before anything is written.
1. Run `python3 -I -B "${CLAUDE_PLUGIN_ROOT}/scripts/via_skills.py" plan`. Show the user its output: what it would fetch (repo, folder, commit) and any collision. A collision means a folder with the same name is already there and was not installed by this tool. It is never touched; tell the user and move on.
2. Before asking, run `python3 -I -B "${CLAUDE_PLUGIN_ROOT}/scripts/via_skills.py" check` and tell the user which locked skills ship scripts. The lock fixes the bytes of a script, not what it does. As of the first lock: `impeccable` has a launcher that downloads a native engine binary from GitHub releases on first use and can add an edit hook to Claude's settings; `swiftui-expert-skill` has Python scripts that run `xcrun xctrace`. Ask the install question on its own, not folded into the doctor table.
   If `plan` lists skills to install, it ends with a `digest` line. Ask the user. Run `python3 -I -B "${CLAUDE_PLUGIN_ROOT}/scripts/via_skills.py" install --confirm <digest>` only after a clear yes in chat, with the digest from the plan the user saw. The tool refuses any other digest, so a plan that changed after the user looked is not installed.
3. A checksum mismatch stops the install and nothing is written. Tell the user and do not retry or work around it.
4. Run `... via_skills.py verify` and include its result in the report. Any `DRIFT` line means an installed skill was edited or is out of date.
5. The digest and the checksum only catch a plan or a lock that changed after the user looked. They are not consent: the user's yes in chat is. Never run `install --confirm` or `--approve` without it, and never copy a digest or checksum from the same command output into the next command in one go.
6. Never run `add` or `update` unless the user asks. They print the files or the diff and a sha256, and write the lock only when `--approve <sha256>` carries that checksum (`update` also needs `--commit`). Show the user the preview first, and pass the checksum only after they approve what they saw.

## 4. Report
- Print a table: integration | status | why it matters | fix link. Use the script's JSON.
- Outbound allow-list: tell the user that a project sends nothing to Gemini, Jira, Figma, Artifacts or Obsidian until it is listed in `outbound_allowlist` in `~/.config/nousfx/via.json` (key `owner/repo`, value `{"destinations": [...]}`, plus `gemini_code_paths` for the legacy Argon code review). Add a project only when the user says so in chat. The file must stay inside `~/.config/nousfx`, outside any git work tree: the scripts refuse a config kept in a project. The home comes from the system user record, so no variable moves it. An old `~/.claude/via.json` is copied there once and still read for one release.
- If an Obsidian vault shows as `detected`, tell the user the path and ask before setting `obsidian.vault_path`.
- Print the script's `summary` line as the last line.
- Say whether the config was `written` or `unchanged`.

## Do not
- Do not read, print or store any API key.
- Do not install anything except through `via_skills.py install --confirm <digest>`, and only after the user said yes to the plan.
