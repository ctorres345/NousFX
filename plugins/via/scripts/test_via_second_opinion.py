#!/usr/bin/env python3
"""Tests for via_second_opinion.py. Run: python3 -B plugins/via/scripts/test_via_second_opinion.py

The wrapper's contract: exit 0 means a second opinion ran and its findings are on stdout, exit 10 means
"fall back to a fresh-context Claude reviewer", exit 1 means the input was invalid. Until Argon is wired
in (NFX-9) it always answers 10 for valid input.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "via_second_opinion.py")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class ViaSecondOpinionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.shots = []
        for i in range(9):
            path = os.path.join(self.dir, "shot%d.png" % i)
            with open(path, "wb") as f:
                f.write(PNG)
            self.shots.append(path)

    def run_cli(self, shots, brief="Home screen of the sample app."):
        args = []
        for s in shots:
            args += ["--shot", s]
        if brief is not None:
            args += ["--brief", brief]
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, cwd=self.dir)

    def test_valid_input_answers_fallback_with_exit_10_and_a_reason(self):
        r = self.run_cli(self.shots[:2])
        self.assertEqual(r.returncode, 10, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "fallback")
        self.assertTrue(out["reason"].strip())

    def test_it_echoes_the_screenshots_to_review_as_absolute_paths(self):
        r = self.run_cli([os.path.relpath(self.shots[0], self.dir)])
        self.assertEqual(json.loads(r.stdout)["shots"], [os.path.realpath(self.shots[0])])

    def test_it_writes_no_files(self):
        before = sorted(os.listdir(self.dir))
        self.run_cli(self.shots[:3])
        self.assertEqual(sorted(os.listdir(self.dir)), before)

    def test_eight_screenshots_are_the_most_it_takes(self):
        self.assertEqual(self.run_cli(self.shots[:8]).returncode, 10)
        self.assertEqual(self.run_cli(self.shots[:9]).returncode, 1)

    def test_no_screenshots_is_refused(self):
        self.assertEqual(self.run_cli([]).returncode, 1)

    def test_a_missing_file_a_non_image_and_a_symlink_are_refused(self):
        text = os.path.join(self.dir, "notes.txt")
        with open(text, "w") as f:
            f.write("hi")
        link = os.path.join(self.dir, "link.png")
        os.symlink(self.shots[0], link)
        for bad in (os.path.join(self.dir, "missing.png"), text, link):
            with self.subTest(bad=os.path.basename(bad)):
                r = self.run_cli([self.shots[0], bad])
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertTrue(r.stderr.strip())

    def test_a_brief_is_required_so_the_reviewer_knows_what_it_is_looking_at(self):
        self.assertEqual(self.run_cli(self.shots[:1], brief=None).returncode, 1)
        self.assertEqual(self.run_cli(self.shots[:1], brief="  ").returncode, 1)


if __name__ == "__main__":
    unittest.main()
