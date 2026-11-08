#!/usr/bin/env python3
"""nousfx_sync: write each tool's global rules and MCP servers from neutral sources.

    python3 -B scripts/nousfx_sync.py [--dry-run | --check | --restore] [--clean-backups]
        [--tools claude,agy] [--project DIR]
        [--home DIR] [--state-home DIR] [--repo DIR] [--platform posix|windows]

Writes under <home>/.claude (tool "claude", the default):
  CLAUDE.md                  neutral rules + Claude overlay + local.md + local.claude.md, with a header
  settings.json              the review gate registered as a PreToolUse hook, and the deny list for secret
                             files and lockfiles in permissions.deny (nousfx_deny.py), and the cesar/* branch
                             deletes in autoMode.allow (nousfx_automode.py); other settings kept
  hooks/review-gate.py       the gate hook and its parser, copied from the repo
  hooks/pr_create_parser.py
  agents/*.md                from harness/agents (roles) and harness/models.toml (role -> model)
Writes under <home>/.gemini/config/plugins/nousfx (tool "agy", only when named):
  plugin.json, rules/AGENTS.md, hooks.json and hooks/ (the review gate), skills/<name> links, mcp_config.json
MCP servers (harness/mcp.toml, <state-home>/mcp.toml) go to <home>/.claude.json (only the entries
sync added) and the agy plugin. --project DIR sets up one repo instead (nousfx_project.py, docs/sync.md
"Project mode"): stubs, the pre-push hook, a skills link and its MCP files from DIR/.nousfx/mcp.toml.

Outside skills: <repo>/harness/skills.lock.json pins skills from other repos (commit and sha256). A real run
fetches the missing ones into <state-home>/outside-skills/ and links them like NousFX's own skills
(nousfx_outside.py). --check and --dry-run fetch nothing. A skill that fails its fetch or checksum is
reported, left out, and makes the run exit 1 once the rest is applied.

Sources: <repo>/harness/AGENTS.md, the tool's overlay under <repo>/harness/overlays/ and the optional,
never committed <state-home>/local.md (every tool), local.claude.md, local.agy.md. The home comes from
the user record, never from the HOME variable. The state home defaults to <home>/.config/nousfx.

Rules budget: every run except --check --summary warns on stderr when a tool's rules body (header excluded)
is over 6144 bytes (NOUSFX_RULES_BUDGET, or a `rules-budget: <n>` line in local.md, overrides it), and lists
each sentence of 6+ words that a local layer repeats from a harness source. Warn only: the exit code stays.

Every write is preceded by a backup in <state-home>/backups/<stamp>/. A second run changes nothing.
Exit codes: 0 ok, 1 drift (--check) or a failed guard, 2 bad usage or missing input.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import nousfx_agents
import nousfx_agy
import nousfx_automode
import nousfx_deny
import nousfx_mcp
import nousfx_outside
import nousfx_project
import nousfx_skills

HERE = os.path.dirname(os.path.abspath(__file__))
GATE_BUDGET, GATE_TIMEOUT = nousfx_agy.GATE_BUDGET, nousfx_agy.GATE_TIMEOUT  # one budget for every gate hook
HOOK_FILES = nousfx_agy.GATE_FILES  # the gate and its parser: the same pair goes to Claude and to agy
DRIFT_HOOK = "nousfx-drift.py"  # Claude only: agy has no session-start event, so its rules carry a line instead
CLAUDE_HOOK_FILES = HOOK_FILES + (DRIFT_HOOK,)
DRIFT_TIMEOUT = 5  # seconds; the hook keeps its own 2 s budget and skips quietly past it
MATCHER = "Bash|PowerShell|mcp__.*create_pull_request"
TOOLS = nousfx_mcp.TOOLS
RULES_BUDGET = 6144  # bytes of generated rules (header excluded), local layers included; warn only (NFX-78)
MIN_REPEAT_WORDS = 6  # a local sentence this long that is also in a harness source is a repeat

SECRET_PATTERNS = [
    re.compile(p) for p in (
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"sk-[A-Za-z0-9]{32,}",
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{30,}",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"xox[abprs]-[A-Za-z0-9-]{10,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"(?i)\b(?:api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{24,}",
    )
]


class SyncError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def user_home():
    """Home from the user record, not the HOME variable (which a tool or test can change)."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except ImportError:  # Windows has no pwd
        return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_or_none(path):
    try:
        return read_text(path)
    except FileNotFoundError:
        return None


def scan_secrets(texts):
    """Names the file of the first hit, never the match itself."""
    for name, text in texts.items():
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise SyncError(f"secret scan failed: {name} contains something that looks like a "
                                f"secret (pattern {pattern.pattern[:24]}...). Nothing was written.")


def warn_local(state, tools):
    """local.md goes to every tool; say so when it holds a term that only Claude Code has."""
    if "agy" not in tools:
        return
    found = nousfx_agy.claude_only_terms(read_or_none(os.path.join(state, "local.md")) or "")
    if found:
        names = ", ".join(t.replace("\\b", "").replace("\\", "") for t in found)
        print(f"warning: {os.path.join(state, 'local.md')} goes to agy too but mentions Claude-only "
              f"terms ({names}). Move those lines to local.claude.md.", file=sys.stderr)


def build_rules(repo, state, home=None):
    # The header holds a hash of the sources, not the repo commit, so --check flags real changes only.
    return nousfx_agy.assemble_rules(nousfx_agy.rule_sources(repo, state, "claude"), SyncError, home=home)


def rules_budget(state):
    """Bytes the rules body may take: NOUSFX_RULES_BUDGET, else a `rules-budget: <n>` line in local.md."""
    env = os.environ.get("NOUSFX_RULES_BUDGET", "").strip()
    if env.isdecimal() and env.isascii():  # isdigit() also takes "²", which int() rejects
        return int(env)
    m = nousfx_agy.BUDGET_LINE.search(read_or_none(os.path.join(state, "local.md")) or "")
    return int(m.group(1)) if m else RULES_BUDGET


def rules_report(wanted, repo, home, state, tools):
    """{"bytes", "budget", "repeats"} for the rules sync builds, plus the warning lines that explain them.
    bytes is the largest body over the tools; a repeat is a sentence of MIN_REPEAT_WORDS words or more that a
    local layer shares with a harness source of the same tool (each pair counted once)."""
    import real_machine_regression as regression  # here, not at the top: it imports this module
    paths = {"claude": os.path.join(home, ".claude", "CLAUDE.md"),
             "agy": os.path.join(nousfx_agy.plugin_dir(home), "rules", "AGENTS.md")}
    budget, size, lines, seen = rules_budget(state), 0, [], set()
    for tool in (t for t in TOOLS if t in tools and paths.get(t) in wanted):
        body = len(nousfx_agy.rules_body(wanted[paths[tool]]).encode("utf-8"))
        size = max(size, body)
        if body > budget:
            lines.append(f"warning: rules budget: {shown_name(paths[tool], home)} is {body} bytes without its "
                         f"header, over the {budget} byte budget. Trim the local files in {state}; shared rules "
                         f"go in the harness (docs/sync.md).")
        sources = [p for _, p, _ in nousfx_agy.rule_sources(repo, state, tool)]
        texts = {p: read_or_none(p) for p in sources}
        for local in sources[2:]:
            mine = regression.sentences(texts[local] or "", MIN_REPEAT_WORDS)
            for shared in sources[:2]:
                for s in sorted(mine & regression.sentences(texts[shared] or "", MIN_REPEAT_WORDS)):
                    if (local, shared, s) not in seen:
                        seen.add((local, shared, s))
                        lines.append(f'warning: repeated rule: "{s[:80]}" is in {local} and {shared}')
    return {"bytes": size, "budget": budget, "repeats": len(seen)}, lines


def is_windows(platform):
    if platform:
        return platform == "windows"
    return os.name == "nt" or sys.platform in ("msys", "cygwin")


def windows_path(path):
    """cygpath -m under MSYS or Cygwin, else forward slashes."""
    try:
        r = subprocess.run(["cygpath", "-m", path], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return path.replace("\\", "/")


def native_for(win):
    """How a path is written inside a command: cygpath-style on Windows, as is elsewhere."""
    return windows_path if win else (lambda p: p)


def interpreter(win):
    """python3 on POSIX; on Windows the launcher is `python`."""
    return "python" if win else "python3"


def owned_hook(command, timeout, win):
    hook = {"type": "command", "timeout": timeout, "command": command}
    if win:
        hook["shell"] = "powershell"
    return hook


def replace_owned(entries, marker, entry):
    """Swap our entry (any whose command names `marker`, from an earlier sync) for the fresh one, in the same
    place, so a hook the user adds after ours does not make settings.json look drifted. New: add at the end."""
    mine = lambda e: any(marker in h.get("command", "") for h in e.get("hooks", []))  # noqa: E731
    at = next((i for i, e in enumerate(entries) if mine(e)), len(entries))
    entries[:] = [e for e in entries if not mine(e)]
    entries.insert(min(at, len(entries)), entry)


def merge_settings(current, gate_path, win, drift=None, deny=None, where="settings.json"):
    """Register the review gate and, when `drift` is (script, repo, tools), the session-start drift warning.
    `deny` is the list of deny rules sync owns (None: leave permissions as they are). The auto-mode allow rules
    are always merged."""
    cfg = json.loads(current) if current else {}
    if not isinstance(cfg, dict):
        raise SyncError(f"{where} is not a JSON object; fix it first", 2)
    py = interpreter(win)
    hooks = cfg.setdefault("hooks", {})
    # -I: PYTHONPATH must not shadow stdlib in the hooks
    gate = owned_hook(f'{py} -I -B "{gate_path}" --budget {GATE_BUDGET}', GATE_TIMEOUT, win)
    replace_owned(hooks.setdefault("PreToolUse", []), "review-gate.py", {"matcher": MATCHER, "hooks": [gate]})
    if drift:
        script, repo, tools = drift
        warn = owned_hook(f'{py} -I -B "{script}" --repo "{repo}" --tools {",".join(tools)}', DRIFT_TIMEOUT, win)
        replace_owned(hooks.setdefault("SessionStart", []), DRIFT_HOOK,
                      {"matcher": "startup", "hooks": [warn]})  # not on resume, clear or compact
    if deny is not None:
        nousfx_deny.merge(cfg, deny, SyncError, where)
        nousfx_deny.merge(cfg, nousfx_deny.ASK_RULES, SyncError, where, section="ask")
    nousfx_automode.merge(cfg, SyncError, where)
    return json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"


def claude_files(repo, home, state, platform, tools):
    claude = os.path.join(home, ".claude")
    win = is_windows(platform)
    native = native_for(win)
    gate_cmd = native(os.path.join(claude, "hooks", "review-gate.py"))
    drift_cmd = native(os.path.join(claude, "hooks", DRIFT_HOOK))
    wanted = {os.path.join(claude, "CLAUDE.md"): build_rules(repo, state, home)}
    wanted.update(nousfx_agents.plan_agents(repo, claude, SyncError))
    for name in CLAUDE_HOOK_FILES:
        src = os.path.join(repo, "claude", "hooks", name)
        text = read_or_none(src)
        if text is None:
            raise SyncError(f"missing hook source: {src}", 2)
        wanted[os.path.join(claude, "hooks", name)] = text
    settings = os.path.join(claude, "settings.json")
    deny = nousfx_deny.wanted_rules(nousfx_deny.load_excludes(state, SyncError))
    try:
        wanted[settings] = merge_settings(read_or_none(settings), gate_cmd, win,
                                          (drift_cmd, native(repo), sorted(tools, key=TOOLS.index)), deny, settings)
    except ValueError as e:
        raise SyncError(f"{settings} is not valid JSON ({e}); fix it first", 2)
    return wanted


def mcp_merged_files(home, project=None):
    """Files Claude also writes, where sync owns only some MCP servers."""
    if project:
        return {os.path.join(project, nousfx_mcp.PROJECT_CLAUDE_REL)}
    return {os.path.join(home, nousfx_mcp.CLAUDE_JSON_REL)}


def merged_files(home, project=None):
    """Files sync merges into, so they hold the user's own content: never scanned as a whole."""
    return mcp_merged_files(home, project) | (set() if project else {os.path.join(home, ".claude", "settings.json")})


def plan(repo, home, state, platform, tools=("claude",), project=None, yes_replace=(), extras=None):
    """Map of absolute path -> expected text for every file sync owns. In project mode `extras` (a dict)
    receives the ProjectPlan under "project": links to make and notes to print."""
    tools = set(tools)
    if project:  # project mode writes only that project's files, never global ones
        wanted = nousfx_mcp.plan_mcp(repo, home, state, tools, SyncError, SECRET_PATTERNS, project)
        pp = nousfx_project.plan_project(repo, project, SyncError, yes_replace)
        wanted.update(pp.files)
        if extras is not None:
            extras["project"] = pp
        plugin = os.path.join(project, ".agents", "plugins", "nousfx")
        opted_in = os.path.exists(os.path.join(project, ".nousfx", "mcp.toml")) or os.path.isdir(plugin)
        if "agy" in tools and opted_in:  # never put files in a project that has no list and no earlier sync
            wanted[os.path.join(plugin, "plugin.json")] = nousfx_agy.manifest_text()
        elif "agy" in tools:
            wanted = {p: t for p, t in wanted.items() if not p.startswith(plugin + os.sep)}
    else:
        wanted = claude_files(repo, home, state, platform, tools) if "claude" in tools else {}
        if "agy" in tools:
            win = is_windows(platform)
            wanted.update(nousfx_agy.plan_agy(repo, home, state, SyncError, interpreter(win), native_for(win)))
        wanted.update(nousfx_mcp.plan_mcp(repo, home, state, tools, SyncError, SECRET_PATTERNS))
    # Folders the user may legitimately link (dotfiles) are not checked; sync's own files and folders are.
    skip = {os.path.join(home, ".claude"), home, state} | ({project} if project else set())
    links = [l for l, _ in extras["project"].links] if project and extras and "project" in extras else []
    for path in list(wanted) + links:  # a symlinked target or folder would be replaced or written through: refuse
        chain = {path, os.path.dirname(path)}
        d = os.path.dirname(path)
        while project and d.startswith(project + os.sep):  # a link planted inside the project sends the write elsewhere
            chain.add(d)
            d = os.path.dirname(d)
        for p in chain - skip:
            if os.path.islink(p):
                raise SyncError(f"{p} is a symlink. Sync would replace or write through it; "
                                f"make it a regular file or folder first, nothing was changed.", 2)
    return wanted


def changes(wanted):
    return {p: t for p, t in wanted.items() if read_or_none(p) != t}


def servers_summary(path, new):
    """Names only: a merged file holds the user's own content, so a dry run must not print it."""
    def servers(text):
        try:
            return json.loads(text).get("mcpServers", {}) if text else {}
        except (ValueError, AttributeError):
            return {}
    old, now = servers(read_or_none(path)), servers(new)
    parts = [f"{sign}{n}" for sign, names in (("+", sorted(set(now) - set(old))), ("-", sorted(set(old) - set(now))),
             ("~", sorted(n for n in set(old) & set(now) if old[n] != now[n]))) for n in names]
    return f"would update the MCP servers in {path}: {' '.join(parts) or 'no server change'}\n"


def diff_of(path, new):
    old = read_or_none(path)
    return "".join(difflib.unified_diff(
        (old or "").splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=path if old is not None else "/dev/null", tofile=path))


def write_atomic(path, text, mode=None):
    """Write via a temp file. mkstemp makes it 0600, which suits sync's own files; mode overrides that."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".nousfx-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def backup(state, paths, merged=(), written=None):
    """Copy the current version of each path (or note it was absent) before it is overwritten.
    `merged` lists files sync only changes a part of (they hold the user's own content): restore
    undoes just sync's part there, never the whole file, so only the mcpServers part is saved (the rest
    holds account data that does not belong in a backup). The backup folder is private to me."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(os.path.join(state, "backups"), mode=0o700, exist_ok=True)
    os.chmod(os.path.join(state, "backups"), 0o700)  # also for a folder an older version created
    root = os.path.join(state, "backups", stamp)
    n = 0
    while os.path.exists(root):
        n += 1
        root = os.path.join(state, "backups", f"{stamp}-{n}")
    os.mkdir(root, 0o700)
    manifest = {}
    for i, p in enumerate(paths):
        if p in merged and os.path.exists(p):
            write_atomic(os.path.join(root, str(i)), nousfx_mcp.servers_slice(read_text(p)))
            manifest[p] = str(i)
        elif os.path.exists(p):
            shutil.copy2(p, os.path.join(root, str(i)))
            manifest[p] = str(i)
        else:
            manifest[p] = None
    write_atomic(os.path.join(root, "manifest.json"), json.dumps(manifest, indent=2))
    # Project mode: a repo file that did not exist is removed on restore only while it still holds what sync
    # wrote, since the user fills the stubs in. Global files roll back fully, run after run.
    made = {p: hashlib.sha256(t.encode("utf-8")).hexdigest() for p, t in (written or {}).items()
            if manifest.get(p, "") is None}
    if made:
        write_atomic(os.path.join(root, "written.json"), json.dumps(made, indent=2))
    if merged:
        write_atomic(os.path.join(root, "merged.json"), json.dumps(sorted(merged), indent=2))
    return root


def undo_merged(path, saved_path, names):
    """Put back only the MCP servers sync changed in a file Claude also writes; never delete a file
    that holds anything else."""
    live = read_or_none(path)
    if live is None:  # the file vanished since; a backup holds only its servers, so it is not recreated
        if saved_path:
            print(f"warning: {path} no longer exists; left as it is", file=sys.stderr)
        return
    saved = read_text(saved_path) if saved_path else None
    try:
        text = nousfx_mcp.unmerge(live, saved, names)
    except ValueError as e:
        raise SyncError(f"{path} is not usable JSON now ({e}); fix it by hand. Nothing in it was changed.")
    if text is None:
        os.remove(path)
    elif text != live:
        # a project .mcp.json is a repo file and keeps its mode; ~/.claude.json stays private, as sync writes it
        write_atomic(path, text, repo_file_mode(path) if os.path.basename(path) == nousfx_mcp.PROJECT_CLAUDE_REL else None)


def restore(state):
    base = os.path.join(state, "backups")
    sets = sorted(d for d in (os.listdir(base) if os.path.isdir(base) else [])
                  if not d.endswith(".restored") and os.path.exists(os.path.join(base, d, "manifest.json")))
    if not sets:
        raise SyncError(f"no backup to restore in {base}")
    root = os.path.join(base, sets[-1])
    manifest = json.loads(read_text(os.path.join(root, "manifest.json")))
    merged = set(json.loads(read_or_none(os.path.join(root, "merged.json")) or "[]"))
    made = json.loads(read_or_none(os.path.join(root, "written.json")) or "{}")
    def recorded(path):
        """Names sync added to a merged file, from each record as it is now and as the backup saved it."""
        names, repo_found = set(), False
        for record, key in reversed(nousfx_mcp.record_keys(state, path)):  # a project's repo record first
            saved = manifest.get(record)
            now = read_or_none(record)
            then = read_or_none(os.path.join(root, saved)) if saved else None
            for text in (now, then):
                try:
                    names |= set(nousfx_mcp.owned_names(text, key))
                    repo_found |= key == nousfx_mcp.PROJECT_CLAUDE_REL and bool(text and text.strip())
                except ValueError:
                    if repo_found:  # as in sync: a project with its own record does not need the state record
                        continue
                    raise SyncError(f"{record} is not usable, so sync cannot tell which servers it added to "
                                    f"{path}. Nothing was changed. Fix or remove that servers entry by hand, "
                                    "then run --restore again.")
        return names
    owned = {p: recorded(p) for p in manifest if p in merged}  # before the records themselves are put back
    # Merged files first: the ownership record is put back last, so a failed run can be repeated.
    for path, saved in sorted(manifest.items(), key=lambda kv: kv[0] not in merged):
        if path in merged:
            names = owned[path]
            if not names:
                print(f"warning: no servers recorded for {path}; it was left as it is", file=sys.stderr)
                continue
            undo_merged(path, os.path.join(root, saved) if saved else None, names)
        elif saved is None:
            if nousfx_skills._is_link(path):  # also a link whose target is gone; a junction needs rmdir
                nousfx_skills._remove_link(path)
            elif os.path.isdir(path):
                print(f"warning: {path} is a folder now; left as it is", file=sys.stderr)
            elif os.path.exists(path):
                if path in made and file_sha256(path) != made[path]:
                    print(f"warning: {path} changed since sync wrote it; left as it is", file=sys.stderr)
                else:
                    os.remove(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy2(os.path.join(root, saved), path)
    try:
        nousfx_skills.undo(root, state)
    except (OSError, RuntimeError) as e:
        raise SyncError(f"restoring the skills failed ({e}). Fix that and run --restore again; "
                        f"finished steps are skipped.")
    os.rename(root, root + ".restored")
    print(f"restored {len(manifest)} file(s) from {root}")


def self_test(hook_path):
    """Run the installed gate once: a PR create on a repo with a wrong branch must block (2),
    and a harmless command must pass (0). A broken or always-allow hook fails one of them."""
    with tempfile.TemporaryDirectory(prefix="nousfx-selftest-") as d:
        # no inherited GIT_DIR and friends: run from a git hook they would point at another repo
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_SYSTEM": os.devnull}
        for git in (["init", "-q", "-b", "selftest/none"],
                    ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"]):
            if subprocess.run(["git", *git], cwd=d, capture_output=True, env=env).returncode != 0:
                raise SyncError("self-test could not create a temp git repo")
        def run(command):
            return subprocess.run([sys.executable, "-I", "-B", hook_path, "check", "--cwd", d, "--command", command],
                                  capture_output=True, text=True, timeout=60, env=env)
        blocked, allowed = run("gh pr create"), run("git status")
    if blocked.returncode != 2 or "is not named cesar/" not in blocked.stderr or allowed.returncode != 0:
        raise SyncError(f"hook self-test failed: PR create exited {blocked.returncode} (want 2 with the branch-name "
                        f"message), harmless command exited {allowed.returncode} (want 0). Fix the hook source and run "
                        f"sync again; --restore undoes the files a run wrote.")


def file_sha256(path):
    """Hash of the raw bytes, or None when the file cannot be read (then it counts as changed)."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def shown_name(path, home):
    """A path relative to home for messages; another Windows drive has no relative form."""
    try:
        return os.path.relpath(path, home)
    except ValueError:
        return path


def repo_file_mode(path):
    """A repo file keeps its mode when replaced; a new one gets what a normal checkout would (umask applies)."""
    try:
        return os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


def make_project_links(links, platform):
    for path, target in links:
        nousfx_skills.make_link(path, target, is_windows(platform))
        print(f"linked {path} -> {target}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print a diff, write nothing")
    mode.add_argument("--check", action="store_true", help="report drift, exit 1 on drift, write nothing")
    ap.add_argument("--summary", action="store_true",
                    help="with --check: print one JSON line of counts (files, skills, outside, and the rules "
                         "bytes, budget and repeats) instead of the listing")
    mode.add_argument("--restore", action="store_true", help="put back the last backup")
    ap.add_argument("--home", help="home dir (default: from the user record)")
    ap.add_argument("--state-home", help="state dir (default: <home>/.config/nousfx)")
    ap.add_argument("--repo", default=os.path.dirname(HERE), help="repo with harness/ and claude/hooks/")
    ap.add_argument("--yes-replace", action="append", default=[], metavar="NAME",
                    help="let the NousFX skill NAME replace an unowned folder (backed up first); in --project mode "
                         "also CLAUDE.md or .githooks/pre-push when hand-written")
    ap.add_argument("--clean-backups", action="store_true",
                    help="move old install.sh backups (*.bak-YYYYMMDD-HHMMSS) into this run's backup")
    ap.add_argument("--tools", default="claude",
                    help="comma list of tools to write: claude, agy (default: claude)")
    ap.add_argument("--project", metavar="DIR",
                    help="set up one repo: CLAUDE.md and AGENTS.md stubs, pre-push hook, skills link, and "
                         "the MCP files from DIR/.nousfx/mcp.toml")
    ap.add_argument("--platform", choices=("posix", "windows"), help="override detection (tests)")
    args = ap.parse_args(argv)
    tools = tuple(dict.fromkeys(t.strip() for t in args.tools.split(",") if t.strip()))
    if not tools or any(t not in TOOLS for t in tools):
        ap.error(f"--tools takes a comma list of {', '.join(TOOLS)}")
    if args.summary and not args.check:
        ap.error("--summary only goes with --check")
    project = os.path.abspath(args.project) if args.project else None
    if project and not os.path.isdir(project):
        ap.error(f"--project: {project} is not a folder")

    home = os.path.abspath(args.home or user_home())
    state = os.path.abspath(args.state_home or os.path.join(home, ".config", "nousfx"))
    repo = os.path.abspath(args.repo)
    try:
        if args.restore:
            return restore(state) or 0
        outside = {} if project else nousfx_outside.load(repo, nousfx_skills.sources(repo), SyncError)
        seen_before = {p: read_or_none(p) for p in mcp_merged_files(home, project)}  # before plan() reads them
        extras = {}
        wanted = plan(repo, home, state, args.platform, tools, project, args.yes_replace, extras)
        pp = extras.get("project") or nousfx_project.ProjectPlan()
        # Merged files hold the user's own content, so only what sync generates is scanned.
        merged = merged_files(home, project)
        scan_secrets({shown_name(p, home): t for p, t in wanted.items() if p not in merged})
        warn_local(state, tools)
        todo = changes(wanted)
        mcp_merged = mcp_merged_files(home, project) & set(todo)
        seen = {p: seen_before[p] for p in mcp_merged}
        failed, need = {}, {}
        if outside and (args.check or args.dry_run):  # these fetch nothing: they only report what would happen
            need = {n: s for n, s in nousfx_outside.status(state, outside).items() if s != "ok"}
        elif outside:
            failed = nousfx_outside.ensure(state, outside, lambda m: print(f"nousfx_sync: {m}"))
            for n, why in sorted(failed.items()):
                print(f"nousfx_sync: outside skill {n}: {why}", file=sys.stderr)
        if project:  # project mode does not touch skills
            sk = nousfx_skills.Plan()
        else:
            extra = [os.path.join(nousfx_agy.plugin_dir(home), "skills")] if "agy" in tools else []
            sk = nousfx_skills.plan(
                repo, home, state, args.yes_replace, args.clean_backups, extra, claude="claude" in tools,
                extra_sources={n: nousfx_outside.cache_dir(state, n, e) for n, e in outside.items()
                               if n not in failed},
                identical=lambda n, path: n in outside and nousfx_outside.same_as_lock(path, outside[n]["sha256"]),
                keep=set(failed))
        rc = 1 if failed else 0  # a failed outside skill is reported after everything else was applied
        links = pp.links
        unrunnable = nousfx_project.not_executable(pp.executable)
        pending = bool(todo or links or unrunnable or sk.pending or need)
        counts = {"files": len(todo) + len(links) + len(unrunnable),
                  "skills": len(sk.actions) + len(sk.collisions) + len(sk.broken), "outside": len(need)}
        bad = any(counts.values())
        # The rules budget only warns: it is never part of `bad`, so it never changes the exit code.
        rules, rule_lines = rules_report(wanted, repo, home, state, tools) if not project else (None, [])
        if args.check and args.summary:  # one line for the session-start warning; notes and listings stay out
            print(json.dumps({**counts, "rules": rules} if rules else counts))
            return 1 if bad else 0
        for line in rule_lines:
            print(line, file=sys.stderr)
        for note in pp.notes:
            print(note)
        if args.check:
            for p in todo:
                print(f"drift: {p}")
            for l, t in links:
                print(f"drift: {l} is missing (would link to {t})")
            for p in unrunnable:
                print(f"drift: {p} is not executable")
            for n, st in need.items():
                print(f"drift: outside skill {n} {st}")
            for line in sk.lines() + sk.listing():
                print(line)
            print("drift found" if bad else "no drift")
            return 1 if bad else 0
        if args.dry_run:
            for p, t in todo.items():
                print(servers_summary(p, t) if p in mcp_merged else diff_of(p, t), end="")
            for l, t in links:
                print(f"would link {l} -> {t}")
            for p in unrunnable:
                print(f"would make {p} executable")
            for n, st in need.items():
                print(f"would {'fetch' if st == 'missing' else 'fetch again'} {n}@{outside[n]['commit'][:7]}")
            for line in sk.lines() + sk.listing():
                print(line)
            print(f"{len(todo) + len(links) + len(unrunnable) + len(need)} file(s) would change" if pending
                  else "nothing would change")
            return 0
        for line in sk.lines():
            if "collision" in line:
                print(line)
        for line in sk.listing():
            print(line)
        def self_tests():  # also after a run that writes nothing: files left by a failed run must not pass for good
            if "claude" in tools and not project:
                self_test(os.path.join(home, ".claude", "hooks", "review-gate.py"))
                print("hook self-test passed")
            if "agy" in tools and not project:
                nousfx_agy.self_test(nousfx_agy.plugin_dir(home), SyncError)
                print("agy gate self-test passed")
        if not pending:
            print("nothing to change")
            self_tests()
            return rc
        root = backup(state, list(todo) + [l for l, _ in links], mcp_merged, todo if project else None)
        try:
            nousfx_skills.apply(sk, root, is_windows(args.platform), state)
        except (OSError, RuntimeError) as e:
            raise SyncError(f"skills step failed ({e}). Run --restore to put the previous files back.")
        for line in sk.lines():
            if "collision" not in line:
                print(line)
        for p, old in seen.items():  # Claude rewrites its own files while it runs: never write over a newer one
            if read_or_none(p) != old:
                raise SyncError(f"{p} changed while sync was running. Nothing was written to it; "
                                "run sync again (--restore undoes the rest).")
        for p, t in todo.items():
            write_atomic(p, t, repo_file_mode(p) if project and p.startswith(project + os.sep) else None)
            print(f"wrote {p}")
        try:
            make_project_links(links, args.platform)
            for p in nousfx_project.not_executable(pp.executable):
                os.chmod(p, 0o755)
        except (OSError, RuntimeError) as e:
            raise SyncError(f"a project step failed ({e}). The backup is {root}; run --restore to put the "
                            "previous files back.")
        print(f"backup: {root}")
        self_tests()
        return rc
    except SyncError as e:
        print(f"nousfx_sync: {e}", file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
