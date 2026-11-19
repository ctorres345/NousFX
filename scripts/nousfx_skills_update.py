#!/usr/bin/env python3
"""nousfx_skills_update: report what changed upstream of the pinned outside skills (harness/skills.lock.json).

  check                 read-only. Per pinned skill: unchanged, changed, or why it could not be checked.
  bump NAME... [--commit SHA]
                        attended sessions only: read the diff first. Writes the new commit and sha256 of
                        the named skills to the lock.

Exit codes of check: 0 nothing changed, 1 something changed, 2 something could not be checked.
Fetching goes through plugins/via/scripts/via_skills.py (https only, pinned commits, trees read from git
objects, nothing run). NOUSFX_ALLOW_FILE_URLS=1 accepts file:// repos; it is test-only.
"""
import argparse
import difflib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "via", "scripts"))
import via_skills  # noqa: E402

LOCK = os.path.join("harness", "skills.lock.json")
ANDROID = "android CLI"  # has a space, so no lock entry (NAME_RE) can share its row
ANDROID_DL = "https://dl.google.com/android/cli"  # <base>/<version or "latest">/<os_arch>/android


def allow_file():
    return os.environ.get("NOUSFX_ALLOW_FILE_URLS") == "1"


URL_RE = re.compile(r"[a-z][a-z0-9+.-]{0,31}://[^\s<>\"'`)\]]+", re.IGNORECASE)  # the scheme is at most 32 characters, so a long run of letters stays linear; no lookbehind, which would hide -https://x
BANG_RE = re.compile(r"!`|```!")  # an inline !`command` or a fenced ```! block, anywhere on the line
FENCE_BANG_RE = re.compile(r"```!")
SENSITIVE = ("description", "allowed-tools", "disable-model-invocation")


def text_of(entry):
    """The file as text for the scans. A byte that is not UTF-8 becomes U+FFFD (as Claude Code reads it), so
    a bad byte cannot hide a command or a URL from them."""
    return entry[1].decode("utf-8", "replace")


def is_utf8(entry):
    try:
        entry[1].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def frontmatter(entry):
    """{key: value} of the top-level `key: value` lines of a SKILL.md front matter ({} when it has none)."""
    body = text_of(entry) if entry else None
    lines = body.splitlines() if body else []
    if not lines or lines[0].strip() != "---":
        return {}
    out, key = {}, None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        head, sep, value = line.partition(":")
        if sep and head and not head[0].isspace() and not head.startswith("-"):
            key = head.strip()
            out[key] = value.strip()
        elif key and line.strip():  # a list item or a block line under the key: part of its value
            out[key] += " | " + line.strip()
    return out


def urls(tree):
    found = set()
    for entry in tree.values():
        body = text_of(entry)
        if body:
            found.update(URL_RE.findall(body))
    return found


def diff_size(old, new):
    files, plus, minus = 0, 0, 0
    for path in set(old) | set(new):
        o, n = old.get(path), new.get(path)
        if o is not None and n is not None and o[1] == n[1]:
            continue
        files += 1
        if not all(is_utf8(x) for x in (o, n) if x is not None):
            continue  # binary: counted as a file, no line counts
        a, b = text_of(o) if o else "", text_of(n) if n else ""
        for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0):
            if line.startswith("+") and not line.startswith("+++"):
                plus += 1
            elif line.startswith("-") and not line.startswith("---"):
                minus += 1
    return files, plus, minus


def exec_lines(text):
    """The lines Claude Code would run: each line with an inline !`command`, and every line of a ```! fence."""
    out, in_fence = [], False
    for line in text.splitlines():
        if in_fence and line.strip().startswith("```"):
            in_fence = False
            continue
        if in_fence or BANG_RE.search(line):
            out.append(line)
        if FENCE_BANG_RE.search(line):
            in_fence = True
    return out


def flags(old, new):
    """The executable and behavior surface a change adds, one line each."""
    out = []
    for path in sorted(new):
        o, n = old.get(path), new[path]
        if o is not None and o[1] == n[1]:
            continue
        if path.lower().endswith(".md") and not via_skills.is_script(path, n[1]):
            if not is_utf8(n):
                out.append("Markdown file is not valid UTF-8: %s" % via_skills.escape(path))
            continue
        out.append("non-Markdown file %s: %s%s" % ("added" if o is None else "changed", via_skills.escape(path),
                                                   " [script]" if via_skills.is_script(path, n[1]) else ""))
    for path in sorted(p for p in new if p.rsplit("/", 1)[-1] == "SKILL.md"):
        before = set(exec_lines(text_of(old[path]) or "")) if path in old else set()
        for line in exec_lines(text_of(new[path]) or ""):
            if line not in before:
                out.append("! command line in %s: %s" % (path, via_skills.escape(line.strip())))
        was, now = frontmatter(old.get(path)), frontmatter(new[path])
        moves = [(k, "added" if k not in was else "removed" if k not in now else "changed")
                 for k in sorted(set(was) | set(now)) if was.get(k) != now.get(k)]
        moves.sort(key=lambda m: m[0] not in SENSITIVE)  # the keys that steer behavior first
        if moves:
            out.append("frontmatter changed: " + ", ".join("%s (%s)" % (via_skills.escape(k), how) for k, how in moves))
    out += ["new URL: " + via_skills.escape(u) for u in sorted(urls(new) - urls(old))]
    return out


def oneline(message, limit=200):
    """A remote or git message on one line, control characters escaped, cut at `limit` characters."""
    text = via_skills.escape(" ".join(str(message).split()))
    return text if len(text) <= limit else text[:limit - 3] + "..."


def open_history(repo, scratch):
    """Bare repo in `scratch` holding the commits and trees (no file contents) behind the upstream HEAD.
    Raises via_skills.Fail when the repo cannot be reached."""
    gd = os.path.join(scratch, "g")
    if via_skills.run_git(["init", "-q", "--bare", gd], scratch, allow_file()).returncode != 0:
        raise via_skills.Fail("git init failed")
    r = via_skills.run_git(["--git-dir", gd, "fetch", "-q", "--no-tags", "--filter=blob:none", repo, "HEAD"],
                           scratch, allow_file())
    if r.returncode != 0:
        raise via_skills.Fail("cannot fetch %s: %s" % (repo, via_skills.text(r)))
    r = via_skills.run_git(["--git-dir", gd, "rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}"], scratch, allow_file())
    head = r.stdout.decode().strip()
    if r.returncode != 0 or not via_skills.SHA1_RE.match(head):
        raise via_skills.Fail("cannot read HEAD of %s" % repo)
    return gd, head


def in_history(gd, scratch, commit, of="FETCH_HEAD"):
    """True when `commit` is part of the history that leads to `of` (default: the upstream HEAD)."""
    r = via_skills.run_git(["--git-dir", gd, "merge-base", "--is-ancestor", commit, of], scratch, allow_file())
    return r.returncode == 0


def has_folder(gd, scratch, path):
    r = via_skills.run_git(["--git-dir", gd, "cat-file", "-t", "FETCH_HEAD:" + path], scratch, allow_file())
    return r.returncode == 0 and r.stdout.decode().strip() == "tree"


def stage_pair(e, target):
    """(old tree, new tree, pin_ok): the pinned commit and `target`. pin_ok is False when the old files do not
    give the lock's sha256."""
    both = via_skills.stage_many([("old", e["repo"], e["path"], e["commit"]),
                                  ("new", e["repo"], e["path"], target)], allow_file())
    return both["old"], both["new"], via_skills.checksum(both["old"]) == e["sha256"]


def size_label(old, new):
    files, plus, minus = diff_size(old, new)
    return "%d file%s, +%d -%d lines" % (files, "" if files == 1 else "s", plus, minus)


def inspect_skill(e, gd, scratch, head):
    """(label, flag lines, kind) for one pinned skill. kind is ok, changed or error."""
    if head == e["commit"]:
        return "unchanged", [], "ok"
    if not in_history(gd, scratch, e["commit"]):
        return "pin gone: %s is no longer in the upstream history (force-push or rewrite)" % e["commit"][:12], [], "error"
    if not has_folder(gd, scratch, e["path"]):
        return "path removed: %s is not in the upstream HEAD" % via_skills.escape(e["path"]), [], "error"
    try:
        old, new, pin_ok = stage_pair(e, head)
    except (via_skills.Fail, OSError) as err:
        return "could not stage: %s" % oneline(err), [], "error"  # a fetch problem or a refused tree (symlink, odd name)
    if not pin_ok:
        return "checksum mismatch: the pinned commit no longer gives the lock's sha256", [], "error"
    if via_skills.checksum(old) == via_skills.checksum(new):
        return "unchanged (upstream HEAD %s, folder the same)" % head[:12], [], "ok"
    return "changed (%s) %s -> %s" % (size_label(old, new), e["commit"][:12], head[:12]), flags(old, new), "changed"


def android_target():
    system = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(platform.system().lower(), "linux")
    machine = platform.machine().lower()
    return "%s_%s" % (system, "arm64" if machine in ("arm64", "aarch64") else "x86_64")


def download_signature(url):
    """(etag, last-modified) of a published build, from a HEAD request: nothing is downloaded. curl does the
    request because it uses the system's certificate store (a python.org Python often has none); -q ignores ~/.curlrc."""
    try:
        r = subprocess.run(["curl", "-q", "-sSI", "-f", "--max-time", "30", "--url", url], capture_output=True, text=True)
    except OSError as err:
        raise via_skills.Fail("cannot run curl: %s" % err)
    if r.returncode != 0:
        raise via_skills.Fail("%s: %s" % (url, " ".join(r.stderr.split()) or "curl exit %d" % r.returncode))
    headers = {}
    for line in r.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    return headers.get("etag"), headers.get("last-modified")


def android_status():
    """(label, kind) for the android CLI. Google publishes no version number for `latest`, so the installed
    build's published file is compared with the latest one by ETag (or Last-Modified)."""
    binary = os.environ.get("NOUSFX_ANDROID_BIN") or shutil.which("android")
    if not binary or not os.path.isfile(binary) or not os.access(binary, os.X_OK):
        return "not installed", [], "ok"
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, errors="replace", timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as err:
        return "could not check: cannot run %s: %s" % (binary, oneline(err)), [], "error"
    found = re.search(r"\d+(?:\.\d+)+", out)
    if not found:
        return "could not check: no version in the output of `android --version`", [], "error"
    version = found.group(0)
    base = (os.environ.get("NOUSFX_ANDROID_DL_BASE") if allow_file() else None) or ANDROID_DL  # the override is test-only
    try:
        latest = download_signature("%s/latest/%s/android" % (base, android_target()))
        mine = download_signature("%s/%s/%s/android" % (base, version, android_target()))
    except (via_skills.Fail, OSError) as err:
        return "could not check: %s" % oneline(err), [], "error"
    if latest[0] and mine[0]:
        same = latest[0] == mine[0]
    elif latest[1] and mine[1]:
        same = latest[1] == mine[1]
    else:
        return "could not check: the download server sent no ETag or Last-Modified to compare", [], "error"
    if same:
        return "up to date (%s)" % version, [], "ok"
    return "newer build published (%s; installed %s)" % (oneline(latest[1] or latest[0] or "date unknown"), version), [], "changed"


def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return os.path.commonpath([path, folder]) == folder


def guarded(check, *args):
    """check(*args), with any failure turned into an error result, so one entry cannot stop the others."""
    try:
        return check(*args)
    except (via_skills.Fail, OSError, ValueError) as err:
        return "could not check: %s" % oneline(err), [], "error"


def stamp():
    return "checked: %s\n" % time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cmd_check(args, lock):
    if args.report:  # until this run finishes, last week's report must not stand: a kill or a hang leaves this one
        via_skills.via_home.write_atomic(args.report, "ERRORS: the check did not finish\n" + stamp())
    results = {}
    by_repo = {}
    for name, e in lock.items():
        by_repo.setdefault(e["repo"], []).append(name)
    for repo, names in sorted(by_repo.items()):
        with tempfile.TemporaryDirectory(prefix="nousfx-update-") as scratch:
            try:
                gd, head = open_history(repo, scratch)
            except (via_skills.Fail, OSError) as err:
                for name in names:
                    results[name] = ("unreachable: %s" % oneline(err), [], "error")
                continue
            for name in names:
                results[name] = guarded(inspect_skill, lock[name], gd, scratch, head)
    results[ANDROID] = guarded(android_status)
    errors = [n for n in sorted(results) if results[n][2] == "error"]
    changed = [n for n in sorted(results) if results[n][2] == "changed"]
    out = []
    if errors:
        out.append("ERRORS: %d could not be checked (%s)" % (len(errors), ", ".join(errors)))
    if changed:
        out.append("UPDATES: %d changed" % len(changed))
    elif not errors:
        out.append("NO UPDATES")
    for name in sorted(results, key=lambda n: (n == ANDROID, n)):  # the android CLI last
        label, notes, _kind = results[name]
        out.append("%s: %s" % (name, label))
        out += ["  flag: " + note for note in notes]
    text = "\n".join(out) + "\n"
    print(text, end="")
    if args.report:
        via_skills.via_home.write_atomic(args.report, text + stamp())
    return 2 if errors else 1 if changed else 0


def cmd_bump(args, doc, lock_path):
    """Pin the new commit of the named skills. All or nothing: the lock is written once, after every
    skill was fetched, checked against its pin and shown."""
    if not args.names:
        raise via_skills.Fail("bump needs at least one skill name")
    unknown = [n for n in args.names if n not in doc["skills"]]
    if unknown:
        raise via_skills.Fail("not in the lock: %s" % ", ".join(unknown))
    if args.commit and len(set(args.names)) != 1:
        raise via_skills.Fail("--commit names one commit: bump one skill at a time with it")
    if args.commit:
        via_skills.check_commit(args.commit)
    pins = {}
    for name in dict.fromkeys(args.names):
        e = doc["skills"][name]
        with tempfile.TemporaryDirectory(prefix="nousfx-update-") as scratch:
            gd, head = open_history(e["repo"], scratch)
            target = args.commit or head
            if target == e["commit"]:
                print("%s: already pinned at %s" % (name, target))
                continue
            if not in_history(gd, scratch, target):
                raise via_skills.Fail("%s: %s is not in the upstream HEAD history (another branch or a fork?); "
                                      "lock unchanged" % (name, target))
            pin_gone = not in_history(gd, scratch, e["commit"])
            older = in_history(gd, scratch, target, of=e["commit"])  # a bump back to before the pin
        old, new, pin_ok = stage_pair(e, target)
        if not pin_ok:
            raise via_skills.Fail("%s: the pinned commit no longer gives the lock's checksum; lock unchanged" % name)
        print("%s: %s -> %s (%s)" % (name, e["commit"][:12], target[:12], size_label(old, new)))
        if older:
            print("WARNING: %s is older than the pinned commit (a downgrade): it can undo an upstream fix" % target[:12])
        if pin_gone:
            print("WARNING: the pinned commit is no longer in the upstream history (force-push or rewrite): "
                  "read the whole diff, not only the flagged lines")
        for note in flags(old, new):
            print("  flag: " + note)
        print(via_skills.show_diff(old, new))
        pins[name] = dict(e, commit=target, sha256=via_skills.checksum(new))
    if pins:
        doc["skills"].update(pins)
        via_skills.write_lock(lock_path, doc)
        print("pinned %s in %s; read the diff above, then run the sync to install it" % (", ".join(sorted(pins)), lock_path))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="nousfx_skills_update", description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=ROOT, help="the NousFX repo whose lock is read (default: this one)")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--report", type=os.path.abspath, metavar="FILE",
                   help="also write the output, with a timestamp, to FILE (outside the repo; its folder is made)")
    p = sub.add_parser("bump")
    p.add_argument("names", nargs="*", metavar="NAME")
    p.add_argument("--commit", help="pin this commit (one skill only) instead of the upstream HEAD")
    p.set_defaults(report=None)
    a = ap.parse_args(argv)
    lock_path = os.path.join(a.repo, LOCK)
    report_ok = False
    try:
        if a.report and inside(a.report, a.repo):
            raise via_skills.Fail("--report %s is inside the repo; reports live outside it" % a.report)
        report_ok = bool(a.report)
        doc = via_skills.load_lock(lock_path, allow_file())
        if a.command == "bump":
            return cmd_bump(a, doc, lock_path)
        return cmd_check(a, doc["skills"])
    except Exception as err:  # a bug too: last week's report must not outlive a check that did not run
        if report_ok:
            try:
                via_skills.via_home.write_atomic(a.report, "ERRORS: the check failed: %s\n%s" % (oneline(err, 400), stamp()))
            except OSError:
                pass
        if not isinstance(err, (via_skills.Fail, via_skills.via_home.Refused, OSError)):
            traceback.print_exc()  # exit 2, not Python's 1: 1 means "something changed"
        else:
            print("nousfx_skills_update: %s" % oneline(err, 400), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
