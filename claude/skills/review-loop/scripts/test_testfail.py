#!/usr/bin/env python3
"""Tests for testfail.py (NFX-190), the script that checks new tests fail without the change.
Run: python3 -B claude/skills/review-loop/scripts/test_testfail.py

Seam: the script's CLI, its printed verdict and exit code. Each test builds a throwaway git repo under a temp dir whose
check command is one line (`python3 -B -m unittest -q`); the real repo and HOME are never touched.
"""
import atexit
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TESTFAIL = os.path.join(HERE, "testfail.py")
sys.dont_write_bytecode = True
# NFX-217: testfail.py and run_checks.main take a suite slot; keep these runs off the machine-wide slots.
_SLOTS = tempfile.mkdtemp(prefix="nousfx-test-slots-")
atexit.register(shutil.rmtree, _SLOTS, True)
os.environ.setdefault("NOUSFX_SUITE_SLOT_DIR", _SLOTS)

CHECK = "python3 -B -m unittest -q"
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
COVERING = ("import unittest\nimport calc\n\n\nclass T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n\n\n"
            "if __name__ == '__main__':\n    unittest.main()\n")
UNRELATED = ("import unittest\n\n\nclass T(unittest.TestCase):\n"
             "    def test_truth(self):\n        self.assertTrue(True)\n")
UNRELATED_MORE = UNRELATED + "\n    def test_more(self):\n        self.assertEqual(1, 1)\n"  # passes either way
VERDICT = re.compile(r"^Tests-Fail-Without: (\S+)", re.M)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "cesar/NFX-1")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.write(".nousfx/project.toml", f'[check]\ncommand = "{CHECK}"\ntimeout = 60\n')
        self.write("calc.py", BUGGY)
        self.write("test_truth.py", UNRELATED)
        self.write("README.md", "hello\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "NFX-1: base")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def commit_base(self, rel, text):
        """Add a file to the base: commit it and move self.base there."""
        self.write(rel, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "NFX-1: more base")
        self.base = self.git("rev-parse", "HEAD")

    def fix(self):
        """The change most tests use: a production fix and a test that covers it."""
        self.write("calc.py", FIXED)
        self.write("test_calc.py", COVERING)

    def tf(self, *args, env=None):
        return subprocess.run([sys.executable, "-B", TESTFAIL, *args], cwd=self.repo, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=120, env=env)

    def run_base(self, env=None):
        return self.tf("run", "--base", self.base, env=env)

    def verdict(self, r):
        m = VERDICT.search(r.stdout)
        self.assertIsNotNone(m, f"no verdict line\nstdout: {r.stdout}\nstderr: {r.stderr}")
        return m.group(1)


class Verdicts(Base):
    def test_a_fix_with_a_covering_test_prints_yes_and_names_the_failing_test(self):
        self.fix()
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")
        self.assertIn("test_add", r.stdout)

    def test_a_test_that_passes_without_the_change_prints_no_and_exits_1(self):
        self.write("calc.py", FIXED)
        self.write("test_truth.py", UNRELATED_MORE)
        r = self.run_base()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "no")

    def test_a_failing_baseline_prints_baseline_fail_never_yes(self):
        self.write("calc.py", FIXED)
        self.write("test_calc.py", COVERING.replace("(2, 3), 5", "(2, 3), 6"))
        r = self.run_base()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "baseline-fail")

    def test_a_restore_that_only_breaks_the_build_prints_build(self):
        self.write("newmod.py", "VALUE = 1\n")
        self.write("test_newmod.py", "import unittest\nimport newmod\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_value(self):\n        self.assertEqual(newmod.VALUE, 1)\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "build")

    def test_a_docs_only_diff_prints_n_a_and_exits_0(self):
        self.write("README.md", "changed\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "n/a")

    def test_a_tests_only_diff_prints_n_a_and_exits_0(self):
        self.write("test_truth.py", UNRELATED_MORE)
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "n/a")

    def test_a_production_only_diff_prints_n_a_and_exits_0(self):
        self.write("calc.py", FIXED)
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "n/a")


class Snapshot:
    """What `git status`, the index bytes and every file's content say about a repo."""

    def __init__(self, repo):
        self.status = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=repo, capture_output=True,
                                     text=True).stdout
        with open(os.path.join(repo, ".git", "index"), "rb") as f:
            self.index = hashlib.sha256(f.read()).hexdigest()
        self.files = {}
        for folder, dirs, names in os.walk(repo):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in names:
                path = os.path.join(folder, name)
                with open(path, "rb") as f:
                    self.files[os.path.relpath(path, repo)] = hashlib.sha256(f.read()).hexdigest()

    def __eq__(self, other):
        return (self.status, self.index, self.files) == (other.status, other.index, other.files)


class Purity(Base):
    def test_the_working_tree_and_index_are_identical_before_and_after_a_run(self):
        self.fix()
        self.write(".gitignore", "secret.env\n")
        self.write("secret.env", "TOKEN=1\n")
        before = Snapshot(self.repo)
        self.assertEqual(self.run_base().returncode, 0)
        self.assertTrue(before == Snapshot(self.repo))

    def test_the_run_leaves_no_temporary_copy_behind(self):
        self.fix()
        scratch = tempfile.mkdtemp(prefix="tf-tmp-")
        self.addCleanup(shutil.rmtree, scratch, True)
        env = {**os.environ, "TMPDIR": scratch, "TEMP": scratch, "TMP": scratch}
        r = self.run_base(env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.listdir(scratch), [])

    def test_a_gitignored_file_is_not_in_the_copy(self):
        self.fix()
        self.write(".gitignore", "secret.env\n")
        self.write("secret.env", "TOKEN=1\n")
        self.write("test_secret.py", "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_no_secret(self):\n        self.assertFalse(os.path.exists('secret.env'))\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # a copy that held it would be baseline-fail


class Attest(Base):
    def test_attest_prints_the_line_a_run_recorded(self):
        self.fix()
        ran = self.run_base()
        r = self.tf("attest", "--base", self.base)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.verdict(r), "yes")
        self.assertEqual(r.stdout.splitlines()[0], ran.stdout.splitlines()[0])

    def test_attest_refuses_when_the_tree_changed_after_run(self):
        self.fix()
        self.run_base()
        self.write("calc.py", FIXED + "# edit\n")
        r = self.tf("attest", "--base", self.base)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("run `testfail.py run` again", r.stderr)

    def test_attest_refuses_without_a_run(self):
        self.fix()
        r = self.tf("attest", "--base", self.base)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_attest_refuses_when_the_check_changed(self):
        self.fix()
        self.run_base()
        self.write(".nousfx/project.toml", f'[check]\ncommand = "{CHECK} -v"\ntimeout = 60\n')
        r = self.tf("attest", "--base", self.base)
        self.assertEqual(r.returncode, 2)

    def test_attest_exits_like_run_for_a_no(self):
        self.write("calc.py", FIXED)
        self.write("test_truth.py", UNRELATED_MORE)
        self.assertEqual(self.run_base().returncode, 1)
        r = self.tf("attest", "--base", self.base)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(self.verdict(r), "no")

    def test_a_failed_run_removes_an_older_stamp(self):
        self.fix()
        self.run_base()
        self.write(".nousfx/project.toml", '[check]\ncommand = "sleep 30"\ntimeout = 1\n')
        self.assertEqual(self.run_base().returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "claude-testfail-pass")))


class Limits(Base):
    def test_a_timeout_is_a_refusal_never_a_pass(self):
        self.fix()
        self.write(".nousfx/project.toml", '[check]\ncommand = "sleep 30"\ntimeout = 1\n')
        r = self.run_base()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("did not finish within 1 seconds", r.stderr)

    def test_no_check_command_is_a_refusal(self):
        self.fix()
        os.remove(os.path.join(self.repo, ".nousfx", "project.toml"))
        r = self.run_base()
        self.assertEqual(r.returncode, 2)
        self.assertIn("no [check] command", r.stderr)

    def test_a_detached_head_is_a_refusal(self):
        self.fix()
        self.git("checkout", "-q", "--detach")
        r = self.run_base()
        self.assertEqual(r.returncode, 2)
        self.assertIn("detached", r.stderr)

    def test_a_missing_base_is_a_refusal(self):
        self.fix()
        r = self.tf("run", "--base", "0" * 40)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--base", r.stderr)


class Config(Base):
    def test_tests_globs_decide_which_files_are_tests(self):
        self.write("calc.py", FIXED)
        self.write("specs/calc_spec.py", COVERING.replace("import calc", "import sys\nsys.path.insert(0, '.')\nimport calc"))
        self.write(".nousfx/project.toml",
                   f'[check]\ncommand = "python3 -B -m unittest discover -s specs -p \'*_spec.py\'"\n'
                   'timeout = 60\ntests = ["specs/*_spec.py"]\n')
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")

    def test_without_the_glob_the_same_diff_has_no_test_file(self):
        self.write("calc.py", FIXED)
        self.write("specs/calc_spec.py", COVERING)
        r = self.run_base()
        self.assertEqual(self.verdict(r), "n/a")

    def test_failure_pattern_names_the_failures_of_another_runner(self):
        self.fix()
        script = ("import sys, unittest; r = unittest.TextTestRunner(stream=open('/dev/null', 'w')).run("
                  "unittest.defaultTestLoader.discover('.')); "
                  "[print('NOT OK ' + str(f[0].id())) for f in r.failures + r.errors]; sys.exit(not r.wasSuccessful())")
        self.write("runner.py", script)
        self.write(".nousfx/project.toml",
                   '[check]\ncommand = "python3 -B runner.py"\ntimeout = 60\ntests = ["test_*.py", "runner.py"]\n'
                   'failure_pattern = "^NOT OK (.+)$"\n')
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("failing: test_calc.T.test_add", r.stdout)

    def test_a_bad_failure_pattern_is_a_refusal(self):
        self.fix()
        self.write(".nousfx/project.toml", f'[check]\ncommand = "{CHECK}"\nfailure_pattern = "("\n')
        r = self.run_base()
        self.assertEqual(r.returncode, 2)
        self.assertIn("failure_pattern", r.stderr)

    def test_a_path_with_glob_characters_restores_only_itself(self):
        self.commit_base("helperx.py", "X = 1\n")
        self.write("helper[x].py", "Y = 2\n")  # as a pathspec, helper[x].py also matches helperx.py
        self.write("test_helper.py", "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_base_helper_stays(self):\n        self.assertTrue(os.path.exists('helperx.py'))\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "no", r.stdout + r.stderr)

    def test_the_clone_runs_no_git_hooks(self):
        hooks = os.path.join(self._tmp.name, "hooks")
        os.makedirs(hooks)
        marker = os.path.join(self._tmp.name, "hook-ran")
        with open(os.path.join(hooks, "post-checkout"), "w") as f:
            f.write(f"#!/bin/sh\ntouch '{marker}'\n")
        os.chmod(os.path.join(hooks, "post-checkout"), 0o755)
        config = os.path.join(self._tmp.name, "gitconfig")
        with open(config, "w") as f:
            f.write(f"[core]\n\thooksPath = {hooks}\n")
        self.fix()
        env = {**os.environ, "GIT_CONFIG_GLOBAL": config}
        r = self.run_base(env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_a_file_turned_into_a_folder_is_restored(self):
        self.commit_base("data", "one\n")
        os.remove(os.path.join(self.repo, "data"))
        self.write("data/one.txt", "one\n")  # the change replaces a file with a folder of the same name
        self.write("test_data.py", "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_data_is_a_folder(self):\n        self.assertTrue(os.path.isdir('data'))\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")

    def test_changed_docs_are_restored_too(self):
        # An old test ties README.md to version.py. The change bumps both and adds a test that proves nothing:
        # restoring only version.py would fail the old test by name and print a false `yes`.
        self.write("README.md", "hello v1\n")
        self.write("version.py", "VERSION = 'v1'\n")
        self.commit_base("test_sync.py", "import unittest\nimport version\n\n\nclass S(unittest.TestCase):\n"
                         "    def test_readme_names_the_version(self):\n"
                         "        self.assertIn(version.VERSION, open('README.md').read())\n")
        self.write("README.md", "hello v2\n")
        self.write("version.py", "VERSION = 'v2'\n")
        self.write("test_new.py", UNRELATED)
        r = self.run_base()
        self.assertEqual(self.verdict(r), "no", r.stdout + r.stderr)

    def test_failure_lines_the_passing_run_also_prints_are_not_names(self):
        # A suite that prints a failure-looking line even when green; the restore only breaks an import.
        self.commit_base("test_noise.py", "print('FAIL: noise (fake.Case)')\n" + UNRELATED)
        self.write("calc.py", BUGGY + "\n\ndef mul(a, b):\n    return a * b\n")
        self.write("test_mul.py", "import unittest\nfrom calc import mul\n\n\nclass M(unittest.TestCase):\n"
                   "    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "build", r.stdout + r.stderr)

    def test_attest_refuses_on_another_branch(self):
        self.fix()
        self.assertEqual(self.run_base().returncode, 0)
        self.git("checkout", "-q", "-b", "cesar/NFX-2")
        r = self.tf("attest", "--base", self.base)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_a_doc_in_a_tests_folder_is_not_a_test(self):
        self.write("calc.py", FIXED)
        self.write("tests/README.md", "how to run the tests\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "n/a")

    def test_production_change_with_no_matching_test_names_the_tests_key(self):
        self.write("calc.py", FIXED)
        self.write("CalcTest.kt", "class CalcTest\n")  # a test, but not by the default convention
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "n/a")
        self.assertIn("tests = [", r.stdout)

    def test_a_tracked_file_that_matches_gitignore_is_in_the_copy(self):
        self.write(".gitignore", "*.jar\n")
        self.write("wrapper.jar", "jar\n")
        self.git("add", "-A")
        self.git("add", "-f", "wrapper.jar")
        self.git("commit", "-q", "-m", "NFX-1: forced file")
        self.base = self.git("rev-parse", "HEAD")
        self.fix()
        self.write("test_jar.py", "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_jar_is_there(self):\n        self.assertTrue(os.path.exists('wrapper.jar'))\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # without it the copy would be baseline-fail
        self.assertEqual(self.verdict(r), "yes")

    def test_a_folder_turned_into_a_file_is_restored(self):
        self.commit_base("data/one.txt", "one\n")
        shutil.rmtree(os.path.join(self.repo, "data"))
        self.write("data", "one\n")  # the change replaces a folder with a file of the same name
        self.write("test_data.py", "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_data_is_a_file(self):\n        self.assertTrue(os.path.isfile('data'))\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")

    def test_git_dir_in_the_environment_does_not_reach_the_real_repo(self):
        self.fix()
        before = Snapshot(self.repo)
        env = {**os.environ, "GIT_DIR": os.path.join(self.repo, ".git"), "GIT_WORK_TREE": self.repo}
        r = self.run_base(env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")
        self.assertTrue(before == Snapshot(self.repo))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD"), "cesar/NFX-1")

    def test_a_production_file_the_change_deleted_is_restored(self):
        self.commit_base("helper.py", "X = 1\n")
        os.remove(os.path.join(self.repo, "helper.py"))
        self.write("test_gone.py", "import os\nimport unittest\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_helper_is_gone(self):\n        self.assertFalse(os.path.exists('helper.py'))\n")
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")


if __name__ == "__main__":
    unittest.main()
