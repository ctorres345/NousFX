"""nousfx_deny: the deny list sync writes to permissions.deny in ~/.claude/settings.json (NFX-74).

Claude's file tools may not read or edit secret files, and may not edit lockfiles. The rules are plain
Claude Code permission rules, so no model decides anything. Claude Code also applies them to the Bash file
commands it recognizes (cat, head, sed, tee, redirections), but not to other programs: `grep -r`, a script,
or an `npm install` that rewrites a lockfile still pass (docs/sync.md, "Deny list").

A deny rule wins over every allow, so a project cannot undo one. Exclusions come from one sync input
instead, <state-home>/deny.toml:

    exclude = ["//**/*.key", "Edit(//**/pubspec.lock)"]

A bare pattern drops every rule for that pattern (Read and Edit); a full rule drops only that rule. A deny
rule cannot carve one file out of a pattern, so an exclusion always drops a whole rule. An entry
that matches no rule fails the run, so a typo cannot leave a rule in place without notice.

Sync owns exactly the rules in RULES. The user's own deny rules, and every other setting, are kept.
"""
import os
import re
import tomllib

# "//" anchors a pattern at the file system root, so a rule matches anywhere on disk, not only under the
# session's folder (a bare "**/x" is relative to the current directory). On Windows Claude Code turns
# C:\Users\me into /c/Users/me before matching, so "//**/x" covers every drive. The same text works on both.
SECRETS = ("//**/.env*", "//**/*.pem", "//**/*.key", "//**/id_rsa*", "//**/id_ed25519*", "//**/id_ecdsa*",
           "//**/id_dsa*")
LOCKFILES = ("//**/package-lock.json", "//**/yarn.lock", "//**/pnpm-lock.yaml", "//**/pubspec.lock",
             "//**/Podfile.lock", "//**/gradle.lockfile", "//**/gradle/dependency-locks/*.lockfile")
# Read applies to Grep and Glob too (best effort); Edit to every built-in tool that changes files (Write,
# MultiEdit, NotebookEdit). Write(...) or NotebookEdit(...) path rules never match, so there are none.
# Docs: https://code.claude.com/docs/en/permissions, "Read and Edit".
RULES = tuple(f"{tool}({p})" for p in SECRETS for tool in ("Read", "Edit")) + tuple(f"Edit({p})" for p in LOCKFILES)
FILE_NAME = "deny.toml"
_RULE = re.compile(r"^(Read|Edit)\((.+)\)$")


def load_excludes(state, SyncError):
    """The rules to leave out, from <state>/deny.toml (missing file: none)."""
    path = os.path.join(state, FILE_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return set()
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise SyncError(f"{path} is not valid TOML ({e}); fix it first", 2)
    unknown = sorted(set(data) - {"exclude"})
    excludes = data.get("exclude", [])
    if unknown or not isinstance(excludes, list) or not all(isinstance(x, str) for x in excludes):
        raise SyncError(f"{path} must hold only exclude = [\"pattern or rule\", ...]", 2)
    out = set()
    for entry in excludes:
        entry = entry.strip()
        hits = {r for r in RULES if r == entry or _RULE.match(r).group(2) == entry}
        if not hits:
            raise SyncError(f"{path}: {entry!r} matches no deny rule. The rules are: {', '.join(RULES)}", 2)
        out |= hits
    return out


def wanted_rules(excludes):
    return [r for r in RULES if r not in excludes]


def merge(cfg, rules, SyncError, where):
    """Put `rules` into cfg["permissions"]["deny"]. Rules in RULES that are not wanted (now excluded) go;
    the user's own rules stay where they are. Ours go where the first of ours was, else at the end."""
    had_perms, perms = "permissions" in cfg, cfg.get("permissions", {})
    if not isinstance(perms, dict):
        raise SyncError(f"{where}: \"permissions\" is not an object; fix it first", 2)
    had_deny, deny = "deny" in perms, perms.get("deny", [])
    if not isinstance(deny, list) or not all(isinstance(r, str) for r in deny):
        raise SyncError(f"{where}: \"permissions.deny\" is not a list of strings; fix it first", 2)
    owned = set(RULES)
    at = next((i for i, r in enumerate(deny) if r in owned), len(deny))  # everything before it is the user's
    kept = [r for r in deny if r not in owned]
    deny = kept[:at] + list(rules) + kept[at:]
    if deny or had_deny:  # never leave an empty key behind that the user did not have
        perms["deny"] = deny
    if perms or had_perms:
        cfg["permissions"] = perms
