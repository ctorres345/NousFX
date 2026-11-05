#!/usr/bin/env python3
"""via_skills: install third-party skills only at a pinned commit with a verified checksum.

Run as `python3 -I -B via_skills.py [options] <command>`.

  plan                     print what install would fetch and change; fetches nothing
  install --confirm DIGEST fetch, verify, then install the missing or outdated locked skills (none if any check
                           fails). DIGEST is printed by `plan`: install runs only the plan that was shown.
  verify                   check the installed skills against the lock (exit 1 on any drift)
  add NAME --repo U --path P --commit SHA [--approve SHA256]
                           show the files and checksum of a new skill; write the lock entry only when --approve
                           carries the checksum that was shown
  update NAME [--commit SHA] [--approve SHA256]
                           show the diff between the pinned and the new commit (default: the remote HEAD);
                           rewrite the lock entry only when --commit and --approve carry what was shown.
                           It never installs by itself.

The lock (plugins/via/skills.lock.json) is committed, so a PR shows every pin change:
  {"version": 1, "skills": {"NAME": {"repo": "https://...", "path": "folder/in/repo",
                                     "commit": "<40 hex>", "sha256": "<64 hex>"}}}
The checksum is the sha256 over every file of the skill folder sorted by path: path, NUL, byte length, NUL, bytes.
It covers file contents and names only (no times, no modes), so it is the same on every machine.

The digest and the --approve checksum bind a step to what was shown: they catch a plan or a lock that changed
after the user looked. They are not consent by themselves, because the same tool prints them. Consent is the
user's yes in chat and Claude Code's permission prompt for the command; setup.md tells the agent to wait for both.

Safety rules, all enforced before anything is written under the skills home:
  - the pin must be a full 40-character commit sha (never a branch or tag); https URLs only
    (file:// only with --allow-file-urls, which tests use);
  - names that would land in one file on a case-insensitive disk (case or Unicode twins, a file and a folder
    with the same name) and control characters in file names are refused, and the written files are hashed
    again before they replace anything;
  - the tree is read straight from git objects (no checkout, so no filters or hooks); symlinks, submodules,
    paths with `..` or `.git`, and more than 2000 files or 20 MB are refused;
  - git runs with a clean environment (no GIT_* variables, no user or system git config);
  - a skill is installed only if every locked skill verified, and an unowned folder is never touched;
  - nothing from a skill is run. Files are only copied.

Installed skills go to the shared skills home (default ~/.agents/skills) with a link in Claude's skills folder
(default ~/.claude/skills). Ownership is recorded in <state>/locked-skills.json (state = ~/.config/nousfx).
Only a folder listed there is ever replaced, and the old copy is moved into <state>/backups first.
Callers run this with `python3 -I -B`, like the other Via scripts.
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
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # `python3 -I` adds no script folder to sys.path
import via_home  # noqa: E402

MAX_FILES = 2000
MAX_BYTES = 20 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy")
DIFF_LINES = 2000


class Fail(Exception):
    pass


# --- lock ---------------------------------------------------------------------------------------------------
def check_path(path):
    parts = path.split("/")
    if (not path or path.startswith("/") or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(p in ("", ".", "..", ".git") for p in parts)):
        raise Fail("unsafe path %r" % path)


def check_repo(repo, allow_file):
    if repo.startswith("https://") or (allow_file and repo.startswith("file://")):
        return
    raise Fail("the repo must be an https URL, got %r" % repo)


def check_commit(commit):
    if not isinstance(commit, str) or not SHA1_RE.match(commit):
        raise Fail("the pin must be a full 40-character commit sha, got %r" % (commit,))


def fold(path):
    """Key under which two names are the same file on a case-insensitive or normalizing filesystem."""
    return unicodedata.normalize("NFC", path).casefold()


def check_source(repo, path, commit, allow_file):
    check_repo(repo, allow_file)
    check_path(path)
    check_commit(commit)


def check_entry(name, e, allow_file):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise Fail("bad skill name %r" % (name,))
    if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("repo", "path", "commit", "sha256")):
        raise Fail("lock entry %s needs repo, path, commit and sha256 as strings" % name)
    check_source(e["repo"], e["path"], e["commit"], allow_file)
    if not SHA256_RE.match(e["sha256"]):
        raise Fail("lock entry %s: sha256 must be 64 hex characters" % name)


def load_lock(path, allow_file):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        raise Fail("cannot read the lock %s: %s" % (path, e))
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("skills"), dict):
        raise Fail("the lock %s must be {\"version\": 1, \"skills\": {...}}" % path)
    for name, e in doc["skills"].items():
        check_entry(name, e, allow_file)
    if len({fold(n) for n in doc["skills"]}) != len(doc["skills"]):
        raise Fail("two skill names in the lock differ only by case or Unicode form")
    return doc


def write_lock(path, doc):
    via_home.write_atomic(os.path.abspath(path), json.dumps(doc, indent=2, sort_keys=True) + "\n")


# --- git ----------------------------------------------------------------------------------------------------
def git_env(scratch):
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": scratch, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    env.update({k: os.environ[k] for k in PROXY_VARS if k in os.environ})
    return env


def run_git(args, scratch, allow_file, data=None):
    cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "core.hooksPath=" + os.devnull]
    if allow_file:
        cmd += ["-c", "protocol.file.allow=always"]
    try:
        return subprocess.run(cmd + args, env=git_env(scratch), input=data, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise Fail("cannot run git: %s" % e)


def text(r):
    return r.stderr.decode("utf-8", "replace").strip()


def fetch(repo, commit, scratch, allow_file):
    """Bare repo in scratch holding `commit`. Returns its path."""
    gd = os.path.join(scratch, "g")
    if run_git(["init", "-q", "--bare", gd], scratch, allow_file).returncode != 0:
        raise Fail("git init failed")
    r = run_git(["--git-dir", gd, "fetch", "-q", "--depth", "1", "--no-tags", repo, commit], scratch, allow_file)
    if r.returncode != 0:  # a server may refuse a fetch by sha; fall back to its branches
        r = run_git(["--git-dir", gd, "fetch", "-q", "--no-tags", repo, "+refs/heads/*:refs/remotes/o/*"],
                    scratch, allow_file)
        if r.returncode != 0:
            raise Fail("cannot fetch %s: %s" % (repo, text(r)))
    r = run_git(["--git-dir", gd, "rev-parse", "--verify", "-q", commit + "^{commit}"], scratch, allow_file)
    if r.returncode != 0 or r.stdout.decode().strip() != commit:
        raise Fail("commit %s is not in %s" % (commit, repo))
    return gd


def read_tree(gd, commit, path, scratch, allow_file):
    """{relative path: (executable, bytes)} for the folder `path` at `commit`, after the safety rules."""
    r = run_git(["--git-dir", gd, "ls-tree", "-r", "-z", "--long", "%s:%s" % (commit, path)], scratch, allow_file)
    if r.returncode != 0:
        raise Fail("%r is not a folder in commit %s" % (path, commit))
    entries, total = [], 0
    for rec in r.stdout.split(b"\0"):
        if not rec:
            continue
        meta, _, name = rec.partition(b"\t")
        mode, kind, sha, size = meta.decode().split()
        try:
            rel = name.decode("utf-8")
        except UnicodeDecodeError:
            raise Fail("a file name in %s is not valid UTF-8" % path)
        if mode == "120000":
            raise Fail("the skill holds a symlink (%s); refused" % rel)
        if kind != "blob" or mode not in ("100644", "100755"):
            raise Fail("the skill holds %s (%s), only plain files are allowed" % (rel, kind))
        check_path(rel)
        total += int(size)
        entries.append((rel, mode == "100755", sha))
    if not entries:
        raise Fail("%r holds no files in commit %s" % (path, commit))
    seen = set()
    for rel, _exe, _sha in entries:  # two names that land in one file would hide what the checksum covers
        key = fold(rel)
        if key in seen:
            raise Fail("two files in the skill share a name on a case-insensitive disk (%s); refused" % rel)
        seen.add(key)
    for rel, _exe, _sha in entries:
        parts = fold(rel).split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise Fail("a file and a folder in the skill share a name (%s); refused" % rel)
    if len(entries) > MAX_FILES or total > MAX_BYTES:
        raise Fail("the skill is too large (%d files, %d bytes; limit %d files, %d MB)"
                   % (len(entries), total, MAX_FILES, MAX_BYTES // (1024 * 1024)))
    batch = run_git(["--git-dir", gd, "cat-file", "--batch"], scratch, allow_file,
                    data="".join(e[2] + "\n" for e in entries).encode())
    if batch.returncode != 0:
        raise Fail("cannot read the skill files: %s" % text(batch))
    out, pos, files = memoryview(batch.stdout), 0, {}
    for rel, exe, sha in entries:
        end = batch.stdout.find(b"\n", pos)
        head = bytes(out[pos:end]).decode("ascii", "replace").split() if end >= 0 else []
        if len(head) != 3 or head[0] != sha or head[1] != "blob" or not head[2].isdigit():
            raise Fail("unexpected answer from git while reading %s" % rel)
        size = head[2]
        start = end + 1
        files[rel] = (exe, bytes(out[start:start + int(size)]))
        pos = start + int(size) + 1
    return files


def checksum(files):
    h = hashlib.sha256()
    for path in sorted(files):
        data = files[path][1]
        h.update(path.encode() + b"\0" + str(len(data)).encode() + b"\0")
        h.update(data)
    return h.hexdigest()


def stage_many(sources, allow_file):
    """{key: files} for [(key, repo, path, commit)]. One fetch per (repo, commit), all in temp folders that are removed."""
    for _key, repo, path, commit in sources:
        check_source(repo, path, commit, allow_file)
    out = {}
    for repo, commit in sorted({(r, c) for _k, r, _p, c in sources}):
        with tempfile.TemporaryDirectory(prefix="via-skills-") as scratch:
            gd = fetch(repo, commit, scratch, allow_file)
            for key, r, path, c in sources:
                if (r, c) == (repo, commit):
                    out[key] = read_tree(gd, commit, path, scratch, allow_file)
    return out


def stage(repo, path, commit, allow_file):
    return stage_many([(0, repo, path, commit)], allow_file)[0]


def verified_many(entries, allow_file):
    """{name: files} for {name: lock entry}; fails on the first checksum that differs."""
    files = stage_many([(n, e["repo"], e["path"], e["commit"]) for n, e in entries.items()], allow_file)
    for name, e in entries.items():
        got = checksum(files[name])
        if got != e["sha256"]:
            raise Fail("checksum mismatch for %s: the lock says %s, commit %s gives %s; nothing installed"
                       % (name, e["sha256"], e["commit"], got))
    return files


# --- installed state ----------------------------------------------------------------------------------------
def manifest_path():
    return os.path.join(via_home.anchor(), "locked-skills.json")


def load_manifest():
    path = manifest_path()
    via_home.validate(path, via_home.anchor())
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise Fail("cannot read %s: %s" % (path, e))
    if (not isinstance(doc, dict) or not isinstance(doc.get("skills"), dict)
            or not all(isinstance(v, dict) for v in doc["skills"].values())):
        raise Fail("%s is not a valid ownership record" % path)
    return doc["skills"]


def save_manifest(owned):
    path = manifest_path()
    via_home.validate(path, via_home.anchor())
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    via_home.write_atomic(path, json.dumps({"version": 1, "skills": owned}, indent=2, sort_keys=True) + "\n")


def disk_tree(folder):
    """Files of an installed folder in the same shape as read_tree, or None if it is not a plain folder."""
    if os.path.islink(folder) or not os.path.isdir(folder):
        return None
    files = {}
    for base, dirs, names in os.walk(folder):
        for n in names + [d for d in dirs if os.path.islink(os.path.join(base, d))]:
            p = os.path.join(base, n)
            if os.path.islink(p):
                return None
            with open(p, "rb") as f:
                files[os.path.relpath(p, folder).replace(os.sep, "/")] = (False, f.read())
    return files


def points_to(link, target):
    return os.path.islink(link) and os.path.realpath(link) == os.path.realpath(target)


def classify(name, e, owned, shared, claude):
    """One of: ok, install, replace, collision (with a reason)."""
    folder, link = os.path.join(shared, name), os.path.join(claude, name)
    mine = owned.get(name)
    if mine is None:
        if os.path.lexists(folder):
            return "collision", "%s exists and is not mine (not in the ownership record); left untouched" % folder
        if os.path.lexists(link):
            return "collision", "%s exists and is not mine; left untouched" % link
        return "install", ""
    if os.path.lexists(link) and not points_to(link, folder):
        return "collision", "%s exists and is not mine; left untouched" % link
    if not os.path.isdir(folder) or os.path.islink(folder):
        return "install", ""
    if (mine.get("commit"), mine.get("sha256")) == (e["commit"], e["sha256"]):
        if points_to(link, folder):
            return "ok", ""
        return "install", ""
    return "replace", ""


# --- commands -----------------------------------------------------------------------------------------------
def show_plan(lock, owned, shared, claude):
    """Print the plan. Returns (has_collision, todo, digest); the digest binds an install to exactly this plan."""
    rows, bad, todo, h = [], False, [], hashlib.sha256()
    h.update(json.dumps([os.path.abspath(shared), os.path.abspath(claude)]).encode())
    for name in sorted(lock["skills"]):
        e = lock["skills"][name]
        action, why = classify(name, e, owned, shared, claude)
        if action == "ok":
            rows.append("ok        %s (already at %s)" % (name, e["commit"]))
            continue
        if action == "collision":
            bad = True
            rows.append("collision %s: %s" % (name, why))
            continue
        todo.append(name)
        h.update(json.dumps([name, action, e["repo"], e["path"], e["commit"], e["sha256"]]).encode())
        rows.append("%-9s %s  fetch %s  path %s  commit %s" % (action, name, e["repo"], e["path"], e["commit"]))
    digest = h.hexdigest()
    print("\n".join(rows) if rows else "the lock is empty")
    if todo and not bad:
        print("digest %s" % digest)
    return bad, todo, digest


def cmd_plan(a, lock, owned):
    return 1 if show_plan(lock, owned, a.skills_home, a.claude_skills)[0] else 0


def write_skill(stage_dir, files):
    os.makedirs(stage_dir)
    root = os.path.realpath(stage_dir)
    for rel, (exe, data) in files.items():
        target = os.path.normpath(os.path.join(root, rel))
        if os.path.commonpath([target, root]) != root:
            raise Fail("unsafe path %r" % rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        os.chmod(target, 0o755 if exe else 0o644)


def cmd_install(a, lock, owned):
    bad, todo, digest = show_plan(lock, owned, a.skills_home, a.claude_skills)
    if bad:
        raise Fail("a collision stops the install; nothing was changed")
    if not todo:
        print("nothing to do")
        return 0
    if a.confirm != digest:
        raise Fail("the plan above is not confirmed; show it to the user, then run install --confirm %s" % digest)
    staged = verified_many({n: lock["skills"][n] for n in todo}, a.allow_file_urls)  # every skill verified first
    os.makedirs(a.skills_home, exist_ok=True)
    os.makedirs(a.claude_skills, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        for name in todo:
            e, folder = lock["skills"][name], os.path.join(a.skills_home, name)
            tmp = os.path.join(a.skills_home, ".via-stage-" + name)
            shutil.rmtree(tmp, ignore_errors=True)
            write_skill(tmp, staged[name])
            on_disk = disk_tree(tmp)  # what a case-insensitive disk really holds must still match the lock
            if on_disk is None or checksum(on_disk) != e["sha256"]:
                raise Fail("the files written for %s do not match the lock; nothing was put in place" % name)
            kept = None
            if os.path.isdir(folder):  # owned (classify refused anything else): keep the old copy
                backup = os.path.join(via_home.anchor(), "backups", "skills-" + stamp)
                os.makedirs(backup, exist_ok=True)
                kept = os.path.join(backup, name)
                shutil.move(folder, kept)
            try:
                os.replace(tmp, folder)
            except OSError:
                if kept:
                    shutil.move(kept, folder)
                raise
            owned[name] = {k: e[k] for k in ("repo", "path", "commit", "sha256")}  # before the link, so a failure can be repaired
            link = os.path.join(a.claude_skills, name)
            if not os.path.lexists(link):
                os.symlink(folder, link, target_is_directory=True)
            print("installed %s at %s" % (name, e["commit"]))
    finally:
        for name in todo:
            shutil.rmtree(os.path.join(a.skills_home, ".via-stage-" + name), ignore_errors=True)
        save_manifest(owned)
    return 0


def cmd_verify(a, lock, owned):
    bad = False
    for name in sorted(lock["skills"]):
        e = lock["skills"][name]
        action, why = classify(name, e, owned, a.skills_home, a.claude_skills)
        files = disk_tree(os.path.join(a.skills_home, name))
        if files is None:
            problem = "missing"
        elif action == "collision":
            problem = why
        elif action == "install" and name not in owned:
            problem = "not mine (not in the ownership record)"
        elif action == "replace":
            problem = "installed at %s, the lock says %s (run install)" % (owned[name].get("commit"), e["commit"])
        elif checksum(files) != e["sha256"]:
            problem = "files differ from the lock (edited after install)"
        elif action == "install":
            problem = "the Claude skills link is missing (run install)"
        else:
            problem = None
        bad = bad or bool(problem)
        print("%-7s %s%s" % ("DRIFT" if problem else "ok", name, ": " + problem if problem else ""))
    return 1 if bad else 0


def listing(files):
    return "\n".join("  %s  (%d bytes)" % (p, len(files[p][1])) for p in sorted(files))


def show_diff(old, new):
    lines = []
    for path in sorted(set(old) | set(new)):
        o, n = old.get(path), new.get(path)
        if o is not None and n is not None and o[1] == n[1]:
            continue
        if o is None:
            lines.append("added: " + path)
        elif n is None:
            lines.append("removed: " + path)
        else:
            lines.append("changed: " + path)
        try:
            a_ = o[1].decode("utf-8").splitlines() if o else []
            b_ = n[1].decode("utf-8").splitlines() if n else []
        except UnicodeDecodeError:
            lines.append("  (binary file)")
            continue
        lines += list(difflib.unified_diff(a_, b_, "pinned/" + path, "new/" + path, lineterm=""))
    if not lines:
        return "no file changes"
    if len(lines) > DIFF_LINES:
        lines = lines[:DIFF_LINES] + ["... diff cut at %d lines" % DIFF_LINES]
    return "\n".join(lines)


def remote_head(repo, allow_file):
    with tempfile.TemporaryDirectory(prefix="via-skills-") as scratch:
        r = run_git(["ls-remote", repo, "HEAD"], scratch, allow_file)
        first = r.stdout.decode("utf-8", "replace").split()[:1]
        if r.returncode != 0 or not first or not SHA1_RE.match(first[0]):
            raise Fail("cannot read HEAD of %s: %s" % (repo, text(r)))
        return first[0]


def cmd_add(a, lock):
    if not (a.repo and a.path and a.commit):
        raise Fail("add needs --repo, --path and --commit")
    if not NAME_RE.match(a.name):
        raise Fail("bad skill name %r" % a.name)
    if a.name in lock["skills"]:
        raise Fail("%s is already locked; use update" % a.name)
    files = stage(a.repo, a.path, a.commit, a.allow_file_urls)
    got = checksum(files)
    print("%s from %s path %s commit %s\n%s\nsha256 %s" % (a.name, a.repo, a.path, a.commit, listing(files), got))
    if a.approve is None:
        print("lock unchanged (read the files above, then pass --approve %s to pin them)" % got)
        return 0
    if a.approve != got:
        raise Fail("--approve does not match the checksum shown above; lock unchanged")
    lock["skills"][a.name] = {"repo": a.repo, "path": a.path, "commit": a.commit, "sha256": got}
    write_lock(a.lock, lock)
    print("pinned %s in %s" % (a.name, a.lock))
    return 0


def cmd_update(a, lock):
    e = lock["skills"].get(a.name)
    if e is None:
        raise Fail("%s is not in the lock; use add" % a.name)
    if a.approve is not None and not a.commit:
        raise Fail("--approve needs --commit, so the commit you approve is the one you saw")
    target = a.commit or remote_head(e["repo"], a.allow_file_urls)
    check_commit(target)
    if target == e["commit"]:
        print("%s is already pinned at %s" % (a.name, target))
        return 0
    both = stage_many([("old", e["repo"], e["path"], e["commit"]), ("new", e["repo"], e["path"], target)],
                      a.allow_file_urls)
    old, new = both["old"], both["new"]
    if checksum(old) != e["sha256"]:
        raise Fail("the pinned copy of %s no longer matches its checksum; lock unchanged" % a.name)
    got = checksum(new)
    print("%s: %s -> %s\n%s\nnew sha256 %s" % (a.name, e["commit"], target, show_diff(old, new), got))
    if a.approve is None:
        print("lock unchanged (read the diff above, then pass --commit %s --approve %s to pin it)" % (target, got))
        return 0
    if a.approve != got:
        raise Fail("--approve does not match the checksum shown above; lock unchanged")
    lock["skills"][a.name] = dict(e, commit=target, sha256=got)
    write_lock(a.lock, lock)
    print("pinned %s at %s; run install to apply it" % (a.name, target))
    return 0


def parser():
    here = os.path.dirname(os.path.abspath(__file__))
    home = via_home.home_dir()
    ap = argparse.ArgumentParser(prog="via_skills", description=__doc__.split("\n")[0])
    ap.add_argument("--lock", default=os.path.join(os.path.dirname(here), "skills.lock.json"))
    ap.add_argument("--skills-home", default=os.path.join(home, ".agents", "skills"))
    ap.add_argument("--claude-skills", default=os.path.join(home, ".claude", "skills"))
    ap.add_argument("--allow-file-urls", action="store_true", help="accept file:// repos (tests only)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    sub.add_parser("verify")
    p = sub.add_parser("install")
    p.add_argument("--confirm", metavar="DIGEST", help="the digest printed by plan, after the user agreed to it")
    p = sub.add_parser("add")
    p.add_argument("name")
    p.add_argument("--repo")
    p.add_argument("--path")
    p.add_argument("--commit")
    p.add_argument("--approve", metavar="SHA256", help="the sha256 printed by the preview")
    p = sub.add_parser("update")
    p.add_argument("name")
    p.add_argument("--commit")
    p.add_argument("--approve", metavar="SHA256", help="the sha256 printed by the preview (needs --commit)")
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        lock = load_lock(a.lock, a.allow_file_urls)
        if a.command == "add":
            return cmd_add(a, lock)
        if a.command == "update":
            return cmd_update(a, lock)
        owned = load_manifest()
        return {"plan": cmd_plan, "install": cmd_install, "verify": cmd_verify}[a.command](a, lock, owned)
    except (Fail, via_home.Refused, OSError) as e:
        print("via_skills: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
