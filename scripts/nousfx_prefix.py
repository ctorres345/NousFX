"""nousfx_prefix: the ticket branch prefix, so branches are `<prefix>/<KEY>` (NFX-118). Used by nousfx_sync.py.

It comes from the personal, never committed <state-home>/local.toml:

    [branch]
    prefix = "ana"

An explicit value must match VALID, or sync stops with code 2 before writing anything. Without one, the
prefix is the git user.name cleaned up (lowercase, accents dropped, each other run of characters a dash),
or "me" when nothing is left; sync prints a note naming it and never blocks. Sync puts it in place of
PLACEHOLDER in the rules sources (harness/AGENTS.md has it) and in the auto-mode allow entry
(nousfx_automode.py).
"""
import os
import re
import subprocess
import tomllib
import unicodedata

FILE = "local.toml"
PLACEHOLDER = "<prefix>"
VALID = re.compile(r"[a-z0-9][a-z0-9-]*")  # fullmatch: no trailing newline; no leading dash, git reads it as an option
FALLBACK = "me"


def path(state):
    return os.path.join(state, FILE)


def from_name(name):
    """A valid prefix from a person's name: "Cesar Torres" -> "cesar-torres", "José" -> "jose"."""
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or FALLBACK


def git_user_name():
    """The global git user.name (following include.path), or "" when git or the setting is missing."""
    try:
        r = subprocess.run(["git", "config", "--global", "--includes", "--get", "user.name"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def resolve(state, SyncError, git_name=git_user_name):
    """(prefix, note): note is None for an explicit prefix, else one line saying where the default came from."""
    where = path(state)
    try:
        with open(where, encoding="utf-8") as f:
            data = tomllib.loads(f.read())
    except FileNotFoundError:
        data = {}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise SyncError(f"{where} is not valid TOML ({e}); fix it first. Nothing was written.", 2)
    branch = data.get("branch", {})
    if not isinstance(branch, dict):
        raise SyncError(f"{where}: \"branch\" must be a table ([branch]); fix it first. Nothing was written.", 2)
    if "prefix" in branch:
        value = branch["prefix"]
        if not isinstance(value, str) or not VALID.fullmatch(value):
            raise SyncError(f"{where}: [branch] prefix = {value!r} must be lowercase letters, digits and "
                            "dashes, not starting with a dash, no slash. Nothing was written.", 2)
        return value, None
    prefix = from_name(git_name())
    return prefix, (f"note: branch prefix \"{prefix}\" (from git user.name); set [branch] prefix in {where} "
                    "to choose another")


def edit(state, SyncError, prefix=None):
    """An assemble_rules edit that puts the prefix (resolved now when None) in place of PLACEHOLDER."""
    prefix = prefix or resolve(state, SyncError)[0]
    return lambda label, text: text.replace(PLACEHOLDER, prefix)
