#!/usr/bin/env python3
"""Check a project's Claude Code memory notes and its handoff against the repo (NFX-79). Report only.

    python3 -I check_memory.py PROJECT [--json] [--home DIR]
    python3 -I check_memory.py --all [--json] [--home DIR]

Reads only <home>/.claude/projects/<slug>/memory/ (never the transcripts beside it) and the project's
.claude/handoff.md or root HANDOFF.md. Repo facts come from file existence and read-only git commands.
It writes nothing. Exit 0 when there are no findings, 1 when there are, 2 on a usage error, 3 when it could not run.
The --json format is documented in ../SKILL.md ("JSON output").
"""
import argparse
import functools
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True  # -I does not imply -B; importing the repo scripts must not write .pyc files
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "..", "scripts"))  # the repo's scripts/ (skills are links)
try:
    from real_machine_regression import sentences  # noqa: E402  (also loads nousfx_sync: Python 3.11+)
except Exception as e:  # exit 1 means findings, so a failed import is 3
    print(f"check_memory: cannot import the repo's scripts/real_machine_regression.py ({e}); "
          "run from the NousFX skill link with Python 3.11+", file=sys.stderr)
    sys.exit(3)

INDEX = "MEMORY.md"
INDEX_LINES = 200   # Claude Code loads only the first 200 lines of MEMORY.md
DUP_WORDS = 6
HANDOFFS = (".claude/handoff.md", "HANDOFF.md")  # reported with '/' on every OS
HEADER = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[,·]\s*`?([^\s,`()]+)`?\s*[,·]\s*`?([0-9a-f]{7,40})\b`?")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SHA = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
HEAD_BRANCH = re.compile(r"(?:\bbranch\s+`?|\bon\s+`)(?!\d{4}-\d{2}-\d{2})([\w./-]+)`?|`?([\w./-]+)`?\s*@\s*[0-9a-f]{7}")
PLACEHOLDER = re.compile(r"\.\.\.|x\.y\.z|-[nN]$|[<>{}*]|://")
LINK = re.compile(r"\]\(<?([^)>#\s]+\.md)(?:#[^)>]*)?>?\)")
CODE = re.compile(r"`([^`\n]+)`")
BRANCH = re.compile(r"\bbranch(?:es)?\s+`([^`\s]+)`", re.I)
EXT = re.compile(r"\.(py|md|json|toml|ya?ml|sh|js|ts|tsx|kt|kts|swift|dart|gradle|txt|cfg|ini|lock)$")
SYMBOL = re.compile(r"^([A-Za-z_][\w.]*)\(\)$")


def slug(path):
    """Claude Code's project folder name: every character that is not a letter or digit becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(path))


def unslug(name):
    """Every folder whose slug is name: at each level, the children whose own slug starts the rest."""
    def walk(base, rest):
        if not rest:
            yield base
            return
        try:
            children = sorted(os.listdir(base))
        except OSError:
            return
        for child in children:
            part = re.sub(r"[^A-Za-z0-9]", "-", child)
            path = os.path.join(base, child)
            if (rest == part or rest.startswith(part + "-")) and os.path.isdir(path):
                yield from walk(path, rest[len(part) + 1:])

    drive = re.match(r"([A-Za-z])--", name)  # Windows: C:\\Users\\x becomes C--Users-x
    if drive:
        return list(walk(drive.group(1) + ":\\", name[3:]))
    return list(walk(os.sep, name[1:])) if name.startswith("-") else []


def resolve(name):
    """(project folder or None, evidence of an ambiguous slug or None). A git main checkout wins a tie."""
    found = unslug(name)
    if len(found) > 1:
        repos = [p for p in found if checkouts(p) == (p, p)]
        found = repos if repos else found
    if len(found) > 1:
        return None, "the slug fits several folders: " + ", ".join(found)
    return (found[0] if found else None), None


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def body(text):
    """A note without its YAML frontmatter."""
    m = re.match(r"---\n.*?\n---\n?", text, re.S)
    return text[m.end():] if m else text


def git(repo, *args):
    # no inherited GIT_DIR and friends: run from a git hook they would point at another repo
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout.strip()


class Repo:
    def __init__(self, path):
        self.path = path
        self.is_git = bool(path) and git(path, "rev-parse", "--is-inside-work-tree")[1] == "true"

    @functools.cached_property
    def known(self):
        """Every run of path parts in a tracked file (`scripts/kept.py` gives `scripts`, `kept.py` and itself)."""
        out = set()
        for f in (git(self.path, "ls-files", "-z")[1].split("\0") if self.is_git else []):
            parts = [x for x in f.split("/") if x]
            out.update("/".join(parts[i:j]) for i in range(len(parts)) for j in range(i + 1, len(parts) + 1))
        return out

    def has_path(self, token):
        token = token.rstrip("/")
        return token in self.known or os.path.exists(os.path.join(self.path, token))

    @functools.lru_cache(maxsize=None)
    def has_branch(self, name):
        if name.endswith("/"):  # a branch prefix such as `ana/`
            return bool(git(self.path, "for-each-ref", "--count=1", f"refs/heads/{name}", f"refs/remotes/origin/{name}")[1])
        return any(git(self.path, "rev-parse", "--verify", "--quiet", ref)[0] == 0
                   for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}", f"refs/remotes/{name}"))

    @functools.lru_cache(maxsize=None)
    def ignored(self, token):
        return any(git(self.path, "check-ignore", "-q", "--no-index", "--", t)[0] == 0 for t in {token, token.rstrip("/")})

    @functools.lru_cache(maxsize=None)
    def has_symbol(self, name):
        return git(self.path, "grep", "-q", "-w", "-F", "--", name)[0] == 0


def finding(kind, file, evidence):
    return {"kind": kind, "file": file, "evidence": evidence}


def check_index(mem, notes):
    out = []
    index_path = os.path.join(mem, INDEX)
    if os.path.islink(index_path):
        return [finding("note-symlink", INDEX, f"{INDEX} is a link; not read")]
    if not os.path.isfile(index_path):
        return [finding("index-missing", INDEX, "memory folder has notes but no MEMORY.md")] if notes else []
    lines = read(index_path).splitlines()
    linked = set()
    for n, line in enumerate(lines, 1):
        for target in (t for t in LINK.findall(line) if "://" not in t and not t.startswith("/")):
            linked.add(os.path.normcase(os.path.normpath(target)))
            if not os.path.isfile(os.path.join(mem, target)):
                out.append(finding("index-dangling", INDEX, f"line {n} links {target}, which does not exist"))
    for note in notes:
        if os.path.normcase(note) not in linked:
            out.append(finding("index-unlisted", note, f"{note} is not linked from {INDEX}"))
    if len(lines) > INDEX_LINES:
        out.append(finding("index-too-long", INDEX,
                           f"{len(lines)} lines; Claude Code loads only the first {INDEX_LINES}"))
    return out


def refs(text):
    """(kind, token) pairs a note mentions: branches, symbols and repo paths, all from `code` spans."""
    branches = set(BRANCH.findall(text))
    for token in CODE.findall(text):
        token = re.sub(r"(#.*|(:\d+)+(-\d+)?)$", "", token.strip())  # drop #anchor and :line[:col][-end]
        if token in branches or " " in token or PLACEHOLDER.search(token) or token.startswith(("/", "~", "-")):
            continue
        sym = SYMBOL.match(token)
        if sym:
            yield "symbol", sym.group(1).rsplit(".", 1)[-1]
        elif "/" in token or EXT.search(token):
            yield "path", token
    for b in sorted(branches):
        if not PLACEHOLDER.search(b) and not b.startswith(("/", "~", "-")):
            yield "branch", b


def check_refs(repo, notes, texts):
    out = []
    if not repo.is_git:
        return out
    for note in notes:
        seen = set()
        for kind, token in refs(texts[note]):
            if (kind, token) in seen:
                continue
            seen.add((kind, token))
            if kind == "path" and not repo.has_path(token) and not repo.has_branch(token):
                if repo.ignored(token):
                    continue  # build output and other gitignored paths come and go
                looks_like_ref = "/" in token.rstrip("/") and not re.search(r"\.\w{1,5}$", token)
                if looks_like_ref and not repo.has_path(token.split("/")[0]):
                    out.append(finding("stale-branch", note, f"`{token}` is neither a repo path nor a branch"))
                else:
                    out.append(finding("stale-path", note, f"`{token}` is not in the repo"))
            elif kind == "branch" and not repo.has_branch(token):
                out.append(finding("stale-branch", note, f"branch `{token}` is not a local or origin branch"))
            elif kind == "symbol" and not repo.has_symbol(token):
                out.append(finding("stale-symbol", note, f"`{token}` is not found by git grep"))
    return out


def check_duplicates(notes, texts):
    out = []
    sets = {n: sentences(texts[n], DUP_WORDS) for n in notes}
    for i, a in enumerate(notes):
        for b in notes[i + 1:]:
            shared = sorted(sets[a] & sets[b])
            if shared:
                out.append(finding("duplicate", a, f"shares {len(shared)} sentence(s) with {b}: \"{shared[0][:100]}\""))
    return out


def parse_header(text):
    """(date, branch, sha) from a handoff header, as the handoff skill writes it or in common variants."""
    m = HEADER.search(text)
    if m:
        return m.groups()
    for line in text.splitlines():  # variants: all three on one line
        date, sha, branch = DATE.search(line), SHA.search(line), HEAD_BRANCH.search(line)
        if date and sha and branch:
            return date.group(0), branch.group(1) or branch.group(2), sha.group(0)
    return None


def check_handoff(repo):
    for rel in HANDOFFS:
        path = os.path.join(repo.path, *rel.split("/"))
        if os.path.isfile(path):
            break
    else:
        return None, []
    if os.path.islink(path):
        return rel, [finding("handoff-symlink", rel, f"{rel} is a link; not read")]
    if not repo.is_git:
        return rel, []
    parsed = parse_header("\n".join(read(path).splitlines()[:5]))
    if not parsed:
        return rel, [finding("handoff-header", rel, "no \"date, branch, short sha\" in the first lines")]
    date, branch, sha = parsed
    out = []
    current = git(repo.path, "branch", "--show-current")[1] or "(detached HEAD)"
    if branch != current:
        out.append(finding("handoff-branch", rel, f"header ({date}) says branch {branch}; checkout is on {current}"))
    code, full = git(repo.path, "rev-parse", "--verify", "--quiet", sha + "^{commit}")
    head_sha = git(repo.path, "rev-parse", "HEAD")[1]
    if code != 0:
        out.append(finding("handoff-sha", rel, f"header ({date}) says {sha}, which is not a commit here; HEAD is {head_sha[:7]}"))
    elif full != head_sha:
        if git(repo.path, "merge-base", "--is-ancestor", full, "HEAD")[0] == 0:
            later = git(repo.path, "rev-list", "--count", f"{full}..HEAD")[1]
            where = f"{later} commit(s) later"
        else:
            where = "and the header's commit is not an ancestor of HEAD"
        out.append(finding("handoff-sha", rel, f"header ({date}) says {sha}; HEAD is {head_sha[:7]}, {where}"))
    return rel, out


def check_project(mem, project, ambiguous=None):
    """Findings for one memory folder. project is the repo path, or None when it cannot be found."""
    has_mem = os.path.isdir(mem)
    entries = sorted(f for f in os.listdir(mem) if f.endswith(".md") and f != INDEX) if has_mem else []
    links = [f for f in entries if os.path.islink(os.path.join(mem, f))]  # may point outside memory/: never read
    notes = [f for f in entries if f not in links and os.path.isfile(os.path.join(mem, f))]
    texts = {n: body(read(os.path.join(mem, n))) for n in notes}
    found = bool(project) and os.path.isdir(project)
    repo = Repo(project if found else None)
    findings = check_index(mem, notes) + check_refs(repo, notes, texts) + check_duplicates(notes, texts)
    handoff, more = check_handoff(repo) if found else (None, [])
    findings += more + [finding("note-symlink", f, f"{f} is a link; not read") for f in links]
    if found and not repo.is_git:
        findings.append(finding("project-not-git", "", f"{project} is not a git work tree (or git refused it); "
                                "path, branch and handoff checks skipped"))
    if not found:
        findings.append(finding("project-missing", "", (ambiguous or "the project folder for this memory was not found")
                                + "; path, branch and handoff checks skipped"))
    findings.sort(key=lambda f: (f["kind"], f["file"], f["evidence"]))
    return {"project": project, "memory": mem if has_mem else None, "handoff": handoff,
            "findings": findings}


def run(home, project=None, all_projects=False):
    root = os.path.join(home, ".claude", "projects")
    if all_projects:
        names = sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n, "memory"))) \
            if os.path.isdir(root) else []
        results = []
        for n in names:
            project, ambiguous = resolve(n)
            results.append(check_project(os.path.join(root, n, "memory"), project and checkouts(project)[0], ambiguous))
        return results
    top, main = checkouts(project)
    result = check_project(os.path.join(root, slug(main), "memory"), top)
    if result["memory"] is None:
        result["findings"].insert(0, finding("memory-missing", "", f"no memory folder for {main} "
                                             f"(looked in {os.path.join(root, slug(main))})"))
    return [result]


def checkouts(project):
    """(this checkout's top level, the main checkout). Claude Code keys memory on the main checkout, so
    every worktree and subfolder of a repo shares one memory folder."""
    project = os.path.realpath(project)
    code, top = git(project, "rev-parse", "--show-toplevel")
    if code != 0:
        return project, project
    code, common = git(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    main = os.path.dirname(common) if code == 0 and os.path.basename(common) == ".git" else top
    return os.path.realpath(top), os.path.realpath(main)


def text_report(results):
    lines = []
    for r in results:
        lines.append(f"{r['project'] or '(unknown project)'}  memory: {r['memory'] or 'none'}  "
                     f"handoff: {r['handoff'] or 'none'}")
        for f in r["findings"]:
            lines.append(f"  {f['kind']}: {f['file'] or '-'}: {f['evidence']}")
        if not r["findings"]:
            lines.append("  no findings")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project", nargs="?", help="project folder (default: none; use --all for every project)")
    ap.add_argument("--all", action="store_true", help="every project with a memory folder")
    ap.add_argument("--json", action="store_true", help="print JSON (see SKILL.md)")
    ap.add_argument("--home", default=os.path.expanduser("~"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if bool(args.project) == args.all:
        ap.error("give a project folder or --all")
    try:
        results = run(args.home, args.project, args.all)
    except Exception as e:  # exit 1 means findings, so any failure to run is 3
        print(f"check_memory: {e}", file=sys.stderr)
        return 3
    print(json.dumps({"version": 1, "projects": results}, indent=2, sort_keys=True) if args.json
          else text_report(results))
    return 1 if any(r["findings"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
