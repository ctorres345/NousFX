#!/usr/bin/env python3
"""Tests for testfail-running.py (NFX-271). Run: python3 -B claude/hooks/test_testfail_running.py [-k NAME]

Each test runs the hook as a subprocess on a throwaway git repo; it must exit 0 every time and print only when a live
`testfail.py run` has a fresh marker for the work tree the edit is in.
"""
import atexit
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "testfail-running.py")
SCRIPTS = os.path.join(HERE, "..", "skills", "review-loop", "scripts")
TESTFAIL = os.path.join(SCRIPTS, "testfail.py")
sys.dont_write_bytecode = True
sys.path.insert(0, SCRIPTS)
_SLOTS = tempfile.mkdtemp(prefix="nousfx-test-slots-")  # NFX-217: keep the real run off the machine-wide slots
atexit.register(shutil.rmtree, _SLOTS, True)
os.environ.setdefault("NOUSFX_SUITE_SLOT_DIR", _SLOTS)
TREE = "0123456789abcdef0123456789abcdef01234567"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def marker_body(pid=None, expires=None, start=None):
    start = time.time() if start is None else start
    return {"tree": TREE, "pid": os.getpid() if pid is None else pid, "start": start,
            "expires": start + 600 if expires is None else expires}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.repo = self.init_repo("repo")

    def init_repo(self, name):
        repo = os.path.join(self.tmp, name)
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@example.com")
        git(repo, "config", "user.name", "T")
        with open(os.path.join(repo, "a.py"), "w") as f:
            f.write("x = 1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        return repo

    def put_marker(self, repo, body):
        path = os.path.join(repo, git(repo, "rev-parse", "--git-path", "claude-testfail-running"))
        with open(path, "w", encoding="utf-8") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        return path

    def hook(self, tool_input, tool="Edit", cwd=None, raw=None):
        payload = raw if raw is not None else json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": cwd or self.tmp})
        r = subprocess.run([sys.executable, "-I", "-B", HOOK], input=payload, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def assertWarns(self, out, pid=None):
        data = json.loads(out)
        text = data["systemMessage"]
        self.assertEqual(data["hookSpecificOutput"], {"hookEventName": "PreToolUse", "additionalContext": text})
        self.assertIn(f"pid {os.getpid() if pid is None else pid}", text)
        self.assertIn(TREE[:12], text)
        self.assertNotIn(TREE[:13], text)
        self.assertNotIn("permissionDecision", out)


class Warns(Base):
    def test_an_edit_in_a_repo_with_a_live_marker_warns(self):
        self.put_marker(self.repo, marker_body())
        self.assertWarns(self.hook({"file_path": os.path.join(self.repo, "a.py")}))

    def test_write_and_notebook_edit_warn(self):
        self.put_marker(self.repo, marker_body())
        self.assertWarns(self.hook({"file_path": os.path.join(self.repo, "a.py")}, tool="Write"))
        self.assertWarns(self.hook({"notebook_path": os.path.join(self.repo, "n.ipynb")}, tool="NotebookEdit"))

    def test_a_new_file_in_a_new_subfolder_warns(self):
        self.put_marker(self.repo, marker_body())
        self.assertWarns(self.hook({"file_path": os.path.join(self.repo, "new", "deep", "b.py")}, tool="Write"))

    def test_a_relative_path_is_read_from_cwd(self):
        self.put_marker(self.repo, marker_body())
        self.assertWarns(self.hook({"file_path": "a.py"}, cwd=self.repo))

    def test_a_linked_worktree_uses_its_own_marker(self):
        linked = os.path.join(self.tmp, "linked")
        git(self.repo, "worktree", "add", "-q", "-b", "other", linked)
        self.put_marker(self.repo, marker_body())
        self.assertEqual(self.hook({"file_path": os.path.join(linked, "a.py")}), "")
        self.assertWarns(self.hook({"file_path": os.path.join(self.repo, "a.py")}))
        os.remove(os.path.join(self.repo, ".git", "claude-testfail-running"))
        self.put_marker(linked, marker_body())
        self.assertWarns(self.hook({"file_path": os.path.join(linked, "a.py")}))
        self.assertEqual(self.hook({"file_path": os.path.join(self.repo, "a.py")}), "")


class Quiet(Base):
    def edit(self):
        return self.hook({"file_path": os.path.join(self.repo, "a.py")})

    def test_no_marker_is_quiet(self):
        self.assertEqual(self.edit(), "")

    def test_an_expired_marker_is_quiet(self):
        self.put_marker(self.repo, marker_body(expires=time.time() - 1))
        self.assertEqual(self.edit(), "")

    @unittest.skipIf(os.name == "nt", "pid liveness is POSIX only")
    def test_a_dead_pid_is_quiet(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.put_marker(self.repo, marker_body(pid=p.pid))
        self.assertEqual(self.edit(), "")

    def test_a_path_outside_any_repo_is_quiet(self):
        outside = os.path.join(self.tmp, "plain")
        os.makedirs(outside)
        self.put_marker(self.repo, marker_body())
        self.assertEqual(self.hook({"file_path": os.path.join(outside, "x.py")}), "")

    def test_a_broken_marker_is_quiet(self):
        for body in ("not json", "[]", "{}", json.dumps({**marker_body(), "pid": "7"}),
                     json.dumps({**marker_body(), "pid": 0}), json.dumps({**marker_body(), "tree": 5}),
                     json.dumps({**marker_body(), "expires": None}), json.dumps({**marker_body(), "expires": float("inf")}),
                     json.dumps({**marker_body(), "start": float("nan")})):
            self.put_marker(self.repo, body)
            self.assertEqual(self.edit(), "", body)

    def test_bad_input_is_quiet(self):
        self.put_marker(self.repo, marker_body())
        for raw in ("", "not json", "[]", json.dumps({"tool_input": {}}), json.dumps({"tool_input": {"file_path": 5}})):
            self.assertEqual(self.hook(None, raw=raw), "", raw)


class Budget(Base):
    def test_past_the_budget_the_hook_is_quiet(self):
        spec = importlib.util.spec_from_file_location("testfail_running_budget", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.put_marker(self.repo, marker_body())
        path = os.path.join(self.repo, "deep", "er", "a.py")
        self.assertIsNotNone(module.warning(path, time.monotonic() + 2))
        self.assertIsNone(module.warning(path, time.monotonic() - 1))
        self.assertEqual(module.BUDGET_SECONDS, 2.0)


class Windows(Base):
    def test_windows_never_calls_os_kill_and_trusts_expires(self):
        spec = importlib.util.spec_from_file_location("testfail_running_hook", HOOK)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.put_marker(self.repo, marker_body(pid=p.pid))
        with mock.patch.object(module, "IS_WINDOWS", True), \
                mock.patch.object(module.os, "kill", side_effect=AssertionError("os.kill called")) as kill:
            text = module.warning(os.path.join(self.repo, "a.py"), time.monotonic() + 2)
        self.assertIn(f"pid {p.pid}", text)
        kill.assert_not_called()


class Replay(Base):
    @staticmethod
    def finish(proc):
        """Let testfail end, so its finally removes its clone from TMPDIR; kill it only after 60 s."""
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def test_replay_nfx_218(self):
        """NFX-218: an agent edited scripts/test_prove.py while testfail.py was running; the hook now says so first."""
        from gate_lookup_cases import python
        for rel, text in (("scripts/prove.py", "def f():\n    return 1\n"),
                          ("scripts/test_prove.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_a(self):\n        pass\n"),
                          (".nousfx/project.toml", '[check]\ncommand = \'"%s" -c "import time; time.sleep(3)"\'\ntimeout = 60\n' % sys.executable)):
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "NFX-218: base")
        base = git(self.repo, "rev-parse", "HEAD")
        for rel in ("scripts/prove.py", "scripts/test_prove.py"):
            with open(os.path.join(self.repo, rel), "a", encoding="utf-8") as f:
                f.write("# change\n")
        argv, env = python()
        proc = subprocess.Popen([*argv, TESTFAIL, "run", "--base", base], cwd=self.repo, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.finish, proc)
        marker = os.path.join(self.repo, git(self.repo, "rev-parse", "--git-path", "claude-testfail-running"))
        deadline = time.time() + 20
        while not os.path.exists(marker) and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(os.path.exists(marker), "testfail.py never wrote its marker")
        out = self.hook({"file_path": os.path.join(self.repo, "scripts", "test_prove.py")})
        data = json.loads(out)
        self.assertIn(f"pid {proc.pid}", data["systemMessage"])


if __name__ == "__main__":
    unittest.main()
