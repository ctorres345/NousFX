#!/usr/bin/env python3
"""Tests for review.py and gemini_common.py. Run: python3 -B claude/skills/gemini-review/scripts/test_gemini_review.py

Each test runs the script as a subprocess in a throwaway git repo, with a throwaway home and a stub gemini-api
(and a stub gh). The Windows class runs the same cases with --platform windows and a stub named gemini-api.exe.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
VIA = os.path.join(REPO_ROOT, "plugins", "via", "scripts")
sys.path.insert(0, VIA)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import gemini_common  # noqa: E402
import nousfx_testhome as th  # noqa: E402
import via_setup  # noqa: E402

SCRIPT = os.path.join(HERE, "review.py")
STUB = """#!/bin/sh
case "$1" in
  version) cat "$HOME/stub.version" 2>/dev/null || echo "gemini-api 0.4.5" ;;
  whoami) echo "{\\"credentials\\": {\\"api-key\\": {\\"source\\": \\"$(cat "$HOME/stub.source" 2>/dev/null || echo keyring)\\", \\"value\\": \\"AI******99\\"}}}" ;;
  analyze)
    echo "$@" > "$HOME/args.log"; pwd > "$HOME/pwd.log"; ls -A > "$HOME/ls.log"
    echo "${GEMINI_API_KEY:-unset}" > "$HOME/env.log"
    [ -f diff.txt ] && cp diff.txt "$HOME/diff.log"
    case " $* " in *" --stdin "*) cat > "$HOME/stdin.log" ;; esac
    [ -f "$HOME/stub.fail" ] && exit 5
    echo '{"text": "NO FINDINGS", "usage": {}}' ;;
esac
"""
GH = """#!/bin/sh
echo "{\\"nameWithOwner\\": \\"acme/app\\", \\"visibility\\": \\"$(cat "$HOME/stub.vis" 2>/dev/null || echo PUBLIC)\\"}"
"""


def write_exe(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class ReviewCases:
    CLI_NAME = "gemini-api"
    PLATFORM_ARGS = []

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.repo)
        write_exe(os.path.join(self.home, ".local", "bin", self.CLI_NAME), STUB)
        write_exe(os.path.join(self.bin, "gh"), GH)
        for cmd in (["init", "-q"], ["remote", "add", "origin", "git@github.com:acme/app.git"]):
            subprocess.run(["git", "-C", self.repo, *cmd], check=True, capture_output=True)
        self.edit("a.txt", "first\n")
        subprocess.run(["git", "-C", self.repo, "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.email=t@example.com", "-c", "user.name=T",
                        "commit", "-q", "-m", "init"], check=True, capture_output=True)

    def edit(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def stub_setting(self, name, value):
        with open(os.path.join(self.home, name), "w") as f:
            f.write(value)

    def run_script(self, *args, **env_extra):
        env = {"PATH": os.pathsep.join([self.bin, "/usr/bin", "/bin"]), **th.env(self.home), **env_extra}
        return subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, *self.PLATFORM_ARGS, *args],
                              cwd=self.repo, capture_output=True, text=True, env=env)

    def logged(self, name):
        path = os.path.join(self.home, name)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def test_a_normal_diff_is_sent_and_the_reply_is_printed(self):
        self.edit("a.txt", "first\nsecond\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NO FINDINGS", r.stdout)
        self.assertIn("+second", self.logged("diff.log"))
        self.assertIn("gemini-4-argon", self.logged("args.log"))

    def test_a_private_repo_stops_with_exit_4_and_sends_nothing(self):
        self.stub_setting("stub.vis", "PRIVATE")
        self.edit("a.txt", "first\nsecond\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_private_ok_lets_a_private_repo_through(self):
        self.stub_setting("stub.vis", "PRIVATE")
        self.edit("a.txt", "first\nsecond\n")
        r = self.run_script("--private-ok")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_oversized_diff_stops_with_exit_6(self):
        self.edit("a.txt", "abcdefgh\n" * 25000)
        r = self.run_script()
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_secret_in_the_diff_stops_with_exit_7(self):
        self.edit("a.txt", "token = " + "AKIA" + "A" * 16 + "\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_an_empty_diff_stops_with_exit_5(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_secret_looking_files_are_left_out_of_the_diff(self):
        self.edit("a.txt", "first\nsecond\n")
        self.edit("prod.env", "TOKEN=" + "AKIA" + "A" * 16 + "\n")
        subprocess.run(["git", "-C", self.repo, "add", "-N", "prod.env"], check=True, capture_output=True)
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("prod.env", self.logged("diff.log"))

    def test_a_range_that_looks_like_an_option_stops_with_exit_3(self):
        r = self.run_script("--output=x")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_wrong_cli_version_stops_with_exit_3(self):
        self.stub_setting("stub.version", "gemini-api 0.4.50\n")
        self.edit("a.txt", "first\nsecond\n")
        r = self.run_script()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_key_that_is_not_in_the_keyring_stops_with_exit_3(self):
        self.edit("a.txt", "first\nsecond\n")
        for source in ("env", "unset"):
            self.stub_setting("stub.source", source)
            r = self.run_script()
            self.assertEqual(r.returncode, 3, source + r.stderr)
            self.assertIn("keyring", r.stderr)
            self.assertIsNone(self.logged("args.log"))

    def test_the_key_variable_is_not_passed_to_the_cli(self):
        self.edit("a.txt", "first\nsecond\n")
        r = self.run_script(GEMINI_API_KEY="leaked", GOOGLE_API_KEY="leaked")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.logged("env.log").strip(), "unset")

    def test_the_cli_runs_in_an_empty_folder_holding_only_the_diff(self):
        self.edit("a.txt", "first\nsecond\n")
        self.assertEqual(self.run_script().returncode, 0)
        self.assertNotEqual(os.path.realpath(self.logged("pwd.log").strip()), os.path.realpath(self.repo))
        self.assertEqual(self.logged("ls.log").strip(), "diff.txt")

    def test_both_credentials_in_use_must_come_from_the_keyring(self):
        self.edit("a.txt", "first\nsecond\n")
        with open(os.path.join(self.home, ".local", "bin", self.CLI_NAME)) as f:
            text = f.read()
        text = text.replace('"value\\": \\"AI******99\\"}}', '"value\\": \\"AI******99\\"}, \\"access-token\\": {\\"source\\": \\"env\\"}}')
        write_exe(os.path.join(self.home, ".local", "bin", self.CLI_NAME), text)
        r = self.run_script()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_cli_inside_the_repo_is_never_used(self):
        os.remove(os.path.join(self.home, ".local", "bin", self.CLI_NAME))
        write_exe(os.path.join(self.repo, self.CLI_NAME), STUB)
        self.edit("a.txt", "first\nsecond\n")
        r = self.run_script(PATH=os.pathsep.join([self.repo, self.bin, "/usr/bin", "/bin"]))
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_a_failing_cli_stops_with_exit_9(self):
        self.edit("a.txt", "first\nsecond\n")
        self.stub_setting("stub.fail", "1")
        r = self.run_script()
        self.assertEqual(r.returncode, 9, r.stderr)

    # --allow-list: the review-loop mode (NFX-64). The via.json entry decides, and only its code paths are sent.
    def config(self, allowlist):
        os.makedirs(os.path.join(self.home, ".config", "nousfx"), exist_ok=True)
        with open(os.path.join(self.home, ".config", "nousfx", "via.json"), "w") as f:
            json.dump({"version": 1, "outbound_allowlist": allowlist}, f)

    def two_folders(self):
        self.edit("a.txt", "first\nsecond\n")
        for name in ("src/In.kt", "other/Out.kt"):
            os.makedirs(os.path.join(self.repo, os.path.dirname(name)), exist_ok=True)
            self.edit(name, "// new file %s\n" % name)

    def test_allow_list_sends_only_the_code_paths_the_entry_names(self):
        self.two_folders()
        self.config({"acme/app": {"destinations": ["gemini"], "gemini_code_paths": ["src/"]}})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = self.logged("diff.log")
        self.assertIn("src/In.kt", sent)  # an untracked file inside the paths is sent
        self.assertNotIn("other/Out.kt", sent)
        self.assertNotIn("+second", sent)  # a.txt is outside the paths

    def test_allow_list_stops_with_exit_4_when_the_project_is_not_listed(self):
        self.two_folders()
        self.config({})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("not allow-listed", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_allow_list_has_no_exemption_for_a_public_repo(self):
        self.two_folders()  # the gh stub says PUBLIC
        r = self.run_script("--allow-list", "HEAD")  # no via.json at all
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_allow_list_stops_with_exit_4_when_gemini_is_not_a_listed_destination(self):
        self.two_folders()
        self.config({"acme/app": {"destinations": ["jira"], "gemini_code_paths": ["src/"]}})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_allow_list_lets_a_private_repo_through_when_listed(self):
        self.two_folders()
        self.stub_setting("stub.vis", "PRIVATE")
        self.config({"acme/app": {"destinations": ["gemini"], "gemini_code_paths": ["src/"]}})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allow_list_with_a_change_only_outside_the_paths_stops_with_exit_5(self):
        self.edit("a.txt", "first\nsecond\n")
        self.config({"acme/app": {"destinations": ["gemini"], "gemini_code_paths": ["src/"]}})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 5, r.stderr)
        self.assertIn("gemini_code_paths", r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_allow_list_with_no_code_paths_stops_with_exit_4(self):
        self.two_folders()
        self.config({"acme/app": {"destinations": ["gemini"]}})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_allow_list_and_private_ok_together_stop_with_exit_3(self):
        self.two_folders()
        self.config({"acme/app": {"destinations": ["gemini"], "gemini_code_paths": ["src/"]}})
        for args in (("--allow-list", "--private-ok", "HEAD"), ("--private-ok", "--allow-list", "HEAD")):
            r = self.run_script(*args)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIsNone(self.logged("args.log"))

    def test_allow_list_stops_on_a_huge_tracked_diff_before_reading_untracked_files(self):
        self.two_folders()
        self.edit("src/Big.kt", "x\n")
        subprocess.run(["git", "-C", self.repo, "add", "src/Big.kt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.email=t@example.com", "-c", "user.name=T",
                        "commit", "-q", "-m", "big"], check=True, capture_output=True)
        self.edit("src/Big.kt", "abcdefgh\n" * 30000)  # tracked diff alone is over the limit
        self.config({"acme/app": {"destinations": ["gemini"], "gemini_code_paths": ["src/"]}})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertIn("the diff is over", r.stderr)  # printed by the budget guard, before any untracked file is read
        self.assertIsNone(self.logged("args.log"))

    def test_allow_list_keeps_the_secret_and_size_stops(self):
        self.two_folders()
        self.edit("src/In.kt", "token = " + "AKIA" + "A" * 16 + "\n")
        self.config({"acme/app": {"destinations": ["gemini"], "gemini_code_paths": ["src/"]}})
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 7, r.stderr)
        self.edit("src/In.kt", "abcdefgh\n" * 25000)
        r = self.run_script("--allow-list", "HEAD")
        self.assertEqual(r.returncode, 6, r.stderr)
        self.assertIsNone(self.logged("args.log"))

    def test_not_in_a_git_repo_stops_with_exit_3(self):
        r = subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, *self.PLATFORM_ARGS], cwd=self.tmp,
                           capture_output=True, text=True,
                           env={"PATH": os.pathsep.join([self.bin, "/usr/bin", "/bin"]), **th.env(self.home),
                                "GIT_CEILING_DIRECTORIES": os.path.dirname(self.tmp)})
        self.assertEqual(r.returncode, 3, r.stderr)


class ReviewPosix(ReviewCases, unittest.TestCase):
    pass


class ReviewWindows(ReviewCases, unittest.TestCase):
    CLI_NAME = "gemini-api.exe"
    PLATFORM_ARGS = ["--platform", "windows"]


class Common(unittest.TestCase):
    def test_the_secret_pattern_is_the_one_via_setup_uses(self):
        self.assertEqual(gemini_common.SECRET_PATTERN, via_setup.SECRET_RE.pattern)

    def test_the_secret_scan_sees_a_secret_on_a_line_with_invalid_bytes(self):
        data = b"\xff\xfe junk " + b"ghp_" + b"a" * 36 + b"\n"
        with self.assertRaises(SystemExit) as cm:
            gemini_common.stop_on_secret(data)
        self.assertEqual(cm.exception.code, 7)

    def test_a_secret_at_the_start_of_a_removed_diff_line_is_caught(self):
        with self.assertRaises(SystemExit) as cm:
            gemini_common.stop_on_secret(b"-" + b"sk-" + b"a" * 40 + b"\n")
        self.assertEqual(cm.exception.code, 7)

    def test_clean_text_passes_the_secret_scan(self):
        gemini_common.stop_on_secret(b"print('hello')\n")


if __name__ == "__main__":
    unittest.main()
