#!/usr/bin/env python3
"""Parity test for the one worktree_tree (NFX-207, absorbs NFX-206).

The review gate, testfail.py and via_run.py must record the same tree for the same repo state, because the gate
compares their records against its own hash. Each case below builds a throwaway repo and runs the three real callers:
the gate's function, the `testfail.py run` CLI (its Tests-Fail-Without line) and the `via_run.py pass` CLI (the tree it
writes to via-check-ok). The real repo and HOME are never touched.
Run: python3 -B scripts/test_worktree_tree_parity.py
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "claude", "hooks", "review-gate.py")
TESTFAIL = os.path.join(ROOT, "claude", "skills", "review-loop", "scripts", "testfail.py")
VIA_RUN = os.path.join(ROOT, "plugins", "via", "scripts", "via_run.py")
TREE_LINE = re.compile(r"^Tests-Fail-Without: \S+ ([0-9a-f]{40}|[0-9a-f]{64}) base:", re.M)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(VIA_RUN))
import nousfx_testhome as th  # noqa: E402


def load_gate():
    spec = importlib.util.spec_from_file_location("review_gate_for_parity", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Parity(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, "home")  # empty: via_run finds no installed gate and uses this checkout's
        os.makedirs(self.home)
        self.repo = os.path.join(tmp.name, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "cesar/NFX-1")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        # ctime cannot be set back, so turn its check off: with the mtime pinned too, a same-size edit keeps the
        # index entry's stat data, as an edit in the commit's own second does on a coarse clock (NFX-198).
        self.git("config", "core.trustctime", "false")
        self.write(".nousfx/project.toml", '[check]\ncommand = "python3 -B -m unittest -q"\ntimeout = 60\n')
        self.write(".gitignore", "*.log\n")
        self.write("sub/keep.txt", "kept\n")
        self.old = time.time_ns() - 100 * 10**9
        self.write("f.txt", "aaaa", self.old)
        self.sub = os.path.join(self.repo, "sub")
        self.run_dirs = {}  # a merged Via run for each folder a case starts in: Via's project is that folder
        for folder in (self.repo, self.sub):
            run_dir = self.via_ok("init", cwd=folder)["run_dir"]
            with open(os.path.join(run_dir, "findings.json"), "w", encoding="utf-8") as f:
                json.dump({"version": 1, "mode": "audit", "scope": "all", "platforms": ["compose"], "findings": []}, f)
            self.via_ok("merge", run_dir, cwd=folder)
            self.run_dirs[folder] = run_dir
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "NFX-1: base")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, rel, text, ns=None):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if ns is not None:
            os.utime(path, ns=(ns, ns))

    def via_ok(self, *args, cwd):
        """via_run as the skill runs it from `cwd`, with no --project: the project is that folder."""
        r = subprocess.run([th.python(self.home), "-I", "-B", VIA_RUN, *args], cwd=cwd, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, f"via_run {args[0]} failed\nstdout: {r.stdout}\nstderr: {r.stderr}")
        return json.loads(r.stdout)

    def trees(self, cwd):
        """{caller: tree} for the same repo state, each caller started in `cwd`."""
        via = self.via_ok("pass", self.run_dirs[cwd], cwd=cwd)["tree"]
        r = subprocess.run([sys.executable, "-B", TESTFAIL, "run", "--base", self.base], cwd=cwd, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=120)
        m = TREE_LINE.search(r.stdout)
        self.assertIsNotNone(m, f"no Tests-Fail-Without line\nstdout: {r.stdout}\nstderr: {r.stderr}")
        return {"gate": load_gate().worktree_tree(cwd), "testfail": m.group(1), "via_run": via}

    def assert_parity(self, cwd=None):
        trees = self.trees(cwd or self.repo)
        self.assertEqual(len(set(trees.values())), 1, trees)
        return trees["gate"]

    def test_a_same_size_edit_right_after_a_commit(self):
        self.write("f.txt", "bbbb", self.old)
        tree = self.assert_parity()
        self.assertNotEqual(tree, self.git("rev-parse", "HEAD^{tree}"))

    def test_a_force_added_ignored_file(self):
        self.write("n.log", "new\n")
        self.git("add", "-f", "n.log")
        tree = self.assert_parity()
        self.assertIn("n.log", self.git("ls-tree", "--name-only", tree).split())

    def test_a_call_from_a_subfolder(self):
        self.write("f.txt", "bbbb", self.old)
        self.write("t.log", "ignored, outside sub\n")
        tree = self.assert_parity(self.sub)
        names = self.git("ls-tree", "--name-only", tree).split()
        self.assertIn("f.txt", names)
        self.assertNotIn("t.log", names)
        self.assertEqual(self.git("rev-parse", f"{tree}:f.txt"), self.git("hash-object", "f.txt"))


if __name__ == "__main__":
    unittest.main()
