#!/usr/bin/env python3
"""Gate PR creation behind a recorded review.

Hook mode (default): PreToolUse hook. Reads the hook JSON on stdin and, only for
`gh pr create` or a create-pull-request MCP tool, exits 2 (blocking) unless a review
marker matches the code being submitted. Every other call, and every error, exits 0.

Branch check: the branch must be named cesar/<JIRA-KEY> (for example cesar/MOB-23). If the
user approves another name for this PR, `review-gate.py --allow-branch "reason"` records an
exception for the current branch (stored in the git dir, never committed).

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

GH_PR_CREATE = re.compile(r"(?:^|[;&|(\n])\s*(?:\w+=\S*\s+)*gh\s+(?:-\S+\s+\S+\s+)*pr\s+create\b")
CD_PREFIX = re.compile(r"^\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)\s*(?:&&|;)")
BRANCH_OK = re.compile(r"^cesar/[A-Z][A-Z0-9]+-\d+$")
MCP_PR_CREATE = re.compile(r"create_pull_request", re.I)

BLOCK = """\
Blocked: no review recorded for this code. Before opening a PR:
- Run the review now by invoking the `review-loop` skill (Skill tool); do not ask the user to type
  it. In unattended or /goal runs, do the same steps by hand: /simplify, then the `reviewer` subagent for up to 5
  rounds, fixing real high/med findings and showing test results.
- If the change touches auth, migrations, concurrency, or a public API, offer the
  `reviewer-deep` agent (ask first).
- When done, record it: python "{script}" --mark --findings <open high/med count>
- If the user explicitly approves skipping the review: python "{script}" --mark --skip "<reason>"
Editing code after the review invalidates the marker; re-run on the final diff."""

BRANCH_BLOCK = """\
Blocked: branch "{branch}" is not named cesar/<JIRA-KEY> (for example cesar/MOB-23).
- Create or find the Jira ticket, then rename the branch: git branch -m cesar/<KEY>
  (rename only an unpushed branch, or ask the user first).
- If the user explicitly approves this name for the PR: python "{script}" --allow-branch "<reason>"
"""


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


def allow_branch(argv):
    cwd = os.getcwd()
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    reason = argv[argv.index("--allow-branch") + 1] if len(argv) > argv.index("--allow-branch") + 1 else ""
    with open(git_path("claude-branch-ok", cwd), "w", encoding="utf-8") as f:
        json.dump({"branch": branch, "reason": reason}, f)
    print(f"branch name exception recorded for {branch}")
    return 0


def branch_blocked(cwd):
    """Message to print when the current branch breaks the naming rule, else None."""
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch == "HEAD" or BRANCH_OK.match(branch):  # detached HEAD: nothing to judge
        return None
    try:
        with open(git_path("claude-branch-ok", cwd), encoding="utf-8") as f:
            if json.load(f)["branch"] == branch:
                return None
    except (OSError, KeyError, ValueError):
        pass
    return BRANCH_BLOCK.format(branch=branch, script=os.path.abspath(__file__).replace("\\", "/"))


def gate():
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        tool = payload.get("tool_name", "")
        command = (payload.get("tool_input") or {}).get("command", "")
        if not (GH_PR_CREATE.search(command) or MCP_PR_CREATE.search(tool)):
            return 0
        cwd = payload.get("cwd") or os.getcwd()
        cd = CD_PREFIX.match(command)  # a leading `cd repo &&` means the command runs in repo, not the session cwd
        if cd:
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(cd.group(1).strip("\"'"))))
        git(["rev-parse", "--git-dir"], cwd)  # not a repo -> RuntimeError -> allow
        msg = branch_blocked(cwd)
        if msg:
            print(msg, file=sys.stderr)
            return 2
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
    if "--allow-branch" in sys.argv:
        sys.exit(allow_branch(sys.argv))
    sys.exit(mark(sys.argv) if "--mark" in sys.argv else gate())
