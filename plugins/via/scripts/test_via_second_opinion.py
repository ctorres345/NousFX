#!/usr/bin/env python3
"""Tests for via_second_opinion.py. Run: python3 -B plugins/via/scripts/test_via_second_opinion.py

The wrapper's contract: exit 0 means Argon ran and its answer is on stdout, exit 10 means "fall back to a
fresh-context reviewer", exit 1 means the input was invalid and nothing was sent.
Each test runs the script as a subprocess on a throwaway git project, with a throwaway HOME, a config
file and a stub `gemini-api`. PATH holds only the system folders, so a real gemini-api is never found.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

import nousfx_testhome as th

sys.dont_write_bytecode = True

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "via_second_opinion.py")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
KEY = "acme/app"
FAKE_KEY = "AQ.FAKEKEYFAKEKEYFAKEKEYFAKEKEY99"
STUB = """#!/bin/sh
echo "$@" > "$HOME/args.log"
pwd > "$HOME/cwd.log"
ls -A | wc -l | tr -d ' ' > "$HOME/cwd-files.log"
[ -f "$HOME/fail" ] && exit 3
echo '{"text": "high | home-light-phone.png | low contrast | use a darker grey", "usage": {"input_tokens": 900, "output_tokens": 120}}'
"""


class ViaSecondOpinionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, "home")
        self.project = os.path.join(tmp.name, "project")
        self.elsewhere = os.path.join(tmp.name, "elsewhere")
        for d in (self.home, self.project, self.elsewhere):
            os.makedirs(d)
        os.makedirs(os.path.join(self.home, ".config", "nousfx"))
        self.config = os.path.join(self.home, ".config", "nousfx", "via.json")  # a config must live in ~/.config/nousfx
        self.git("init", "-q")
        self.git("remote", "add", "origin", "git@github.com:%s.git" % KEY)
        self.run_dir = os.path.join(self.project, ".via", "runs", "20261103T000000Z")
        os.makedirs(self.run_dir)
        self.allow()
        self.names = ["shot%d.png" % i for i in range(9)]
        self.write_record(self.names, review="kept")

    def git(self, *args):
        subprocess.run(["git", "-C", self.project, *args], check=True, capture_output=True)

    def allow(self, destinations=("gemini",), gemini="present"):
        with open(self.config, "w") as f:
            json.dump({"version": 1, "integrations": {"gemini": gemini},
                       "outbound_allowlist": {KEY: {"destinations": list(destinations)}}}, f)

    def write_record(self, names, review="kept"):
        shots = []
        for n in names:
            with open(os.path.join(self.run_dir, n), "wb") as f:
                f.write(PNG)
            shots.append({"path": n, "screen": "home", "theme": "light", "size": "phone", "redacted": True,
                          **({"review": review} if review else {})})
        with open(os.path.join(self.run_dir, "findings.json"), "w") as f:
            json.dump({"version": 1, "mode": "audit", "scope": "all", "platforms": ["web"],
                       "findings": [], "screenshots": shots}, f)

    def stub(self):
        path = os.path.join(self.home, ".local", "bin", "gemini-api")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(STUB)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def shot(self, n):
        return os.path.join(self.run_dir, self.names[n])

    def run_cli(self, shots=None, brief="Home screen of the sample app.", extra=()):
        shots = [self.shot(0)] if shots is None else shots
        args = ["--run-dir", self.run_dir, "--project", self.project, "--config", self.config, *extra]
        for s in shots:
            args += ["--shot", s]
        if brief is not None:
            args += ["--brief", brief]
        return subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, *args], capture_output=True, text=True,
                              cwd=self.elsewhere, env={"PATH": "/usr/bin:/bin", **th.env(self.home)})

    def logged(self, name):
        path = os.path.join(self.home, name)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read().strip()

    def fallback(self, r, text):
        self.assertEqual(r.returncode, 10, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "fallback")
        self.assertIn(text, out["reason"])
        self.assertIsNone(self.logged("args.log"), "gemini-api must not run on a fallback")
        return out

    # --- the Argon branch and its fallbacks ---

    def test_an_allowed_project_with_a_gemini_key_runs_argon_on_the_kept_shots(self):
        self.stub()
        r = self.run_cli([self.shot(0), self.shot(1)])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["status"], out["who"]), ("ran", "argon"))
        self.assertIn("low contrast", out["text"])
        self.assertEqual(out["usage"], {"input_tokens": 900, "output_tokens": 120})
        args = self.logged("args.log")
        self.assertIn("gemini-4-argon", args)
        for n in (0, 1):
            self.assertIn("-i " + os.path.realpath(self.shot(n)), args)

    def test_argon_runs_from_an_empty_folder_so_no_repo_file_is_in_reach(self):
        self.stub()
        self.run_cli()
        self.assertEqual(self.logged("cwd-files.log"), "0")
        self.assertNotEqual(os.path.realpath(self.logged("cwd.log")), os.path.realpath(self.project))

    def test_a_project_not_on_the_allow_list_falls_back_and_sends_nothing(self):
        self.stub()
        with open(self.config, "w") as f:
            json.dump({"version": 1, "integrations": {"gemini": "present"}, "outbound_allowlist": {}}, f)
        self.fallback(self.run_cli(), "allow-list")

    def test_an_allowed_project_that_does_not_list_gemini_falls_back(self):
        self.stub()
        self.allow(destinations=("jira",))
        self.fallback(self.run_cli(), "allow")

    def test_no_gemini_cli_falls_back_and_the_reason_says_no_gemini_key(self):
        self.fallback(self.run_cli(), "no Gemini key")

    def test_a_config_that_does_not_report_a_gemini_key_falls_back(self):
        self.stub()
        self.allow(gemini="no-key")
        self.fallback(self.run_cli(), "no Gemini key")

    def test_a_failing_gemini_cli_falls_back_with_its_exit_code(self):
        self.stub()
        open(os.path.join(self.home, "fail"), "w").close()
        r = self.run_cli()
        self.assertEqual(r.returncode, 10, r.stderr)
        self.assertIn("argon failed (exit 3)", json.loads(r.stdout)["reason"])

    def test_a_fallback_echoes_the_shots_as_absolute_paths(self):
        out = self.fallback(self.run_cli([self.shot(0), self.shot(1)]), "no Gemini key")
        self.assertEqual(out["shots"], [os.path.realpath(self.shot(0)), os.path.realpath(self.shot(1))])

    def test_a_malformed_config_is_refused_not_treated_as_allowed(self):
        self.stub()
        with open(self.config, "w") as f:
            f.write("{nope")
        r = self.run_cli()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIsNone(self.logged("args.log"))

    # --- what may leave the run folder ---

    def test_a_shot_not_marked_kept_is_refused(self):
        self.stub()
        for review in ("dropped", None):
            with self.subTest(review=review):
                self.write_record(self.names, review=review)
                r = self.run_cli()
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIsNone(self.logged("args.log"))

    def test_a_shot_outside_the_run_folder_is_refused_even_if_it_has_the_same_name(self):
        self.stub()
        outside = os.path.join(self.elsewhere, self.names[0])
        with open(outside, "wb") as f:
            f.write(PNG)
        r = self.run_cli([outside])
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIsNone(self.logged("args.log"))

    def test_eight_screenshots_are_the_most_it_takes(self):
        self.assertEqual(self.run_cli([self.shot(i) for i in range(8)]).returncode, 10)
        r = self.run_cli([self.shot(i) for i in range(9)])
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_no_screenshots_is_refused(self):
        self.assertEqual(self.run_cli([]).returncode, 1)

    def test_a_non_image_and_a_symlink_are_refused(self):
        text = os.path.join(self.run_dir, "notes.txt")
        with open(text, "w") as f:
            f.write("hi")
        link = os.path.join(self.run_dir, "link.png")
        os.symlink(self.shot(0), link)
        for bad in (text, link):
            with self.subTest(bad=os.path.basename(bad)):
                r = self.run_cli([self.shot(0), bad])
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertTrue(r.stderr.strip())

    # --- the brief and the cost ---

    def test_a_brief_is_required_so_the_reviewer_knows_what_it_is_looking_at(self):
        self.assertEqual(self.run_cli(brief=None).returncode, 1)
        self.assertEqual(self.run_cli(brief="  ").returncode, 1)

    def test_a_brief_that_looks_like_a_secret_is_refused_before_anything_is_sent(self):
        self.stub()
        r = self.run_cli(brief="Login screen, key " + FAKE_KEY)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIsNone(self.logged("args.log"))

    def test_it_writes_no_files(self):
        self.stub()
        before = (sorted(os.listdir(self.run_dir)), sorted(os.listdir(self.project)))
        self.run_cli([self.shot(0), self.shot(1)])
        self.assertEqual((sorted(os.listdir(self.run_dir)), sorted(os.listdir(self.project))), before)


if __name__ == "__main__":
    unittest.main()
