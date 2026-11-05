#!/usr/bin/env python3
"""Tests for the plain `check` entry and the portable pre-push hook.
Run: python3 -B claude/hooks/test_review_gate_check.py

Each test uses a throwaway git repo under a temp dir. BLOCKED is exit 2, ALLOWED exit 0.
"""
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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="gate-check-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.sh("git", "init", "-q", "-b", "cesar/DEM-1")
        with open(os.path.join(self.repo, "f.txt"), "w", encoding="utf-8") as f:
            f.write("x")
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
        return self.check("--cwd", cwd or self.repo, "--command", "gh pr create").returncode


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

    def test_command_that_is_not_a_create_is_allowed(self):
        self.assertEqual(self.check("--cwd", self.repo, "--command", "git status").returncode, ALLOWED)

    def test_bad_input_fails_closed(self):
        for args in ([], ["--cwd", self.repo], ["--command", "gh pr create"],
                     ["--cwd", self.repo, "--command"],
                     ["--cwd", os.path.join(self.tmp, "nope"), "--command", "gh pr create"],
                     ["--cwd", self.tmp, "--command", "gh pr create"]):  # not a git repo
            self.assertEqual(self.check(*args).returncode, BLOCKED, args)

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

    def test_blocks_a_sha_that_is_not_a_commit(self):
        hook = self.install()
        self.mark("--findings", "0")
        self.assertEqual(self.run_hook(hook, self.line("cesar/DEM-1", ONE)), BLOCKED)

    def test_judges_the_pushed_branch_name(self):
        hook = self.install()
        self.mark("--findings", "0")
        self.assertEqual(self.run_hook(hook, self.line("claude/x", self.head())), BLOCKED)

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
