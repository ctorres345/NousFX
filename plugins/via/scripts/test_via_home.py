#!/usr/bin/env python3
"""Tests for via_home.py: the interpreter flags that keep the anchor fixed, and the fail-closed work-tree check.

Run: python3 -B plugins/via/scripts/test_via_home.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
OUTBOUND = os.path.join(HERE, "via_outbound.py")
SETUP = os.path.join(HERE, "via_setup.py")
sys.path.insert(0, HERE)
import via_home  # noqa: E402


def done(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class HostileEnvironmentTest(unittest.TestCase):
    """A repo's committed .claude/settings.json `env` block can set PYTHONPATH. `-I` must ignore it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.evil = os.path.join(tmp.name, "evil")
        self.marker = os.path.join(tmp.name, "ran")
        os.makedirs(self.evil)
        with open(os.path.join(self.evil, "sitecustomize.py"), "w") as f:
            f.write("import pwd\n"
                    "open(%r, 'w').close()\n"
                    "_real = pwd.getpwuid\n"
                    "def _evil(uid):\n"
                    "    r = _real(uid)\n"
                    "    return pwd.struct_passwd((r.pw_name, r.pw_passwd, r.pw_uid, r.pw_gid, r.pw_gecos, %r, r.pw_shell))\n"
                    "pwd.getpwuid = _evil\n" % (self.marker, tmp.name))
        self.env = {"PATH": os.environ["PATH"], "PYTHONPATH": self.evil}

    def run_script(self, flags, script):
        return subprocess.run([sys.executable, *flags, script, "--help"], capture_output=True, text=True, env=self.env)

    def test_the_shim_is_loaded_without_i_so_this_test_can_fail(self):
        self.assertEqual(self.run_script(["-B"], OUTBOUND).returncode, 0)
        self.assertTrue(os.path.exists(self.marker))

    def test_a_hostile_pythonpath_sitecustomize_is_not_loaded_under_i(self):
        for script in (OUTBOUND, SETUP):
            with self.subTest(script=os.path.basename(script)):
                self.assertEqual(self.run_script(["-I", "-B"], script).returncode, 0)
                self.assertFalse(os.path.exists(self.marker))

    def test_the_anchor_is_not_moved_under_i(self):
        code = "import sys; sys.path.insert(0, %r); import via_home; print(via_home.anchor())" % HERE
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(r.stdout.strip().startswith(os.path.realpath(os.path.dirname(self.evil))))
        self.assertFalse(os.path.exists(self.marker))

    def test_via_setup_finds_its_sibling_with_no_script_folder_on_the_path(self):
        r = subprocess.run([sys.executable, "-I", "-B", SETUP, "--help"], capture_output=True, text=True, env={})
        self.assertEqual(r.returncode, 0, r.stderr)


class NoPwdPlatformTest(unittest.TestCase):
    """Windows has no pwd module: the home then comes from USERPROFILE and the module must still import."""

    def run_without_pwd(self, profile):
        code = ("import sys; sys.modules['pwd'] = None; sys.path.insert(0, %r); import via_home; "
                "print(via_home.home_dir()); print(via_home.anchor())" % HERE)
        env = {"PATH": os.environ["PATH"], "USERPROFILE": profile}
        return subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, env=env)

    def test_the_module_imports_and_the_home_comes_from_userprofile(self):
        with tempfile.TemporaryDirectory() as d:
            profile = os.path.realpath(d)
            r = self.run_without_pwd(profile)
            self.assertEqual(r.returncode, 0, r.stderr)
            home, anchor = r.stdout.split("\n")[:2]
            self.assertEqual(home, profile)
            self.assertEqual(anchor, os.path.join(profile, ".config", "nousfx"))


class LegacyConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.realpath(tmp.name)
        self.legacy_dir = os.path.join(self.home, ".claude")
        os.makedirs(self.legacy_dir)
        with open(os.path.join(self.legacy_dir, "via.json"), "w") as f:
            json.dump({"version": 1, "outbound_allowlist": {}}, f)
        patcher = mock.patch.object(via_home, "home_dir", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def init_repo(self, folder):
        subprocess.run(["git", "-C", folder, "init", "-q"], check=True, capture_output=True)

    def test_a_legacy_config_outside_a_work_tree_is_still_read(self):
        self.assertEqual(via_home.config_path(), os.path.join(self.legacy_dir, "via.json"))

    def test_a_legacy_config_inside_a_git_work_tree_is_refused(self):
        self.init_repo(self.legacy_dir)  # a dotfiles repo pulled from a remote
        with self.assertRaises(via_home.Refused):
            via_home.config_path()

    def test_migrate_refuses_a_legacy_config_inside_a_git_work_tree(self):
        self.init_repo(self.legacy_dir)
        with self.assertRaises(via_home.Refused):
            via_home.migrate()
        self.assertFalse(os.path.exists(via_home.default_config()))


class WorkTreeCheckFailsClosedTest(unittest.TestCase):
    def check(self, result=None, error=None):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(via_home.subprocess, "run", side_effect=error, return_value=result):
                return via_home._inside_git_worktree(d)

    def test_a_clean_true_means_inside(self):
        self.assertTrue(self.check(done(0, "true\n")))

    def test_a_clean_false_means_outside(self):
        self.assertFalse(self.check(done(0, "false\n")))

    def test_a_clean_not_a_git_repository_means_outside(self):
        self.assertFalse(self.check(done(128, "", "fatal: not a git repository (or any of the parent directories): .git\n")))

    def test_any_other_git_outcome_is_refused(self):
        for r in (done(128, "", "fatal: detected dubious ownership in repository"),
                  done(1, "", "fatal: boom"),
                  done(0, "", ""),
                  done(0, "maybe\n"),
                  done(127, "", "git: not found")):
            with self.subTest(returncode=r.returncode, stderr=r.stderr):
                with self.assertRaises(via_home.Refused):
                    self.check(r)

    def test_git_missing_or_timing_out_is_refused(self):
        for e in (FileNotFoundError("git"), subprocess.TimeoutExpired("git", 10)):
            with self.subTest(error=type(e).__name__):
                with self.assertRaises(via_home.Refused):
                    self.check(error=e)


if __name__ == "__main__":
    unittest.main()
