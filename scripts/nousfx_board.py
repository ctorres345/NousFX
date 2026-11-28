"""nousfx_board: ship NousBoard's board skill, `board` CLI and unread hook from its local clone (NFX-295).
Used by nousfx_sync.py.

The source is the clone at <home>/Projects/NousBoard, or the path in the personal <state-home>/local.toml:

    [nousboard]
    path = "~/code/NousBoard"

A clone counts when it has skills/board/SKILL.md and bin/board.mjs. Then sync:
  - links the skill like its own (H/.agents/skills/board, H/.claude/skills/board; nousfx_skills.py),
  - links H/.local/bin/board to bin/board.mjs (POSIX only), recorded in the skills manifest,
  - registers `"<H>/.local/bin/board" hook` on SessionStart and Stop in Claude's settings.json
    (Windows: `node "<clone>/bin/board.mjs" hook`), with a 3 s timeout over the hook's own 2 s budget.
    agy gets the skill but no hook.
HOOK_MARKER finds every board hook form: install-local.sh's unquoted `<bin>/board hook`, ours quoted, and the
Windows one.
Without a clone it prints one note, ships nothing and removes what an earlier sync made, so no link is left
pointing at a deleted clone. The links and entries NousBoard's own install-local.sh wrote are taken over in place.
"""
import json
import os
import re

import nousfx_prefix

NAME = "board"
HOOK_EVENTS = ("SessionStart", "Stop")
HOOK_TIMEOUT_S = 3
HOOK_MARKER = re.compile(r'[/\\](?:\.local[/\\]bin[/\\]board|bin[/\\]board\.mjs)"? hook$')
SYNC_FORM = re.compile(r'^(?:"[^"]*[/\\]\.local[/\\]bin[/\\]board"|node "[^"]*[/\\]bin[/\\]board\.mjs") hook$')


def wrote_hook(settings_text):
    """True when settings.json holds a board hook entry in a form only sync writes (SYNC_FORM: quoted, which
    install-local.sh never does), so sync shipped board. Unreadable JSON: False (claude_files reports it)."""
    try:
        hooks = json.loads(settings_text or "{}").get("hooks", {})
        return any(SYNC_FORM.match(h.get("command", "")) for event in HOOK_EVENTS
                   for e in hooks.get(event, []) for h in e.get("hooks", []))
    except (ValueError, AttributeError, TypeError):
        return False


def locate(home, state, SyncError, win=False):
    """(clone, note): clone is None when there is none, and note says why."""
    table = nousfx_prefix.load(state, SyncError).get("nousboard", {})
    value = table.get("path", "") if isinstance(table, dict) else None
    if not isinstance(value, str):
        raise SyncError(f"{nousfx_prefix.path(state)}: [nousboard] must be a table with path a string; fix it "
                        "first. Nothing was written.", 2)
    if value:
        clone = os.path.join(home, value[2:]) if value.startswith(("~/", "~\\")) else value
        if not os.path.isabs(clone):
            raise SyncError(f"{nousfx_prefix.path(state)}: [nousboard] path = {value!r} must be absolute or "
                            "start with ~/. Nothing was written.", 2)
    else:
        clone = os.path.join(home, "Projects", "NousBoard")
    clone = os.path.normpath(clone)
    if any(c in clone for c in '"\r\n' + ("$`" if win else "")):
        raise SyncError(f"{nousfx_prefix.path(state)}: the NousBoard path {clone!r} may not hold a quote or a line "
                        "break (on Windows also $ or `, which PowerShell expands), since it goes inside the hook "
                        "command. Nothing was written.", 2)
    if not os.path.isdir(clone):
        return None, f"nousboard: no clone at {clone}; board skill, CLI and hook skipped"
    for need in (os.path.join("skills", NAME, "SKILL.md"), os.path.join("bin", "board.mjs")):
        if not os.path.isfile(os.path.join(clone, need)):
            return None, f"nousboard: {clone} has no {need}; board skill, CLI and hook skipped"
    if not win and not os.access(os.path.join(clone, "bin", "board.mjs"), os.X_OK):
        return None, f"nousboard: {clone}/bin/board.mjs is not executable; board skill, CLI and hook skipped"
    return clone, None


def links(home, clone, win):
    """(extra_sources, extra_links) for nousfx_skills.plan: the skill folder and, on POSIX, the CLI link."""
    cli = cli_link(home, clone, win)
    return {NAME: os.path.join(clone, "skills", NAME)}, {NAME: [cli] if cli else []}


def cli_link(home, clone, win):
    """(link path, target) for the CLI, or None on Windows, where a link to a .mjs file does not run."""
    if win:
        return None
    return os.path.join(home, ".local", "bin", NAME), os.path.join(clone, "bin", "board.mjs")


def hook_command(home, clone, win, native):
    if win:
        return f'node "{native(os.path.join(clone, "bin", "board.mjs"))}" hook'
    return f'"{os.path.join(home, ".local", "bin", NAME)}" hook'
