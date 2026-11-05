#!/usr/bin/env python3
"""Tests for claude/skills/ui-review/scripts/argon_review.py. Run: python3 -B plugins/via/scripts/test_argon_review.py

It must read the outbound allow-list in ~/.claude/via.json (through via_outbound.py) instead of a separate text file.
Each test runs the script as a subprocess in a throwaway git repo, with a throwaway HOME and a stub gemini-api.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

import nousfx_testhome as th

sys.dont_write_bytecode = True

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
SKILLS = os.path.join(REPO_ROOT, "claude", "skills")
SCRIPT = os.path.join(SKILLS, "ui-review", "scripts", "argon_review.py")
KEY = "acme/app"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
STUB = """#!/bin/sh
case "$1" in
  version) echo "gemini-api 0.4.5" ;;
  whoami) echo '{"credentials": {"api-key": {"source": "keyring", "value": "AI******99"}}}' ;;
  analyze) echo "$@" > "$HOME/args.log"; cat > "$HOME/stdin.log"; echo '{"text": "NO FINDINGS", "usage": {}}' ;;
esac
"""


class ArgonReviewTest(unittest.TestCase):
    CLI_NAME = "gemini-api"
    PLATFORM_ARGS = []

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.home, ".local", "bin"))
        os.makedirs(os.path.join(self.repo, "ui"))
        os.makedirs(os.path.join(self.repo, "other"))
        stub = os.path.join(self.home, ".local", "bin", self.CLI_NAME)
        with open(stub, "w") as f:
            f.write(STUB)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)
        for cmd in (["init", "-q"], ["remote", "add", "origin", "git@github.com:%s.git" % KEY]):
            subprocess.run(["git", "-C", self.repo, *cmd], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.email=t@example.com", "-c", "user.name=T",
                        "commit", "-q", "--allow-empty", "-m", "init"], check=True, capture_output=True)
        for name in ("ui/Seen.kt", "other/Private.kt"):
            with open(os.path.join(self.repo, name), "w") as f:
                f.write("// new file %s\n" % name)
        self.shot = os.path.join(self.tmp, "shot.png")
        with open(self.shot, "wb") as f:
            f.write(PNG)

    def config(self, allowlist):
        os.makedirs(os.path.join(self.home, ".config", "nousfx"), exist_ok=True)
        with open(os.path.join(self.home, ".config", "nousfx", "via.json"), "w") as f:
            json.dump({"version": 1, "outbound_allowlist": allowlist}, f)

    def run_script(self, script=SCRIPT, brief="Home screen.", extra=()):
        env = {"PATH": os.pathsep.join([th.bin_dir(self.home), os.path.dirname(sys.executable), "/usr/bin", "/bin"]),
               **th.env(self.home)}
        return subprocess.run([th.python(self.home), "-I", "-B", script, "--shot", self.shot, "--range", "HEAD",
                               "--brief", brief, *extra, *self.PLATFORM_ARGS],
                              cwd=self.repo, capture_output=True, text=True, env=env)

    def logged(self, name):
        path = os.path.join(self.home, name)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def test_a_project_not_on_the_allow_list_stops_with_exit_4_and_sends_nothing(self):
        self.config({})
        r = self.run_script()
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("allow-list", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_project_listed_without_gemini_is_stopped_too(self):
        self.config({KEY: {"destinations": ["jira"], "gemini_code_paths": ["ui/"]}})
        self.assertEqual(self.run_script().returncode, 4)
        self.assertIsNone(self.logged("args.log"))

    def test_a_listed_project_with_no_code_paths_is_stopped(self):
        self.config({KEY: {"destinations": ["gemini"]}})
        r = self.run_script()
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_listed_project_sends_only_the_code_paths_it_names(self):
        self.config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = self.logged("stdin.log")
        self.assertIn("ui/Seen.kt", sent)
        self.assertNotIn("other/Private.kt", sent)

    def test_a_malformed_config_stops_with_exit_3(self):
        os.makedirs(os.path.join(self.home, ".config", "nousfx"))
        with open(os.path.join(self.home, ".config", "nousfx", "via.json"), "w") as f:
            f.write("{nope")
        r = self.run_script()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_without_via_outbound_the_script_stops_instead_of_sending(self):
        copy = os.path.join(self.tmp, "copy", "ui-review", "scripts")
        os.makedirs(copy)
        shutil.copy(SCRIPT, copy)
        shutil.copytree(os.path.join(SKILLS, "gemini-review"), os.path.join(self.tmp, "copy", "gemini-review"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script(os.path.join(copy, "argon_review.py"))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("via_outbound.py", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_via_outbound_in_the_plugin_cache_is_never_used(self):
        cache = os.path.join(self.home, ".claude", "plugins", "cache", "nousfx", "via", "9.9.9", "scripts")
        os.makedirs(cache)
        with open(os.path.join(cache, "via_outbound.py"), "w") as f:
            f.write("import sys\nif sys.argv[1] == 'paths':\n    sys.stdout.write('ui/\\0')\nsys.exit(0)\n")  # always allows
        copy = os.path.join(self.tmp, "copy", "ui-review", "scripts")
        os.makedirs(copy)
        shutil.copy(SCRIPT, copy)
        shutil.copytree(os.path.join(SKILLS, "gemini-review"), os.path.join(self.tmp, "copy", "gemini-review"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script(os.path.join(copy, "argon_review.py"))
        self.assertEqual(r.returncode, 3, r.stderr)  # not found next to the script, and the cache copy is ignored
        self.assertIsNone(self.logged("args.log"))

    def test_via_is_found_next_to_the_repo_without_any_plugin_cache(self):
        self.config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["ui/"]}})
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "plugins")))
        self.assertEqual(self.run_script().returncode, 0)

    def test_a_secret_in_the_brief_stops_with_exit_7(self):
        self.config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script(brief="key " + "AKIA" + "A" * 16)
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_key_that_is_not_in_the_keyring_stops_with_exit_3(self):
        self.config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["ui/"]}})
        stub = os.path.join(self.home, ".local", "bin", self.CLI_NAME)
        with open(stub) as f:
            text = f.read().replace('"source": "keyring"', '"source": "env"')
        with open(stub, "w") as f:
            f.write(text)
        r = self.run_script()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_no_reference_to_the_old_allow_list_file_remains_in_the_script(self):
        with open(SCRIPT) as f:
            self.assertNotIn("argon-" + "repos", f.read())  # the retired allow-list file's name


class ArgonReviewWindowsTest(ArgonReviewTest):
    CLI_NAME = "gemini-api.exe"
    PLATFORM_ARGS = ["--platform", "windows"]


if __name__ == "__main__":
    unittest.main()
