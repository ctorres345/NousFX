#!/usr/bin/env python3
"""Tests for the plain `check` entry and the portable pre-push hook.
Run: python3 -B claude/hooks/test_review_gate_check.py

Each test uses a throwaway git repo under a temp dir. BLOCKED is exit 2, ALLOWED exit 0.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "review-gate.py")
PARSER = os.path.join(HERE, "pr_create_parser.py")
PRE_PUSH = os.path.join(os.path.dirname(HERE), "githooks", "pre-push")
BLOCKED, ALLOWED = 2, 0


def attested_body(root):
    """Write <root>/pr-body.md with a `Review-Gate:` line for HEAD's tree of every repo directly under root
    (NFX-85: a create needs one) and return its path."""
    lines = []
    for name in sorted(os.listdir(root)):
        r = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=os.path.join(root, name),
                           capture_output=True, text=True) if os.path.isdir(os.path.join(root, name, ".git")) else None
        if r and r.returncode == 0:
            lines.append("Review-Gate: " + r.stdout.strip())
    path = os.path.join(root, "pr-body.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("## Summary\n\n" + "\n".join(lines) + "\n")
    return path


def with_body(command, root):
    """command with every literal `gh pr create` given --body-file of attested_body(root)."""
    return command.replace("gh pr create", f"gh pr create --body-file {attested_body(root)}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="gate-check-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.sh("git", "init", "-q", "-b", "cesar/DEM-1")
        with open(os.path.join(self.repo, "f.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        os.makedirs(os.path.join(self.repo, ".nousfx"))  # the check command --mark runs (NFX-54)
        with open(os.path.join(self.repo, ".nousfx", "project.toml"), "w", encoding="utf-8") as f:
            f.write('[check]\ncommand = "exit 0"\n')
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    def sh(self, *args):
        subprocess.run(args, cwd=self.repo, check=True, capture_output=True)

    def mark(self, *args):
        return subprocess.run([sys.executable, "-B", GATE, "--mark", *args], cwd=self.repo,
                              capture_output=True, text=True)

    def check(self, *args, gate=GATE):
        return subprocess.run([sys.executable, "-B", gate, "check", *args], cwd=self.tmp,
                              capture_output=True, text=True)

    def check_create(self, cwd=None):
        return self.check("--cwd", cwd or self.repo, "--command", with_body("gh pr create", self.tmp)).returncode


class Check(Base):
    def test_blocked_without_review(self):
        self.assertEqual(self.check_create(), BLOCKED)

    def test_allowed_after_review(self):
        self.mark("--findings", "0")
        self.assertEqual(self.check_create(), ALLOWED)

    def test_open_findings_block(self):
        self.mark("--findings", "2")
        self.assertEqual(self.check_create(), BLOCKED)

    def test_blocked_on_wrong_branch_name(self):
        self.mark("--findings", "0")
        self.sh("git", "branch", "-m", "claude/x")
        self.assertEqual(self.check_create(), BLOCKED)

    def marker_file(self):
        return os.path.join(self.repo, ".git", "claude-review-ok")

    def test_mark_records_the_branch(self):
        self.mark("--findings", "0")
        with open(self.marker_file(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["branch"], "cesar/DEM-1")

    def test_mark_from_another_branch_does_not_cover_a_new_branch_with_no_commits(self):
        """After a merge, main's tree equals the marked tree; a new branch cut from it must not pass (NFX-71)."""
        self.mark("--findings", "0")
        self.sh("git", "checkout", "-q", "-b", "cesar/DEM-2")  # zero commits: same tree as the marked one
        r = self.check("--cwd", self.repo, "--command", "gh pr create")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn('recorded on branch "cesar/DEM-1"', r.stderr)
        self.sh("git", "checkout", "-q", "cesar/DEM-1")
        self.assertEqual(self.check_create(), ALLOWED)

    def test_mark_is_refused_on_a_detached_head(self):
        self.sh("git", "checkout", "-q", "--detach")
        r = self.mark("--findings", "0")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("HEAD is detached", r.stderr)
        self.assertFalse(os.path.exists(self.marker_file()))

    def test_a_create_from_a_detached_head_says_so(self):
        self.mark("--findings", "0")
        self.sh("git", "checkout", "-q", "--detach")
        r = self.check("--cwd", self.repo, "--command", "gh pr create")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("HEAD is detached", r.stderr)

    def test_a_branch_exception_matches_when_a_tag_shares_the_name(self):
        self.sh("git", "branch", "-m", "claude/x")
        self.sh("git", "tag", "claude/x")
        subprocess.run([sys.executable, "-B", GATE, "--allow-branch", "ok"], cwd=self.repo, check=True,
                       capture_output=True)
        self.mark("--findings", "0")
        self.assertEqual(self.check_create(), ALLOWED)

    def test_a_tag_with_the_branch_name_does_not_change_the_recorded_name(self):
        self.sh("git", "tag", "cesar/DEM-1")  # makes rev-parse --abbrev-ref answer heads/cesar/DEM-1
        self.mark("--findings", "0")
        with open(self.marker_file(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["branch"], "cesar/DEM-1")
        self.assertEqual(self.check_create(), ALLOWED)

    def test_a_deleted_marked_branch_gives_no_re_mark_shortcut(self):
        """`gh pr merge -d` deletes the marked branch; a new branch from main must still be sent to the review."""
        self.mark("--findings", "0")
        self.sh("git", "checkout", "-q", "-b", "cesar/DEM-2")
        self.sh("git", "branch", "-D", "cesar/DEM-1")
        r = self.check("--cwd", self.repo, "--command", "gh pr create")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("review-loop", r.stderr)
        self.assertNotIn("same --mark", r.stderr)

    def test_a_skip_from_another_branch_does_not_cover_this_one(self):
        self.mark("--skip", "user approved")
        self.sh("git", "checkout", "-q", "-b", "cesar/DEM-2")
        self.assertEqual(self.check_create(), BLOCKED)

    def test_an_old_mark_without_a_branch_blocks_with_a_clear_message(self):
        self.mark("--findings", "0")
        with open(self.marker_file(), encoding="utf-8") as f:
            marker = json.load(f)
        marker.pop("branch", None)
        with open(self.marker_file(), "w", encoding="utf-8") as f:
            json.dump(marker, f)
        r = self.check("--cwd", self.repo, "--command", "gh pr create")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("has no branch", r.stderr)

    def test_command_that_is_not_a_create_is_allowed(self):
        self.assertEqual(self.check("--cwd", self.repo, "--command", "git status").returncode, ALLOWED)

    def test_bad_input_fails_closed(self):
        for args in ([], ["--cwd", self.repo], ["--command", "gh pr create"],
                     ["--cwd", self.repo, "--command"],
                     ["--cwd", os.path.join(self.tmp, "nope"), "--command", "gh pr create"],
                     ["--cwd", self.tmp, "--command", "gh pr create"]):  # not a git repo
            self.assertEqual(self.check(*args).returncode, BLOCKED, args)

    def test_allow_non_repo_judges_the_command_not_the_folder(self):
        """agy has no per-command folder: a workspace that is not a repo must not block every command,
        but a create that cd's into a repo with no review is still blocked."""
        flag = "--allow-non-repo"
        self.assertEqual(self.check("--cwd", self.tmp, "--command", "git status", flag).returncode, ALLOWED)
        self.assertEqual(self.check("--cwd", self.tmp, "--command", f"cd {self.repo} && gh pr create",
                                    flag).returncode, BLOCKED)
        self.mark("--findings", "0")
        self.assertEqual(self.check("--cwd", self.tmp, "--command", with_body(f"cd {self.repo} && gh pr create", self.tmp),
                                    flag).returncode, ALLOWED)
        self.assertEqual(self.check("--cwd", self.repo, "--command", with_body("gh pr create", self.tmp), flag).returncode, ALLOWED)

    def test_allow_non_repo_still_fails_closed_on_bad_input(self):
        flag = "--allow-non-repo"
        for args in ([flag], ["--cwd", self.tmp, flag],
                     ["--cwd", os.path.join(self.tmp, "nope"), "--command", "git status", flag]):
            self.assertEqual(self.check(*args).returncode, BLOCKED, args)

    def test_allow_non_repo_blocks_when_git_cannot_run(self):
        """Lenient only for a real "not a git repository" answer; a missing git is not that."""
        env = {**os.environ, "PATH": os.path.join(self.tmp, "no-bin")}
        r = subprocess.run([sys.executable, "-B", GATE, "check", "--cwd", self.repo, "--command", "gh pr create",
                            "--allow-non-repo"], cwd=self.tmp, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, BLOCKED)

    def test_git_repo_variables_in_a_create_command_block(self):
        """GIT_DIR / GIT_WORK_TREE in the command can point gh at another repo than the one checked."""
        self.mark("--findings", "0")
        for command in ("GIT_DIR=/x/.git GIT_WORK_TREE=/x gh pr create", "env GIT_DIR=/x/.git gh pr create",
                        "export GIT_DIR=/x/.git; gh pr create", "GIT_WORK_TREE=/x gh pr create --fill"):
            self.assertEqual(self.check("--cwd", self.repo, "--command", command).returncode, BLOCKED, command)
        for command in ("GIT_DIR=/x/.git git log", "git status", with_body("gh pr create", self.tmp)):
            self.assertEqual(self.check("--cwd", self.repo, "--command", command).returncode, ALLOWED, command)

    def test_allow_non_repo_blocks_an_mcp_create_outside_a_repo(self):
        args = ("--cwd", self.tmp, "--command", "x", "--tool", "mcp__github__create_pull_request",
                "--allow-non-repo")
        self.assertEqual(self.check(*args).returncode, BLOCKED)

    def test_a_command_may_start_with_dashes(self):
        self.assertEqual(self.check("--cwd", self.repo, "--command", "--version").returncode, ALLOWED)

    def test_tool_option_picks_the_parser(self):
        """The same obfuscated create is caught as PowerShell and missed as bash (known parser limit)."""
        obfuscated = "g`h pr create"
        self.assertEqual(self.check("--cwd", self.repo, "--command", obfuscated, "--tool", "PowerShell").returncode,
                         BLOCKED)
        self.assertEqual(self.check("--cwd", self.repo, "--command", "gh pr create", "--tool", "Bash").returncode,
                         BLOCKED)

    def test_tool_option_accepts_an_mcp_create_tool(self):
        args = ("--cwd", self.repo, "--command", "x", "--tool", "mcp__github__create_pull_request")
        self.assertEqual(self.check(*args).returncode, BLOCKED)
        self.mark("--findings", "0")
        r = self.check(*args)
        self.assertEqual(r.returncode, BLOCKED)  # no --body-file: the MCP call has no body (NFX-85)
        self.assertIn("passes no PR body", r.stderr)
        self.assertEqual(self.check(*args, "--body-file", attested_body(self.tmp)).returncode, ALLOWED)
        self.assertEqual(self.check(*args, "--body-file", os.path.join(self.tmp, "nope")).returncode, BLOCKED)

    def test_missing_parser_module_fails_closed(self):
        d = os.path.join(self.tmp, "hooks")
        os.makedirs(d)
        shutil.copy(GATE, d)
        r = self.check("--cwd", self.repo, "--command", "gh pr create", gate=os.path.join(d, "review-gate.py"))
        self.assertEqual(r.returncode, BLOCKED)


ZERO = "0" * 40
ONE = "1" * 40
TWO = "2" * 40
MAIN = "refs/heads/main " + ONE + " refs/heads/main " + TWO + "\n"


class PrePush(Base):
    def install(self, with_gate=True, branch=None):
        """Copy the hook into the repo and the gate into a folder OUTSIDE the repo, as the docs say."""
        self.gate_dir = os.path.join(self.tmp, "gate-home")
        if with_gate:
            os.makedirs(self.gate_dir)
            shutil.copy(GATE, self.gate_dir)
            shutil.copy(PARSER, self.gate_dir)
        dest = os.path.join(self.repo, ".git", "hooks", "pre-push")
        shutil.copy(PRE_PUSH, dest)
        os.chmod(dest, 0o755)
        if branch:
            self.sh("git", "branch", "-m", branch)
        return dest

    def head(self):
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def feature(self):
        return "refs/heads/cesar/DEM-1 " + self.head() + " refs/heads/cesar/DEM-1 " + ZERO + "\n"

    def line(self, branch, sha):
        return f"refs/heads/{branch} {sha} refs/heads/{branch} {ZERO}\n"

    def run_hook(self, hook, stdin=None, env=None):
        stdin = self.feature() if stdin is None else stdin
        env = env or {**os.environ, "GIT_DIR": os.path.join(self.repo, ".git"),
                      "REVIEW_GATE_DIR": self.gate_dir}
        return subprocess.run(["/bin/sh", hook, "origin", "x"], cwd=self.repo, input=stdin, env=env,
                              capture_output=True, text=True).returncode

    def test_blocks_when_script_missing(self):
        self.assertEqual(self.run_hook(self.install(with_gate=False)), BLOCKED)

    def test_blocks_without_review(self):
        self.assertEqual(self.run_hook(self.install()), BLOCKED)

    def test_allows_after_review(self):
        hook = self.install()
        self.mark("--findings", "0")
        self.assertEqual(self.run_hook(hook), ALLOWED)

    def test_home_lookup_ignores_a_hostile_pythonpath(self):
        """A sitecustomize on PYTHONPATH that fakes the user record must not choose which gate runs."""
        hook = self.install()
        fake_home = os.path.join(self.tmp, "fake-home")
        os.makedirs(os.path.join(fake_home, ".claude", "hooks"))
        ran = os.path.join(self.tmp, "fake-gate-ran")
        with open(os.path.join(fake_home, ".claude", "hooks", "review-gate.py"), "w") as f:
            f.write("import sys\nopen(%r, 'w').close()\nsys.exit(0)\n" % ran)  # a permissive gate that leaves a trace
        shadow = os.path.join(self.tmp, "shadow")
        os.makedirs(shadow)
        with open(os.path.join(shadow, "sitecustomize.py"), "w") as f:  # runs at startup, unless -I
            f.write("import pwd\n\nclass R:\n    pw_dir = %r\n\npwd.getpwuid = lambda uid: R()\n" % fake_home)
        env = {k: v for k, v in os.environ.items() if k != "REVIEW_GATE_DIR"}
        env.update({"GIT_DIR": os.path.join(self.repo, ".git"), "PYTHONPATH": shadow})
        self.run_hook(hook, env=env)  # the exit code depends on the gate installed in the real home; only the fake one matters
        self.assertFalse(os.path.exists(ran), "the pre-push hook ran a gate chosen through PYTHONPATH")

    def test_ignores_gate_copy_inside_the_repo(self):
        hook = self.install()
        inside = os.path.join(self.repo, "claude", "hooks")
        os.makedirs(inside)
        with open(os.path.join(inside, "review-gate.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nsys.exit(0)\n")
        shutil.copy(PARSER, inside)
        self.assertEqual(self.run_hook(hook), BLOCKED)

    def test_blocks_non_cesar_branch(self):
        hook = self.install(branch="main")
        self.mark("--findings", "0")
        stdin = "refs/heads/claude/x " + ONE + " refs/heads/claude/x " + ZERO + "\n"
        self.assertEqual(self.run_hook(hook, stdin), BLOCKED)

    def test_blocks_when_python_missing(self):
        hook = self.install()
        self.mark("--findings", "0")
        env = {"PATH": "/nonexistent", "GIT_DIR": os.path.join(self.repo, ".git"),
               "REVIEW_GATE_DIR": self.gate_dir}
        self.assertEqual(self.run_hook(hook, env=env), BLOCKED)

    def test_allows_push_of_default_branch_without_review(self):
        self.assertEqual(self.run_hook(self.install(branch="main"), MAIN), ALLOWED)

    def test_allows_delete_and_tag_pushes(self):
        hook = self.install()
        self.assertEqual(self.run_hook(hook, "(delete) " + ZERO + " refs/heads/cesar/DEM-1 " + TWO + "\n"), ALLOWED)
        self.assertEqual(self.run_hook(hook, "refs/tags/v1 " + ONE + " refs/tags/v1 " + ZERO + "\n"), ALLOWED)

    def test_blocks_when_any_ref_is_a_feature_branch(self):
        self.assertEqual(self.run_hook(self.install(), MAIN + self.feature()), BLOCKED)

    def test_judges_the_pushed_commit_not_the_checked_out_branch(self):
        hook = self.install()
        self.mark("--findings", "0")  # covers the tree of cesar/DEM-1
        reviewed = self.head()
        self.sh("git", "checkout", "-q", "-b", "cesar/DEM-2")
        with open(os.path.join(self.repo, "g.txt"), "w", encoding="utf-8") as f:
            f.write("y")
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "two")
        unreviewed = self.head()
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-1", reviewed)), ALLOWED)
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-2", unreviewed)), BLOCKED)
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-1", unreviewed)), BLOCKED)

    def test_a_mark_does_not_cover_a_push_of_another_branch_with_the_same_tree(self):
        hook = self.install()
        self.mark("--findings", "0")  # recorded on cesar/DEM-1
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-2", self.head())), BLOCKED)

    def test_blocks_a_sha_that_is_not_a_commit(self):
        hook = self.install()
        self.mark("--findings", "0")
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-1", ONE)), BLOCKED)

    def test_judges_the_pushed_branch_name(self):
        hook = self.install()
        self.mark("--findings", "0")
        self.assertEqual(self.run_hook(hook, self.line("claude/x", self.head())), BLOCKED)

    def shots_commit(self, files, branch="cesar/DEM-1-shots"):
        self.sh("git", "checkout", "-q", "-b", branch)
        for path, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(self.repo, path)), exist_ok=True)
            with open(os.path.join(self.repo, path), "w", encoding="utf-8") as f:
                f.write(text)
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "DEM-1: shots")
        return self.head()

    def test_a_media_only_shots_branch_passes_without_a_review(self):
        hook = self.install()
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")
        self.sh("git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        sha = self.shots_commit({"shots/DEM-1/a.png": "x"})
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-1-shots", sha)), ALLOWED)

    def test_a_shots_branch_that_carries_code_is_still_blocked(self):
        hook = self.install()
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")
        self.sh("git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        sha = self.shots_commit({"shots/DEM-1/a.png": "x", "evil.py": "x"})
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-1-shots", sha)), BLOCKED)

    def test_a_shots_branch_is_blocked_by_a_gate_that_does_not_know_check_shots(self):
        hook = self.install()
        with open(os.path.join(self.gate_dir, "review-gate.py"), "w", encoding="utf-8") as f:
            # An old gate: it exits 0 and prints nothing for a command it does not know, and blocks a plain check.
            f.write("import sys\nsys.exit(2 if sys.argv[1:2] == ['check'] else 0)\n")
        sha = self.shots_commit({"shots/DEM-1/a.png": "x"})
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-1-shots", sha)), BLOCKED)

    def test_refuses_a_gate_dir_inside_a_work_tree(self):
        hook = self.install()
        self.mark("--findings", "0")
        inside = os.path.join(self.repo, "gate-copy")
        os.makedirs(inside)
        shutil.copy(GATE, inside)
        shutil.copy(PARSER, inside)
        env = {**os.environ, "GIT_DIR": os.path.join(self.repo, ".git"), "REVIEW_GATE_DIR": inside}
        r = subprocess.run(["/bin/sh", hook, "origin", "x"], cwd=self.repo, input=self.feature(), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("work tree", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
