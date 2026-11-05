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


class PrePush(Base):
    def install(self, with_gate=True, branch=None):
        """Copy the hook and the gate into the repo the way a user would."""
        hooks = os.path.join(self.repo, ".claude", "hooks")
        if with_gate:
            os.makedirs(hooks)
            shutil.copy(GATE, hooks)
            shutil.copy(PARSER, hooks)
        dest = os.path.join(self.repo, ".git", "hooks", "pre-push")
        shutil.copy(PRE_PUSH, dest)
        os.chmod(dest, 0o755)
        if branch:
            self.sh("git", "branch", "-m", branch)
        return dest

    def run_hook(self, hook, env=None):
        env = env or {**os.environ, "GIT_DIR": os.path.join(self.repo, ".git")}
        return subprocess.run(["/bin/sh", hook, "origin", "x"], cwd=self.repo, input="", env=env,
                              capture_output=True, text=True).returncode

    def test_blocks_when_script_missing(self):
        self.assertEqual(self.run_hook(self.install(with_gate=False)), BLOCKED)

    def test_blocks_without_review(self):
        self.assertEqual(self.run_hook(self.install()), BLOCKED)

    def test_allows_after_review(self):
        hook = self.install()
        self.mark("--findings", "0")  # recorded after the install, so the tree hash includes .claude
        self.assertEqual(self.run_hook(hook), ALLOWED)

    def test_blocks_non_cesar_branch(self):
        hook = self.install(branch="main")
        self.mark("--findings", "0")
        self.assertEqual(self.run_hook(hook), BLOCKED)

    def test_blocks_when_python_missing(self):
        hook = self.install()
        self.mark("--findings", "0")
        self.assertEqual(self.run_hook(hook, {"PATH": "/nonexistent", "GIT_DIR": os.path.join(self.repo, ".git")}),
                         BLOCKED)


if __name__ == "__main__":
    unittest.main(verbosity=1)
