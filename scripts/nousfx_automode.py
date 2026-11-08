"""nousfx_automode: the auto-mode allow rules sync writes to autoMode.allow in ~/.claude/settings.json (NFX-117).

After a PR merges, the agent deletes the `cesar/<KEY>` branch it made, locally and on origin (the post-merge
rule in harness/AGENTS.md). Auto mode's classifier blocks both as Git Destructive unless an allow rule names
them, so sync owns these two rules and nothing else in autoMode.

Sync owns exactly RULES. "$defaults" (the built-in allow list), the user's own entries and every other
autoMode key are kept; autoMode.environment holds machine-specific trust lines and is never touched. A
missing allow list is created starting with "$defaults", so the built-in rules are not lost; a list the
user wrote without it stays that way.
"""

DEFAULTS = "$defaults"
RULES = ("Bash(git branch -D cesar/*)", "Bash(git push origin --delete cesar/*)")


def merge(cfg, SyncError, where):
    """Put RULES into cfg["autoMode"]["allow"]. Ours go where the first of ours was, else at the end."""
    mode = cfg.get("autoMode", {})
    if not isinstance(mode, dict):
        raise SyncError(f"{where}: \"autoMode\" is not an object; fix it first", 2)
    current = mode.get("allow", [DEFAULTS])
    if not isinstance(current, list) or not all(isinstance(r, str) for r in current):
        raise SyncError(f"{where}: \"autoMode.allow\" is not a list of strings; fix it first", 2)
    at = next((i for i, r in enumerate(current) if r in RULES), len(current))  # everything before it is the user's
    kept = [r for r in current if r not in RULES]
    mode["allow"] = kept[:at] + list(RULES) + kept[at:]
    cfg["autoMode"] = mode
