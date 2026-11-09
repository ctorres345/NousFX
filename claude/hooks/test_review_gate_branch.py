#!/usr/bin/env python3
"""Tests for the branch rule (NFX-119): the gate reads the prefix from local.toml, falls back to the shape, and
CI runs the base gate's `check-branch` on the PR branch.
Run: python3 -B claude/hooks/test_review_gate_branch.py

Throwaway git repos and a fake home under a temp dir. The gate is loaded in this process with user_home patched.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from test_review_gate_check import GATE, Base  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "review-gate.yml")


def load_gate():
    spec = importlib.util.spec_from_file_location("review_gate_branch_test", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Prefix(Base):
    """The local gate: exact with a prefix, shape-only without one, never blocked by a broken config."""

    def setUp(self):
        super().setUp()
        self.gate = load_gate()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".config", "nousfx"))
        patcher = mock.patch.object(self.gate, "user_home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_toml(self, text, mode="w"):
        with open(os.path.join(self.home, ".config", "nousfx", "local.toml"), mode) as f:
            f.write(text)

    def blocked(self, branch):
        return self.gate.branch_blocked(self.repo, branch)

    def test_with_a_prefix_only_that_prefix_passes(self):
        self.write_toml('[branch]\nprefix = "cesar"\n')
        self.assertIsNone(self.blocked("cesar/NFX-1"))
        for bad in ("ana/NFX-1", "fix-stuff", "cesar/NFX-1-shots", "cesar/nfx-1", "cesarx/NFX-1"):
            with self.subTest(branch=bad):
                msg = self.blocked(bad)
                self.assertIsNotNone(msg)
                self.assertIn("cesar/<JIRA-KEY>", msg)  # the expected form names my prefix
                self.assertIn("git branch -m cesar/<KEY>", msg)

    def test_without_config_any_prefix_of_the_right_shape_passes(self):
        self.assertIsNone(self.gate.branch_prefix())
        self.assertIsNone(self.blocked("ana/NFX-1"))
        self.assertIsNone(self.blocked("cesar-torres/AB2-10"))
        for bad in ("fix-stuff", "Ana/NFX-1", "ana/NFX-1\n", "ana/sub/NFX-1", "ana/NFX-1-shots", "/NFX-1"):
            with self.subTest(branch=bad):
                msg = self.blocked(bad)
                self.assertIsNotNone(msg)
                self.assertIn("<prefix>/<JIRA-KEY>", msg)

    def test_broken_configs_fall_back_to_the_shape(self):
        for text in ("not toml [", 'branch = "x"\n', '[branch]\nprefix = 3\n', '[branch]\nprefix = "Bad/Name"\n',
                     '[branch]\nprefix = "-x"\n', '[other]\nx = 1\n'):
            with self.subTest(text=text):
                self.write_toml(text)
                self.assertIsNone(self.gate.branch_prefix())
                self.assertIsNone(self.blocked("ana/NFX-1"))
                self.assertIsNotNone(self.blocked("fix-stuff"))

    def test_unreadable_file_falls_back(self):
        self.write_toml(b"\xff\xfe[branch]\n", mode="wb")  # not UTF-8
        self.assertIsNone(self.gate.branch_prefix())
        self.assertIsNone(self.blocked("ana/NFX-1"))
        os.remove(os.path.join(self.home, ".config", "nousfx", "local.toml"))
        os.makedirs(os.path.join(self.home, ".config", "nousfx", "local.toml"))  # a folder: open() fails
        self.assertIsNone(self.gate.branch_prefix())
        self.assertIsNone(self.blocked("ana/NFX-1"))

    def test_no_tomllib_falls_back(self):
        self.write_toml('[branch]\nprefix = "cesar"\n')
        with mock.patch.object(self.gate, "tomllib", None):
            self.assertIsNone(self.gate.branch_prefix())
            self.assertIsNone(self.blocked("ana/NFX-1"))
            self.assertIsNotNone(self.blocked("fix-stuff"))

    def test_allow_branch_exception_still_counts(self):
        self.write_toml('[branch]\nprefix = "cesar"\n')
        with open(self.gate.git_path("claude-branch-ok", self.repo), "w", encoding="utf-8") as f:
            f.write('{"branch": "fix-stuff", "reason": "approved"}')
        self.assertIsNone(self.blocked("fix-stuff"))
        self.assertIsNotNone(self.blocked("ana/NFX-1"))


class SameFileAsSync(unittest.TestCase):
    """The gate reads the local.toml that sync reads, both from the user record's home (not $HOME)."""

    def test_fake_home(self):
        import nousfx_prefix
        import nousfx_sync
        gate = load_gate()
        tmp = os.path.realpath(tempfile.mkdtemp(prefix="gate-home-test-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        record = types.SimpleNamespace(pw_dir=tmp)
        with mock.patch("pwd.getpwuid", return_value=record), mock.patch.dict(os.environ, {"HOME": "/nowhere"}):
            state = os.path.join(nousfx_sync.user_home(), ".config", "nousfx")  # sync's default state home
            self.assertEqual(gate.local_toml(), nousfx_prefix.path(state))
            os.makedirs(state)
            with open(nousfx_prefix.path(state), "w", encoding="utf-8") as f:
                f.write('[branch]\nprefix = "ana"\n')
            self.assertEqual(nousfx_prefix.resolve(state, Exception), ("ana", None))
            self.assertEqual(gate.branch_prefix(), "ana")

    def test_valid_prefix_rule_matches_sync(self):
        import nousfx_prefix
        self.assertEqual(load_gate().PREFIX_VALID.pattern, nousfx_prefix.VALID.pattern)


class CheckBranch(unittest.TestCase):
    def run_cb(self, *args):
        return subprocess.run([sys.executable, "-B", GATE, "check-branch", *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def test_exit_codes(self):
        for good in ("ana/NFX-1", "cesar/NFX-119", "a-b2/AB2-10"):
            r = self.run_cb(good)
            self.assertEqual(r.returncode, 0, r.stderr)
        for bad in ("fix-stuff", "Ana/NFX-1", "ana/NFX-1-shots", "ana/nfx-1", "ana/sub/NFX-1"):
            r = self.run_cb(bad)
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("<prefix>/<JIRA-KEY>", r.stderr)
            self.assertIn("branch-exception", r.stderr)

    def test_usage(self):
        self.assertEqual(self.run_cb().returncode, 1)
        self.assertEqual(self.run_cb("").returncode, 1)
        self.assertEqual(self.run_cb("a/B-1", "extra").returncode, 1)

    def test_shape_only_whatever_the_local_prefix(self):
        """CI cannot know whose branch it is: a local prefix never narrows check-branch."""
        gate = load_gate()
        with mock.patch.object(gate, "branch_prefix", return_value="cesar"):
            self.assertEqual(gate.check_branch(["x", "check-branch", "ana/NFX-1"]), 0)


def workflow_step():
    """The run script of the CI branch step, dedented, from the marker line to the next step."""
    with open(WORKFLOW, encoding="utf-8") as f:
        text = f.read()
    body = text.split("          # NFX-119 branch check\n")[1].split("\n      - name:")[0]
    return "\n".join(line[10:] for line in body.splitlines()) + "\n"


class Workflow(unittest.TestCase):
    """Runs the CI step's script with bash against a repo whose origin/main holds a given gate."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="gate-ci-branch-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "claude", "hooks"))
        self.script = os.path.join(self.tmp, "step.sh")
        with open(self.script, "w", encoding="utf-8") as f:
            f.write(workflow_step())
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.repo, check=True,
                       capture_output=True)

    def base_gate(self, source=GATE):
        shutil.copy(source, os.path.join(self.repo, "claude", "hooks", "review-gate.py"))
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def step(self, head, labels="[]", association="CONTRIBUTOR"):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(BASE_REF="main", HEAD_REF=head, AUTHOR_ASSOCIATION=association, LABELS=labels,
                   RUNNER_TEMP=self.tmp)
        return subprocess.run(["bash", self.script], cwd=self.repo, env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def test_bad_branch_fails_with_the_expected_form(self):
        self.base_gate()
        r = self.step("fix-stuff")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("::error::", r.stdout)
        self.assertIn("<prefix>/<JIRA-KEY>", r.stdout)

    def test_good_branch_passes(self):
        self.base_gate()
        r = self.step("ana/NFX-3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fits", r.stdout)

    def test_label_skips_for_owner_and_member_only(self):
        self.base_gate()
        labels = '[\n  "bug",\n  "branch-exception"\n]'
        for assoc in ("OWNER", "MEMBER"):
            r = self.step("fix-stuff", labels, assoc)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("skipped", r.stdout)
        for assoc in ("COLLABORATOR", "CONTRIBUTOR", "NONE", "owner", ""):
            r = self.step("fix-stuff", labels, assoc)
            self.assertEqual(r.returncode, 1, assoc)
            self.assertIn("::warning::", r.stdout)
            self.assertIn("::error::", r.stdout)

    def test_other_labels_and_bad_json_do_not_skip(self):
        self.base_gate()
        for labels in ('["branch-exception-x"]', '["x\\"branch-exception\\""]', "not json", ""):
            self.assertEqual(self.step("fix-stuff", labels, "OWNER").returncode, 1, labels)

    def test_base_gate_without_check_branch_warns(self):
        old = os.path.join(self.tmp, "old-gate.py")
        with open(old, "w", encoding="utf-8") as f:
            f.write('import sys\nif sys.argv[1:2] == ["verify-tier"]:\n    pass\n')
        self.base_gate(old)
        r = self.step("fix-stuff")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("::warning::", r.stdout)
        self.assertIn("no check-branch", r.stdout)

    def test_trigger_reruns_on_labels_and_inputs_come_through_env(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("types: [opened, synchronize, reopened, edited, labeled, unlabeled]", text)
        step = text.split("Check the branch name with the base branch's gate")[1].split("\n      - name:")[0]
        self.assertIn("HEAD_REF: ${{ github.head_ref }}", step)
        self.assertIn('git show "origin/$BASE_REF:claude/hooks/review-gate.py"', step)
        run = step.split("run: |")[1]
        self.assertNotIn("${{", run)  # nothing from the PR is interpolated into the shell
        # The branch step runs before the attestation step, so a bad name fails fast.
        self.assertLess(text.index("NFX-119 branch check"), text.index("Verify the review attestation"))


if __name__ == "__main__":
    unittest.main()
