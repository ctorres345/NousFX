"""nousfx_automode: the auto-mode allow rules sync writes to autoMode.allow in ~/.claude/settings.json (NFX-117).

The post-merge rule in harness/AGENTS.md has the agent delete its `cesar/<KEY>` branch, locally and on origin;
auto mode blocks both as Git Destructive unless an allow rule names them. Sync owns exactly RULES: "$defaults",
the user's own entries and every other autoMode key (environment holds machine-specific trust lines) are kept.
A missing allow list starts with "$defaults", so the built-in rules are not lost.
"""
from nousfx_deny import splice_owned

DEFAULTS = "$defaults"
RULES = ("Bash(git branch -D cesar/*)", "Bash(git push origin --delete cesar/*)")


def merge(cfg, SyncError, where):
    """Put RULES into cfg["autoMode"]["allow"], where the first of ours was, else at the end."""
    mode = cfg.get("autoMode", {})
    if not isinstance(mode, dict):
        raise SyncError(f"{where}: \"autoMode\" is not an object; fix it first", 2)
    current = mode.get("allow", [DEFAULTS])
    if not isinstance(current, list) or not all(isinstance(r, str) for r in current):
        raise SyncError(f"{where}: \"autoMode.allow\" is not a list of strings; fix it first", 2)
    cfg.setdefault("autoMode", mode)["allow"] = splice_owned(current, RULES, RULES)
