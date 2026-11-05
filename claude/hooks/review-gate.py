#!/usr/bin/env python3
"""Gate PR creation behind a recorded review.

Hook mode (default): PreToolUse hook. Reads the hook JSON on stdin and, only for
`gh pr create` or a create-pull-request MCP tool, exits 2 (blocking) unless a review
marker matches the code being submitted. Every other call exits 0, and so does a create run
outside a git repo. An error while checking a create blocks.

Detection lives in pr_create_parser.py next to this file (its docstring has the known limits).
If that module is missing, any command mentioning `gh` and `pr` blocks.

Branch check: the branch must be named cesar/<JIRA-KEY> (for example cesar/MOB-23). If the
user approves another name for this PR, `review-gate.py --allow-branch "reason"` records an
exception for the current branch (stored in the git dir, never committed).

Check mode: `review-gate.py check --cwd DIR --command "gh pr create"` asks the same question without
a hook payload (pre-push hook, CI, other tools). Exit 0 allows, 2 blocks, and bad input blocks.
The hook above parses its payload and calls the same evaluate() function.

Attest mode: `review-gate.py attest` prints `Review-Gate: <tree>` (with ` skipped: <reason>` for a skip)
when a valid mark covers the current code, else exits 2. `verify-attestation --tree H --text-file F`
(CI) exits 0 when F holds that line for tree H. An honor-system claim, see docs/review-gate.md.

Mark mode: `review-gate.py --mark --findings N` or `--mark --skip "reason"` records that a
review ran for the current working tree. /review-loop runs it as its last step.
Open findings above 0 keep the PR blocked unless a --skip reason was recorded.

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

SCRIPT = os.path.abspath(__file__).replace("\\", "/")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # found even under python3 -P
sys.dont_write_bytecode = True  # a __pycache__ in a project's .claude/hooks would change the tree hash
try:
    from pr_create_parser import CD_MENTION, create_dirs, mentions_create
    PARSER_ERROR = None
except Exception as e:  # missing or broken: a crash exits 1, which lets the call through
    PARSER_ERROR = e

BRANCH_OK = re.compile(r"^cesar/[A-Z][A-Z0-9]+-\d+$")
MCP_PR_CREATE = re.compile(r"create_pull_request", re.I)

BLOCK = """\
Blocked: no review recorded for this code. Before opening a PR:
- Run the review now by invoking the `review-loop` skill (Skill tool); do not ask the user to type
  it. In unattended or /goal runs, do the same steps by hand: /simplify, then the `reviewer` subagent for up to 5
  rounds, fixing real high/med findings and showing test results.
- If the change touches auth, migrations, concurrency, or a public API, offer the
  `reviewer-deep` agent (ask first).
- When done, record it: python3 "{script}" --mark --findings <open high/med count>
- If the user explicitly approves skipping the review: python3 "{script}" --mark --skip "<reason>"
Editing code after the review invalidates the marker; re-run on the final diff."""

FINDINGS_BLOCK = """\
Blocked: the recorded review has {count} open high/med finding(s).
- Fix them and run the `review-loop` skill again. When none are open, it records: python3 "{script}" --mark --findings 0
- If the user explicitly approves opening the PR anyway: python3 "{script}" --mark --skip "<reason>"
"""

CD_BLOCK = """\
Blocked: `gh pr create` runs after a `cd` whose target cannot be worked out from the command,
so the gate cannot tell which repo to check. Run it from the repo, or `cd` to a literal path.
"""

ERROR_BLOCK = """\
Blocked: the review gate could not check this repo: {error}
Fix the problem and try again. If the user explicitly approves skipping the review: python3 "{script}" --mark --skip "<reason>"
"""

PARSER_BLOCK = """\
Blocked: the review gate cannot load pr_create_parser.py next to {script}: {error}
Until it loads, every command mentioning `gh` and `pr` is blocked, and --mark --skip does not help.
Reinstall the gate and its parser together (re-run install.sh, or copy both files).
"""

BRANCH_BLOCK = """\
Blocked: branch "{branch}" is not named cesar/<JIRA-KEY> (for example cesar/MOB-23).
- Create or find the Jira ticket, then rename the branch: git branch -m cesar/<KEY>
  (rename only an unpushed branch, or ask the user first).
- If the user explicitly approves this name for the PR: python3 "{script}" --allow-branch "<reason>"
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
    try:
        if "--findings" in argv:
            findings = int(argv[argv.index("--findings") + 1])
            if findings < 0:
                raise ValueError
        if "--skip" in argv:
            skip = argv[argv.index("--skip") + 1]
            if not skip.strip() or skip.startswith("--"):
                raise ValueError
    except (IndexError, ValueError):
        findings, skip = None, None
    if findings is None and skip is None:
        print('Nothing recorded. Use --mark --findings <open high/med count> or --mark --skip "<reason>".',
              file=sys.stderr)
        return 2
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
    return BRANCH_BLOCK.format(branch=branch, script=SCRIPT)


def check_repo(cwd):
    """Exit code for a create running in cwd."""
    script = SCRIPT
    if cwd is None:
        print(CD_BLOCK, file=sys.stderr)
        return 2
    try:
        try:
            git(["rev-parse", "--git-dir"], cwd, {**os.environ, "LC_ALL": "C"})
        except OSError:
            return 0  # no such folder, or no git: nothing to judge
        except RuntimeError as e:
            if "not a git repository" in str(e):
                return 0
            raise
        msg = branch_blocked(cwd)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        try:
            with open(git_path("claude-review-ok", cwd), encoding="utf-8") as f:
                marker = json.load(f)
            ok = marker["tree"]
        except (OSError, KeyError, ValueError):
            ok = None
        if ok and ok in {worktree_tree(cwd), head_tree(cwd)}:
            if marker.get("skipped"):
                return 0
            count = marker.get("findings_open")
            if count == 0:
                return 0
            if count is not None:
                print(FINDINGS_BLOCK.format(count=count, script=script), file=sys.stderr)
                return 2
        print(BLOCK.format(script=script), file=sys.stderr)
        return 2
    except Exception as e:  # this is a create, so an unverifiable repo must not let it through
        print(ERROR_BLOCK.format(error=e, script=script), file=sys.stderr)
        return 2


def option(argv, name):
    i = argv.index(name) + 1 if name in argv else len(argv)
    return argv[i] if i < len(argv) and not argv[i].startswith("--") else None


def check(argv):
    """Plain entry for tools without Claude hooks: `check --cwd DIR --command "..."`.

    Same allow (0) and block (2) codes as the hook. Unlike the hook, bad input blocks: a missing
    option, a missing folder or a folder that is not a git repo all exit 2.
    """
    cwd, command = option(argv, "--cwd"), option(argv, "--command")
    if not cwd or not command:
        print('Blocked: usage: review-gate.py check --cwd DIR --command "gh pr create"', file=sys.stderr)
        return 2
    try:
        git(["rev-parse", "--git-dir"], cwd)
    except (OSError, RuntimeError) as e:
        print(ERROR_BLOCK.format(error=e, script=SCRIPT), file=sys.stderr)
        return 2
    return evaluate("Bash", command, cwd)


ATTEST_LINE = re.compile(r"^[ \t]*Review-Gate:[ \t]+([0-9a-f]{40}|[0-9a-f]{64})(?:[ \t]+skipped:[ \t]*(.*?))?[ \t]*$",
                         re.I | re.M)


def attest(cwd):
    """Print `Review-Gate: <tree>` (plus ` skipped: <reason>`) when a valid mark covers this code.

    Same validity rule as check_repo: the mark's tree is the working tree or HEAD, and the review
    is clean (0 open findings) or skipped with a reason. Otherwise exit 2 with nothing on stdout.
    """
    try:
        with open(git_path("claude-review-ok", cwd), encoding="utf-8") as f:
            marker = json.load(f)
        tree = marker["tree"]
        if tree not in {worktree_tree(cwd), head_tree(cwd)}:
            raise ValueError("the review mark is for other code")
        skipped = " ".join(str(marker.get("skipped") or "").split())
        if not skipped and marker.get("findings_open") != 0:
            raise ValueError("the review has open findings or none was recorded")
    except (OSError, KeyError, ValueError, RuntimeError) as e:
        print(f"No attestation: no valid review for the current code ({e}). Run the review loop first.",
              file=sys.stderr)
        return 2
    print(f"Review-Gate: {tree}" + (f" skipped: {skipped}" if skipped else ""))
    return 0


def verify_attestation(argv):
    """`verify-attestation --tree HASH --text-file F [--text-file G ...]`: exit 0 if any file
    holds a line `Review-Gate: HASH`. Prints `skipped: <reason>` for a skip attestation."""
    tree = option(argv, "--tree")
    files = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--text-file"]
    if not tree or not files:
        print("Blocked: usage: review-gate.py verify-attestation --tree HASH --text-file FILE",
              file=sys.stderr)
        return 2
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            print(f"Blocked: cannot read {path}: {e}", file=sys.stderr)
            return 2
        for m in ATTEST_LINE.finditer(text):
            if m.group(1).lower() == tree.strip().lower():
                if m.group(2):
                    print(f"skipped: {m.group(2)}")
                print(f"Review attestation found for tree {tree}.")
                return 0
    print(f"Blocked: no `Review-Gate: {tree}` line in the PR description or last commit message. "
          "Run `review-gate.py attest` on the final code and paste its line into the PR body.",
          file=sys.stderr)
    return 2


def gate():
    """Claude PreToolUse hook: parse the payload, then ask evaluate()."""
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
        tool = str(payload.get("tool_name") or "")
        command = (payload.get("tool_input") or {}).get("command", "")
        cwd = payload.get("cwd") or os.getcwd()
    except Exception:
        return 0  # not a call we can read: never break unrelated work
    return evaluate(tool, command, cwd)


def evaluate(tool, command, cwd):
    """Exit code for one tool call. The single decision path for the hook and for `check`."""
    if MCP_PR_CREATE.search(tool):
        dirs = [cwd]
    elif PARSER_ERROR:
        if not re.search(r"\bgh\b.*\bpr\b", re.sub(r"[\"'\\`]", "", str(command)), re.S | re.I):
            return 0  # quotes are dropped first, so `g""h "pr" cre'ate'` is still caught
        print(PARSER_BLOCK.format(error=PARSER_ERROR, script=SCRIPT), file=sys.stderr)
        return 2
    else:
        try:
            dirs = create_dirs(command, cwd, tool.lower() == "powershell")
        except Exception:
            if not (isinstance(command, str) and mentions_create(command)):
                return 0
            dirs = [None if CD_MENTION.search(command) else cwd]  # the parse lost track of any cd
    for d in dict.fromkeys(dirs):
        code = check_repo(d)
        if code:
            return code
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"]:
        sys.exit(check(sys.argv))
    if sys.argv[1:2] == ["attest"]:
        sys.exit(attest(os.getcwd()))
    if sys.argv[1:2] == ["verify-attestation"]:
        sys.exit(verify_attestation(sys.argv))
    if "--allow-branch" in sys.argv:
        sys.exit(allow_branch(sys.argv))
    sys.exit(mark(sys.argv) if "--mark" in sys.argv else gate())
