#!/usr/bin/env python3
"""Gate PR creation behind a recorded review.

Hook mode (default): PreToolUse hook. Reads the hook JSON on stdin and, only for
`gh pr create` or a create-pull-request MCP tool, exits 2 (blocking) unless a review
marker matches the code being submitted. Every other call, and every error, exits 0.

Mark mode: `review-gate.py --mark [--findings N] [--skip "reason"]` records that a
review ran for the current working tree. /review-loop runs it as its last step.

The marker is keyed on a git tree hash of the working tree (tracked + untracked files,
.gitignore respected), so it survives committing but not further edits. It lives in the
git dir, so it is never committed and is per-worktree.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

GH_PR_CREATE = re.compile(r"(?:^|[;&|(]|&&|\|\|)\s*gh\s+pr\s+create\b")
MCP_PR_CREATE = re.compile(r"create_pull_request", re.I)

BLOCK = """\
Blocked: no review recorded for this code. Before opening a PR:
- Ask the user to run /review-loop (you cannot invoke it yourself). In unattended or /goal
  runs, do the same steps by hand: /simplify, then the `reviewer` subagent for up to 5
  rounds, fixing real high/med findings and showing test results.
- If the change touches auth, migrations, concurrency, or a public API, offer the
  `reviewer-deep` agent (ask first).
- When done, record it: python "{script}" --mark --findings <open high/med count>
- If the user explicitly approves skipping the review: python "{script}" --mark --skip "<reason>"
Editing code after the review invalidates the marker; re-run on the final diff."""


def git(args, cwd, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


def git_path(name, cwd):
    return os.path.abspath(os.path.join(cwd, git(["rev-parse", "--git-path", name], cwd)))


def worktree_tree(cwd):
    """Tree hash of the working tree, computed on a copy of the index (real one untouched)."""
    index = git_path("index", cwd)
    fd, tmp = tempfile.mkstemp(prefix="review-gate-index-")
    os.close(fd)
    try:
        if os.path.exists(index):
            with open(index, "rb") as src, open(tmp, "wb") as dst:
                dst.write(src.read())
        else:
            os.remove(tmp)  # git creates it
        env = {**os.environ, "GIT_INDEX_FILE": tmp}
        git(["add", "-A"], cwd, env)
        return git(["write-tree"], cwd, env)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def head_tree(cwd):
    try:
        return git(["rev-parse", "HEAD^{tree}"], cwd)
    except RuntimeError:
        return None


def mark(argv):
    findings, skip = None, None
    if "--findings" in argv:
        findings = int(argv[argv.index("--findings") + 1])
    if "--skip" in argv:
        skip = argv[argv.index("--skip") + 1]
    cwd = os.getcwd()
    tree = worktree_tree(cwd)
    marker = {"tree": tree, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "findings_open": findings, "skipped": skip}
    with open(git_path("claude-review-ok", cwd), "w", encoding="utf-8") as f:
        json.dump(marker, f)
    print(f"review recorded for tree {tree[:10]}" + (f" (skipped: {skip})" if skip else ""))
    return 0


def gate():
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        command = (payload.get("tool_input") or {}).get("command", "")
        if not (GH_PR_CREATE.search(command) or MCP_PR_CREATE.search(tool)):
            return 0
        cwd = payload.get("cwd") or os.getcwd()
        git(["rev-parse", "--git-dir"], cwd)  # not a repo -> RuntimeError -> allow
        try:
            with open(git_path("claude-review-ok", cwd), encoding="utf-8") as f:
                ok = json.load(f)["tree"]
        except (OSError, KeyError, ValueError):
            ok = None
        if ok and ok in {worktree_tree(cwd), head_tree(cwd)}:
            return 0
        print(BLOCK.format(script=os.path.abspath(__file__).replace("\\", "/")), file=sys.stderr)
        return 2
    except Exception:
        return 0  # fail open: never break unrelated work


if __name__ == "__main__":
    sys.exit(mark(sys.argv) if "--mark" in sys.argv else gate())
