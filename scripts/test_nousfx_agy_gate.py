#!/usr/bin/env python3
"""Tests for the agy gate adapter (harness/agy/agy_gate.py).
Run: python3 -B scripts/test_nousfx_agy_gate.py

The adapter is run as agy runs it: a JSON payload on stdin, the answer as JSON on stdout. It lives in a
temp "hooks" folder next to a copy of the gate, like the installed plugin. A deny is a JSON line with
decision "deny"; an allow is silence, so agy's own permission prompt still applies.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ADAPTER = os.path.join(REPO, "harness", "agy", "agy_gate.py")
GATE_DIR = os.path.join(REPO, "claude", "hooks")
GATE_FILES = ("review-gate.py", "pr_create_parser.py")
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}  # tests must not touch another repo


def payload(command, workspaces, name="run_command", **args):
    return {"toolCall": {"name": name, "args": {"CommandLine": command, **args}}, "workspacePaths": workspaces}


def body_line(repo):
    """The `Review-Gate:` line for HEAD's tree in repo (NFX-85: a create needs it in the PR body)."""
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, capture_output=True, text=True,
                          env=CLEAN_ENV).stdout.strip()
    return "Review-Gate: " + tree


def load_adapter(path):
    spec = importlib.util.spec_from_file_location("agy_gate_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="agy-gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hooks = os.path.join(self.tmp, "plugins", "nousfx", "hooks")  # where sync puts it
        os.makedirs(self.hooks)
        shutil.copy(ADAPTER, self.hooks)
        for name in GATE_FILES:
            shutil.copy(os.path.join(GATE_DIR, name), self.hooks)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.init_repo(self.repo, "cesar/DEM-1")

    def init_repo(self, path, branch):
        """A repo with one commit: an unborn repo would be denied by a git error, not by the gate's rules."""
        self.sh("git", "init", "-q", "-b", branch, cwd=path)
        with open(os.path.join(path, "f.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        os.makedirs(os.path.join(path, ".nousfx"))  # the check command --mark runs (NFX-54)
        with open(os.path.join(path, ".nousfx", "project.toml"), "w", encoding="utf-8") as f:
            f.write('[check]\ncommand = "exit 0"\n')
        self.sh("git", "add", "-A", cwd=path)
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=path)

    def sh(self, *args, cwd=None):
        subprocess.run(args, cwd=cwd or self.repo, check=True, capture_output=True, env=CLEAN_ENV)

    def mark(self):
        subprocess.run([sys.executable, "-B", os.path.join(GATE_DIR, "review-gate.py"), "--mark", "--findings", "0"],
                       cwd=self.repo, check=True, capture_output=True, env=CLEAN_ENV)

    def expect(self):
        """The --expect value sync writes into hooks.json: hashes of the three files as they are now."""
        names = ("agy_gate.py", "review-gate.py", "pr_create_parser.py")
        return ",".join(hashlib.sha256(open(os.path.join(self.hooks, n), "rb").read()).hexdigest() for n in names)

    def run_adapter(self, stdin, expect=None):
        text = stdin if isinstance(stdin, str) else json.dumps(stdin)
        args = [] if expect == "" else ["--expect", expect or self.expect()]
        return subprocess.run([sys.executable, "-I", "-B", os.path.join(self.hooks, "agy_gate.py"), *args],
                              input=text, cwd=self.tmp, capture_output=True, text=True, timeout=60, env=CLEAN_ENV)

    def decision(self, stdin):
        """(exit code, decision or None) for one payload."""
        r = self.run_adapter(stdin)
        out = r.stdout.strip()
        return r.returncode, (json.loads(out).get("decision") if out else None)

    def assertDeny(self, stdin):
        code, decision = self.decision(stdin)
        self.assertEqual((code, decision), (0, "deny"), stdin)

    def assertSilent(self, stdin):
        r = self.run_adapter(stdin)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""), (stdin, r.stderr))


class Gate(Base):
    def test_blocked_without_review_with_a_reason(self):
        r = self.run_adapter(payload("gh pr create --fill", [self.repo]))
        out = json.loads(r.stdout)
        self.assertEqual(out["decision"], "deny")
        self.assertIn("review", out["reason"].lower())

    def test_allowed_after_review(self):
        self.mark()
        self.assertDeny(payload("gh pr create --fill", [self.repo]))  # no Review-Gate line in a body (NFX-85)
        self.assertSilent(payload(f"gh pr create --body '{body_line(self.repo)}'", [self.repo]))

    def test_blocked_on_wrong_branch(self):
        self.mark()
        self.sh("git", "branch", "-m", "claude/x")
        self.assertDeny(payload("gh pr create", [self.repo]))

    def test_other_commands_are_left_to_agy(self):
        self.assertSilent(payload("git status", [self.repo]))

    def test_other_tools_are_left_to_agy(self):
        self.assertSilent(payload("gh pr create", [self.repo], name="view_file"))

    def test_workspace_that_is_not_a_repo_judges_the_command(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertSilent(payload("ls", [plain]))
        self.assertDeny(payload(f"cd {self.repo} && gh pr create", [plain]))

    def test_every_workspace_is_checked(self):
        self.mark()
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self.init_repo(other, "cesar/DEM-2")
        r = self.run_adapter(payload(f"gh pr create -b '{body_line(self.repo)}'", [self.repo, other]))  # the second repo has no review
        self.assertIn("no review recorded", json.loads(r.stdout)["reason"])

    def test_a_folder_argument_is_checked_too(self):
        """A Cwd in the call can point at another repo than the workspace: check that one as well."""
        self.mark()
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        unreviewed = os.path.join(self.tmp, "unreviewed")
        os.makedirs(unreviewed)
        self.init_repo(unreviewed, "cesar/DEM-3")
        self.assertDeny(payload("gh pr create", [plain], Cwd=unreviewed))
        self.assertDeny(payload("gh pr create", [self.repo], Cwd=unreviewed))
        self.assertDeny(payload("gh pr create", [self.tmp], WorkingDirectory="unreviewed"))  # relative to the workspace
        create = f"gh pr create -b '{body_line(self.repo)}'"
        self.assertSilent(payload(create, [self.repo], Cwd=self.repo))
        self.assertSilent(payload(create, [self.repo], Cwd=""))  # empty means "not given"

    def test_unrelated_argument_names_are_not_folders(self):
        for key, value in (("IsDirect", True), ("RedirectOutput", "out.txt"), ("Direction", 3), ("IsDaemon", False)):
            self.assertSilent(payload("ls", [self.repo], **{key: value}))

    def test_git_environment_variables_cannot_hide_the_repo(self):
        env = {**CLEAN_ENV, "GIT_DIR": os.path.join(self.tmp, "missing")}
        self.sh("git", "branch", "-m", "claude/x")
        r = subprocess.run([sys.executable, "-I", "-B", os.path.join(self.hooks, "agy_gate.py")],
                           input=json.dumps(payload("gh pr create", [self.repo])), capture_output=True, text=True,
                           env=env)
        self.assertEqual(json.loads(r.stdout)["decision"], "deny")

    def test_a_stale_workspace_is_dropped_but_none_left_is_denied(self):
        self.assertSilent(payload("ls", [self.repo, os.path.join(self.tmp, "gone")]))
        self.assertDeny(payload("ls", [os.path.join(self.tmp, "gone")]))

    def test_a_bad_folder_argument_is_denied_for_a_create_and_skipped_otherwise(self):
        for value in (7, ["x"], os.path.join(self.tmp, "missing")):
            self.assertDeny(payload("gh pr create", [self.repo], Cwd=value))
            self.assertSilent(payload("ls", [self.repo], Cwd=value))  # nothing can run in a missing folder

    def test_other_folder_argument_names_are_checked(self):
        unreviewed = os.path.join(self.tmp, "unreviewed")
        os.makedirs(unreviewed)
        self.init_repo(unreviewed, "cesar/DEM-4")
        self.mark()
        for key in ("WorkDir", "workdir", "WorkingFolder", "Folder", "Location", "Path"):
            self.assertDeny(payload("gh pr create", [self.repo], **{key: unreviewed}))

    def test_unrelated_tools_are_left_to_agy_even_without_workspaces(self):
        self.assertSilent({"toolCall": {"name": "read_file", "args": {}}})

    def test_mcp_create_tool_is_gated(self):
        call = {"toolCall": {"name": "mcp_github_create_pull_request", "args": {}}, "workspacePaths": [self.repo]}
        self.assertDeny(call)
        self.mark()
        self.assertDeny(call)  # no body, so no Review-Gate line (NFX-85)
        for key in ("body", "Body"):
            call["toolCall"]["args"] = {key: "## Summary\n" + body_line(self.repo) + "\n"}
            self.assertSilent(call)
        call["toolCall"]["args"] = {"body": "no line"}
        self.assertDeny(call)

    def test_any_name_holding_run_command_is_a_shell_call(self):
        self.assertDeny(payload("gh pr create", [self.repo], name="x_run_command_2"))

    def test_windows_tries_both_parsers(self):
        mod = load_adapter(ADAPTER)
        self.assertEqual(mod.shells_for("nt"), ("Bash", "PowerShell"))
        self.assertEqual(mod.shells_for("posix"), ("Bash",))

    def test_one_deadline_covers_every_check(self):
        with open(os.path.join(self.hooks, "review-gate.py"), "w", encoding="utf-8") as f:
            f.write("import time\ntime.sleep(5)\n")
        mod = load_adapter(os.path.join(self.hooks, "agy_gate.py"))
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        started = time.monotonic()
        reason = mod.first_block(("Bash",), "ls", [self.repo, other], total=1)
        self.assertIn("review gate", reason)
        self.assertLess(time.monotonic() - started, 4)


class Integrity(Base):
    def test_a_missing_or_wrong_hash_denies(self):
        call = payload("ls", [self.repo])
        r = self.run_adapter(call, expect="")  # hooks.json without --expect
        self.assertIn("no file hashes", json.loads(r.stdout)["reason"])
        good = self.expect().split(",")
        for i, name in enumerate(("agy_gate.py", "review-gate.py", "pr_create_parser.py")):
            bad = good[:]
            bad[i] = "0" * 64
            r = self.run_adapter(call, expect=",".join(bad))
            self.assertIn(name, json.loads(r.stdout)["reason"])
        r = self.run_adapter(call, expect="abc")
        self.assertIn("malformed", json.loads(r.stdout)["reason"])

    def test_an_edited_gate_copy_is_caught(self):
        expect = self.expect()
        with open(os.path.join(self.hooks, "review-gate.py"), "a", encoding="utf-8") as f:
            f.write("\n# edited\n")
        r = self.run_adapter(payload("ls", [self.repo]), expect=expect)
        self.assertIn("review-gate.py does not match", json.loads(r.stdout)["reason"])

    def test_a_shell_command_naming_the_plugin_folder_is_denied(self):
        for command in (f"sed -i s/2/0/ {self.hooks}/review-gate.py", "echo x > ~/.gemini/config/plugins/nousfx/hooks.json",
                        "cd plugins\\nousfx && ls"):
            r = self.run_adapter(payload(command, [self.repo]))
            self.assertIn("own files", json.loads(r.stdout)["reason"], command)
        self.assertSilent(payload("sed -i s/2/0/ claude/hooks/review-gate.py", [self.repo]))  # the repo's own copy


class FailClosed(Base):
    def test_malformed_payloads_are_denied(self):
        for bad in ("", "not json", "[]", "null", "{}",
                    json.dumps({"toolCall": {"name": "run_command"}, "workspacePaths": [self.repo]}),
                    json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": 7}},
                                "workspacePaths": [self.repo]}),
                    json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": "ls"}}}),
                    json.dumps(payload("ls", [])), json.dumps(payload("ls", "not a list")),
                    json.dumps(payload("ls", [self.repo, 3])),
                    json.dumps(payload("ls", [os.path.join(self.tmp, "missing")]))):  # none exists
            self.assertDeny(bad)

    def test_missing_gate_is_denied(self):
        expect = self.expect()
        os.remove(os.path.join(self.hooks, "review-gate.py"))
        r = self.run_adapter(payload("git status", [self.repo]), expect=expect)
        self.assertEqual(json.loads(r.stdout)["decision"], "deny")

    def test_crashing_gate_is_denied(self):
        with open(os.path.join(self.hooks, "review-gate.py"), "w", encoding="utf-8") as f:
            f.write("raise SystemExit(1)\n")
        self.assertDeny(payload("git status", [self.repo]))


class SingleCodePath(Base):
    def test_adapter_calls_the_check_entry(self):
        """The adapter hands the command to `review-gate.py check`, the entry every adapter shares."""
        record = os.path.join(self.tmp, "args.json")
        with open(os.path.join(self.hooks, "review-gate.py"), "w", encoding="utf-8") as f:
            f.write("import json, sys\n"
                    f"json.dump(sys.argv[1:], open({record!r}, 'w'))\n")
        self.assertSilent(payload("gh pr create --fill", [self.repo]))
        with open(record, encoding="utf-8") as f:
            args = json.load(f)
        self.assertEqual(args[:-2], ["check", "--cwd", self.repo, "--command", "gh pr create --fill",
                                     "--tool", "Bash", "--allow-non-repo"])
        self.assertEqual(args[-2], "--budget")  # what is left of the adapter's deadline, less a second
        self.assertTrue(0 < float(args[-1]) < 25, args[-1])


if __name__ == "__main__":
    unittest.main()
