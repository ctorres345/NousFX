#!/usr/bin/env python3
"""Check a project's Claude Code memory notes and its handoff against the repo (NFX-79). Report only.

    python3 -I check_memory.py PROJECT [--json] [--home DIR]
    python3 -I check_memory.py --all [--json] [--home DIR]

Reads only <home>/.claude/projects/<slug>/memory/ (never the transcripts beside it) and the project's
.claude/handoff.md or root HANDOFF.md. Repo facts come from file existence and read-only git commands.
It writes nothing. Exit 0 when there are no findings, 1 when there are, 2 on a usage error.
The --json format is documented in ../SKILL.md ("JSON output").
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.dont_write_bytecode = True  # -I does not imply -B; importing the repo scripts must not write .pyc files
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "..", "scripts"))  # the repo's scripts/ (skills are links)
from real_machine_regression import sentences  # noqa: E402

INDEX = "MEMORY.md"
INDEX_LINES = 200   # Claude Code loads only the first 200 lines of MEMORY.md
DUP_WORDS = 6
HANDOFFS = (os.path.join(".claude", "handoff.md"), "HANDOFF.md")
HEADER = re.compile(r"(\d{4}-\d{2}-\d{2})\s*[,·]\s*`?([^\s,`()]+)`?\s*[,·]\s*`?([0-9a-f]{7,40})\b`?")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SHA = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
HEAD_BRANCH = re.compile(r"(?:\bbranch|\bon)\s+`?([\w./-]+)`?|`?([\w./-]+)`?\s*@\s*[0-9a-f]{7}")
PLACEHOLDER = re.compile(r"\.\.\.|x\.y\.z|-[nN]$|[<>{}*]|://")
LINK = re.compile(r"\]\(([^)#\s]+\.md)\)")
CODE = re.compile(r"`([^`\n]+)`")
BRANCH = re.compile(r"\bbranch(?:es)?\s+`([^`\s]+)`", re.I)
EXT = re.compile(r"\.(py|md|json|toml|ya?ml|sh|js|ts|tsx|kt|kts|swift|dart|gradle|txt|cfg|ini|lock)$")
SYMBOL = re.compile(r"^([A-Za-z_][\w.]*)\(\)$")


def slug(path):
    """Claude Code's project folder name: every character that is not a letter or digit becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def unslug(name):
    """Best guess at the folder a slug came from, by trying '/', '-', '.' and '_' at each dash (None if none exists)."""
    parts = name.lstrip("-").split("-")

    def walk(base, i):
        if i == len(parts):
            return base
        seg = ""
        for j in range(i, len(parts)):
            seg = parts[j] if j == i else seg + "-" + parts[j]
            for cand in {seg, seg.replace("-", "."), seg.replace("-", "_"), seg.replace("-", " ")}:
                path = os.path.join(base, cand)
                if os.path.isdir(path):
                    found = walk(path, j + 1)
                    if found:
                        return found
        return None

    return walk(os.sep, 0) if parts and parts[0] else None


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def body(text):
    """A note without its YAML frontmatter."""
    m = re.match(r"---\n.*?\n---\n?", text, re.S)
    return text[m.end():] if m else text


def git(repo, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout.strip()


class Repo:
    def __init__(self, path):
        self.path = path
        self.is_git = bool(path) and git(path, "rev-parse", "--is-inside-work-tree")[1] == "true"
        self._files = None

    def files(self):
        if self._files is None:
            self._files = git(self.path, "ls-files")[1].splitlines() if self.is_git else []
        return self._files

    def has_path(self, token):
        token = token.rstrip("/")
        if os.path.exists(os.path.join(self.path, token)):
            return True
        return any(f == token or f.endswith("/" + token) or f.startswith(token + "/") for f in self.files())

    def has_branch(self, name):
        if name.endswith("/"):  # a branch prefix such as `cesar/`
            return bool(git(self.path, "for-each-ref", "--count=1", f"refs/heads/{name}", f"refs/remotes/origin/{name}")[1])
        return any(git(self.path, "rev-parse", "--verify", "--quiet", ref)[0] == 0
                   for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"))

    def ignored(self, token):
        return any(git(self.path, "check-ignore", "-q", "--no-index", t)[0] == 0 for t in {token, token.rstrip("/")})

    def has_symbol(self, name):
        return git(self.path, "grep", "-q", "-w", "-F", name)[0] == 0


def finding(kind, file, evidence):
    return {"kind": kind, "file": file, "evidence": evidence}


def check_index(mem, notes):
    out = []
    index_path = os.path.join(mem, INDEX)
    if not os.path.isfile(index_path):
        return [finding("index-missing", INDEX, "memory folder has notes but no MEMORY.md")] if notes else []
    lines = read(index_path).splitlines()
    linked = set()
    for n, line in enumerate(lines, 1):
        for target in LINK.findall(line):
            linked.add(os.path.normpath(target))
            if not os.path.isfile(os.path.join(mem, target)):
                out.append(finding("index-dangling", INDEX, f"line {n} links {target}, which does not exist"))
    for note in notes:
        if note not in linked:
            out.append(finding("index-unlisted", note, f"{note} is not linked from {INDEX}"))
    if len(lines) > INDEX_LINES:
        out.append(finding("index-too-long", INDEX,
                           f"{len(lines)} lines; Claude Code loads only the first {INDEX_LINES}"))
    return out


def refs(text):
    """(kind, token) pairs a note mentions: branches, symbols and repo paths, all from `code` spans."""
    branches = set(BRANCH.findall(text))
    for token in CODE.findall(text):
        token = re.sub(r":\d+(-\d+)?$", "", token.strip())
        if token in branches or " " in token or PLACEHOLDER.search(token) or token.startswith(("/", "~")):
            continue
        sym = SYMBOL.match(token)
        if sym:
            yield "symbol", sym.group(1).rsplit(".", 1)[-1]
        elif "/" in token and not token.startswith("-") or EXT.search(token):
            yield "path", token
    for b in sorted(branches):
        yield "branch", b


def check_refs(repo, mem, notes, texts):
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
                if "/" in token.rstrip("/") and not EXT.search(token) and not repo.has_path(token.split("/")[0]):
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
    date, sha, branch = DATE.search(text), SHA.search(text), HEAD_BRANCH.search(text)
    if date and sha and branch:
        return date.group(0), branch.group(1) or branch.group(2), sha.group(0)
    return None


def check_handoff(repo):
    for rel in HANDOFFS:
        path = os.path.join(repo.path, rel)
        if os.path.isfile(path):
            break
    else:
        return None, []
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
        behind = git(repo.path, "rev-list", "--count", f"{full}..HEAD")[1]
        out.append(finding("handoff-sha", rel, f"header ({date}) says {sha}; HEAD is {head_sha[:7]}, {behind} commit(s) later"))
    return rel, out


def check_project(mem, project):
    """Findings for one memory folder. project is the repo path, or None when it cannot be found."""
    notes = sorted(f for f in os.listdir(mem) if f.endswith(".md") and f != INDEX
                   and os.path.isfile(os.path.join(mem, f))) if os.path.isdir(mem) else []
    texts = {n: body(read(os.path.join(mem, n))) for n in notes}
    repo = Repo(project) if project and os.path.isdir(project) else Repo(None)
    findings = check_index(mem, notes) if os.path.isdir(mem) else []
    findings += check_refs(repo, mem, notes, texts) + check_duplicates(notes, texts)
    handoff, more = check_handoff(repo) if repo.path else (None, [])
    findings += more
    if not repo.path:
        findings.append(finding("project-missing", "", "the project folder for this memory was not found; "
                                "path, branch and handoff checks skipped"))
    findings.sort(key=lambda f: (f["kind"], f["file"], f["evidence"]))
    return {"project": project, "memory": mem if os.path.isdir(mem) else None, "handoff": handoff,
            "findings": findings}


def run(home, project=None, all_projects=False):
    root = os.path.join(home, ".claude", "projects")
    if all_projects:
        names = sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n, "memory"))) \
            if os.path.isdir(root) else []
        return [check_project(os.path.join(root, n, "memory"), unslug(n)) for n in names]
    project = os.path.abspath(project)
    return [check_project(os.path.join(root, slug(project), "memory"), project)]


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
    results = run(args.home, args.project, args.all)
    print(json.dumps({"version": 1, "projects": results}, indent=2, sort_keys=True) if args.json
          else text_report(results))
    return 1 if any(r["findings"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
