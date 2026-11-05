#!/usr/bin/env python3
"""Pick the review tier (0 tiny, 1 small, 2 normal, 3 risky or large) for the current branch (NFX-62).

Python only, standard library only; runs the same on macOS and Windows.

    python3 -B tier.py [--base HASH] [--labels fable-review,size-L] [--cwd DIR]

Prints one JSON object: {"tier", "reasons", "ui", "stats"}. Exits 2 (nothing on stdout) when the branch has no
merge base with the default branch; any other error gives Tier 2 with the reason, never a lower tier.
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:  # Python < 3.11: a project.toml that exists cannot be read, which is an error (Tier 2)
    tomllib = None

DOC_EXTENSIONS = (".md", ".txt", ".rst", ".adoc")
IGNORED_NAMES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock", "Gemfile.lock",
                 "Podfile.lock", "Pipfile.lock", "composer.lock", "go.sum", "gradle.lockfile", "pubspec.lock",
                 "Package.resolved", "uv.lock")
IGNORED_SUFFIXES = (".lock", ".snap", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp4", ".mov", ".g.dart",
                    ".freezed.dart", ".pb.go", ".min.js", ".min.css")
IGNORED_PARTS = ("__snapshots__", "/generated/", "/build/", "/screenshots/", "/snapshots/")

# Risk paths. A directory entry matches any file below a folder with that path; a name entry is a glob on the
# file name. Both apply to every changed file, including lock files and docs.
RISK_DIRS = (".github", ".githooks", "githooks", ".husky", "migrations")
RISK_NAMES = ("review-gate.py", "pr_create_parser.py", "*.gradle", "*.gradle.kts", "gradle.properties",
              "libs.versions.toml", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
              "pubspec.yaml", "pubspec.lock", "Podfile*", "Package.swift", "Package.resolved", "pyproject.toml",
              "poetry.lock", "uv.lock", "requirements*.txt", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
              "Gemfile", "Gemfile.lock", "AndroidManifest.xml", "*.sql", "schema.*")
# Whole-word keywords, matched on path parts (code files) and on added lines of code files.
RISK_KEYWORDS = frozenset("""auth oauth authorization authorize authentication authenticate login logout password
passwd credential credentials token jwt crypto cryptography encrypt encryption decrypt keystore keychain secret
secrets payment payments billing migration migrations schema mutex semaphore atomic synchronized""".split())
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
MAX_KEYWORD_REASONS = 10

UI_EXTENSIONS = (".tsx", ".jsx", ".vue", ".svelte", ".astro", ".css", ".scss", ".sass", ".less", ".html", ".htm",
                 ".xib", ".storyboard")
UI_FOLDERS = ("ui", "screens", "screen", "theme", "compose", "composables", "widgets", "views", "pages")
UI_NATIVE_EXTENSIONS = (".kt", ".java", ".dart", ".swift")
UI_NAME_ENDINGS = ("Screen", "View", "Theme", "Activity", "Fragment", "Widget", "Page")
UI_RES = re.compile(r"/res/(layout|drawable|values|font|mipmap|anim|menu|navigation)[^/]*/[^/]+\.xml$")

TINY_LINES, TINY_FILES, TINY_HUNKS = 20, 2, 3
SMALL_LINES, SMALL_FILES, SMALL_HUNKS = 150, 5, 10
BIG_LINES, BIG_FILES = 400, 15


def norm(path):
    return path.replace("\\", "/")


def is_ignored(path):
    p = "/" + norm(path)
    name = p.rsplit("/", 1)[-1]
    return name in IGNORED_NAMES or name.endswith(IGNORED_SUFFIXES) or any(part in p for part in IGNORED_PARTS)


def is_doc(path):
    p = norm(path)
    return p.lower().endswith(DOC_EXTENSIONS) or p.startswith("docs/")


def words(text):
    return [w.lower() for w in WORD.findall(text)]


def risk_path_hit(path, project_paths):
    """Why this path is a risk path, or None. Folder and name lists, then the project's own globs."""
    p = norm(path)
    name = p.rsplit("/", 1)[-1]
    parts = ("/" + p).split("/")[:-1]
    if any(d in parts for d in RISK_DIRS) or any(fnmatch.fnmatchcase(name, g) for g in RISK_NAMES):
        return True
    return any(fnmatch.fnmatchcase(p, g) or fnmatch.fnmatchcase(name, g) for g in project_paths)


def path_keyword(path):
    """A risk keyword used as a whole word in a folder or file name, or None."""
    for w in words(norm(path)):
        if w in RISK_KEYWORDS:
            return w
    return None


def line_keywords(lines):
    found = []
    for line in lines:
        for w in words(line):
            if w in RISK_KEYWORDS and w not in found:
                found.append(w)
    return found


def is_ui(path):
    p = "/" + norm(path)
    name = p.rsplit("/", 1)[-1]
    stem, dot, ext = name.rpartition(".")
    ext = dot + ext.lower()
    if ext in UI_EXTENSIONS or ".xcassets/" in p or UI_RES.search(p):
        return True
    if ext in UI_NATIVE_EXTENSIONS:
        folders = p.lower().split("/")[:-1]
        return any(x in UI_FOLDERS for x in folders) or stem.endswith(UI_NAME_ENDINGS)
    return False


def classify(files, labels=(), project_paths=()):
    """files: [{"path", "added": [line, ...], "removed": int, "hunks": int}]. Returns {tier, reasons, ui, stats}."""
    labels = {str(x).strip().lower() for x in labels}
    counted = [x for x in files if not is_ignored(x["path"])]
    code = [x for x in counted if not is_doc(x["path"])]
    lines = sum(len(x["added"]) + x["removed"] for x in code)
    hunks = sum(x["hunks"] for x in code)
    stats = {"lines": lines, "files": len(code), "hunks": hunks}

    why3 = []  # every reason for Tier 3
    for x in files:
        if risk_path_hit(x["path"], project_paths):
            why3.append("Tier 3: touches " + x["path"])
    keyword_reasons = []
    for x in code:
        hit = path_keyword(x["path"])
        if hit:
            keyword_reasons.append('Tier 3: keyword "%s" in the path %s' % (hit, x["path"]))
        for w in line_keywords(x["added"]):
            keyword_reasons.append('Tier 3: keyword "%s" in %s' % (w, x["path"]))
    if len(keyword_reasons) > MAX_KEYWORD_REASONS:
        extra = len(keyword_reasons) - MAX_KEYWORD_REASONS
        keyword_reasons = keyword_reasons[:MAX_KEYWORD_REASONS] + ["Tier 3: and %d more keyword hit(s)" % extra]
    why3 += keyword_reasons
    if "fable-review" in labels:
        why3.append("Tier 3: label fable-review")
    if lines > BIG_LINES:
        why3.append("Tier 3: %d changed lines, over %d" % (lines, BIG_LINES))
    if len(code) > BIG_FILES:
        why3.append("Tier 3: %d files, over %d" % (len(code), BIG_FILES))

    if why3:
        tier, reasons = 3, why3
    elif not code:
        tier, reasons = 0, ["Tier 0: docs or text only"]
    elif lines <= TINY_LINES and len(code) <= TINY_FILES and hunks <= TINY_HUNKS:
        tier, reasons = 0, ["Tier 0: %d code lines in %d file(s)" % (lines, len(code))]
    elif lines <= SMALL_LINES and len(code) <= SMALL_FILES and hunks <= SMALL_HUNKS:
        tier, reasons = 1, ["Tier 1: %d lines, %d file(s), %d hunk(s)" % (lines, len(code), hunks)]
    else:
        tier, reasons = 2, ["Tier 2: %d lines, %d file(s), %d hunk(s)" % (lines, len(code), hunks)]

    if "size-l" in labels and tier < 3:
        tier += 1
        reasons.append("+1 tier: label size-L")
    ui_files = [x["path"] for x in counted if not is_doc(x["path"]) and is_ui(x["path"])]
    if ui_files and tier < 1:
        tier = 1
        reasons.append("Tier 1: UI change (%s)" % ui_files[0])
    return {"tier": tier, "reasons": reasons, "ui": bool(ui_files), "stats": stats}


# ---- git layer -------------------------------------------------------------------------------------------------
# The default-branch rule below is a copy of default_ref / exists_at / the `git show <ref>:<file>` read in
# claude/hooks/review-gate.py (a skill cannot import the gate). Keep them in step.

PROJECT_FILE = ".nousfx/project.toml"
GIT_TIMEOUT = 60
MAX_UNTRACKED_BYTES = 2_000_000
NO_BASE_HINT = (
    "Stopped: no merge base with origin/HEAD or origin/main, so the branch diff cannot be taken (a bare "
    "`git diff` would review only part of the branch). Run `git remote set-head origin -a` or "
    "`git fetch --unshallow`, then retry. Record nothing.")


class NoMergeBase(Exception):
    pass


def find_git(path=None):
    """The git program from an absolute PATH entry. Never the current folder (on Windows a bare name is searched
    there first, and the folder is the repo under review). None when not found."""
    windows = os.name == "nt"
    names = ("git.exe", "git.com") if windows else ("git",)
    for entry in (os.environ.get("PATH", "") if path is None else path).split(os.pathsep):
        if not entry or not os.path.isabs(entry):
            continue
        for name in names:
            candidate = os.path.join(entry, name)
            if os.path.isfile(candidate) and (windows or os.access(candidate, os.X_OK)):
                return candidate
    return None


def git(args, cwd, raw=False, env=None):
    program = find_git()
    if not program:
        raise RuntimeError("git was not found on PATH")
    r = subprocess.run([program, "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT,
                       env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
    return r.stdout if raw else r.stdout.decode("utf-8", "replace").strip()


def repo_top(cwd):
    return git(["rev-parse", "--show-toplevel"], cwd)


def merge_base(top):
    """The hash review-loop uses: `git merge-base origin/HEAD HEAD || git merge-base origin/main HEAD`."""
    for ref in ("origin/HEAD", "origin/main"):
        try:
            return git(["merge-base", ref, "HEAD"], top)
        except RuntimeError:
            continue
    raise NoMergeBase()


def default_ref(top):
    """origin/HEAD (only when it points at another refs/remotes/origin/ ref), else origin/main, else origin/master."""
    candidates = []
    try:
        target = git(["symbolic-ref", "-q", "refs/remotes/origin/HEAD"], top)
        if target.startswith("refs/remotes/origin/") and target != "refs/remotes/origin/HEAD":
            candidates.append(target)
    except RuntimeError:
        pass
    candidates += ["refs/remotes/origin/main", "refs/remotes/origin/master"]
    for ref in candidates:
        try:
            git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], top)
            return ref
        except RuntimeError:
            continue
    return None


def exists_at(top, ref, path):
    """True when `path` exists in the commit `ref`. Any other git failure raises, so it cannot read as 'absent'."""
    try:
        git(["cat-file", "-e", "%s:%s" % (ref, path)], top, env={**os.environ, "LC_ALL": "C"})
        return True
    except RuntimeError as e:
        if "does not exist" in str(e) or "exists on disk, but not in" in str(e) or "Not a valid object name" in str(e):
            return False
        raise


def load_project_risk_paths(cwd):
    """`[review] risk_paths` from .nousfx/project.toml. The default branch's file wins whenever it exists (even
    without a [review] table); only when it has no such file, or there is no default branch, does the working-tree
    copy count. Raises on an unreadable file or a bad value."""
    top = repo_top(cwd)
    ref = default_ref(top)
    if ref and exists_at(top, ref, PROJECT_FILE):
        text = git(["show", "%s:%s" % (ref, PROJECT_FILE)], top)
    else:
        try:
            with open(os.path.join(top, *PROJECT_FILE.split("/")), encoding="utf-8") as fh:
                text = fh.read()
        except FileNotFoundError:
            return []
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is needed to read " + PROJECT_FILE)
    table = tomllib.loads(text).get("review")
    if table is None:
        return []
    paths = table.get("risk_paths", []) if isinstance(table, dict) else None
    if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
        raise ValueError("[review] risk_paths in %s must be a list of strings" % PROJECT_FILE)
    return paths


def parse_diff(text):
    """Files of a `git diff -U0 --no-renames` text as classify() wants them."""
    files, current, in_hunk = [], None, False
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            rest = line[len("diff --git a/"):]
            path = rest[:(len(rest) - 3) // 2] if len(rest) % 2 == 1 else rest.split(" b/")[0]
            current = {"path": path.strip('"'), "added": [], "removed": 0, "hunks": 0}
            files.append(current)
            in_hunk = False
        elif current is None:
            continue
        elif line.startswith("@@"):
            current["hunks"] += 1
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            current["added"].append(line[1:])
        elif in_hunk and line.startswith("-"):
            current["removed"] += 1
    return files


def untracked_files(top):
    out = git(["ls-files", "--others", "--exclude-standard", "-z"], top, raw=True).decode("utf-8", "replace")
    files = []
    for rel in filter(None, out.split("\0")):
        full = os.path.join(top, *rel.split("/"))
        try:
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            with open(full, "rb") as fh:
                data = fh.read(MAX_UNTRACKED_BYTES)
        except OSError:
            continue
        added = [] if b"\0" in data[:8000] else data.decode("utf-8", "replace").splitlines()
        files.append({"path": rel, "added": added, "removed": 0, "hunks": 1 if added else 0})
    return files


def collect(top, base):
    if base.startswith("-"):
        raise RuntimeError("not a commit: " + base)
    text = git(["diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames", base, "--"], top, raw=True)
    return parse_diff(text.decode("utf-8", "replace")) + untracked_files(top)


def raise_for_labels(tier, reasons, labels):
    """A tier never goes down: labels can still raise it when the diff could not be read."""
    labels = {str(x).strip().lower() for x in labels}
    if "fable-review" in labels:
        tier = 3
        reasons.append("Tier 3: label fable-review")
    elif "size-l" in labels and tier < 3:
        tier += 1
        reasons.append("+1 tier: label size-L")
    return tier, reasons


def run(cwd, base, labels):
    """The result dict for the repo at cwd. Raises NoMergeBase when there is no merge base."""
    try:
        top = repo_top(cwd)
    except Exception as e:
        tier, reasons = raise_for_labels(2, ["Tier 2: could not find the repo (%s)" % e], labels)
        return {"tier": tier, "reasons": reasons, "ui": False, "stats": {}}
    base = base or merge_base(top)
    try:
        files = collect(top, base)
    except Exception as e:
        tier, reasons = raise_for_labels(2, ["Tier 2: could not read the diff (%s)" % e], labels)
        return {"tier": tier, "reasons": reasons, "ui": False, "stats": {"base": base}}
    project_error = None
    try:
        project_paths = load_project_risk_paths(top)
    except Exception as e:
        project_paths, project_error = [], e
    result = classify(files, labels=labels, project_paths=project_paths)
    result["stats"]["base"] = base
    if project_error is not None:
        result["tier"] = max(result["tier"], 2)
        result["reasons"].insert(0, "Tier 2: could not read [review] risk_paths (%s)" % project_error)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pick the review tier for the current branch.")
    ap.add_argument("--base", help="merge base hash (default: merge base with origin/HEAD, else origin/main)")
    ap.add_argument("--labels", default="", help="comma-separated Jira labels, for example fable-review,size-L")
    ap.add_argument("--cwd", default=".", help="folder inside the repo (default: current folder)")
    args = ap.parse_args(argv)
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    try:
        result = run(os.path.abspath(args.cwd), args.base, labels)
    except NoMergeBase:
        print(NO_BASE_HINT, file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
