#!/usr/bin/env python3
"""Tests for the machine-wide suite slot (NFX-217). Run: python3 -B scripts/test_suite_slot.py

Seams: the run_checks.py and testfail.py CLIs, run as subprocesses, and suite_slot.hold(). Every test points
NOUSFX_SUITE_SLOT_DIR at a temp dir and drops NOUSFX_SUITE_SLOT_HELD, so it never touches the real slots and never
inherits the slot of the run_checks.py that runs it.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_SCRIPTS = os.path.join(ROOT, "claude", "skills", "review-loop", "scripts")
sys.path.insert(0, SKILL_SCRIPTS)
sys.dont_write_bytecode = True
import suite_slot  # noqa: E402

WAITING = "waiting for a suite slot (1 of 1 busy)"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.slots = os.path.join(self.tmp, "slots")
        self.env = {k: v for k, v in os.environ.items() if k != suite_slot.ENV_HELD}
        self.env.update({suite_slot.ENV_DIR: self.slots, suite_slot.ENV_SLOTS: "1"})
        self._saved_env = dict(os.environ)
        self.addCleanup(self._restore_env)
        os.environ.clear()
        os.environ.update(self.env)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._saved_env)

    def holder(self):
        """A separate process that takes the slot, says so, and sleeps until killed."""
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); import suite_slot\n"
                "with suite_slot.hold():\n    print('held', flush=True)\n    time.sleep(120)\n")
        proc = subprocess.Popen([sys.executable, "-B", "-c", code, SKILL_SCRIPTS], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        self.addCleanup(self._stop, proc)
        self.assertEqual(proc.stdout.readline().strip(), "held", proc.stderr.read() if proc.poll() is not None else "")
        return proc

    @staticmethod
    def _stop(proc):
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for f in (proc.stdout, proc.stderr):
            if f:
                f.close()


class RunChecksWaits(Base):
    """AC1: with N=1, two concurrent run_checks.py runs never overlap; the second waits, then passes."""

    def make_repo(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "scripts"))
        shutil.copy(os.path.join(HERE, "run_checks.py"), os.path.join(repo, "scripts"))
        dest = os.path.join(repo, "claude", "skills", "review-loop", "scripts")
        os.makedirs(dest)
        shutil.copy(os.path.join(SKILL_SCRIPTS, "suite_slot.py"), dest)
        self.marks = os.path.join(self.tmp, "marks")
        os.makedirs(self.marks)
        with open(os.path.join(repo, "scripts", "test_sleep.py"), "w") as f:
            f.write("import os, sys, time\n"
                    f"mark = os.path.join({self.marks!r}, str(os.getppid()))\n"
                    "open(mark + '.start', 'w').write(repr(time.time()))\n"
                    "time.sleep(1.5)\n"
                    "open(mark + '.end', 'w').write(repr(time.time()))\n")
        return os.path.join(repo, "scripts", "run_checks.py")

    def test_two_concurrent_runs_never_overlap_and_the_second_waits_then_passes(self):
        script = self.make_repo()
        first = subprocess.Popen([sys.executable, "-B", script], env=self.env, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        self.addCleanup(self._stop, first)
        deadline = time.time() + 30
        while not os.listdir(self.marks) and time.time() < deadline:  # the first suite is running
            time.sleep(0.05)
        second = subprocess.Popen([sys.executable, "-B", script], env=self.env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        self.addCleanup(self._stop, second)
        out1, err1 = first.communicate(timeout=60)
        out2, err2 = second.communicate(timeout=60)
        self.assertEqual(first.returncode, 0, out1 + err1)
        self.assertEqual(second.returncode, 0, out2 + err2)
        self.assertIn(WAITING, err2)
        self.assertNotIn("waiting", err1)

        def span(pid):
            with open(os.path.join(self.marks, f"{pid}.start")) as s, open(os.path.join(self.marks, f"{pid}.end")) as e:
                return float(s.read()), float(e.read())
        a, b = span(first.pid), span(second.pid)
        self.assertLessEqual(a[1], b[0], f"the suites overlapped: {a} and {b}")


class KilledHolder(Base):
    """AC2: a slot left by a killed process is reclaimed."""

    def test_a_slot_held_by_a_process_killed_with_sigkill_is_free_again(self):
        proc = self.holder()
        fd, busy = suite_slot.try_slot(self.slots, 1)
        self.assertIsNone(fd)
        self.assertEqual(busy, 1)
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        fd, path = suite_slot.try_slot(self.slots, 1)
        self.assertIsNotNone(fd, "the killed holder's slot was not reclaimed")
        suite_slot.release(fd)

    def test_a_waiter_gets_the_slot_once_the_holder_is_killed(self):
        proc = self.holder()
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import suite_slot\n"
                "with suite_slot.hold():\n    print('ran', flush=True)\n")
        waiter = subprocess.Popen([sys.executable, "-B", "-c", code, SKILL_SCRIPTS], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        self.addCleanup(self._stop, waiter)
        time.sleep(1.5)
        self.assertIsNone(waiter.poll(), "the waiter ran while the slot was held")
        proc.send_signal(signal.SIGKILL)
        out, err = waiter.communicate(timeout=30)
        self.assertEqual(out.strip(), "ran", err)
        self.assertIn(WAITING, err)

    def test_a_leftover_slot_file_naming_a_dead_pid_does_not_block(self):
        os.makedirs(self.slots)
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True).stdout.strip()
        with open(os.path.join(self.slots, "slot-0.lock"), "w") as f:
            f.write(dead)
        err = self.hold_in_child()
        self.assertNotIn("waiting", err)

    def hold_in_child(self, env=None):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import suite_slot\n"
                "with suite_slot.hold():\n    pass\n")
        r = subprocess.run([sys.executable, "-B", "-c", code, SKILL_SCRIPTS], env=env or self.env,
                           capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stderr


class Nesting(Base):
    """A process tree takes one slot: a child of the holder runs inside it (no deadlock at N=1)."""

    def child(self, env):
        code = ("import os, sys; sys.path.insert(0, sys.argv[1]); import suite_slot\n"
                "with suite_slot.hold():\n    print(os.getpid(), os.environ.get(suite_slot.ENV_HELD, ''), flush=True)\n")
        return subprocess.run([sys.executable, "-B", "-c", code, SKILL_SCRIPTS], env=env, capture_output=True,
                              text=True, timeout=10, stdin=subprocess.DEVNULL)

    def test_a_child_of_the_holder_does_not_take_a_second_slot(self):
        with suite_slot.hold():
            marker = os.environ[suite_slot.ENV_HELD]
            self.assertTrue(marker.startswith(f"{os.getpid()}:"))
            r = self.child(dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("waiting", r.stderr)
        self.assertEqual(r.stdout.split()[1], marker)  # it ran inside the parent's slot
        self.assertNotIn(suite_slot.ENV_HELD, os.environ)  # released and unset after the block

    def test_a_marker_whose_holder_is_gone_counts_for_nothing(self):
        proc = self.holder()
        with open(os.path.join(self.slots, "slot-0.lock")) as f:
            self.assertEqual(f.read(), str(proc.pid))
        env = dict(self.env, **{suite_slot.ENV_HELD: f"{proc.pid}:{os.path.join(self.slots, 'slot-0.lock')}"})
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        r = self.child(env)
        self.assertEqual(r.returncode, 0, r.stderr)
        child_pid, marker = r.stdout.split()
        self.assertTrue(marker.startswith(f"{child_pid}:"), marker)  # it took a slot of its own

    def test_a_marker_from_an_unrelated_live_process_that_holds_no_slot_counts_for_nothing(self):
        env = dict(self.env, **{suite_slot.ENV_HELD: f"{os.getpid()}:{os.path.join(self.slots, 'slot-0.lock')}"})
        proc = self.holder()  # holds slot 0 with its own PID, not ours
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import suite_slot\n"
                "with suite_slot.hold():\n    pass\n")
        waiter = subprocess.Popen([sys.executable, "-B", "-c", code, SKILL_SCRIPTS], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
        self.addCleanup(self._stop, waiter)
        time.sleep(1.5)
        self.assertIsNone(waiter.poll(), "a forged marker let the waiter skip the slot")
        self._stop(proc)
        waiter.communicate(timeout=30)
        self.assertEqual(waiter.returncode, 0)


class TestfailRuns(Base):
    """testfail.py holds one slot across its two clone runs; the check it starts runs inside that slot."""

    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.log = os.path.join(self.tmp, "probe.log")
        probe = os.path.join(self.tmp, "probe.py")
        with open(probe, "w") as f:  # logs the slot marker it inherits, then runs the repo's tests
            f.write("import os, subprocess, sys\n"
                    f"open({self.log!r}, 'a').write(os.environ.get({suite_slot.ENV_HELD!r}, '-') + '\\n')\n"
                    "sys.exit(subprocess.call([sys.executable, '-B', '-m', 'unittest', '-q']))\n")
        self.git("init", "-q", "-b", "cesar/NFX-1")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.write(".nousfx/project.toml", f'[check]\ncommand = "python3 -B {probe}"\ntimeout = 60\n')
        self.write("calc.py", "def add(a, b):\n    return a - b\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "NFX-1: base")
        self.base = self.git("rev-parse", "HEAD")
        self.write("calc.py", "def add(a, b):\n    return a + b\n")
        self.write("test_calc.py", "import unittest\nimport calc\n\n\nclass T(unittest.TestCase):\n"
                   "    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def start_testfail(self):
        return subprocess.Popen([sys.executable, "-B", os.path.join(SKILL_SCRIPTS, "testfail.py"), "run", "--base",
                                 self.base], cwd=self.repo, env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)

    def test_both_clone_runs_run_inside_one_slot_that_testfail_holds(self):
        proc = self.start_testfail()
        self.addCleanup(self._stop, proc)
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertIn("Tests-Fail-Without: yes", out)
        with open(self.log) as f:
            markers = f.read().split()
        self.assertEqual(len(markers), 2, markers)
        self.assertEqual(markers[0], markers[1])
        self.assertTrue(markers[0].startswith(f"{proc.pid}:"), markers)

    def test_testfail_waits_for_a_busy_slot_then_gives_its_verdict(self):
        holder = self.holder()
        proc = self.start_testfail()
        self.addCleanup(self._stop, proc)
        time.sleep(2)
        self.assertFalse(os.path.exists(self.log), "the check ran while another process held the only slot")
        self._stop(holder)
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertIn(WAITING, err)
        self.assertIn("Tests-Fail-Without: yes", out)


class Release(Base):
    def test_the_slot_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with suite_slot.hold():
                raise RuntimeError("suite crashed")
        fd, _ = suite_slot.try_slot(self.slots, 1)
        self.assertIsNotNone(fd)
        suite_slot.release(fd)


class Degrades(Base):
    def run_hold(self):
        import io
        out = io.StringIO()
        ran = []
        with suite_slot.hold(out=out):
            ran.append(True)
        return ran, out.getvalue()

    def test_windows_without_flock_runs_with_no_limit_and_a_note(self):
        saved = suite_slot.fcntl
        suite_slot.fcntl = None
        self.addCleanup(setattr, suite_slot, "fcntl", saved)
        ran, out = self.run_hold()
        self.assertEqual(ran, [True])
        self.assertIn("no suite slot limit on this platform", out)

    def test_a_folder_that_cannot_be_made_runs_with_no_limit_and_a_note(self):
        blocker = os.path.join(self.tmp, "a-file")
        open(blocker, "w").close()
        os.environ[suite_slot.ENV_DIR] = os.path.join(blocker, "slots")
        ran, out = self.run_hold()
        self.assertEqual(ran, [True])
        self.assertIn("no suite slot limit: cannot use", out)

    def test_slot_count_default_and_override(self):
        cpus = os.cpu_count() or 1
        self.assertEqual(suite_slot.slot_count({}), max(1, cpus // 4))
        self.assertEqual(suite_slot.slot_count({suite_slot.ENV_SLOTS: "2"}), 2)
        import io
        note = io.StringIO()
        for bad in ("0", "-1", "two"):
            self.assertEqual(suite_slot.slot_count({suite_slot.ENV_SLOTS: bad}, out=note), max(1, cpus // 4))
        self.assertEqual(note.getvalue().count("is not a whole number"), 3)

    def test_the_default_folder_is_outside_any_repo_under_the_user_cache(self):
        path = suite_slot.slot_dir({})
        self.assertTrue(path.startswith(os.path.expanduser("~")), path)
        self.assertTrue(path.endswith(os.path.join("nousfx", "suite-slots")), path)


if __name__ == "__main__":
    unittest.main()
