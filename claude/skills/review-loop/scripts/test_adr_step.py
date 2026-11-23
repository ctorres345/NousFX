#!/usr/bin/env python3
"""Tests for adr_step.py, the review loop's ADR step. No network, no real ~/Releases.

Run: python3 -B claude/skills/review-loop/scripts/test_adr_step.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
SCRIPT = os.path.join(HERE, "adr_step.py")
REPORT = os.path.join(HERE, "session_report.py")


def adr(status, title, body):
    return "---\nstatus: %s\n---\n\n# %s\n\n%s\n" % (status, title, body)


class AdrStep(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.env = dict(os.environ, NOUSFX_REPORT_ROOT=os.path.join(self.tmp, "releases"))
        self.adr_dir = os.path.join(self.tmp, "adr")
        os.makedirs(self.adr_dir)

    def write_adr(self, name, text):
        with open(os.path.join(self.adr_dir, name), "w", encoding="utf-8") as f:
            f.write(text)

    def decide(self, decision, *extra):
        r = subprocess.run([sys.executable, "-B", REPORT, "decide", "NFX-191", "--decision", decision,
                            "--why", "w", "--rejected", "r", "--evidence", "#1", "--result", "open",
                            "--date", "2026-11-19", "--name", "t", "--new", *extra],
                           cwd=ROOT, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def step(self, *extra):
        r = subprocess.run([sys.executable, "-B", SCRIPT, "--name", "t", "--date", "2026-11-19",
                            "--adr-dir", self.adr_dir, *extra], cwd=ROOT, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def flagged_lines(self, out):
        return [line for line in out.splitlines() if line.startswith("- entry ")]

    def test_only_the_current_flagged_entry_is_listed_with_the_next_number(self):
        self.decide("Keep logs outside the repo", "--adr", "Logs live outside the repo")
        self.decide("Name the folder by date")
        self.decide("Use one lock file", "--adr", "One lock file")
        self.decide("Use flock instead", "--supersedes", "3")
        self.write_adr("0001-a.md", adr("accepted", "A", "Body (NFX-1)."))
        self.write_adr("0002-b.md", adr("accepted", "B", "Body (NFX-2)."))
        out = self.step()
        self.assertEqual(self.flagged_lines(out),
                         ["- entry 1: Logs live outside the repo (decision: Keep logs outside the repo)"])
        self.assertIn("Next free ADR number: 0003", out)

    def test_the_accepted_list_leaves_out_a_superseded_adr(self):
        self.write_adr("0001-old-lock.md", adr("superseded by ADR-0002", "Use a lock file", "We lock a file (NFX-1)."))
        self.write_adr("0002-flock.md", adr("accepted", "Use flock", "We use flock. It frees on exit (NFX-2)."))
        self.write_adr("0003-idea.md", adr("proposed", "Maybe later", "Not decided (NFX-3)."))
        out = self.step()
        listed = [line for line in out.splitlines() if line.startswith("- ADR-")]
        self.assertEqual(listed, ["- ADR-0002 Use flock: We use flock."])
        self.assertIn("Accepted ADRs: 1", out)
        self.assertIn("Next free ADR number: 0004", out)

    def test_no_adr_folder_says_so_and_goes_on(self):
        os.rmdir(self.adr_dir)
        out = self.step()
        self.assertIn("no %s in this project" % self.adr_dir, out)
        self.assertNotIn("Next free ADR number", out)

    def test_no_decision_log_says_so_and_still_lists_the_adrs(self):
        self.write_adr("0001-a.md", adr("accepted", "A", "Body (NFX-1)."))
        out = self.step()
        self.assertIn("no decision log at", out)
        self.assertIn("- ADR-0001 A: Body (NFX-1).", out)
        self.assertIn("Next free ADR number: 0002", out)

    def test_the_repo_adrs_are_listed(self):
        r = subprocess.run([sys.executable, "-B", SCRIPT, "--name", "t", "--date", "2026-11-19"],
                           cwd=ROOT, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- ADR-0001 ", r.stdout)

    def test_the_file_name_rule_matches_the_repo_format_check(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        sys.path.insert(0, HERE)
        import adr_step
        import test_adr_format
        for name in ("0001-a.md", "0012-two-words.md", "1-a.md", "0001-A.md", "0001-a.txt", "0001-.md", "0001-a--b.md"):
            self.assertEqual(bool(adr_step.NAME_RE.match(name)), bool(test_adr_format.NAME.fullmatch(name)), name)


if __name__ == "__main__":
    unittest.main()
