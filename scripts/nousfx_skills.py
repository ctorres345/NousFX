"""nousfx_skills: link NousFX skills into the shared skills home. Used by nousfx_sync.py.

Layout (H = home):
  <repo>/claude/skills/<name>, <repo>/plugins/via/skills/via   the sources
  H/.agents/skills/<name>   link to the source          (the shared skills folder)
  H/.claude/skills/<name>   link to the source          (Claude's folder)
  <state>/skills-manifest.json  every link sync created. Sync owns only what is listed there.

Rules: a folder that is not in the manifest is never overwritten or deleted. A NousFX skill replaces
one only when its name is passed as --yes-replace NAME, and the old folder is moved into the run's
backup first. Every change is journaled in <backup root>/skills.json so --restore can undo it.
Old install.sh backups (<file>.bak-YYYYMMDD-HHMMSS under H/.claude) are listed and moved into the
backup too.

Windows: if a symlink cannot be created, a junction is made with `mklink /J` (NOUSFX_JUNCTION_CMD
replaces that command, NOUSFX_NO_SYMLINK=1 forces the fallback; both exist for tests).
"""
import json
import os
import re
import shlex
import shutil
import subprocess

MANIFEST = "skills-manifest.json"
BAK_RE = re.compile(r"\.bak-\d{8}-\d{6}$")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _is_link(p):
    return os.path.islink(p) or (hasattr(os.path, "isjunction") and os.path.isjunction(p))


def _remove_link(p):
    try:
        os.unlink(p)
    except OSError:
        os.rmdir(p)  # a junction


def _readlink(p):
    try:
        return os.readlink(p)
    except OSError:
        return None


def make_link(path, target, win):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        if os.environ.get("NOUSFX_NO_SYMLINK") == "1":
            raise OSError("symlinks disabled")
        os.symlink(target, path, target_is_directory=True)
        return
    except OSError as e:
        if not win:
            raise
        err = e
    cmd = shlex.split(os.environ.get("NOUSFX_JUNCTION_CMD", "")) or ["cmd", "/c", "mklink", "/J"]
    r = subprocess.run([*cmd, path, target], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cannot link {path}: symlink failed ({err}) and junction failed ({r.stderr.strip()})")


def sources(repo):
    """name -> source folder for every NousFX skill."""
    found = {}
    for base in (os.path.join(repo, "claude", "skills"), os.path.join(repo, "plugins", "via", "skills")):
        for name in sorted(os.listdir(base) if os.path.isdir(base) else []):
            if not name.startswith(".") and os.path.isdir(os.path.join(base, name)):
                found[name] = os.path.join(base, name)
    return found


class Plan:
    def __init__(self):
        self.actions, self.collisions, self.broken, self.old_backups = [], [], [], []

    @property
    def pending(self):
        return bool(self.actions or self.old_backups)

    def lines(self):
        out = [f"skill {a['op']}: {a['path']}" for a in self.actions]
        out += [f"skill collision (not mine, left untouched; pass --yes-replace {n} to replace): {p}"
                for n, p in self.collisions]
        out += [f"broken skill link: {p}" for p in self.broken]
        out += [f"old install.sh backup: {p}" for p in self.old_backups]
        return out


def _load(state):
    text = _read(os.path.join(state, MANIFEST))
    return json.loads(text).get("links", {}) if text else {}


def plan(repo, home, state, yes_replace=()):
    manifest = _load(state)
    p = Plan()
    p.manifest = dict(manifest)
    want = sources(repo)
    agents = os.path.join(home, ".agents", "skills")
    claude = os.path.join(home, ".claude", "skills")
    for name, src in want.items():
        real = os.path.realpath(src)
        steps, blocked = [], False
        for path, target in ((os.path.join(agents, name), src),
                             (os.path.join(claude, name), os.path.join(agents, name))):
            if not os.path.lexists(path):
                steps.append(("create", path, target))
            elif path in manifest:
                if not _is_link(path):
                    blocked = True
                    p.collisions.append((name, path))
                elif os.path.realpath(path) != real:
                    steps.append(("relink", path, target))
            elif _is_link(path) and os.path.realpath(path) == real:
                continue  # someone else's link to the same place: fine, not recorded
            elif name in yes_replace:
                steps.append(("replace", path, target))
            else:
                blocked = True
                p.collisions.append((name, path))
        if not blocked:
            p.actions += [{"op": o, "path": a, "target": t} for o, a, t in steps]
    for path in sorted(manifest):
        if os.path.basename(path) in want:
            continue
        p.actions.append({"op": "unlink" if _is_link(path) else "forget", "path": path})
    p.broken = [path for path in sorted(manifest)
                if os.path.islink(path) and not os.path.exists(path)]
    dot_claude = os.path.join(home, ".claude")
    for base, dirs, files in os.walk(dot_claude):
        p.old_backups += [os.path.join(base, n) for n in sorted(files + dirs) if BAK_RE.search(n)]
        dirs[:] = [d for d in dirs if not BAK_RE.search(d)]
    return p


def apply(p, root, win):
    """Make the planned changes. Everything that is replaced or removed is journaled in root."""
    os.makedirs(os.path.join(root, "skills"), exist_ok=True)
    state = os.path.dirname(os.path.dirname(root))
    journal = {"manifest_before": _read(os.path.join(state, MANIFEST)), "ops": []}
    links = dict(p.manifest)
    n = 0

    def save(path):
        nonlocal n
        n += 1
        dest = os.path.join(root, "skills", str(n))
        shutil.move(path, dest)
        return str(n)

    for old in p.old_backups:
        journal["ops"].append({"op": "moved", "path": old, "saved": save(old)})
    for a in p.actions:
        op, path = a["op"], a["path"]
        entry = {"op": op, "path": path}
        if op == "forget":
            links.pop(path, None)
            continue
        if op in ("relink", "unlink") or (op == "replace" and _is_link(path)):
            entry["old_target"] = _readlink(path)
            _remove_link(path)
        elif op == "replace":
            entry["saved"] = save(path)
        journal["ops"].append(entry)
        if op == "unlink":
            links.pop(path, None)
        else:
            make_link(path, a["target"], win)
            links[path] = a["target"]
    _write(os.path.join(root, "skills.json"), json.dumps(journal, indent=2))
    _write(os.path.join(state, MANIFEST), json.dumps({"links": links}, indent=2, sort_keys=True) + "\n")


def undo(root):
    """Reverse apply() from the journal in a backup root (a root without one is skipped)."""
    text = _read(os.path.join(root, "skills.json"))
    if text is None:
        return
    journal = json.loads(text)
    for e in reversed(journal["ops"]):
        op, path = e["op"], e["path"]
        if op == "moved":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.move(os.path.join(root, "skills", e["saved"]), path)
            continue
        if op in ("create", "relink", "replace") and _is_link(path):
            _remove_link(path)
        if "saved" in e:
            shutil.move(os.path.join(root, "skills", e["saved"]), path)
        elif e.get("old_target"):
            os.symlink(e["old_target"], path, target_is_directory=True)
    state = os.path.dirname(os.path.dirname(root))
    before = journal["manifest_before"]
    if before is None:
        if os.path.exists(os.path.join(state, MANIFEST)):
            os.remove(os.path.join(state, MANIFEST))
    else:
        _write(os.path.join(state, MANIFEST), before)
