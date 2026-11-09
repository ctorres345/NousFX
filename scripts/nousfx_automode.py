"""nousfx_automode: the auto-mode allow entry sync writes to autoMode.allow in ~/.claude/settings.json (NFX-117,
NFX-118).

The branch rule in harness/AGENTS.md has the agent delete its merged `<prefix>/<KEY>` branch and its screenshot
branch, locally and on origin; auto mode blocks both as Git Destructive unless an allow entry describes them.
The entry is prose the classifier reads, built by rule(prefix). It starts with MARKER, and the prefix comes
only after it, so sync can find its own entry whatever prefix it was written with.

Sync owns the entry, any entry that starts with MARKER (a prefix change replaces the old one) and the two
NFX-117 Bash entries in LEGACY, hand-added copies too. "$defaults", the user's own entries and every other
autoMode key (environment holds machine-specific trust lines) are kept. A missing allow list starts with
"$defaults", so the built-in rules are not lost.
"""
from nousfx_deny import splice_owned

DEFAULTS = "$defaults"
MARKER = "Deleting my merged ticket branches is routine cleanup:"
LEGACY = ("Bash(git branch -D cesar/*)", "Bash(git push origin --delete cesar/*)")


def rule(prefix):
    return (f"{MARKER} `git branch -D` or `git push origin --delete` on exactly one branch named `{prefix}/<KEY>` "
            f"or `{prefix}/<KEY>-shots`, where <KEY> is a ticket key matching [A-Z][A-Z0-9]+-[0-9]+ (such as "
            "ABC-123). Allowed once a `gh pr view` or `gh pr list` result in this session shows that branch's PR as "
            "MERGED, or I named that branch or its key as merged in this session. `-D` is the only force form "
            "allowed, because squash merges look unmerged to git: no `--force`, `-f` or `:branch` refspec deletes. "
            "The local and the remote delete of that one branch may be chained with && and nothing else, against "
            "the current repo's origin. Never other branches, globs or wildcards.")


def merge(cfg, SyncError, where, prefix):
    """Put rule(prefix) into cfg["autoMode"]["allow"], where the first entry we own was, else at the end."""
    mode = cfg.get("autoMode", {})
    if not isinstance(mode, dict):
        raise SyncError(f"{where}: \"autoMode\" is not an object; fix it first", 2)
    current = mode.get("allow", [DEFAULTS])
    if not isinstance(current, list) or not all(isinstance(r, str) for r in current):
        raise SyncError(f"{where}: \"autoMode.allow\" is not a list of strings; fix it first", 2)
    owned = set(LEGACY) | {r for r in current if r.startswith(MARKER)}
    cfg.setdefault("autoMode", mode)["allow"] = splice_owned(current, [rule(prefix)], owned)
