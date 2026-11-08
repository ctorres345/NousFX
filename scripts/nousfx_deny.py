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

Sync owns exactly the rules in RULES (deny) and ASK_RULES (ask). The user's own deny rules, and every other setting, are kept.
"""
import importlib.util
import os
import re
import tomllib

# "//" anchors a pattern at the file system root, so a rule matches anywhere on disk, not only under the
# session's folder (a bare "**/x" is relative to the current directory). On Windows Claude Code turns
# C:\Users\me into /c/Users/me before matching, so "//**/x" covers every drive. The same text works on both.
# The secret patterns live in one list shared with the Gemini excludes (NFX-103), loaded by path from this repo
# so sync never needs the skill installed first. If it cannot be loaded, SECRETS is empty and LOAD_ERROR says
# why; load_excludes and merge then stop sync with code 2 before anything is written, never a shorter list.
PATTERNS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                             "claude", "skills", "gemini-review", "scripts", "secret_patterns.py")


def load_patterns(path=PATTERNS_FILE):
    """The DENY tuple from secret_patterns.py at path. Raises on any failure."""
    spec = importlib.util.spec_from_file_location("nousfx_secret_patterns", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    deny = module.DENY
    if not deny or not isinstance(deny, tuple) or not all(isinstance(p, str) and p for p in deny):
        raise ValueError("DENY must be a non-empty tuple of patterns")
    return deny


try:
    SECRETS, LOAD_ERROR = tuple(f"//**/{p}" for p in load_patterns()), None
except Exception as e:  # noqa: BLE001 - any failure must stop sync, not shorten the list
    SECRETS, LOAD_ERROR = (), f"{type(e).__name__}: {e}"
LOCKFILES = ("//**/package-lock.json", "//**/yarn.lock", "//**/pnpm-lock.yaml", "//**/pubspec.lock",
             "//**/Podfile.lock", "//**/gradle.lockfile", "//**/gradle/dependency-locks/*.lockfile")
# Read applies to Grep and Glob too (best effort); Edit to every built-in tool that changes files (Write,
# MultiEdit, NotebookEdit). Write(...) or NotebookEdit(...) path rules never match, so there are none.
# Docs: https://code.claude.com/docs/en/permissions, "Read and Edit".
EXCLUDABLE = (tuple(f"{tool}({p})" for p in SECRETS for tool in ("Read", "Edit"))
              + tuple(f"Edit({p})" for p in LOCKFILES))
# The review gate's marker, pass stamp, stamp key and branch-name exception (NFX-101), so an agent's file tools cannot read the key
# or write a mark by hand. The gate finds them with `git rev-parse --git-path`, which may be .git/, a
# worktree's .git/worktrees/<name>/, a submodule's .git/modules/<name>/ or a separate git dir, so the rule
# matches the (unique) file name anywhere. The gate itself writes them from python, which no Read or Edit
# rule sees. deny.toml cannot exclude these: they are part of the review gate, not a preference.
GATE_FILES = ("claude-review-ok", "claude-review-stamp-key", "claude-review-check-pass", "claude-branch-ok")
GATE_RULES = tuple(f"{tool}(//**/{name})" for name in GATE_FILES for tool in ("Read", "Edit"))
RULES = EXCLUDABLE + GATE_RULES
# Best-effort ask rules (permissions.ask) for the gate's waivers, which only the user may approve (NFX-101).
# An ask rule still prompts in auto and bypassPermissions mode
# (https://code.claude.com/docs/en/permission-modes, "Actions no mode auto-approves"). A "*" in a Bash rule matches any text, so
# any path to the gate matches; `python3 -c`, a copy of the script or `eval` do not (docs/review-gate.md).
# The gate hook also runs on Windows' PowerShell tool, whose rules have the same shape as Bash rules.
ASK_RULES = tuple(f"{tool}(*review-gate.py*{flag}*)" for tool in ("Bash", "PowerShell")
                  for flag in ("--skip", "--tier-override", "--allow-branch"))
FILE_NAME = "deny.toml"
_RULE = re.compile(r"^(Read|Edit)\((.+)\)$")


def require_patterns(SyncError):
    if LOAD_ERROR:
        raise SyncError(f"cannot load the secret patterns from {PATTERNS_FILE} ({LOAD_ERROR}); nothing was written", 2)


def load_excludes(state, SyncError):
    """The rules to leave out, from <state>/deny.toml (missing file: none)."""
    require_patterns(SyncError)
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
        hits = {r for r in EXCLUDABLE if r == entry or _RULE.match(r).group(2) == entry}
        if not hits:
            raise SyncError(f"{path}: {entry!r} matches no deny rule. The rules are: {', '.join(EXCLUDABLE)}", 2)
        out |= hits
    return out


def wanted_rules(excludes):
    return [r for r in RULES if r not in excludes]


def splice_owned(current, rules, owned):
    """`current` with every rule in `owned` replaced by `rules`, placed where the first owned one was, else at
    the end. The user's own entries keep their order (shared with nousfx_automode)."""
    owned = set(owned)
    at = next((i for i, r in enumerate(current) if r in owned), len(current))  # everything before it is the user's
    kept = [r for r in current if r not in owned]
    return kept[:at] + list(rules) + kept[at:]


def merge(cfg, rules, SyncError, where, section="deny"):
    """Put `rules` into cfg["permissions"][section] ("deny" or "ask"). Rules sync owns there that are not wanted (now excluded) go;
    the user's own rules stay where they are. Ours go where the first of ours was, else at the end."""
    require_patterns(SyncError)
    had_perms, perms = "permissions" in cfg, cfg.get("permissions", {})
    if not isinstance(perms, dict):
        raise SyncError(f"{where}: \"permissions\" is not an object; fix it first", 2)
    had_list, current = section in perms, perms.get(section, [])
    if not isinstance(current, list) or not all(isinstance(r, str) for r in current):
        raise SyncError(f"{where}: \"permissions.{section}\" is not a list of strings; fix it first", 2)
    current = splice_owned(current, rules, {"deny": RULES, "ask": ASK_RULES}[section])
    if current or had_list:  # never leave an empty key behind that the user did not have
        perms[section] = current
    if perms or had_perms:
        cfg["permissions"] = perms
