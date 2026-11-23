#!/usr/bin/env python3
"""Tests for agy_code.py (budget mode, NFX-281). Run: python3 -B claude/skills/budget-mode/scripts/test_agy_code.py"""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
VIA = os.path.join(REPO_ROOT, "plugins", "via", "scripts")
sys.path.insert(0, VIA)
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import agy_code  # noqa: E402
import nousfx_testhome as th  # noqa: E402

SCRIPT = os.path.join(HERE, "agy_code.py")


def write_exe(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


class UnitTests(unittest.TestCase):
    def test_build_command_flags(self):
        cmd = agy_code.build_command("/usr/local/bin/agy", "do the task", agy_code.MODEL, 900)
        self.assertEqual(cmd[0], "/usr/local/bin/agy")
        self.assertEqual(cmd[1], "-p")
        self.assertIn(agy_code.RULES, cmd[2])
        self.assertIn("do the task", cmd[2])
        pairs = list(zip(cmd, cmd[1:]))
        self.assertIn(("--model", "gemini-4-neon-medium"), pairs)
        self.assertIn(("--mode", "accept-edits"), pairs)
        self.assertIn(("--output-format", "json"), pairs)
        self.assertIn(("--print-timeout", "900s"), pairs)
        self.assertIn("--disable-slash-commands", cmd)

    def test_build_command_denies_shell_and_outside_access(self):
        cmd = agy_code.build_command("/usr/local/bin/agy", "do the task", agy_code.MODEL, 900)
        settings = json.loads(cmd[cmd.index("--settings") + 1])
        self.assertIs(settings["allowNonWorkspaceAccess"], False)
        self.assertIn("command(*)", settings["permissions"]["deny"])
        self.assertIn("mcp(*)", settings["permissions"]["deny"])
        self.assertNotIn("edit_file(*)", settings["permissions"]["deny"])

    def test_parse_reply_accepts_valid_json(self):
        raw = json.dumps({"status": "SUCCESS", "response": "ok", "usage": {"input_tokens": 12}})
        reply = agy_code.parse_reply(raw)
        self.assertEqual(reply["status"], "SUCCESS")
        self.assertEqual(reply["response"], "ok")
        self.assertEqual(reply["usage"], {"input_tokens": 12})

    def test_agy_exiting_at_its_own_print_timeout_is_a_timeout(self):
        self.assertEqual(agy_code.classify(1, "", "deadline", 60, 61.0)[:2], ("TIMEOUT", 10))
        self.assertEqual(agy_code.classify(1, "", "boom", 60, 5.0)[:2], ("FAILED", 9))

    def test_parse_reply_rejects_bad_input(self):
        for bad in ("not json", "[]", "{}", '{"status": "SUCCESS"}', '{"response": 42}'):
            with self.assertRaises(ValueError):
                agy_code.parse_reply(bad)

    def test_changed_files_tracks_added_modified_and_reverted(self):
        before = {"same.py": "aaa", "edited.py": "bbb", "reverted.py": "ccc"}
        after = {"same.py": "aaa", "edited.py": "ddd", "added.py": "eee"}
        self.assertEqual(agy_code.changed_files(before, after), ["added.py", "edited.py", "reverted.py"])


    def test_deleting_a_dirty_symlink_counts_as_a_change(self):
        with tempfile.TemporaryDirectory() as d:
            link = os.path.join(d, "link")
            os.symlink("target", link)
            before = {"link": agy_code._sha256(link)}
            os.remove(link)
            after = {"link": agy_code._sha256(link)}
        self.assertEqual(agy_code.changed_files(before, after), ["link"])


class SubprocessTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.home = os.path.join(self.tmp, "home")
        self.main = os.path.join(self.tmp, "main")
        self.repo = os.path.join(self.tmp, "repo")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.home)
        os.makedirs(self.main)
        os.makedirs(self.bin)
        for cmd in (["init", "-q"], ["remote", "add", "origin", "git@github.com:acme/app.git"]):
            subprocess.run(["git", "-C", self.main, *cmd], check=True, capture_output=True)
        with open(os.path.join(self.main, "a.txt"), "w", encoding="utf-8") as f:
            f.write("first\n")
        subprocess.run(["git", "-C", self.main, "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", self.main, "-c", "user.email=t@example.com", "-c", "user.name=T",
             "commit", "-q", "-m", "init"],
            check=True, capture_output=True,
        )
        subprocess.run(["git", "-C", self.main, "worktree", "add", "-q", "-b", "work", self.repo],
                       check=True, capture_output=True)
        self.prompt = os.path.join(self.tmp, "prompt.txt")
        with open(self.prompt, "w", encoding="utf-8") as f:
            f.write("Ticket NFX-281: update a.txt\n")
        self.allowlist({"acme/app": {"destinations": ["agy"], "gemini_code_paths": ["."]}})

    def allowlist(self, entries):
        cfg = os.path.join(self.home, ".config", "nousfx", "via.json")
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "outbound_allowlist": entries}, f)

    def install_stub(self, body):
        path = os.path.join(self.bin, "agy")
        write_exe(path, "#!/bin/sh\n" + body + "\n")
        return path

    def run_cli(self, *args, cwd=None):
        env = {"PATH": os.pathsep.join([self.bin, "/usr/bin", "/bin"]), **th.env(self.home)}
        return subprocess.run(
            [th.python(self.home), "-I", "-B", SCRIPT, *args],
            cwd=cwd or self.repo, capture_output=True, text=True, env=env,
        )

    def test_success_reports_changed_file_and_tokens(self):
        self.install_stub(
            'printf "first\\nsecond\\n" > a.txt\n'
            'echo \'{"status":"SUCCESS","response":"updated a.txt","usage":{"input_tokens":100,"output_tokens":20}}\''
        )
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "SUCCESS")
        self.assertEqual(data["model"], agy_code.MODEL)
        self.assertEqual(data["changed"], ["a.txt"])
        self.assertEqual(data["tokens"], {"input_tokens": 100, "output_tokens": 20})
        self.assertEqual(data["response"], "updated a.txt")

    def test_write_to_ignored_file_or_the_git_file_returns_9(self):
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("local.json\n")
        ok = 'echo \'{"status":"SUCCESS","response":"done"}\''
        for write, path in (("echo x > local.json", "local.json"),
                            ("echo '# x' >> .git", ".git")):
            self.install_stub(write + "\n" + ok)
            r = self.run_cli("--prompt-file", self.prompt)
            self.assertEqual(r.returncode, 9, r.stderr)
            self.assertIn(path, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["status"], "UNSAFE")
            self.assertIn(path, data["changed"])

    def test_a_planted_fsmonitor_never_runs(self):
        self.install_stub('git init -q evil\n'
                          'printf "[core]\\n\\tfsmonitor = touch %s\\n" >> evil/.git/config\n'
                          'echo "gitdir: evil/.git" > .git\n'
                          'echo \'{"status":"SUCCESS","response":"done"}\'' % os.path.join(self.tmp, "pwned"))
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 9, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "pwned")))

    def test_a_main_checkout_or_a_nested_repo_returns_3_before_agy_runs(self):
        self.install_stub('touch ran\necho \'{"status":"SUCCESS","response":"ok"}\'')
        r = self.run_cli("--prompt-file", self.prompt, cwd=self.main)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("linked worktree", r.stderr)
        subprocess.run(["git", "init", "-q", os.path.join(self.repo, "nested")], check=True, capture_output=True)
        for ignore in ("", "nested/\n"):
            with self.subTest(ignored=bool(ignore)):
                with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as f:
                    f.write(ignore)
                r = self.run_cli("--prompt-file", self.prompt)
                self.assertEqual(r.returncode, 3, r.stderr)
                self.assertIn("nested", r.stderr)
        shutil.rmtree(os.path.join(self.repo, "nested"))
        os.remove(os.path.join(self.repo, ".gitignore"))
        sha = subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"], check=True, capture_output=True,
                             text=True).stdout.strip()
        subprocess.run(["git", "-C", self.repo, "update-index", "--add", "--cacheinfo", "160000,%s,sub" % sha],
                       check=True, capture_output=True)
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("sub", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.main, "ran")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "ran")))

    def test_code_paths_that_leave_out_a_file_return_4(self):
        self.allowlist({"acme/app": {"destinations": ["agy"], "gemini_code_paths": ["src/"]}})
        self.install_stub('echo \'{"status":"SUCCESS","response":"ok"}\'')
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("gemini_code_paths", r.stderr)
        self.assertIn("a.txt", r.stderr)

    def test_a_secret_in_the_prompt_returns_7(self):
        with open(self.prompt, "a", encoding="utf-8") as f:
            f.write("token ghp_" + "a" * 36 + "\n")
        self.install_stub('echo \'{"status":"SUCCESS","response":"ok"}\'')
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 7, r.stderr)

    def test_dry_run_prints_command_without_running_agy(self):
        marker = os.path.join(self.tmp, "ran")
        self.install_stub('touch "%s"' % marker)
        r = self.run_cli("--prompt-file", self.prompt, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("--mode", data["command"])
        self.assertFalse(os.path.exists(marker))

    def test_nonzero_exit_from_agy_returns_9(self):
        self.install_stub('echo "boom" >&2\nexit 1')
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 9, r.stderr)
        self.assertIn("Stopped:", r.stderr)
        self.assertIn("boom", r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "FAILED")

    def test_error_status_json_returns_9(self):
        self.install_stub('echo \'{"status":"ERROR","response":"could not edit","usage":{}}\'')
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 9, r.stderr)
        self.assertIn("Stopped:", r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "ERROR")

    def test_quota_exceeded_returns_21(self):
        self.install_stub('echo "quota exceeded for today" >&2\nexit 1')
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 21, r.stderr)
        self.assertIn("Stopped:", r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "LIMIT")

    def test_a_secret_in_an_uncommitted_file_returns_7_before_agy_runs(self):
        with open(os.path.join(self.repo, ".gitignore"), "a", encoding="utf-8") as f:
            f.write("*.cfg\n.env\n")
        self.install_stub('touch ran\necho \'{"status":"SUCCESS","response":"ok"}\'')
        for name, text in ((".env", "X=1\n"), ("app.cfg", "token ghp_" + "b" * 36 + "\n"),
                           ("notes.txt", "token ghp_" + "c" * 36 + "\n")):
            with self.subTest(name=name):
                with open(os.path.join(self.repo, name), "w", encoding="utf-8") as f:
                    f.write(text)
                r = self.run_cli("--prompt-file", self.prompt)
                self.assertEqual(r.returncode, 7, r.stderr)
                self.assertIn(name, r.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.repo, "ran")))
                os.remove(os.path.join(self.repo, name))

    def test_missing_agy_returns_22(self):
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 22, r.stderr)
        self.assertIn("Stopped:", r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "UNAVAILABLE")

    def test_not_allow_listed_returns_4(self):
        self.allowlist({})
        self.install_stub('echo \'{"status":"SUCCESS","response":"ok"}\'')
        r = self.run_cli("--prompt-file", self.prompt)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("Stopped:", r.stderr)

    def test_bad_timeout_returns_2(self):
        for bad in ("10", "9999"):
            r = self.run_cli("--prompt-file", self.prompt, "--timeout", bad)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("Stopped:", r.stderr)

    def test_timeout_kills_agy_and_returns_10(self):
        stub = self.install_stub("sleep 30")
        orig_cwd = os.getcwd()
        self.addCleanup(os.chdir, orig_cwd)
        os.chdir(self.repo)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(agy_code, "KILL_GRACE_SECONDS", 0), \
                mock.patch.object(agy_code, "MIN_TIMEOUT", 1), \
                mock.patch.object(agy_code.gc, "plan_route", return_value={"agy": True, "gemini": False, "paths": ["."]}), \
                mock.patch.object(agy_code.gc, "outside_paths", return_value=[]), \
                mock.patch.object(agy_code.vac, "find_agy", return_value=stub), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                agy_code.main(["--prompt-file", self.prompt, "--timeout", "1"])
        self.assertEqual(cm.exception.code, 10)
        self.assertIn("Stopped:", err.getvalue())
        data = json.loads(out.getvalue())
        self.assertEqual(data["status"], "TIMEOUT")


if __name__ == "__main__":
    unittest.main()
