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
`verify-tier --tier-json J --text-file F --author-association A` (CI, NFX-70) exits 2 when a `Review-Tier:` line
in F is below the tier in J (tier.py's JSON for the PR diff). Overrides count only for OWNER or MEMBER (NFX-95).

Mark mode: `review-gate.py --mark --findings N` or `--mark --skip "reason"` records that a
review ran for the current working tree. /review-loop runs it as its last step.
`--tier 0-3 [--reasons "..."]` (from the review loop's tier.py) is stored too; `attest` then prints a separate
`Review-Tier: N (reasons)` line after the `Review-Gate:` line. `--mark` runs tier.py (next to this hook's
skills folder) and refuses a --tier below the computed one unless `--tier-override "<reason>"` is given, which
`attest` then prints. A missing or failing tier.py counts as Tier 2. Only `--mark` reads the tier, never `check`.
Open findings above 0 keep the PR blocked unless a --skip reason was recorded.
`--mark --findings` also runs the project's check command (`[check] command` in .nousfx/project.toml, read
from the default branch, so a branch cannot weaken it) and refuses the mark when it fails, times out,
changes files, or is not configured. `--skip` runs no check.

Run-check mode: `review-gate.py run-check --ref REF` (CI) reads the check command from REF only and runs it
in the current checkout. Exit 0 when the command passes, 2 otherwise.

The marker is keyed on a git tree hash of the working tree (tracked + untracked files,
.gitignore respected), so it survives committing but not further edits. It lives in the
git dir, so it is never committed and is per-worktree.
"""
import collections
import contextlib
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

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

CHECK_FILE = ".nousfx/project.toml"
CHECK_TIMEOUT = 1800
CHECK_LOG = "claude-review-check.log"
GIT_ENV_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")

BRANCH_OK = re.compile(r"^cesar/[A-Z][A-Z0-9]+-\d+$")
MCP_PR_CREATE = re.compile(r"create_pull_request", re.I)
GIT_REPO_VARS = re.compile(r"\bGIT_(?:DIR|WORK_TREE)\b", re.I)

TIER_TOO_LOW = """\
Nothing recorded: --tier {tier} is below the computed Tier {computed} ({why}).
Record the computed tier or a higher one. If the user explicitly approves the lower tier, add
--tier-override "<reason>" to the same command: python3 "{script}" --mark ... --tier {tier} --tier-override "<reason>\""""

BLOCK = """\
Blocked: no review recorded for this code. Before opening a PR:
- Run the review now by invoking the `review-loop` skill (Skill tool); do not ask the user to type
  it. In unattended or /goal runs, do the same steps by hand: /simplify, then the reviewer agent and round cap
  that `review-loop` names for the tier (give it the ticket and the scope, no builder summary), fixing real high findings and real med findings at confidence 60 or more and showing test results.
- If the change touches auth, migrations, concurrency, or a public API, offer the
  `reviewer-deep` agent (ask first).
- When done, record it: python3 "{script}" --mark --findings <open high count + open med at 60 or more> --tier <0-3> --reasons "<tier reasons>"
  The mark refuses a --tier below what tier.py computes. Add --tier-override "<reason>" only with the user's explicit approval.
- If the user explicitly approves skipping the review: python3 "{script}" --mark --skip "<reason>"
Editing code after the review invalidates the marker; re-run on the final diff."""

FINDINGS_BLOCK = """\
Blocked: the recorded review has {count} open high or med (confidence 60+) finding(s).
- Fix them and run the `review-loop` skill again. When none are open, it records: python3 "{script}" --mark --findings 0
- If the user explicitly approves opening the PR anyway: python3 "{script}" --mark --skip "<reason>"
"""

NO_CHECK = """\
Nothing recorded: this repo has no check command, so the gate cannot run the tests itself.
- Add it to {file} and commit it to the default branch:
    [check]
    command = "<your test command, for example ./gradlew test, flutter test or npm test>"
  (the `project-setup` skill can propose one). The command is read from the default branch, so the PR that adds it is marked with the working-tree copy and later PRs with the merged one.
- If the user explicitly approves skipping the tests: python3 "{script}" --mark --skip "<reason>"
"""

CHECK_FAILED = """\
Nothing recorded: the check command `{command}` {why}. Log: {log}
Fix the cause and run --mark again. Last lines:
{tail}
"""

MARK_BRANCH_BLOCK = """\
Blocked: the review mark was recorded on branch "{marked}", not "{branch}". A mark covers only the branch
it was recorded on, even when the code is the same (a new branch cut from a merged one). Commit this
branch's change and run the `review-loop` skill here so it records a new mark (also after a rename).
On a push, the local and the remote branch names must be the same.
"""

DETACHED_BLOCK = """\
Blocked: HEAD is detached, and a review mark covers only the branch it was recorded on.
Check out the branch, run the `review-loop` skill there if it has no mark yet, then try again.
"""

MARK_NO_BRANCH_BLOCK = """\
Blocked: the review mark has no branch (it was recorded before the gate stored one), so it cannot show
which branch was reviewed. Run the `review-loop` skill again so it records a new mark.
"""

CHECK_MISSING_BLOCK = """\
Blocked: the recorded review has no passing check result (it was recorded before the gate ran checks, or
the check did not pass). Run the `review-loop` skill again so it records the mark with the check.
"""

GIT_VARS_BLOCK = """\
Blocked: this PR create sets GIT_DIR or GIT_WORK_TREE, which can point gh at another repo than the one the
gate checked. Run `gh pr create` from the repo, without those variables.
"""

MCP_OUTSIDE_BLOCK = """\
Blocked: a create-PR tool was called from a folder that is not a git repo, so the gate cannot check any
review. Open the repo as the workspace and try again.
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
Reinstall the gate and its parser together (re-run the sync: ./install.sh, or scripts/nousfx_sync.py).
"""

BRANCH_BLOCK = """\
Blocked: branch "{branch}" is not named cesar/<JIRA-KEY> (for example cesar/MOB-23).
- Create or find the Jira ticket, then rename the branch: git branch -m cesar/<KEY>
  (rename only an unpushed branch, or ask the user first). A rename leaves the review mark on the old name,
  so run the `review-loop` skill again after it.
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


def current_branch(cwd):
    """The checked-out branch name, exactly as the pre-push hook names it (refs/heads/ stripped, never the
    `heads/x` form rev-parse gives when a tag shares the name). "HEAD" when detached. Works on an unborn branch."""
    try:
        ref = git(["symbolic-ref", "-q", "HEAD"], cwd)
    except RuntimeError as e:
        if str(e):
            raise  # a real git failure, not a detached HEAD
        return "HEAD"  # -q: exit 1 with no message means detached
    return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else "HEAD"


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


def default_ref(cwd):
    """The remote-tracking branch whose check command is trusted: origin/HEAD (only when it points at another
    refs/remotes/origin/ ref), else origin/main, else origin/master, else None. Local state, never fetched: CI is
    the real check."""
    candidates = []
    try:
        target = git(["symbolic-ref", "-q", "refs/remotes/origin/HEAD"], cwd)
        if target.startswith("refs/remotes/origin/") and target != "refs/remotes/origin/HEAD":
            candidates.append(target)
    except RuntimeError:
        pass
    candidates += ["refs/remotes/origin/main", "refs/remotes/origin/master"]
    for ref in candidates:
        try:
            git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], cwd)
            return ref
        except RuntimeError:
            continue
    return None


def parse_check(text):
    """The `[check]` table of a project.toml text as {command, timeout}, or None when there is no command."""
    if tomllib is None:
        raise RuntimeError("Python 3.11+ is needed to read " + CHECK_FILE)
    table = tomllib.loads(text).get("check")
    if not isinstance(table, dict):
        return None
    command, timeout = table.get("command"), table.get("timeout", CHECK_TIMEOUT)
    if not isinstance(command, str) or not command.strip():
        return None
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("[check] timeout must be a positive number of seconds")
    return {"command": command.strip(), "timeout": timeout}


def exists_at(cwd, ref, path):
    """True when `path` exists in the commit `ref`. Any other git failure raises, so it cannot read as 'absent'."""
    if ref.startswith("-"):
        raise RuntimeError(f"not a ref: {ref}")
    try:
        git(["cat-file", "-e", f"{ref}:{path}"], cwd, {**os.environ, "LC_ALL": "C"})
        return True
    except RuntimeError as e:
        if "does not exist" in str(e) or "exists on disk, but not in" in str(e) or "Not a valid object name" in str(e):
            return False
        raise


def load_check(cwd, ref=None):
    """The check from CHECK_FILE at `ref`, or from the working tree when ref is None. None when absent."""
    if ref:
        if ref.startswith("-"):
            raise RuntimeError(f"not a ref: {ref}")
        try:
            text = git(["show", f"{ref}:{CHECK_FILE}"], cwd)
        except RuntimeError:
            return None
    else:
        try:
            with open(os.path.join(git(["rev-parse", "--show-toplevel"], cwd), CHECK_FILE), encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return None
    check = parse_check(text)
    if check:
        check["source"] = ref or "working tree"
    return check


def run_check(cwd, check, log_path=None):
    """Run the check command at the repo top level. Returns (ok, why). Output goes to log_path, or to our own
    stdout and stderr when there is none."""
    root = git(["rev-parse", "--show-toplevel"], cwd)
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_VARS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    posix = os.name == "posix"

    def stop(proc):  # the shell's children too, not just the shell
        try:
            os.killpg(proc.pid, 9) if posix else proc.kill()
        except OSError:
            proc.kill()

    with open(log_path, "wb") if log_path else contextlib.nullcontext() as out:  # no log: show it (CI job log)
        proc = subprocess.Popen(check["command"], shell=True, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT if out else None, start_new_session=posix)
        try:
            code = proc.wait(timeout=check["timeout"])
        except subprocess.TimeoutExpired:
            stop(proc)
            proc.wait(timeout=30)
            return False, f"did not finish within {check['timeout']} seconds"
        if posix:  # background children must not keep editing files after the tree hash is compared
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                pass
    return (True, "") if code == 0 else (False, f"exited with {code}")


def log_tail(path, lines=40):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(collections.deque(f, maxlen=lines)).rstrip() or "(no output)"
    except OSError:
        return "(no log)"


def tier_args(argv):
    """(tier, reasons, override) from `--tier 0-3 [--reasons TEXT] [--tier-override TEXT]`; Nones without --tier.
    ValueError on bad input: a tier outside 0-3, a missing value, or --reasons / --tier-override without --tier."""
    tier = reasons = override = None
    if "--tier" in argv:
        tier = argv[argv.index("--tier") + 1]
        if tier not in ("0", "1", "2", "3"):
            raise ValueError
        tier = int(tier)
    if "--reasons" in argv:
        reasons = argv[argv.index("--reasons") + 1]
        if tier is None or reasons.startswith("--"):
            raise ValueError
        reasons = " ".join(reasons.split()) or None
    if "--tier-override" in argv:
        override = argv[argv.index("--tier-override") + 1]
        override = " ".join(override.split())
        if tier is None or not override or override.startswith("--"):
            raise ValueError
    return tier, reasons, override


def tier_result(data):
    """(tier, reasons) from tier.py's JSON object. ValueError unless the tier is an int 0-3."""
    tier = data.get("tier") if isinstance(data, dict) else None
    if isinstance(tier, bool) or tier not in (0, 1, 2, 3):
        raise ValueError(f"unexpected tier.py output (tier {tier!r})")
    why = data.get("reasons")
    return tier, [str(x) for x in why] if isinstance(why, list) else []


TIER_PY = os.path.join("..", "skills", "review-loop", "scripts", "tier.py")  # relative to this file's folder
TIER_TIMEOUT = 60


def computed_tier(cwd):
    """(tier, why, note) from tier.py in the folder next to this hook's own. The tier is Tier 2 when tier.py is
    missing, fails, times out or prints something odd: the same floor tier.py and the review loop use."""
    path = os.path.normpath(os.path.join(os.path.dirname(SCRIPT), TIER_PY))
    if not os.path.isfile(path):
        return 2, "", f"tier.py not found at {path}; counted as Tier 2 (re-run the sync)"
    try:
        r = subprocess.run([sys.executable, "-B", path, "--cwd", cwd], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=TIER_TIMEOUT)
        if r.returncode != 0:
            raise ValueError(f"exit {r.returncode}")
        tier, why = tier_result(json.loads(r.stdout))
        return tier, " ".join(why), ""
    except Exception as e:
        return 2, "", f"tier.py failed ({type(e).__name__}: {e}); counted as Tier 2"


def mark(argv):
    try:
        tier, reasons, override = tier_args(argv)
        if override and "--skip" in argv:
            raise ValueError
    except (IndexError, ValueError):
        print("Nothing recorded. --tier takes 0, 1, 2 or 3; --reasons \"...\" and --tier-override \"...\" need --tier "
              "and no --skip.", file=sys.stderr)
        return 2
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
        print('Nothing recorded. Use --mark --findings <open high count + open med at 60 or more> or --mark --skip "<reason>".',
              file=sys.stderr)
        return 2
    cwd = os.getcwd()
    marker_path = git_path("claude-review-ok", cwd)
    if os.path.exists(marker_path):
        os.remove(marker_path)  # a refused mark must not leave an older good one behind
    try:
        branch = current_branch(cwd)
    except RuntimeError as e:
        print(f"Nothing recorded: cannot read the checked-out branch: {e}", file=sys.stderr)
        return 2
    if branch == "HEAD":
        print("Nothing recorded: HEAD is detached, and a mark covers only the branch it was recorded on. "
              "Check out the branch, then run --mark again.", file=sys.stderr)
        return 2
    tree = worktree_tree(cwd)
    marker = {"tree": tree, "branch": branch,
              "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "findings_open": findings, "skipped": skip}
    if tier is not None and not skip:  # a skip ran no review, so it has no tier
        computed, why, note = computed_tier(cwd)
        if note:
            print(f"Note: {note}", file=sys.stderr)
        marker["tier"], marker["tier_reasons"] = tier, reasons
        if tier < computed:
            if not override:
                print(TIER_TOO_LOW.format(tier=tier, computed=computed, why=why or "no reasons",
                                          script=SCRIPT), file=sys.stderr)
                return 2
            marker["tier_computed"], marker["tier_override"] = computed, override
    if not skip:
        try:
            ref = default_ref(cwd)
            # The default branch's file wins whenever it exists, even without a usable [check] (then: no check).
            # Only when that branch has no such file (the PR that adds it) or there is no default branch at all
            # does the working-tree copy count. CI always reads the base branch.
            trusted = bool(ref) and exists_at(cwd, ref, CHECK_FILE)
            check = load_check(cwd, ref if trusted else None)
        except Exception as e:
            print(f"Nothing recorded: cannot read the check command from {CHECK_FILE}: {e}", file=sys.stderr)
            return 2
        if not check:
            print(NO_CHECK.format(file=CHECK_FILE, script=SCRIPT), file=sys.stderr)
            return 2
        log = git_path(CHECK_LOG, cwd)
        ok, why = run_check(cwd, check, log)
        if ok and worktree_tree(cwd) != tree:
            ok, why = False, "changed files in the working tree (a check must not edit files)"
        if not ok:
            if os.path.exists(marker_path):
                os.remove(marker_path)  # the command is branch code: it may have written a mark itself
            print(CHECK_FAILED.format(command=check["command"], why=why, log=log, tail=log_tail(log)),
                  file=sys.stderr)
            return 2
        marker["check"] = {"command": check["command"], "exit": 0, "source": check["source"]}
        if check["source"] == "working tree":
            print(f"note: {CHECK_FILE} is not on the default branch{'' if ref else ' (none found)'}; "
                  "used the working-tree copy. CI reads the base branch.")
    with open(marker_path, "w", encoding="utf-8") as f:
        json.dump(marker, f)
    print(f"review recorded for tree {tree[:10]}" + (f" (skipped: {skip})" if skip else ""))
    return 0


def allow_branch(argv):
    cwd = os.getcwd()
    branch = current_branch(cwd)
    reason = argv[argv.index("--allow-branch") + 1] if len(argv) > argv.index("--allow-branch") + 1 else ""
    with open(git_path("claude-branch-ok", cwd), "w", encoding="utf-8") as f:
        json.dump({"branch": branch, "reason": reason}, f)
    print(f"branch name exception recorded for {branch}")
    return 0


def branch_blocked(cwd, branch=None):
    """Message to print when the branch (default: the current one) breaks the naming rule, else None."""
    branch = branch or current_branch(cwd)
    if branch == "HEAD" or BRANCH_OK.match(branch):  # detached HEAD: nothing to judge
        return None
    try:
        with open(git_path("claude-branch-ok", cwd), encoding="utf-8") as f:
            if json.load(f)["branch"] == branch:
                return None
    except (OSError, KeyError, ValueError):
        pass
    return BRANCH_BLOCK.format(branch=branch, script=SCRIPT)


def check_repo(cwd, tree=None, branch=None):
    """Exit code for a create running in cwd. With tree and branch, judge that tree and branch
    instead of the working tree and the checked-out branch (the pre-push hook does this per ref)."""
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
        branch = branch or current_branch(cwd)
        msg = branch_blocked(cwd, branch)
        if msg:
            print(msg, file=sys.stderr)
            return 2
        try:
            with open(git_path("claude-review-ok", cwd), encoding="utf-8") as f:
                marker = json.load(f)
            ok = marker["tree"]
        except (OSError, KeyError, ValueError):
            ok = None
        if ok and (ok == tree if tree else covers(cwd, ok)):
            marked = marker.get("branch")
            if not isinstance(marked, str) or not marked:
                print(MARK_NO_BRANCH_BLOCK, file=sys.stderr)
                return 2
            if branch == "HEAD":
                print(DETACHED_BLOCK, file=sys.stderr)
                return 2
            if marked != branch:
                print(MARK_BRANCH_BLOCK.format(marked=marked, branch=branch), file=sys.stderr)
                return 2
            if marker.get("skipped"):
                return 0
            count = marker.get("findings_open")
            if count == 0:
                if has_check_pass(marker):
                    return 0
                print(CHECK_MISSING_BLOCK, file=sys.stderr)
                return 2
            if count is not None:
                print(FINDINGS_BLOCK.format(count=count, script=script), file=sys.stderr)
                return 2
        print(BLOCK.format(script=script), file=sys.stderr)
        return 2
    except Exception as e:  # this is a create, so an unverifiable repo must not let it through
        print(ERROR_BLOCK.format(error=e, script=script), file=sys.stderr)
        return 2


def has_check_pass(marker):
    result = marker.get("check")
    return isinstance(result, dict) and result.get("exit") == 0


def covers(cwd, tree):
    """True when tree is HEAD's tree or the working tree. Both are always computed on purpose: a git
    failure on either one must block, so no short-circuit when HEAD already matches."""
    return tree in {worktree_tree(cwd), head_tree(cwd)}


def option(argv, name):
    i = argv.index(name) + 1 if name in argv else len(argv)
    return argv[i] if i < len(argv) and not argv[i].startswith("--") else None


def check(argv):
    """Plain entry for tools without Claude hooks: `check --cwd DIR --command "..." [--tool Bash|PowerShell|<mcp tool name>] [--allow-non-repo]`.

    Same allow (0) and block (2) codes as the hook. Unlike the hook, bad input blocks: a missing
    option, a missing folder or a folder that is not a git repo all exit 2.
    """
    cwd = option(argv, "--cwd")
    command = argv[argv.index("--command") + 1] if "--command" in argv[:-1] else None  # may start with dashes
    if not cwd or not command:
        print('Blocked: usage: review-gate.py check --cwd DIR --command "gh pr create"', file=sys.stderr)
        return 2
    tree, branch, tool = option(argv, "--tree"), option(argv, "--branch"), option(argv, "--tool") or "Bash"
    # --allow-non-repo (agy sends no per-command folder): a workspace that is not a repo is judged by the
    # command alone, so a create that cd's into a repo is still checked there. Only for git's own answer
    # "not a git repository": a missing git, a missing folder or a pushed commit (--tree) still block.
    lenient = "--allow-non-repo" in argv and os.path.isdir(cwd) and not (tree or branch)
    try:
        git(["rev-parse", "--git-dir"], cwd, {**os.environ, "LC_ALL": "C"})
    except (OSError, RuntimeError) as e:
        if not (lenient and isinstance(e, RuntimeError) and "not a git repository" in str(e)):
            print(ERROR_BLOCK.format(error=e, script=SCRIPT), file=sys.stderr)
            return 2
        if MCP_PR_CREATE.search(tool):  # a command can cd into a repo; a create-PR tool has no such thing to judge
            print(MCP_OUTSIDE_BLOCK, file=sys.stderr)
            return 2
    if tree or branch:  # judge a pushed commit: the pre-push hook passes both
        if not (tree and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tree)) or not branch:
            print("Blocked: --tree needs a tree hash and --branch a branch name, together.", file=sys.stderr)
            return 2
        return check_repo(cwd, tree, branch)
    return evaluate(tool, command, cwd)


ATTEST_LINE = re.compile(r"^[ \t]*Review-Gate:[ \t]+([0-9a-f]{40}|[0-9a-f]{64})(?:[ \t]+skipped:[ \t]*(.*?))?[ \t]*$",
                         re.I | re.M)


def attest(cwd):
    """Print `Review-Gate: <tree>` (plus ` skipped: <reason>`) when a valid mark covers this code.

    Same validity rule as check_repo: the mark's tree is the working tree or HEAD, it was recorded on
    the checked-out branch, and the review is clean (0 open findings) or skipped with a reason.
    Otherwise exit 2 with nothing on stdout.
    """
    try:
        with open(git_path("claude-review-ok", cwd), encoding="utf-8") as f:
            marker = json.load(f)
        tree = marker["tree"]
        if not covers(cwd, tree):
            raise ValueError("the review mark is for other code")
        branch = current_branch(cwd)
        if branch == "HEAD":
            raise ValueError("HEAD is detached; check out the branch the mark was recorded on")
        if not marker.get("branch") or marker.get("branch") != branch:
            raise ValueError("the review mark was recorded on another branch, or names none")
        skipped = " ".join(str(marker.get("skipped") or "").split())
        if not skipped and marker.get("findings_open") != 0:
            raise ValueError("the review has open findings or none was recorded")
        if not skipped and not has_check_pass(marker):
            raise ValueError("the mark has no passing check result")
    except (OSError, KeyError, ValueError, RuntimeError) as e:
        print(f"No attestation: no valid review for the current code ({e}). Run the review loop first.",
              file=sys.stderr)
        return 2
    print(f"Review-Gate: {tree}" + (f" skipped: {skipped}" if skipped else ""))
    tier = marker.get("tier")
    if isinstance(tier, int) and not isinstance(tier, bool) and 0 <= tier <= 3:
        reasons = " ".join(str(marker.get("tier_reasons") or "").split())
        line = f"Review-Tier: {tier}" + (f" ({reasons})" if reasons else "")
        override = " ".join(str(marker.get("tier_override") or "").split())
        computed = marker.get("tier_computed")
        if override and isinstance(computed, int) and not isinstance(computed, bool) and computed > tier:
            line += f" override: computed {computed}, {override}"
        print(line)
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


TIER_FALLBACK = "Tier 2: could not"  # tier.py's reason when it could not read the diff (pinned by a test)
TIER_REASON = re.compile(r"Tier ([0-3]):")
TIER_LINE = re.compile(r"^[ \t]*Review-Tier:(.*)$", re.I | re.M)
TIER_VALUE = re.compile(r"[ \t]+([0-3])(?=[ \t]|$)(.*)$")
OVERRIDE_ASSOCIATIONS = ("OWNER", "MEMBER")  # PR authors whose override CI honors (NFX-95)
TIER_OVERRIDE = re.compile(r"[ \t]override:[ \t]+computed[ \t]+([0-3]),[ \t]*(\S.*?)[ \t]*$")


def body_tiers(text):
    """[(line, tier, override_computed, override_reason)] for each `Review-Tier:` line. ValueError on a bad one."""
    found = []
    for m in TIER_LINE.finditer(text):
        line = "Review-Tier:" + m.group(1).rstrip()
        v = TIER_VALUE.match(m.group(1))
        if not v:
            raise ValueError(f"cannot read `{line}`: the tier must be 0, 1, 2 or 3")
        o = TIER_OVERRIDE.search(v.group(2))  # the first one; its reason runs to the end of the line
        found.append((line, int(v.group(1)), int(o.group(1)) if o else None, o.group(2) if o else None))
    return found


def verify_tier(argv):
    """`verify-tier --tier-json FILE --text-file F --author-association A` (CI, NFX-70): exit 2 when a
    `Review-Tier:` line in F is below the tier in FILE (tier.py's JSON for the PR diff). An attested override passes
    when the tier it names as computed is at least the CI tier and A (the PR author's `author_association`) is OWNER
    or MEMBER (NFX-95); a missing A honors no override. It stays self-asserted. No tier line passes (a known hole).
    A tier.py result that could not read the diff fails unless every line claims Tier 3 (NFX-94): a diff too big or
    slow to read is the Tier 3 case. When only `[review] risk_paths` could not be read, the measured tier is compared."""
    tier_json, text_file = option(argv, "--tier-json"), option(argv, "--text-file")
    association = option(argv, "--author-association") or ""  # empty or missing: no override is honored
    if not tier_json or not text_file:
        print("Blocked: usage: review-gate.py verify-tier --tier-json FILE --text-file FILE "
              "[--author-association A]", file=sys.stderr)
        return 2
    try:
        with open(tier_json, encoding="utf-8") as f:
            computed, reasons = tier_result(json.load(f))
        with open(text_file, encoding="utf-8", errors="replace") as f:
            claims = body_tiers(f.read())
    except (OSError, ValueError) as e:
        print(f"Blocked: cannot check the review tier: {e}", file=sys.stderr)
        return 2
    why = "; ".join(reasons) or "no reasons given"
    if not claims:
        print("No Review-Tier line in the PR description; the tier is not checked (known hole, docs/review-gate.md).")
        print("Review tier checked.")
        return 0
    measured_nothing = False
    if any(r.startswith(TIER_FALLBACK) for r in reasons):
        # A "could not" Tier 2 is a floor, not a measurement. Compare against what the diff measured, if anything.
        measured = [int(m.group(1)) for m in (TIER_REASON.match(r) for r in reasons if not r.startswith(TIER_FALLBACK))
                    if m]
        # Nothing measured: tier.py never read the diff. Only Tier 3, the highest, is safe to accept (NFX-94).
        computed, measured_nothing = (max(measured), False) if measured else (3, True)
        if not measured and any(tier < 3 for _, tier, _, _ in claims):
            print(f"Blocked: tier.py could not measure this diff ({why}), so a Review-Tier below 3 cannot be "
                  "checked. Re-run the job; if it fails again, fix the cause or review at Tier 3.", file=sys.stderr)
            return 2
    for line, tier, over_computed, over_reason in claims:
        if tier >= computed:
            continue
        if over_computed is not None and over_computed >= computed:
            if association not in OVERRIDE_ASSOCIATIONS:
                print(f"Blocked: `{line}` overrides the computed Tier {computed}, but CI honors an override only "
                      f"from a PR author who is OWNER or MEMBER (this one: {association or 'unknown'}). Run the "
                      f"review at Tier {computed}, then attest again.", file=sys.stderr)
                return 2
            print(f"Override honored (self-asserted, not verified): Tier {tier} below the computed Tier {computed}, "
                  f"reason: {over_reason}")
            continue
        if over_computed is not None:
            print(f"Blocked: `{line}` overrides computed Tier {over_computed}, but CI computes Tier {computed} "
                  f"({why}). Run the review at Tier {computed}, then attest again.", file=sys.stderr)
        else:
            print(f"Blocked: `{line}` is below the computed Tier {computed} ({why}). Run the review at Tier "
                  f"{computed}, then attest again and paste the new lines.", file=sys.stderr)
        return 2
    if measured_nothing:
        print(f"Warning: tier.py could not measure the diff ({why}); every Review-Tier line claims Tier 3.")
    else:
        print(f"Review tier covers the computed Tier {computed}.")
    print("Review tier checked.")
    return 0


def run_check_ref(argv):
    """`run-check --ref REF` (CI): run the check command that REF holds, in this checkout. 0 passes, 2 blocks."""
    ref = option(argv, "--ref")
    if not ref:
        print("Blocked: usage: review-gate.py run-check --ref REF", file=sys.stderr)
        return 2
    cwd = os.getcwd()
    try:
        check = load_check(cwd, ref)
    except Exception as e:
        print(f"Blocked: cannot read the check command from {ref}:{CHECK_FILE}: {e}", file=sys.stderr)
        return 2
    if not check:
        print(f"Blocked: {ref} has no [check] command in {CHECK_FILE}. The PR that adds it cannot be verified; "
              "merge it by hand as the repo owner.", file=sys.stderr)
        return 2
    print(f"Running the check from {ref}: {check['command']}", flush=True)
    ok, why = run_check(cwd, check)
    if not ok:
        print(f"Blocked: the check command {why}.", file=sys.stderr)
        return 2
    print("Check passed.")
    return 0


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
    if dirs and not MCP_PR_CREATE.search(tool) and isinstance(command, str) and GIT_REPO_VARS.search(command):
        print(GIT_VARS_BLOCK, file=sys.stderr)
        return 2
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
    if sys.argv[1:2] == ["run-check"]:
        sys.exit(run_check_ref(sys.argv))
    if sys.argv[1:2] == ["verify-attestation"]:
        sys.exit(verify_attestation(sys.argv))
    if sys.argv[1:2] == ["verify-tier"]:
        sys.exit(verify_tier(sys.argv))
    if "--allow-branch" in sys.argv:
        sys.exit(allow_branch(sys.argv))
    sys.exit(mark(sys.argv) if "--mark" in sys.argv else gate())
