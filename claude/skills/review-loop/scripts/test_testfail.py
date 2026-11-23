#!/usr/bin/env python3
"""Tests for testfail.py (NFX-190), the script that checks new tests fail without the change.
Run: python3 -B claude/skills/review-loop/scripts/test_testfail.py

Seam: the script's CLI, its printed verdict and exit code. Each test builds a throwaway git repo under a temp dir whose
check command is one line (`python3 -B -m unittest -q`); the real repo and HOME are never touched. The CLI runs with an
empty temp home (gate_lookup_cases), so it never loads the real ~/.claude gate (NFX-214).
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

from gate_lookup_cases import GateLookupCases, run_script

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
        return run_script([TESTFAIL, *args], cwd=self.repo, env=env)

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


class RunningMarker(Base):
    """NFX-271: a run stops after the first half when the tree changes, and leaves a marker while it runs."""
    MARKER = "claude-testfail-running"

    def check_script(self, body):
        """A check command that runs `body` (Python) from a file outside the repo."""
        script = os.path.join(self._tmp.name, "check.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(body)
        self.write(".nousfx/project.toml", f"[check]\ncommand = '\"{sys.executable}\" \"{script}\"'\ntimeout = 60\n")

    def marker_path(self):
        return os.path.join(self.repo, self.git("rev-parse", "--git-path", self.MARKER))

    def test_stops_after_first_half(self):
        counter = os.path.join(self._tmp.name, "counter")
        calc = os.path.join(self.repo, "calc.py")
        self.check_script(
            "import os, subprocess, sys\n"
            f"first = not os.path.exists({counter!r})\n"
            f"open({counter!r}, 'a').write('x\\n')\n"
            f"if first:\n    open({calc!r}, 'a').write('# edited\\n')\n"
            "sys.exit(subprocess.call([sys.executable, '-B', '-m', 'unittest', '-q']))\n")
        self.fix()
        r = self.run_base()
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(r.stdout, "")
        self.assertIn("changed during the run", r.stderr)
        self.assertIn("Stopped after the first half", r.stderr)
        with open(counter, encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "claude-testfail-pass")))

    def test_running_marker_holds_tree_pid_start_and_expires(self):
        copy = os.path.join(self._tmp.name, "marker-copy.json")
        marker = self.marker_path()
        self.check_script(
            "import shutil, subprocess, sys\n"
            f"shutil.copy({marker!r}, {copy!r})\n"
            "sys.exit(subprocess.call([sys.executable, '-B', '-m', 'unittest', '-q']))\n")
        self.fix()
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        import json
        with open(copy, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(set(data), {"tree", "pid", "start", "expires"})
        self.assertEqual(data["tree"], re.search(r"^Tests-Fail-Without: \S+ (\S+)", r.stdout, re.M).group(1))
        self.assertIsInstance(data["pid"], int)
        self.assertEqual(data["expires"], data["start"] + 2 * 60 + 600)
        self.assertFalse(os.path.exists(marker))

    def test_running_marker_is_removed_after_a_refusal(self):
        self.fix()
        self.write(".nousfx/project.toml", '[check]\ncommand = "sleep 30"\ntimeout = 1\n')
        self.assertEqual(self.run_base().returncode, 2)
        self.assertFalse(os.path.exists(self.marker_path()))

    def test_running_marker_is_removed_after_an_exception(self):
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location("testfail_in_process", TESTFAIL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        gate_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "hooks")
        self.fix()
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repo)
        marker = self.marker_path()
        with mock.patch.object(module, "split", side_effect=RuntimeError("boom")), \
                mock.patch.object(module, "find_gate", return_value=(os.path.join(gate_dir, "review-gate.py"), "checkout")):
            with self.assertRaises(RuntimeError):
                module.main(["run", "--base", self.base])
        self.assertFalse(os.path.exists(marker))

    def test_running_marker_is_rewritten_once_the_slot_is_held(self):
        import contextlib
        import importlib.util
        import io
        import json
        import time
        from unittest import mock
        spec = importlib.util.spec_from_file_location("testfail_in_process", TESTFAIL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        gate_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "hooks")
        copy = os.path.join(self._tmp.name, "marker-copy.json")
        self.check_script(
            "import shutil, subprocess, sys\n"
            f"shutil.copy({self.marker_path()!r}, {copy!r})\n"
            "sys.exit(subprocess.call([sys.executable, '-B', '-m', 'unittest', '-q']))\n")
        self.fix()
        held = []

        @contextlib.contextmanager
        def slot_free_after_a_wait():
            time.sleep(0.5)
            held.append(time.time())
            yield

        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repo)
        with mock.patch.object(module, "suite_slot_hold", slot_free_after_a_wait), \
                mock.patch.object(module, "find_gate", return_value=(os.path.join(gate_dir, "review-gate.py"), "checkout")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["run", "--base", self.base]), 0)
        with open(copy, encoding="utf-8") as f:
            data = json.load(f)
        self.assertGreaterEqual(data["start"], held[0])
        self.assertEqual(data["expires"], data["start"] + 2 * 60 + 600)

    def in_process(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("testfail_in_process", TESTFAIL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        gate = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), "hooks", "review-gate.py")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(self.repo)
        return module, gate

    def test_an_edit_while_queued_for_the_slot_stops_before_the_first_half(self):
        import contextlib
        import io
        from unittest import mock
        counter = os.path.join(self._tmp.name, "counter")
        self.check_script(f"open({counter!r}, 'a').write('x\\n')\n")
        self.fix()
        module, gate = self.in_process()

        @contextlib.contextmanager
        def slot_free_after_an_edit():
            self.write("calc.py", FIXED + "# edited while queued\n")
            yield

        err = io.StringIO()
        with mock.patch.object(module, "suite_slot_hold", slot_free_after_an_edit), \
                mock.patch.object(module, "find_gate", return_value=(gate, "checkout")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(module.main(["run", "--base", self.base]), 2)
        self.assertIn("changed during the run", err.getvalue())
        self.assertFalse(os.path.exists(counter), "the first half ran after the tree changed")
        self.assertFalse(os.path.exists(self.marker_path()))

    def test_running_marker_of_another_run_is_kept(self):
        import json
        from unittest import mock
        module, gate_path = self.in_process()
        with mock.patch.object(module, "find_gate", return_value=(gate_path, "checkout")):
            gate = module.load_gate()
        with open(self.marker_path(), "w", encoding="utf-8") as f:
            json.dump({"tree": "t", "pid": os.getpid() + 1, "start": 0, "expires": 1}, f)
        module.remove_marker(gate, self.repo)
        self.assertTrue(os.path.exists(self.marker_path()))
        with open(self.marker_path(), "w", encoding="utf-8") as f:
            json.dump({"tree": "t", "pid": os.getpid(), "start": 0, "expires": 1}, f)
        module.remove_marker(gate, self.repo)
        self.assertFalse(os.path.exists(self.marker_path()))

    def test_running_marker_absent_after_a_pass(self):
        self.fix()
        self.assertEqual(self.run_base().returncode, 0)
        self.assertFalse(os.path.exists(self.marker_path()))


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

    def test_a_jvm_style_test_file_counts_as_a_test_without_a_tests_list(self):
        self.write("calc.py", FIXED)
        self.write("src/test/CalcTest.py",
                   COVERING.replace("import calc", "import sys\nsys.path.insert(0, '.')\nimport calc"))
        command = "python3 -B -m unittest discover -s src/test -p '*Test.py'"
        self.write(".nousfx/project.toml", f'[check]\ncommand = "{command}"\ntimeout = 60\n')
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")

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
        self.write("calc.spec.ts", "it('adds')\n")  # a test, but not by the default convention
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



USES_NEWMOD = ("import unittest\nimport newmod\n\n\nclass T(unittest.TestCase):\n"
               "    def test_value(self):\n        self.assertEqual(newmod.VALUE, 1)\n")


BASE_FILE_GAINS_MUL = BUGGY + "\n\ndef mul(a, b):\n    return a * b\n"
IMPORTS_NEW_NAME_FROM_BASE_FILE = ("import unittest\nfrom calc import mul\n\n\nclass T(unittest.TestCase):\n"
            "    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n")


class DefaultTestNames(unittest.TestCase):
    """The default match when [check] has no tests list (NFX-266: JVM layouts)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, HERE)
        import testfail
        cls.is_test = staticmethod(lambda path: testfail.is_test(path, None))

    def test_jvm_test_source_sets_and_names_are_tests(self):
        for path in ("core/fs/src/test/kotlin/FooTest.kt", "app/src/androidTest/kotlin/BarTest.kt",
                     "src/test/kotlin/Helpers.kt", "shared/src/commonTest/kotlin/Fake.kt",
                     "lib/FooTest.java", "lib/FooTests.kt", "src/test/resources/fixture.json",
                     "app/src/testDebug/kotlin/Helper.kt", "app/src/androidTestFreeDebug/kotlin/Fake.kt"):
            with self.subTest(path=path):
                self.assertTrue(self.is_test(path))

    def test_main_source_sets_stay_production_whatever_the_name(self):
        for path in ("core/fs/src/main/kotlin/FileSystemTesting.kt", "core/fs/src/main/kotlin/ContractTest.kt",
                     "shared/src/commonMain/kotlin/FooTest.kt", "core/src/testFixtures/kotlin/FakeTest.kt",
                     "app/src/main/kotlin/Latest.kt", "lib/Testing.kt", "lib/Contest.kt",
                     "core/src/testFixturesDebug/kotlin/FakeTest.kt", "src/testing/helpers.py"):
            with self.subTest(path=path):
                self.assertFalse(self.is_test(path))

    def test_the_old_default_still_holds(self):
        for path in ("test_calc.py", "pkg/calc_test.go", "pkg/tests/helpers.py", "src/main/kotlin/tests/Fake.kt"):
            with self.subTest(path=path):
                self.assertTrue(self.is_test(path))
        self.assertFalse(self.is_test("calc.py"))

    def test_a_tests_list_replaces_the_default(self):
        import testfail
        self.assertFalse(testfail.is_test("core/fs/src/test/kotlin/FooTest.kt", ["specs/*_spec.py"]))


class ImportCauses(Base):
    """NFX-222: a restored run that dies importing a file the change added names that file."""

    def use_runner(self, body):
        """Make runner.py (a test file, so never restored) the check; it exits with its run's code `r`."""
        self.write("runner.py", "import subprocess, sys\n" + body + "sys.exit(r.returncode)\n")
        self.write(".nousfx/project.toml",
                   '[check]\ncommand = "python3 -B runner.py"\ntimeout = 60\ntests = ["test_*.py", "runner.py"]\n')

    def test_an_added_helper_only_tests_mention_is_build_with_the_tests_key_hint(self):
        self.write("calc.py", FIXED)
        self.write("newmod.py", "VALUE = 1\n")
        self.write("test_newmod.py", USES_NEWMOD)
        r = self.run_base()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "build")
        self.assertIn("newmod.py", r.stdout)
        self.assertIn("[check] tests", r.stdout)

    def test_an_added_module_a_non_test_file_mentions_is_yes_naming_the_import(self):
        self.write("newmod.py", "VALUE = 1\n")
        self.write("app.py", "import newmod\n")
        self.write("test_newmod.py", USES_NEWMOD)
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "yes")
        self.assertIn("  failing: import newmod.py\n", r.stdout)
        self.assertNotIn("[check] tests", r.stdout)

    def test_attest_prints_the_same_lines_as_run_for_both_causes(self):
        for mention in ("", "import newmod\n"):
            with self.subTest(mention=mention):
                self.write("newmod.py", "VALUE = 1\n")
                self.write("app.py", mention)
                self.write("test_newmod.py", USES_NEWMOD)
                ran = self.run_base()
                r = self.tf("attest", "--base", self.base)
                self.assertEqual(r.stdout, ran.stdout)
                self.assertEqual(r.returncode, ran.returncode)

    def test_an_import_error_for_a_file_the_change_did_not_add_changes_nothing(self):
        self.write("calc.py", BASE_FILE_GAINS_MUL)
        self.write("test_other.py", IMPORTS_NEW_NAME_FROM_BASE_FILE)
        r = self.run_base()
        self.assertEqual(self.verdict(r), "build")
        self.assertNotIn("failing:", r.stdout)
        self.assertNotIn("[check] tests", r.stdout)

    def test_a_build_names_the_suites_a_run_checks_style_runner_reports(self):
        self.write("newmod.py", "VALUE = 1\n")
        self.write("test_newmod.py", USES_NEWMOD)
        self.use_runner("r = subprocess.run([sys.executable, '-B', 'test_newmod.py'])\n"
                        "print('ok   test_truth.py (0s)')\n"
                        "print(('FAIL' if r.returncode else 'ok  ') + ' test_newmod.py (0s)')\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "build")
        self.assertIn("  suite: test_newmod.py\n", r.stdout)
        self.assertNotIn("suite: test_truth.py", r.stdout)

    def test_a_build_names_the_test_module_unittest_could_not_import(self):
        self.write("calc.py", BASE_FILE_GAINS_MUL)
        self.write("test_other.py", IMPORTS_NEW_NAME_FROM_BASE_FILE)
        r = self.run_base()
        self.assertIn("  suite: test_other\n", r.stdout)

    def test_a_module_the_passing_run_also_could_not_import_is_noise(self):
        self.write("newmod.py", "VALUE = 1\n")
        self.write("app.py", "import newmod\n")
        self.write("test_newmod.py", USES_NEWMOD + "\nprint(\"ModuleNotFoundError: No module named 'newmod'\")\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "build")
        self.assertNotIn("import newmod.py", r.stdout)

    def test_an_added_package_is_matched_by_its_first_missing_name(self):
        self.write("pkg/__init__.py", "")
        self.write("pkg/core.py", "VALUE = 1\n")
        self.write("app.py", "from pkg import core\n")
        self.write("test_pkg.py", "import unittest\nfrom pkg import core\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_value(self):\n        self.assertEqual(core.VALUE, 1)\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "yes", r.stdout)
        self.assertIn("failing: import pkg/core.py", r.stdout)

    def test_a_package_submodule_only_its_own_init_imports_is_not_called_a_helper(self):
        self.write("pkg/__init__.py", "from .internal import run\n")
        self.write("pkg/internal.py", "def run():\n    return 1\n")
        self.write("app.py", "import pkg\n")
        self.write("test_pkg.py", "import unittest\nimport pkg\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_run(self):\n        self.assertEqual(pkg.run(), 1)\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "yes", r.stdout)
        self.assertIn("failing: import pkg/internal.py", r.stdout)
        self.assertNotIn("added helper", r.stdout)

    def test_a_helper_package_stays_a_helper_when_a_doc_uses_its_submodule_name(self):
        self.write("testkit/__init__.py", "from .config import VALUE\n")
        self.write("testkit/config.py", "VALUE = 1\n")
        self.write("README.md", "hello\nSee the config section.\n")
        self.write("calc.py", FIXED)
        self.write("test_kit.py", "import unittest\nimport testkit\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_value(self):\n        self.assertEqual(testkit.VALUE, 1)\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "build", r.stdout)
        self.assertIn("added helper testkit/config.py", r.stdout)

    def test_a_pytest_style_prefixed_import_error_is_matched(self):
        self.write("newmod.py", "VALUE = 1\n")
        self.write("test_newmod.py", USES_NEWMOD)
        self.use_runner("r = subprocess.run([sys.executable, '-B', 'test_newmod.py'], capture_output=True, text=True)\n"
                        "print(''.join('E   ' + l for l in r.stderr.splitlines(True)))\n")
        r = self.run_base()
        self.assertEqual(self.verdict(r), "build")
        self.assertIn("added helper newmod.py", r.stdout)


COMMENTED = "# Adds two numbers.\n" + BUGGY
UNRELATED_COMMENTED = "# Always true.\n" + UNRELATED


class CommentsOnly(Base):
    """NFX-251: a diff `comment_check.py verify` proves comment-only gets `n/a (comments only)`, with no run."""

    def comment_only(self):
        self.write("calc.py", COMMENTED)
        self.write("test_truth.py", UNRELATED_COMMENTED)

    def assertRanAsToday(self, r):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "no")
        self.assertNotIn("comments only", r.stdout)

    def test_a_comment_only_diff_prints_n_a_comments_only_and_exits_0(self):
        self.comment_only()
        r = self.run_base()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.verdict(r), "n/a")
        self.assertIn(f"base:{self.base} (comments only)", r.stdout.splitlines()[0])

    def test_the_same_diff_with_one_code_token_changed_runs_as_today(self):
        self.comment_only()
        self.write("calc.py", COMMENTED.replace("a - b", "a + b"))
        self.assertRanAsToday(self.run_base())

    def test_attest_prints_the_comments_only_line_the_run_recorded(self):
        self.comment_only()
        ran = self.run_base()
        r = self.tf("attest", "--base", self.base)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], ran.stdout.splitlines()[0])

    def test_fm2_a_file_verify_leaves_to_the_suite_runs_as_today(self):
        self.comment_only()
        self.write("README.md", "changed\n")
        self.assertRanAsToday(self.run_base())

    def checkout_with(self, comment_check):
        """A NousFX checkout layout (claude/{hooks,skills}) whose comment_check.py is `comment_check`, or none."""
        root = os.path.join(self._tmp.name, "NousFX", "claude")
        source = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
        skip = shutil.ignore_patterns("test_*", "__pycache__")
        shutil.copytree(os.path.join(source, "hooks"), os.path.join(root, "hooks"), ignore=skip)
        scripts = os.path.join(root, "skills", "review-loop", "scripts")
        shutil.copytree(HERE, scripts, ignore=skip)
        os.remove(os.path.join(scripts, "comment_check.py"))
        if comment_check is not None:
            with open(os.path.join(scripts, "comment_check.py"), "w", encoding="utf-8") as f:
                f.write(comment_check)
        return os.path.join(scripts, "testfail.py")

    def test_fm1_no_comment_check_next_to_testfail_runs_as_today(self):
        script = self.checkout_with(None)
        self.comment_only()
        self.assertRanAsToday(run_script([script, "run", "--base", self.base], cwd=self.repo))

    def test_fm1_a_verify_that_crashes_runs_as_today(self):
        script = self.checkout_with("def differences(root, base):\n    raise RuntimeError('boom')\n")
        self.comment_only()
        r = run_script([script, "run", "--base", self.base], cwd=self.repo)
        self.assertRanAsToday(r)
        self.assertIn("boom", r.stderr)


class GateLookup(GateLookupCases, unittest.TestCase):
    """NFX-214: a project copy never loads the repo's own .claude/hooks/review-gate.py."""
    SCRIPT = TESTFAIL
    GATE_NAMES = ("trusted_check = run_check = write_stamp = valid_stamp = worktree_tree = None\n"
                  "TESTFAIL_STAMP_FILE = 'claude-testfail-pass'")

    def test_the_lookup_is_the_same_in_prove_and_testfail(self):
        """The lookup is copied in both scripts (a shared file would not reach projects); keep the copies identical."""
        import ast

        def lookup(path):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            names = ("home_dir", "gate_paths", "find_gate", "too_old_hint")
            return {n.name: ast.get_source_segment(src, n) for n in ast.parse(src).body
                    if isinstance(n, ast.FunctionDef) and n.name in names}

        mine = lookup(TESTFAIL)
        self.assertEqual(len(mine), 4)
        self.assertEqual(mine, lookup(os.path.join(HERE, "prove.py")))


if __name__ == "__main__":
    unittest.main()
