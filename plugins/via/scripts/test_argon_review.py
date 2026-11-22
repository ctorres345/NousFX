#!/usr/bin/env python3
"""Tests for claude/skills/gemini-review/scripts/argon_review.py. Run: python3 -B plugins/via/scripts/test_argon_review.py

It must read the outbound allow-list in ~/.claude/via.json (through via_outbound.py) instead of a separate text file.
Each test runs the script as a subprocess in a throwaway git repo, with a throwaway HOME, a stub gemini-api and,
for the agy route (NFX-164), a stub agy in ~/.local/bin whose behavior ~/agy-mode picks.
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
SCRIPT = os.path.join(SKILLS, "gemini-review", "scripts", "argon_review.py")
KEY = "acme/app"
NO_ANSWER = "did not answer in"  # how argon_review.py says via_outbound.py ran out of time
OUTBOUND_LIMIT = 15  # the copy's limit: 15x the slowest answer seen at load 200 (0.9 s), far below the stub's sleep
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
STUB = """#!/bin/sh
case "$1" in
  version) echo "gemini-api 0.4.5" ;;
  whoami) echo '{"credentials": {"api-key": {"source": "keyring", "value": "AI******99"}}}' ;;
  analyze) echo "$@" > "$HOME/args.log"; cat diff.txt > "$HOME/stdin.log"; echo '{"text": "NO FINDINGS", "usage": {}}' ;;
esac
"""
AGY = """#!/bin/sh
printf '%s\\n' "$@" > "$HOME/agy-args.log"
ls > "$HOME/agy-files.log"
cat diff.txt > "$HOME/agy-sent.log"
case "$(cat "$HOME/agy-mode" 2>/dev/null)" in
  limit) echo "Error: RESOURCE_EXHAUSTED: quota exceeded" >&2; exit 1 ;;
  signed-out) echo "Error: not authenticated, please sign in" >&2; exit 1 ;;
  fail) echo "Error: model crashed" >&2; exit 3 ;;
esac
echo '{"conversation_id": "abcdef12-3456", "status": "SUCCESS", "response": "NO FINDINGS", "usage": {"input_tokens": 5, "output_tokens": 2}}'
"""


class ArgonReviewTest(unittest.TestCase):
    CLI_NAME = "gemini-api"
    PLATFORM_ARGS = []
    SLOW_CASES = True  # the via_outbound.py limit does not depend on the platform, so its slow cases run once

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

    def argon(self, script, *args, tries=2):
        """Runs a gemini-review script, and once more when via_outbound.py did not answer in time (NFX-203).
        A loaded machine can stall that one subprocess past its limit; any other result is kept as it is."""
        env = {"PATH": os.pathsep.join([th.bin_dir(self.home), os.path.dirname(sys.executable), "/usr/bin", "/bin"]),
               **th.env(self.home)}
        for _ in range(tries):
            r = subprocess.run([th.python(self.home), "-I", "-B", script, *self.PLATFORM_ARGS, *args],
                               cwd=self.repo, capture_output=True, text=True, env=env)
            if not (r.returncode == 3 and NO_ANSWER in r.stderr):
                break
        return r

    def run_script(self, script=SCRIPT, brief="Home screen.", extra=(), tries=2):
        return self.argon(script, "--shot", self.shot, "--range", "HEAD", "--brief", brief, *extra, tries=tries)

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
        copy = os.path.join(self.tmp, "copy", "gemini-review", "scripts")
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
        copy = os.path.join(self.tmp, "copy", "gemini-review", "scripts")
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

    def stub_agy(self, mode=""):
        path = os.path.join(self.home, ".local", "bin", "agy")
        with open(path, "w") as f:
            f.write(AGY)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        with open(os.path.join(self.home, "agy-mode"), "w") as f:
            f.write(mode)

    def test_a_project_that_lists_agy_reviews_through_agy_and_never_calls_the_billed_key(self):
        self.stub_agy()
        self.config({KEY: {"destinations": ["agy", "gemini"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        reply = json.loads(r.stdout)
        self.assertEqual((reply["route"], reply["text"]), ("agy", "NO FINDINGS"))
        self.assertEqual(reply["usage"], {"input_tokens": 5, "output_tokens": 2})
        self.assertNotIn("fallback", reply)
        self.assertIsNone(self.logged("args.log"))
        self.assertEqual(self.logged("agy-files.log").split(), ["diff.txt", "shot0.png"])
        sent = self.logged("agy-sent.log")
        self.assertIn("ui/Seen.kt", sent)
        self.assertNotIn("other/Private.kt", sent)
        args = self.logged("agy-args.log")
        self.assertIn("gemini-4-argon-medium", args)
        self.assertIn("read no other file", args.lower())

    def test_agy_alone_is_enough_without_a_gemini_key(self):
        self.stub_agy()
        self.config({KEY: {"destinations": ["agy"], "gemini_code_paths": ["ui/"]}})
        os.remove(os.path.join(self.home, ".local", "bin", self.CLI_NAME))
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["route"], "agy")

    def test_at_the_agy_limit_the_billed_key_answers_and_says_so(self):
        for mode in ("limit", "signed-out"):
            with self.subTest(mode=mode):
                self.stub_agy(mode)
                self.config({KEY: {"destinations": ["agy", "gemini"], "gemini_code_paths": ["ui/"]}})
                r = self.run_script()
                self.assertEqual(r.returncode, 0, r.stderr)
                reply = json.loads(r.stdout)
                self.assertEqual(reply["route"], "gemini-api")
                self.assertIn("billed Gemini API key was used", reply["fallback"])
                self.assertIn("billed Gemini API key was used", r.stderr)
                self.assertIn("ui/Seen.kt", self.logged("stdin.log"))

    def test_without_agy_installed_the_billed_key_answers(self):
        self.config({KEY: {"destinations": ["agy", "gemini"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        reply = json.loads(r.stdout)
        self.assertEqual(reply["route"], "gemini-api")
        self.assertIn("not installed", reply["fallback"])

    def test_at_the_agy_limit_a_project_without_gemini_stops_and_never_calls_the_billed_key(self):
        self.stub_agy("limit")
        self.config({KEY: {"destinations": ["agy"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script()
        self.assertEqual(r.returncode, 9, r.stderr)
        self.assertIn("subscription limit", r.stderr)
        self.assertIn("does not allow the billed Gemini API", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_any_other_agy_failure_stops_without_falling_back(self):
        self.stub_agy("fail")
        self.config({KEY: {"destinations": ["agy", "gemini"], "gemini_code_paths": ["ui/"]}})
        r = self.run_script()
        self.assertEqual(r.returncode, 9, r.stderr)
        self.assertIn("agy failed (exit 3)", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_the_code_review_goes_through_agy_too(self):
        self.stub_agy()
        self.config({KEY: {"destinations": ["agy"], "gemini_code_paths": ["ui/"]}})
        r = self.argon(os.path.join(SKILLS, "gemini-review", "scripts", "review.py"), "--allow-list", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["route"], "agy")
        self.assertEqual(self.logged("agy-files.log").split(), ["diff.txt"])
        self.assertNotIn("other/Private.kt", self.logged("agy-sent.log"))

    def test_the_code_review_runs_the_agy_model_it_is_given(self):
        self.stub_agy()
        self.config({KEY: {"destinations": ["agy"], "gemini_code_paths": ["."]}})
        r = self.argon(os.path.join(SKILLS, "gemini-review", "scripts", "review.py"), "--allow-list",
                       "--agy-model", "gemini-4-neon-high", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["route"], "agy")
        args = self.logged("agy-args.log").split("\n")
        self.assertEqual(args[args.index("--model") + 1], "gemini-4-neon-high")

    def test_with_an_agy_model_the_agy_limit_stops_without_the_billed_key(self):
        for mode in ("limit", "signed-out"):
            with self.subTest(mode=mode):
                self.stub_agy(mode)
                self.config({KEY: {"destinations": ["agy", "gemini"], "gemini_code_paths": ["."]}})
                r = self.argon(os.path.join(SKILLS, "gemini-review", "scripts", "review.py"), "--allow-list",
                               "--agy-model", "gemini-4-neon-high", "HEAD")
                self.assertEqual(r.returncode, 9, r.stderr)
                self.assertIsNone(self.logged("args.log"))

    def slow_outbound(self, slow_calls, only=""):
        """A copy of gemini-review whose via_outbound.py stub sleeps past the copy's OUTBOUND_LIMIT on its first
        `slow_calls` calls (-1: always; `only`: just that subcommand), then allows ui/ for gemini (not agy).
        Returns the copy's argon_review.py."""
        if not self.SLOW_CASES:
            self.skipTest("runs in ArgonReviewTest only: each case waits OUTBOUND_LIMIT seconds")
        top = os.path.join(self.tmp, "copy")
        scripts = os.path.join(top, "claude", "skills", "gemini-review", "scripts")
        shutil.copytree(os.path.join(SKILLS, "gemini-review"), os.path.dirname(scripts),
                        ignore=shutil.ignore_patterns("__pycache__"))
        common = os.path.join(scripts, "gemini_common.py")
        with open(common) as f:
            text = f.read()
        self.assertIn("\nVIA_TIMEOUT = 30 ", text)
        with open(common, "w") as f:
            f.write(text.replace("\nVIA_TIMEOUT = 30 ", "\nVIA_TIMEOUT = %d " % OUTBOUND_LIMIT))
        via = os.path.join(top, "plugins", "via", "scripts")
        os.makedirs(via)
        count = os.path.join(self.tmp, "outbound-calls")
        with open(os.path.join(via, "via_outbound.py"), "w") as f:
            f.write("import os, sys, time\n"
                    "count = %r\n"
                    "n = len(open(count).read()) if os.path.exists(count) else 0\n"
                    "open(count, 'a').write('.')\n"
                    "if (%d < 0 or n < %d) and sys.argv[1].startswith(%r):\n"
                    "    time.sleep(120)\n"
                    "if sys.argv[1] == 'paths':\n"
                    "    sys.stdout.write('ui/\\0')\n"
                    "sys.exit(20 if sys.argv[1] == 'check' and 'agy' in sys.argv else 0)\n" % (count, slow_calls, slow_calls, only))
        self.config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["ui/"]}})
        return os.path.join(scripts, "argon_review.py")

    def test_an_allow_list_check_that_does_not_answer_in_time_stops_with_exit_3_and_says_so(self):
        r = self.run_script(self.slow_outbound(slow_calls=-1), tries=1)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("via_outbound.py %s %d s" % (NO_ANSWER, OUTBOUND_LIMIT), r.stderr)
        self.assertIn("Nothing was sent", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_code_path_list_that_does_not_answer_in_time_is_not_reported_as_no_code_paths(self):
        r = self.run_script(self.slow_outbound(slow_calls=-1, only="paths"), tries=1)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn(NO_ANSWER, r.stderr)
        self.assertNotIn("names no gemini_code_paths", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_the_tests_retry_once_when_the_allow_list_check_runs_out_of_time(self):
        r = self.run_script(self.slow_outbound(slow_calls=1))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ui/Seen.kt", self.logged("stdin.log"))

    def test_the_tests_still_fail_when_the_allow_list_check_runs_out_of_time_twice(self):
        r = self.run_script(self.slow_outbound(slow_calls=-1))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertEqual(len(open(os.path.join(self.tmp, "outbound-calls")).read()), 2)  # one retry, no more

    def test_no_reference_to_the_old_allow_list_file_remains_in_the_script(self):
        with open(SCRIPT) as f:
            self.assertNotIn("argon-" + "repos", f.read())  # the retired allow-list file's name


class ArgonReviewWindowsTest(ArgonReviewTest):
    CLI_NAME = "gemini-api.exe"
    PLATFORM_ARGS = ["--platform", "windows"]
    SLOW_CASES = False


if __name__ == "__main__":
    unittest.main()
