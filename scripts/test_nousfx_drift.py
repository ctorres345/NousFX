#!/usr/bin/env python3
"""Tests for claude/hooks/nousfx-drift.py (NFX-57). Run: python3 -B scripts/test_nousfx_drift.py

The hook is installed by a real sync into a temp home, then run as a subprocess the way Claude Code
runs it. Nothing real is touched: the home, the state home and the clone are all temp folders.
"""
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import unittest

from test_nousfx_sync import Base, HERE, read, write

GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


class DriftBase(Base):
    def setUp(self):
        super().setUp()
        shutil.copytree(HERE, os.path.join(self.repo, "scripts"),
                        ignore=shutil.ignore_patterns("test_*", "fixtures", "__pycache__"))
        shutil.copytree(os.path.join(os.path.dirname(HERE), "plugins", "via", "scripts"),  # sync imports it
                        os.path.join(self.repo, "plugins", "via", "scripts"),
                        ignore=shutil.ignore_patterns("test_*", "__pycache__"))
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        self.installed = os.path.join(self.home, ".claude", "hooks", "nousfx-drift.py")

    def run_hook(self, *extra, repo=None, env=None, tools="claude"):
        t0 = time.monotonic()
        r = subprocess.run(
            [sys.executable, "-I", "-B", self.installed, "--repo", repo or self.repo, "--tools", tools,
             "--home", self.home, "--state-home", self.state, *extra],
            input="{}", capture_output=True, text=True, cwd=self.tmp, env=env or os.environ)
        self.elapsed = time.monotonic() - t0
        return r

    def message(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.strip()
        self.assertEqual(len(out.splitlines()), 1, r.stdout)
        data = json.loads(out)
        msg = data["systemMessage"]
        ctx = data["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "SessionStart")
        self.assertEqual(ctx["additionalContext"], msg)
        self.assertNotIn("install.sh", msg)
        return msg

    def drift(self):
        """A changed source: the rules file on disk no longer matches what sync would write."""
        path = os.path.join(self.repo, "harness", "AGENTS.md")
        write(path, read(path) + "\n- A new rule.\n")


class Quiet(DriftBase):
    def test_no_drift_prints_nothing(self):
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_missing_clone_prints_nothing_and_exits_zero(self):
        r = self.run_hook(repo=os.path.join(self.tmp, "nope"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_sync_that_crashes_prints_nothing_and_exits_zero(self):
        write(os.path.join(self.repo, "scripts", "nousfx_sync.py"), "raise RuntimeError('boom')\n")
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_sync_that_prints_garbage_prints_nothing(self):
        write(os.path.join(self.repo, "scripts", "nousfx_sync.py"), "print('not json')\nraise SystemExit(1)\n")
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_sync_usage_error_prints_nothing(self):
        write(os.path.join(self.repo, "scripts", "nousfx_sync.py"), "raise SystemExit(2)\n")
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_slow_sync_is_skipped_inside_the_budget(self):
        write(os.path.join(self.repo, "scripts", "nousfx_sync.py"), "import time\ntime.sleep(30)\n")
        r = self.run_hook()
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertLess(self.elapsed, 4.0)

    def test_unreadable_stdin_does_not_matter(self):
        r = subprocess.run([sys.executable, "-I", "-B", self.installed, "--repo", self.repo, "--home", self.home,
                            "--state-home", self.state], input="\xff not json", capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class Quiet2(DriftBase):
    def test_the_hook_stands_in_the_clone_before_it_starts_any_program(self):
        """Windows looks for a bare `git` in the process's own folder: that folder must be the clone."""
        spec = importlib.util.spec_from_file_location("nousfx_drift_under_test", self.installed)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        seen = []
        mod.start_background_fetch = lambda args: seen.append(os.getcwd())
        mod.main(["--repo", self.repo, "--home", self.home, "--state-home", self.state])
        self.assertEqual(seen, [self.repo])


class Warns(DriftBase):
    def test_changed_source_prints_one_line_naming_the_sync_command(self):
        self.drift()
        msg = self.message(self.run_hook())
        self.assertIn("out of date", msg)
        self.assertIn("1 file", msg)
        self.assertIn("python3 -B scripts/nousfx_sync.py", msg)
        self.assertIn(self.repo, msg)
        self.assertNotIn("--tools", msg)
        self.assertLess(self.elapsed, 2.0)

    def stub_sync(self, counts, rc=1):
        write(os.path.join(self.repo, "scripts", "nousfx_sync.py"),
              f"import sys\nprint({json.dumps(json.dumps(counts))})\nprint('args:', *sys.argv[1:], file=sys.stderr)\n"
              f"raise SystemExit({rc})\n")

    def test_the_hook_asks_for_a_summary_of_the_same_tools_and_the_command_repeats_them(self):
        self.stub_sync({"files": 1, "skills": 0, "outside": 0})
        msg = self.message(self.run_hook(tools="claude,agy"))
        self.assertIn("nousfx_sync.py --tools claude,agy", msg)

    def test_counts_are_spelled_out(self):
        self.stub_sync({"files": 2, "skills": 13, "outside": 1})
        msg = self.message(self.run_hook())
        self.assertIn("2 files", msg)
        self.assertIn("13 skills need a decision", msg)
        self.assertIn("1 outside skill to fetch", msg)

    def test_zero_counts_with_exit_one_still_warn_once_without_numbers(self):
        self.stub_sync({"files": 0, "skills": 0, "outside": 0})
        msg = self.message(self.run_hook())
        self.assertIn("out of date", msg)

    def test_clean_summary_prints_nothing(self):
        self.stub_sync({"files": 0, "skills": 0, "outside": 0}, rc=0)
        self.assertEqual(self.run_hook().stdout, "")

    def test_windows_message_says_python(self):
        self.drift()
        msg = self.message(self.run_hook("--platform", "windows"))
        self.assertIn("python -B scripts/nousfx_sync.py", msg)
        self.assertNotIn("python3", msg)

    def test_hook_never_changes_the_machine(self):
        self.drift()
        before = self.snapshot()
        self.run_hook()
        self.assertEqual(self.snapshot(), before)


class Behind(DriftBase):
    def setUp(self):
        super().setUp()
        self.origin = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "remote", "add", "origin", self.origin)
        git(self.repo, "push", "-q", "origin", "main")
        write(os.path.join(self.repo, ".git", "FETCH_HEAD"), "")  # a fetch just ran: the hook starts none
        self.stub = os.path.join(self.tmp, "stubbin")
        self.mark = os.path.join(self.tmp, "fetch-started")
        real = shutil.which("git")
        write(os.path.join(self.stub, "git"),
              f'#!/bin/sh\nfor a in "$@"; do if [ "$a" = fetch ]; then touch "{self.mark}"; sleep 3; exit 0; fi; done\n'
              f'exec "{real}" "$@"\n')
        os.chmod(os.path.join(self.stub, "git"), os.stat(os.path.join(self.stub, "git")).st_mode | stat.S_IXUSR)
        self.env = {**os.environ, "PATH": self.stub + os.pathsep + os.environ["PATH"]}

    def push_from_elsewhere(self, n=1):
        other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", self.origin, other)
        for i in range(n):
            write(os.path.join(other, f"f{i}.txt"), "x")
            git(other, "add", "-A")
            git(other, "commit", "-q", "-m", f"c{i}")
        git(other, "push", "-q", "origin", "main")
        git(self.repo, "fetch", "-q", "origin")  # what an earlier background fetch would have done

    def test_up_to_date_prints_nothing(self):
        self.assertEqual(self.run_hook(env=self.env).stdout, "")

    def test_behind_prints_the_pull_line(self):
        self.push_from_elsewhere(3)
        msg = self.message(self.run_hook(env=self.env))
        self.assertIn("3 commits behind origin/main", msg)
        self.assertIn("git pull", msg)
        self.assertIn("python3 -B scripts/nousfx_sync.py", msg)
        self.assertNotIn("out of date", msg)

    def test_one_commit_is_singular(self):
        self.push_from_elsewhere(1)
        self.assertIn("1 commit behind", self.message(self.run_hook(env=self.env)))

    def test_drift_and_behind_make_one_combined_line(self):
        self.push_from_elsewhere(2)
        self.drift()
        msg = self.message(self.run_hook(env=self.env))
        self.assertIn("out of date", msg)
        self.assertIn("2 commits behind origin/main", msg)
        self.assertIn("git pull", msg)

    def test_no_origin_is_silent(self):
        git(self.repo, "remote", "remove", "origin")
        self.assertEqual(self.run_hook(env=self.env).stdout, "")

    @unittest.skipIf(os.name == "nt", "the stub git is a POSIX shell script")
    def test_a_slow_check_cannot_starve_the_fetch(self):
        os.utime(os.path.join(self.repo, ".git", "FETCH_HEAD"), (1, 1))
        write(os.path.join(self.repo, "scripts", "nousfx_sync.py"), "import time\ntime.sleep(30)\n")
        self.run_hook(env=self.env)
        deadline = time.monotonic() + 3
        while not os.path.exists(self.mark) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self.mark), "the fetch was starved by the slow check")

    @unittest.skipIf(os.name == "nt", "the stub git is a POSIX shell script")
    def test_old_fetch_starts_a_background_fetch_without_waiting_for_it(self):
        os.utime(os.path.join(self.repo, ".git", "FETCH_HEAD"), (1, 1))
        r = self.run_hook(env=self.env)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertLess(self.elapsed, 2.0)  # the stub fetch sleeps 3 s
        deadline = time.monotonic() + 3
        while not os.path.exists(self.mark) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self.mark), "the fetch was never started")

    @unittest.skipIf(os.name == "nt", "the stub git is a POSIX shell script")
    def test_recent_fetch_starts_none(self):
        r = self.run_hook(env=self.env)
        time.sleep(0.5)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.mark))


if __name__ == "__main__":
    unittest.main()
