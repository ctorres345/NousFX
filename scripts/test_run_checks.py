#!/usr/bin/env python3
"""Tests for scripts/run_checks.py (NFX-134): cheap suites first, FAIL shown at once, optional --fail-fast."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_checks  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RunChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.times = os.path.join(self.root, "times.json")  # outside the globbed pattern
        self._saved = (run_checks.ROOT, run_checks.PATTERNS, run_checks.times_path)
        run_checks.ROOT, run_checks.PATTERNS = self.root, ["test_*.py"]
        run_checks.times_path = lambda: self.times
        self.addCleanup(self._restore)

    def _restore(self):
        run_checks.ROOT, run_checks.PATTERNS, run_checks.times_path = self._saved

    def suite(self, name, body="pass", pad=0):
        path = os.path.join(self.root, f"test_{name}.py")
        with open(path, "w") as f:
            f.write(body + "\n" + "#" * pad + "\n")
        return path

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = run_checks.main(["run_checks.py", *args])
        return code, out.getvalue()

    def test_fast_failure_prints_before_slow_suite_finishes(self):
        self.suite("slow", "import time; time.sleep(2)", pad=5000)
        self.suite("fast", "import sys; print('boom'); sys.exit(1)")
        code, out = self.run_main("-j", "1")
        self.assertEqual(code, 1)
        self.assertLess(out.index("FAIL test_fast.py"), out.index("test_slow.py"))
        self.assertIn("boom", out)

    def test_fail_fast_stops_after_first_failure(self):
        self.suite("slow", "import time; time.sleep(2)", pad=5000)
        self.suite("fast", "import sys; sys.exit(1)")
        code, out = self.run_main("-j", "1", "--fail-fast")
        self.assertEqual(code, 1)
        self.assertIn("FAIL test_fast.py", out)
        self.assertNotIn("test_slow.py", out)
        self.assertIn("stopped after first failure", out)

    def test_without_fail_fast_every_suite_runs_and_count_prints(self):
        self.suite("a", "import sys; sys.exit(1)")
        self.suite("b")
        self.suite("c", pad=3000)
        code, out = self.run_main("-j", "1")
        self.assertEqual(code, 1)
        for name in ("a", "b", "c"):
            self.assertIn(f"test_{name}.py", out)
        self.assertIn("2/3 suites passed", out)
        self.assertNotIn("stopped after first failure", out)

    def test_all_pass_exits_zero_with_fail_fast(self):
        self.suite("a")
        self.suite("b")
        code, out = self.run_main("-j", "1", "--fail-fast")
        self.assertEqual(code, 0)
        self.assertIn("2/2 suites passed", out)

    def test_order_falls_back_to_file_size(self):
        small, big = self.suite("small"), self.suite("big", pad=4000)
        self.assertEqual(run_checks.order([big, small], {}), [small, big])

    def test_recorded_time_beats_file_size(self):
        small, big = self.suite("small"), self.suite("big", pad=4000)
        times = {"test_small.py": 9.0, "test_big.py": 0.1}
        self.assertEqual(run_checks.order([small, big], times), [big, small])

    def test_ordering_keeps_every_suite(self):
        paths = [self.suite(n, pad=i * 100) for i, n in enumerate("abcd")]
        times = {"test_a.py": 3.0, "test_c.py": 1.0}
        self.assertEqual(sorted(run_checks.order(paths, times)), sorted(paths))

    def test_times_are_recorded_and_merged(self):
        with open(self.times, "w") as f:
            json.dump({"test_old.py": 5.0}, f)
        self.suite("a")
        self.run_main("-j", "1")
        with open(self.times) as f:
            saved = json.load(f)
        self.assertIn("test_a.py", saved)
        self.assertEqual(saved["test_old.py"], 5.0)

    def test_corrupt_or_missing_times_file_is_ignored(self):
        self.suite("a")
        self.assertEqual(run_checks.load_times(self.times), {})
        with open(self.times, "w") as f:
            f.write("{not json")
        self.assertEqual(run_checks.load_times(self.times), {})
        code, _ = self.run_main("-j", "1")
        self.assertEqual(code, 0)

    def test_no_suites_fails(self):
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("no test suites found", out)

    def test_gate_check_command_never_fails_fast(self):
        with open(os.path.join(REPO, ".nousfx", "project.toml")) as f:
            self.assertNotIn("--fail-fast", f.read())


if __name__ == "__main__":
    unittest.main()
