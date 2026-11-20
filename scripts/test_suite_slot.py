#!/usr/bin/env python3
"""Tests for the machine-wide suite slot (NFX-217). Run: python3 -B scripts/test_suite_slot.py

Seams: the run_checks.py and testfail.py CLIs, run as subprocesses, and suite_slot.hold(). Every test points
NOUSFX_SUITE_SLOT_DIR at a temp dir and drops NOUSFX_SUITE_SLOT_HELD, so it never touches the real slots and never
inherits the slot of the run_checks.py that runs it.
"""
import contextlib
import importlib.util
import io
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL_SCRIPTS = os.path.join(ROOT, "claude", "skills", "review-loop", "scripts")
sys.path.insert(0, SKILL_SCRIPTS)
sys.dont_write_bytecode = True
import suite_slot  # noqa: E402

WAITING = "waiting for a suite slot (1 of 1 busy)"
# A child process that takes the slot with suite_slot.hold() and runs BODY inside it.
CHILD = "import os, sys, time; sys.path.insert(0, sys.argv[1]); import suite_slot\nwith suite_slot.hold():\n    {body}\n"


def first_line(stream, timeout=30):
    """The next line of a child's pipe, or "" when none comes within `timeout` seconds."""
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(stream.readline()), daemon=True).start()
    try:
        return lines.get(timeout=timeout).strip()
    except queue.Empty:
        return ""


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.slots = os.path.join(self.tmp, "slots")
        self.env = {k: v for k, v in os.environ.items() if k != suite_slot.ENV_HELD}
        self.env.update({suite_slot.ENV_DIR: self.slots, suite_slot.ENV_SLOTS: "1"})
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ.clear()
        os.environ.update(self.env)

    def start(self, args, env=None, cwd=None):
        proc = subprocess.Popen(args, env=env or self.env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, stdin=subprocess.DEVNULL)
        self.addCleanup(self.stop, proc)
        return proc

    def child(self, body, env=None):
        """Start a process that runs `body` while it holds a slot."""
        return self.start([sys.executable, "-B", "-c", CHILD.format(body=body), SKILL_SCRIPTS], env=env)

    def holder(self):
        """A separate process that takes the slot, says so, and sleeps until killed."""
        proc = self.child("print('held', flush=True); time.sleep(120)")
        self.assertEqual(first_line(proc.stdout), "held")
        return proc

    def assert_waiting(self, proc):
        """proc printed the wait line and has not run its body."""
        self.assertEqual(first_line(proc.stderr), WAITING)
        self.assertIsNone(proc.poll())

    def assert_slot_free(self):
        fd, _, _ = suite_slot.try_slot(self.slots, 1)
        self.assertIsNotNone(fd, "the slot is still taken")
        suite_slot.release(fd)

    @staticmethod
    def stop(proc):
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
        self.release = os.path.join(self.tmp, "release")  # the suite runs until the test makes it: no timing race
        with open(os.path.join(repo, "scripts", "test_sleep.py"), "w") as f:
            f.write("import os, sys, time\n"
                    f"mark = os.path.join({self.marks!r}, str(os.getppid()))\n"
                    "open(mark + '.start', 'w').write(repr(time.time()))\n"
                    "deadline = time.time() + 30\n"
                    f"while not os.path.exists({self.release!r}) and time.time() < deadline:\n"
                    "    time.sleep(0.05)\n"
                    "open(mark + '.end', 'w').write(repr(time.time()))\n")
        return os.path.join(repo, "scripts", "run_checks.py")

    def test_two_concurrent_runs_never_overlap_and_the_second_waits_then_passes(self):
        script = self.make_repo()
        first = self.start([sys.executable, "-B", script])
        deadline = time.time() + 30
        while not os.listdir(self.marks) and time.time() < deadline:  # the first suite is running
            time.sleep(0.05)
        second = self.start([sys.executable, "-B", script])
        line = first_line(second.stderr)  # read before the first run may end, so the wait cannot be missed
        open(self.release, "w").close()
        out1, err1 = first.communicate(timeout=60)
        out2, err2 = second.communicate(timeout=60)
        self.assertEqual(first.returncode, 0, out1 + err1)
        self.assertEqual(second.returncode, 0, out2 + err2)
        self.assertEqual(line, WAITING)
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
        self.assertEqual(suite_slot.try_slot(self.slots, 1), (None, None, 1))
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        self.assert_slot_free()

    def test_a_waiter_gets_the_slot_once_the_holder_is_killed(self):
        holder = self.holder()
        waiter = self.child("print('ran', flush=True)")
        self.assert_waiting(waiter)
        holder.send_signal(signal.SIGKILL)
        out, err = waiter.communicate(timeout=30)
        self.assertEqual(out.strip(), "ran", err)

    def test_a_leftover_slot_file_naming_a_dead_pid_does_not_block(self):
        os.makedirs(self.slots)
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True).stdout.strip()
        with open(os.path.join(self.slots, "slot-0.lock"), "w") as f:
            f.write(dead)
        out, err = self.child("pass").communicate(timeout=10)
        self.assertNotIn("waiting", err)


class Nesting(Base):
    """A process tree takes one slot: a child of the holder runs inside it (no deadlock at N=1)."""
    REPORT = "print(os.getpid(), os.environ.get(suite_slot.ENV_HELD, ''), flush=True)"

    def test_a_child_of_the_holder_does_not_take_a_second_slot(self):
        with suite_slot.hold():
            marker = os.environ[suite_slot.ENV_HELD]
            self.assertTrue(marker.startswith(f"{os.getpid()}:"))
            out, err = self.child(self.REPORT, env=dict(os.environ)).communicate(timeout=10)
        self.assertNotIn("waiting", err)
        self.assertEqual(out.split()[1], marker)  # it ran inside the parent's slot
        self.assertNotIn(suite_slot.ENV_HELD, os.environ)  # released and unset after the block

    def test_a_marker_whose_holder_is_gone_counts_for_nothing(self):
        proc = self.holder()
        lock = os.path.join(self.slots, "slot-0.lock")
        with open(lock) as f:
            self.assertEqual(f.read(), str(proc.pid))
        env = dict(self.env, **{suite_slot.ENV_HELD: f"{proc.pid}:{lock}"})
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        out, err = self.child(self.REPORT, env=env).communicate(timeout=10)
        child_pid, marker = out.split()
        self.assertTrue(marker.startswith(f"{child_pid}:"), marker)  # it took a slot of its own

    def test_a_marker_from_a_live_process_that_holds_no_slot_counts_for_nothing(self):
        lock = os.path.join(self.slots, "slot-0.lock")
        holder = self.holder()  # holds slot 0 with its own PID, not ours
        waiter = self.child("pass", env=dict(self.env, **{suite_slot.ENV_HELD: f"{os.getpid()}:{lock}"}))
        self.assert_waiting(waiter)
        self.stop(holder)
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
        return self.start([sys.executable, "-B", os.path.join(SKILL_SCRIPTS, "testfail.py"), "run", "--base",
                           self.base], cwd=self.repo)

    def test_both_clone_runs_run_inside_one_slot_that_testfail_holds(self):
        proc = self.start_testfail()
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertIn("Tests-Fail-Without: yes", out)
        with open(self.log) as f:
            markers = f.read().split()
        self.assertEqual(len(markers), 2, markers)
        self.assertEqual(markers[0], markers[1])
        self.assertTrue(markers[0].startswith(f"{proc.pid}:"), markers)

    def test_one_slot_is_held_across_both_runs_not_taken_per_run(self):
        import testfail
        gate = load_gate(os.path.join(ROOT, "claude", "hooks", "review-gate.py"))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "NFX-1: change")
        busy, takes, real_try = [], [], suite_slot.try_slot

        def counting_try(directory, n):
            got = real_try(directory, n)
            takes.append(got[0] is not None)
            return got

        class FakeGate:  # takes no slot itself, so only testfail's own hold can make the slot busy
            @staticmethod
            def run_check(copy, check, log):
                fd, _, _ = real_try(self.slots, 1)
                busy.append(fd is None)
                if fd is not None:
                    suite_slot.release(fd)
                open(log, "w").close()
                return (len(busy) == 1, "" if len(busy) == 1 else "exited with 1")

        os.makedirs(self.slots, exist_ok=True)  # so a free slot reads as free, not as a missing folder
        suite_slot.try_slot = counting_try
        self.addCleanup(setattr, suite_slot, "try_slot", real_try)
        s = testfail.Setup(self.repo, {"command": "true", "timeout": 60}, None, re.compile(testfail.DEFAULT_PATTERN, re.M),
                           self.base, "cesar/NFX-1", gate.worktree_tree(self.repo), {})
        testfail.run_in_copy(FakeGate, s, ["calc.py"])
        self.assertEqual(busy, [True, True], "the slot was free during a clone run")
        self.assertEqual(takes, [True], "testfail took the slot more than once")
        self.assert_slot_free()

    def test_testfail_waits_for_a_busy_slot_then_gives_its_verdict(self):
        holder = self.holder()
        proc = self.start_testfail()
        self.assert_waiting(proc)
        self.assertFalse(os.path.exists(self.log), "the check ran while another process held the only slot")
        self.stop(holder)
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertIn("Tests-Fail-Without: yes", out)


def load_gate(path):
    spec = importlib.util.spec_from_file_location(f"gate_{abs(hash(path))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GateRunCheck(Base):
    """review-gate.py run_check (--mark, run-check --stamp, testfail) takes the slot before its [check] timeout starts."""

    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.seen = os.path.join(self.tmp, "seen")
        self.check = {"command": f"{sys.executable} -B -c \"import os; open({self.seen!r}, 'w').write("
                                 f"os.environ.get('{suite_slot.ENV_HELD}', '-'))\"", "timeout": 3}

    def test_the_timeout_starts_after_the_wait_and_the_check_runs_inside_the_slot(self):
        gate = load_gate(os.path.join(ROOT, "claude", "hooks", "review-gate.py"))
        holder = self.holder()
        threading.Timer(4, self.stop, [holder]).start()  # longer than the check's 3 s timeout
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, why = gate.run_check(self.repo, self.check, os.path.join(self.tmp, "check.log"))
        self.assertTrue(ok, why)
        self.assertIn(WAITING, err.getvalue())
        with open(self.seen) as f:
            self.assertTrue(f.read().startswith(f"{os.getpid()}:"))  # so run_checks.py there takes no second slot

    def test_a_gate_with_no_skill_folder_next_to_it_runs_with_no_limit_and_a_note(self):
        hooks = os.path.join(self.tmp, "lone", "hooks")
        os.makedirs(hooks)
        shutil.copy(os.path.join(ROOT, "claude", "hooks", "review-gate.py"), hooks)
        gate = load_gate(os.path.join(hooks, "review-gate.py"))
        self.holder()  # would block a gate that found the helper
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok, why = gate.run_check(self.repo, self.check, os.path.join(self.tmp, "check.log"))
        self.assertTrue(ok, why)
        self.assertIn("no suite slot limit", err.getvalue())
        with open(self.seen) as f:
            self.assertEqual(f.read(), "-")


class Release(Base):
    def test_the_slot_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with suite_slot.hold():
                raise RuntimeError("suite crashed")
        self.assert_slot_free()


class Degrades(Base):
    def run_hold(self):
        out, ran = io.StringIO(), []
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

    def test_a_slot_file_that_is_a_symlink_is_never_followed(self):
        victim = os.path.join(self.tmp, "victim.txt")
        with open(victim, "w") as f:
            f.write("keep me")
        os.makedirs(self.slots)
        os.symlink(victim, os.path.join(self.slots, "slot-0.lock"))
        ran, out = self.run_hold()
        self.assertEqual(ran, [True])
        self.assertIn("no suite slot limit: cannot use", out)
        with open(victim) as f:
            self.assertEqual(f.read(), "keep me")

    def test_a_waiter_survives_the_slot_folder_being_removed(self):
        self.holder()
        waiter = self.child("print('ran', flush=True)")
        self.assert_waiting(waiter)
        shutil.rmtree(self.slots)  # a cache cleaner; the holder keeps its lock on the removed file
        out, err = waiter.communicate(timeout=30)
        self.assertEqual(waiter.returncode, 0, err)
        self.assertEqual(out.strip(), "ran", err)
        self.assertNotIn("Traceback", err)

    def test_slot_count_default_and_override(self):
        default = max(1, (os.cpu_count() or 1) // 4)
        self.assertEqual(suite_slot.slot_count({}), default)
        self.assertEqual(suite_slot.slot_count({suite_slot.ENV_SLOTS: "2"}), 2)
        note = io.StringIO()
        for bad in ("0", "-1", "two"):
            self.assertEqual(suite_slot.slot_count({suite_slot.ENV_SLOTS: bad}, out=note), default)
        self.assertEqual(note.getvalue().count("is not a whole number"), 3)

    def test_the_default_folder_is_under_the_user_cache_whatever_home_says(self):
        path = suite_slot.slot_dir({"HOME": self.tmp, "XDG_CACHE_HOME": ""})
        self.assertFalse(path.startswith(self.tmp), path)  # a suite that moves HOME still shares the slots
        self.assertTrue(path.endswith(os.path.join("nousfx", "suite-slots")), path)


if __name__ == "__main__":
    unittest.main()
