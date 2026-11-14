#!/usr/bin/env python3
"""Tests for review-gate.py. Run: python3 claude/hooks/test_review_gate.py

Each test runs the hook as a subprocess on a throwaway git repo. BLOCKED is exit 2, ALLOWED exit 0.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_gate_check import attested_body, with_body  # noqa: E402

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review-gate.py")
BLOCKED, ALLOWED = 2, 0

# Each of these really runs `gh pr create`, so with no review recorded it must block.
RUNS_CREATE = [
    "gh pr create",
    "gh pr create --title x --body y",
    "gh pr new",
    "/usr/local/bin/gh pr create",
    "gh -R a/b pr create",
    'gh -R "$REPO" pr create',
    "gh pr -R a/b create",
    'g""h "pr" cre\'ate\'',
    "FOO=1 gh pr create",
    "git push && gh pr create",
    "git push; gh pr create",
    "true || gh pr create",
    "gh pr create | tee out",
    "gh pr create 2>&1",
    "> out gh pr create",
    "(gh pr create)",
    "{ gh pr create; }",
    "if gh pr create; then echo ok; fi",
    "if ! gh pr create; then echo no; fi",
    "while gh pr create; do break; done",
    "! gh pr create",
    "timeout 60 gh pr create",
    "timeout -k 5 60 gh pr create",
    "nohup gh pr create",
    "env FOO=1 gh pr create",
    "command gh pr create",
    "exec gh pr create",
    "time gh pr create",
    "sudo -u bob gh pr create",
    "echo x | xargs gh pr create --title",
    'eval "gh pr create"',
    "eval gh pr create",
    'bash -c "gh pr create"',
    "sh -c 'gh pr create'",
    "bash -lc 'git push && gh pr create'",
    "zsh -c 'timeout 5 gh pr create'",
    "echo `gh pr create`",
    "url=$(gh pr create)",
    'echo "$(gh pr create)"',
    'echo "`gh pr create`"',
    "gh \\\n  pr \\\n  create",
    "gh pr \\\n create --title x",
    "cat <<EOF\n$(gh pr create)\nEOF",
    "bash <<EOF\ngh pr create\nEOF",
    "cat <<EOF | bash\ngh pr create\nEOF",
    "echo 'gh pr create' | sh",
    'CMD="gh pr create"; $CMD',
    "$GH pr create",
    'SUB="pr create"; $GH $SUB',
    "$GH $SUB --title 'new pr'",
    "$GH pr $SUB",  # pr is already matched, so the expansion counts
    'SUB="p""r create"; $GH $SUB --body x',  # the words are read with quotes and escapes removed
    'SUB=p\\r\\ create; $GH $SUB',
    "$GH $(cat args.txt)",  # a command substitution's output is unknown, so it counts
    "$GH `base64 -d < a`",
    "GH=gh; SUB=$(cat a); $GH $SUB",  # the text names gh
    '"$(command -v gh)" $SUB',
    "SUB=$(cat args.txt); $X $SUB",  # the variable holds a command's output
    "read -r SUB < a; $X $SUB",
    "source ./env.sh; $X $SUB",
    "$X ${ cat a; }",  # bash 5.3 command substitution
    "${GH:-/usr/local/bin/gh} pr create --title t --body b",  # the file name is not plain text
    "$(echo /usr/bin/gh) pr create",
    'printf -v SUB %s "$(cat f)"; $X $SUB',  # nothing follows the variable to show it is not gh
    "O=$(cat f); SUB=$O; $X $SUB",
    "  source env.sh; $X $SUB --title t",
    "SUB=$'pr\\x20create'; $X $SUB",
    "$X $SUB create --title t",  # create after the variable could finish `pr create`
    # NFX-123: a shell named in an assignment or $SHELL, and a later $(...) arg behind another variable.
    'SH=bash; $SH $OPTS "$(cat c.txt)" name',
    'X=sh; Y=-c; CMD=$(cat c.txt); $X $Y "$CMD" name',
    '$SHELL $FLAGS "$CMD" x',
    '${SHELL} $FLAGS "$CMD" x',
    '$X $D "$(cat c.txt)" name',
    '${SHELL:-sh} $FLAGS "$CMD" x',
    '$BASH $FLAGS "$CMD" x',
    # A substitution as a flag's value blocks on purpose: $DEV may hold -c, and `sh -c -s "$(...)"` runs it.
    '$ADB $DEV -s "$(cat serial)" shell input keyevent 4',
    '$X $D $E `cat c.txt` name',
    'X="sh -c"; CMD=$(cat f); $X "$CMD" name',  # a shell in the text: the plain word may be $0
    "$X $A {,}",  # words that may vanish (brace expansion, globs) prove nothing
    "$X $A no*",
    "$EMU $EMU_ARGS -avd Pixel_7 -no-window",  # known false block: only flags follow, so it may be gh
    "gh pr $SUB",
    "gh $ARGS",
    "case x in x) gh pr create;; esac",
    # Quotes in a heredoc or comment inside $(...) must not swallow the rest of the command.
    "git commit -m \"$(cat <<'EOF'\nit's done\nEOF\n)\" && git push && gh pr create",
    "git commit -m \"$(cat <<EOF\nsay \"hi\n)\nEOF\n)\" && gh pr create",
    "git commit -m \"$(cat <<-'EOF'\n\tit's\n\tEOF\n)\"; gh pr create",
    "x=$(cat <<'A' <<'B'\nit's\nA\nit's\nB\n)\ngh pr create",
    "x=$(# it's a comment\necho hi)\ngh pr create",
    "x=\"$(echo \")\")\"; gh pr create",
    "bash -s x <<'EOF'\ngh pr create\nEOF",
    "echo 'gh pr create' | bash -s x",
    "bash -ce 'gh pr create'",
    "sh -cx 'gh pr create'",
    "bash -c -- 'gh pr create'",
    "bash -o pipefail -c 'gh pr create'",
    "pwsh -Command gh pr create",
    "powershell -c 'gh pr create'",
    "gh pr create --title $'it\\'s'",
    'gh --repo="$R" pr create',
    'gh -R"$R" pr create',
    'direnv exec . bash -c "git push && gh pr create"',
    "op run -- sh -c 'gh pr create'",
    "uv run bash -c 'gh pr create'",
    "mise exec -- bash -c 'gh pr create'",
    "docker exec c sh -c 'gh pr create'",
    "someprogram --flag bash -lc 'gh pr create'",
    "direnv exec . gh pr create",
    "uv run gh pr create",
    "command -v cd; gh pr create",
    "command -v cd >/dev/null && gh pr create",
    "time make cd && gh pr create",
    # Unbalanced text cannot be parsed reliably, so a mention blocks.
    "echo $(oops; gh pr create",
    "echo `oops; gh pr create",
    "echo 'oops; gh pr create",
    "echo \"oops; gh pr create",
]

# With a review, each of these gets a readable body from run_gate, so it must pass.
READABLE_BODY = ["gh pr create", "git push && gh pr create", "bash -c 'gh pr create'", 'eval "gh pr create"',
                 "timeout 60 gh pr create", "if gh pr create; then echo ok; fi", "url=$(gh pr create)"]

# None of these runs `gh pr create`, so with no review recorded it must pass.
ONLY_MENTIONS = [
    "git status",
    "gh pr list",
    "gh pr view 12",
    "gh pr view create",
    "gh issue create",
    "gh pr checkout create",
    "ls gh",
    "echo gh pr create",
    "echo 'gh pr create'",
    'echo "if gh pr create; then"',
    'git commit -m "docs: explain gh pr create"',
    "git log --grep 'gh pr create'",
    "grep -r 'timeout 60 gh pr create' .",
    "# gh pr create",
    "echo hi # gh pr create",
    "cat <<EOF\ngh pr create\nEOF",
    "cat <<'EOF'\n$(gh pr create)\nEOF",
    "cat > notes.md <<-EOF\n\tgh pr create\n\tEOF\necho done",
    "git commit -F - <<'EOF'\nDEM-1: mention\n\ngh pr create\nEOF",
    "python3 gate.py --mark --skip 'gh pr create failed'",
    "git commit -m \"$(cat <<'EOF'\nit's about gh pr create\nEOF\n)\" && git push",
    "git commit -m \"$(cat <<'EOF'\nit's\nEOF\n)\"",
    "echo $((1<<2)) && git status",
    "echo 'oops",
    'gh -R "$REPO" pr list',
    'gh --repo "$REPO" pr view 12',
    'gh --repo="$R" issue list',
    "echo bash -c 'gh pr create'",
    "someprogram bash -c 'gh pr list'",
    "command -v gh",
    'gh -R"$R" pr view 1',
    '"$P/x" && gh pr view 1 && echo new',
    '"$VENV/bin/python" x.py --grep "gh pr create"',
    "echo $'it\\'s gh pr create'",
    # A program and its args both in variables (adb scripts): no pr/create words, so not a create (ANI-102).
    'A=adb; D="-s X"; $A $D shell input keyevent 4',
    '"$ADB" $DEV exec-out screencap -p > a.png; $ADB $DEV shell input tap 1 2',
    # A program path whose file name is in the text is not gh, whatever its args hold.
    "S=/tmp/x; APK=$(ls *.apk | head -1); $S/inst.sh $APK",
]


class Base(unittest.TestCase):
    # NFX-20: a PR create asks tier.py whether the diff touches UI, and tier.py needs origin/main to diff against.
    # The --mark tests turn it off: a default-branch ref makes the gate read the check command from it, not the tree.
    origin = True

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="review-gate-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.make_repo("repo")

    def make_repo(self, name, commit=True):
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        self.sh("git", "init", "-q", "-b", "cesar/DEM-1", cwd=path)
        if commit:
            with open(os.path.join(path, "f.txt"), "w", encoding="utf-8") as f:
                f.write(name)
            self.write_check(path, "exit 0")
            self.sh("git", "add", "-A", cwd=path)
            self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=path)
            if self.origin:
                self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=path)  # NFX-20: tier.py's base
        return path

    def add_origin(self):
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=self.repo)

    def sh(self, *args, cwd):
        subprocess.run(args, cwd=cwd, check=True, capture_output=True)

    def write_check(self, path, command, timeout=None):
        """Write <path>/.nousfx/project.toml with a check command (None removes the file)."""
        cfg = os.path.join(path, ".nousfx", "project.toml")
        if command is None:
            if os.path.exists(cfg):
                os.remove(cfg)
            return
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("[check]\ncommand = %s\n" % json.dumps(command))
            if timeout:
                f.write("timeout = %d\n" % timeout)

    def commit(self, path, msg="x"):
        self.sh("git", "add", "-A", cwd=path)
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg, cwd=path)

    def set_base(self, path):
        """Pretend the current commit is the default branch on the remote."""
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=path)

    def run_check(self, *args, cwd=None):
        return subprocess.run([sys.executable, GATE, "run-check", *args], cwd=cwd or self.repo,
                              capture_output=True, text=True)

    def mark(self, *args, cwd=None):
        return subprocess.run([sys.executable, GATE, "--mark", *args], cwd=cwd or self.repo,
                              capture_output=True, text=True)

    def run_gate(self, command=None, cwd=None, tool="Bash", env=None, gate=GATE, body=True, full=False):
        """Exit code of the hook. body: give every literal `gh pr create` (and an MCP create) a PR body with a
        valid `Review-Gate:` line for each repo here (NFX-85), so the test judges the review check alone."""
        payload = {"tool_name": tool, "cwd": cwd or self.repo, "tool_input": {}}
        if command is not None:
            payload["tool_input"]["command"] = with_body(command, self.tmp) if body else command
        if body and isinstance(tool, str) and "create_pull_request" in tool:
            with open(attested_body(self.tmp), encoding="utf-8") as f:
                payload["tool_input"]["body"] = f.read()
        r = subprocess.run([sys.executable, gate], input=json.dumps(payload).encode(),
                           capture_output=True, env={**os.environ, **(env or {})}, cwd=self.tmp)
        return r if full else r.returncode


class Detection(Base):
    def test_commands_that_run_create_block(self):
        for command in RUNS_CREATE:
            with self.subTest(command=command):
                self.assertEqual(self.run_gate(command), BLOCKED)

    def test_text_that_only_mentions_create_passes(self):
        for command in ONLY_MENTIONS:
            with self.subTest(command=command):
                self.assertEqual(self.run_gate(command), ALLOWED)

    def test_reviewed_code_passes_every_form(self):
        """With a review, every form gets past the review check. A form whose body the gate can read passes;
        the rest stop at the body check (NFX-85), never at the review check."""
        self.mark("--findings", "0")
        for command in RUNS_CREATE:
            with self.subTest(command=command):
                r = self.run_gate(command, full=True)
                if r.returncode != ALLOWED:
                    self.assertEqual(r.returncode, BLOCKED)
                    self.assertIn(b"Review-Gate:", r.stderr)
                    self.assertNotIn(b"no review recorded", r.stderr)
                    self.assertTrue(command not in READABLE_BODY, "should have passed")

    def test_body_forms_the_gate_reads_pass(self):
        self.mark("--findings", "0")
        for command in READABLE_BODY:
            with self.subTest(command=command):
                self.assertEqual(self.run_gate(command), ALLOWED)

    def test_mcp_tool_blocks(self):
        self.assertEqual(self.run_gate(tool="mcp__github__create_pull_request"), BLOCKED)

    def test_powershell_windows_path_and_continuation(self):
        ps = "PowerShell"
        self.assertEqual(self.run_gate("gh pr create", tool=ps), BLOCKED)
        self.assertEqual(self.run_gate("gh `\n pr create", tool=ps), BLOCKED)
        self.assertEqual(self.run_gate("& 'C:\\Tools\\gh.exe' pr create", tool=ps), BLOCKED)
        self.assertEqual(self.run_gate("echo 'gh pr create'", tool=ps), ALLOWED)

    def test_null_tool_name_is_still_checked(self):
        self.assertEqual(self.run_gate("gh pr create", tool=None), BLOCKED)

    def test_odd_tool_name_is_still_checked(self):
        self.assertEqual(self.run_gate("gh pr create", tool=5), BLOCKED)

    def test_bad_payload_blocks(self):
        """NFX-102: the tool might be a create, so a payload the gate cannot read blocks (more in Payloads)."""
        r = subprocess.run([sys.executable, GATE], input=b"not json", capture_output=True)
        self.assertEqual(r.returncode, BLOCKED)

    def test_missing_parser_module_still_blocks_create(self):
        lone = os.path.join(self.tmp, "lone")
        os.makedirs(lone)
        shutil.copy(GATE, lone)
        self.mark("--findings", "0")
        for command, want in (("gh pr create", BLOCKED), ("echo 'gh pr create'", BLOCKED),
                              ("g\"\"h \"pr\" cre'ate'", BLOCKED), ("GH PR create", BLOCKED), ("ls", ALLOWED)):
            with self.subTest(command=command):
                self.assertEqual(self.run_gate(command, gate=os.path.join(lone, "review-gate.py")), want)


class WhichRepo(Base):
    """Repo A has a valid review, repo B has none. The gate must judge the repo the command runs in."""

    def setUp(self):
        super().setUp()
        self.mark("--findings", "0")  # self.repo is reviewed
        self.other = self.make_repo("other")  # not reviewed

    def test_dynamic_cd_to_toplevel_judges_this_repo(self):
        cmd = 'cd "$(git rev-parse --show-toplevel)" && gh pr create'
        self.assertEqual(self.run_gate(cmd), ALLOWED)
        self.assertEqual(self.run_gate(cmd, cwd=self.other), BLOCKED)

    def test_dynamic_cd_toplevel_with_subpath_and_backticks(self):
        os.makedirs(os.path.join(self.other, "sub"))
        self.assertEqual(self.run_gate('cd "$(git rev-parse --show-toplevel)/sub" && gh pr create', cwd=self.other), BLOCKED)
        self.assertEqual(self.run_gate("cd `git rev-parse --show-toplevel` && gh pr create", cwd=self.other), BLOCKED)

    def test_unresolvable_cd_blocks_even_with_a_review_here(self):
        self.assertEqual(self.run_gate('cd "$(cat where)" && gh pr create'), BLOCKED)
        self.assertEqual(self.run_gate("cd $UNSET_GATE_TEST_VAR && gh pr create"), BLOCKED)
        self.assertEqual(self.run_gate("cd - && gh pr create"), BLOCKED)

    def test_env_var_cd_is_expanded(self):
        env = {"GATE_TEST_DIR": self.other}
        self.assertEqual(self.run_gate("cd $GATE_TEST_DIR && gh pr create", env=env), BLOCKED)
        env = {"GATE_TEST_DIR": self.repo}
        self.assertEqual(self.run_gate("cd $GATE_TEST_DIR && gh pr create", cwd=self.other, env=env), ALLOWED)

    def test_missing_cd_target_falls_back_to_session_cwd(self):
        for cmd in ("cd $HOME/nope-review-gate && gh pr create", "cd /nope/review-gate; gh pr create"):
            with self.subTest(command=cmd):
                self.assertEqual(self.run_gate(cmd, cwd=self.other), BLOCKED)
                self.assertEqual(self.run_gate(cmd), ALLOWED)

    def test_literal_cd(self):
        self.assertEqual(self.run_gate(f"cd {self.other} && gh pr create"), BLOCKED)
        self.assertEqual(self.run_gate(f"cd '{self.other}' && gh pr create"), BLOCKED)
        self.assertEqual(self.run_gate(f"cd {self.repo} && gh pr create", cwd=self.other), ALLOWED)
        self.assertEqual(self.run_gate("cd ../other && gh pr create"), BLOCKED)

    def test_cd_not_at_the_start(self):
        for cmd in (f"(cd {self.other} && gh pr create)", f"git status && cd {self.other} && gh pr create",
                    f"if cd {self.other}; then gh pr create; fi", f"cd -P {self.other} && gh pr create",
                    f"bash -c 'cd {self.other} && gh pr create'", f"echo $(cd {self.other} && gh pr create)"):
            with self.subTest(command=cmd):
                self.assertEqual(self.run_gate(cmd), BLOCKED)

    def test_subshell_cd_does_not_leak(self):
        self.assertEqual(self.run_gate(f"(cd {self.other} && ls); gh pr create"), ALLOWED)
        self.assertEqual(self.run_gate(f"(cd {self.repo} && ls); gh pr create", cwd=self.other), BLOCKED)

    def test_cd_that_does_not_stick(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        for cmd in (f"cd {plain} & gh pr create", f"cd {plain} | gh pr create",
                    f"pushd {plain} && popd && gh pr create", f"false && cd {plain}; gh pr create",
                    f"false &&\n cd {plain}\n gh pr create", f"if false; then cd {plain}; fi; gh pr create"):
            with self.subTest(command=cmd):
                self.assertEqual(self.run_gate(cmd, cwd=self.other), BLOCKED)

    def test_conditional_cd_still_guards_its_own_chain(self):
        self.assertEqual(self.run_gate(f"false && cd {self.other} && gh pr create"), BLOCKED)
        self.assertEqual(self.run_gate(f"false && cd {self.repo} && gh pr create", cwd=self.other), ALLOWED)
        self.assertEqual(self.run_gate(f"cd {self.repo} || exit 1; gh pr create", cwd=self.other), ALLOWED)

    def test_cd_with_ansi_quote_from_outside_a_repo(self):
        plain = os.path.join(self.tmp, "plain2")
        os.makedirs(plain)
        cmd = f"cd {self.other} && gh pr create --title $'it\\'s'"
        self.assertEqual(self.run_gate(cmd, cwd=plain), BLOCKED)

    def test_unparsable_text_with_a_cd_blocks_outside_a_repo(self):
        plain = os.path.join(self.tmp, "plain3")
        os.makedirs(plain)
        self.assertEqual(self.run_gate(f"cd {self.other} && gh pr create 'oops", cwd=plain), BLOCKED)

    def test_cd_in_a_case_branch_does_not_stick(self):
        plain = os.path.join(self.tmp, "plain4")
        os.makedirs(plain)
        cmd = f"case x in y) cd {plain} ;; esac; gh pr create"
        self.assertEqual(self.run_gate(cmd, cwd=self.other), BLOCKED)

    def test_cd_in_a_nested_case_branch_does_not_stick(self):
        plain = os.path.join(self.tmp, "plain5")
        os.makedirs(plain)
        for cmd in (f"if true; then case x in y) cd {plain};; esac; fi; gh pr create",
                    f"for i in 1; do case $i in 2) cd {plain};; esac; done; gh pr create",
                    f"{{ case x in y) cd {plain};; esac; }}; gh pr create",
                    f"if a; then case x in z) true;; esac; fi; case x in y) cd {plain};; esac; gh pr create"):
            with self.subTest(command=cmd):
                self.assertEqual(self.run_gate(cmd, cwd=self.other), BLOCKED)

    def test_wrapped_cd_still_moves(self):
        self.assertEqual(self.run_gate(f"command cd {self.other} && gh pr create"), BLOCKED)
        self.assertEqual(self.run_gate(f"builtin cd {self.repo} && gh pr create", cwd=self.other), ALLOWED)

    def test_every_create_in_a_command_is_judged(self):
        self.assertEqual(self.run_gate(f"gh pr create; (cd {self.other} && gh pr create)"), BLOCKED)


class Failure(Base):
    def test_outside_a_repo_passes(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertEqual(self.run_gate("gh pr create", cwd=plain), ALLOWED)

    def test_git_failing_for_another_reason_blocks(self):
        stub = os.path.join(self.tmp, "bin")
        os.makedirs(stub)
        path = os.path.join(stub, "git")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho 'fatal: detected dubious ownership' >&2\nexit 128\n")
        os.chmod(path, 0o755)
        env = {"PATH": stub + os.pathsep + os.environ["PATH"]}
        self.assertEqual(self.run_gate("gh pr create", env=env), BLOCKED)

    def test_empty_repo_blocks(self):
        empty = self.make_repo("empty", commit=False)
        self.assertEqual(self.run_gate("gh pr create", cwd=empty), BLOCKED)

    def test_git_failure_during_the_check_blocks(self):
        self.mark("--findings", "0")  # a marker makes the gate hash the tree, which then fails
        os.chmod(os.path.join(self.repo, ".git", "objects"), 0o500)
        self.addCleanup(os.chmod, os.path.join(self.repo, ".git", "objects"), 0o700)
        with open(os.path.join(self.repo, "new.txt"), "w", encoding="utf-8") as f:
            f.write("unhashable")
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)


class CheckCommand(Base):
    """NFX-54: --mark runs the project's check command itself."""
    origin = False

    def test_failing_check_refuses_the_mark(self):
        self.write_check(self.repo, "exit 1")
        self.commit(self.repo)
        r = self.mark("--findings", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "claude-review-ok")))
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_failing_check_removes_an_older_good_mark(self):
        self.assertEqual(self.mark("--findings", "0").returncode, 0)
        self.write_check(self.repo, "exit 1")
        self.assertNotEqual(self.mark("--findings", "0").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "claude-review-ok")))
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_edit_after_marking_invalidates_the_mark(self):
        self.assertEqual(self.mark("--findings", "0").returncode, 0)
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
        self.commit(self.repo)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_branch_edited_check_is_ignored_by_mark(self):
        self.write_check(self.repo, "exit 1")
        self.commit(self.repo)
        self.set_base(self.repo)  # the default branch says: exit 1
        self.write_check(self.repo, "exit 0")  # the branch tries to weaken it
        self.commit(self.repo)
        r = self.mark("--findings", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exited with 1", r.stderr)

    def test_branch_edited_check_is_ignored_by_run_check(self):
        self.write_check(self.repo, "exit 1")
        self.commit(self.repo)
        self.set_base(self.repo)
        self.write_check(self.repo, "exit 0")
        self.commit(self.repo)
        r = self.run_check("--ref", "refs/remotes/origin/main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exited with 1", r.stderr)

    def test_run_check_passes_when_the_base_command_passes(self):
        self.set_base(self.repo)
        self.assertEqual(self.run_check("--ref", "refs/remotes/origin/main").returncode, 0)

    def test_run_check_refuses_a_ref_without_config(self):
        self.write_check(self.repo, None)
        self.commit(self.repo)
        self.set_base(self.repo)
        self.assertNotEqual(self.run_check("--ref", "refs/remotes/origin/main").returncode, 0)

    def test_default_branch_file_without_a_check_is_not_replaced_by_the_branch(self):
        with open(os.path.join(self.repo, ".nousfx", "project.toml"), "w", encoding="utf-8") as f:
            f.write("[other]\nx = 1\n")
        self.commit(self.repo)
        self.set_base(self.repo)  # the default branch has the file, but no [check]
        self.write_check(self.repo, "exit 0")  # the branch supplies its own
        self.commit(self.repo)
        r = self.mark("--findings", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no check command", r.stderr)

    def test_bootstrap_uses_the_working_tree_when_the_default_branch_has_no_file(self):
        self.write_check(self.repo, None)
        self.commit(self.repo)
        self.set_base(self.repo)  # the default branch has no project.toml at all
        self.write_check(self.repo, "exit 0")
        r = self.mark("--findings", "0")
        self.assertEqual(r.returncode, 0)
        self.assertIn("working-tree copy", r.stdout)

    def test_origin_master_counts_as_the_default_branch(self):
        self.write_check(self.repo, "exit 1")
        self.commit(self.repo)
        self.sh("git", "update-ref", "refs/remotes/origin/master", "HEAD", cwd=self.repo)
        self.write_check(self.repo, "exit 0")
        self.commit(self.repo)
        r = self.mark("--findings", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exited with 1", r.stderr)

    def test_origin_head_counts_as_the_default_branch(self):
        self.write_check(self.repo, "exit 1")
        self.commit(self.repo)
        self.sh("git", "update-ref", "refs/remotes/origin/develop", "HEAD", cwd=self.repo)
        self.sh("git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop", cwd=self.repo)
        self.write_check(self.repo, "exit 0")
        self.commit(self.repo)
        r = self.mark("--findings", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exited with 1", r.stderr)

    def test_origin_head_pointing_at_a_local_branch_is_not_trusted(self):
        self.write_check(self.repo, "exit 1")
        self.commit(self.repo)
        self.set_base(self.repo)  # origin/main says: exit 1
        self.write_check(self.repo, "exit 0")
        self.commit(self.repo)
        self.sh("git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/heads/cesar/DEM-1", cwd=self.repo)
        r = self.mark("--findings", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exited with 1", r.stderr)

    def test_a_gate_without_git_refs_refuses_the_mark(self):
        """NFX-108: the default-branch rule lives in git_refs.py; a copy without it must not wave the mark through."""
        lone = os.path.join(self.tmp, "lone-refs")
        os.makedirs(lone)
        for name in ("review-gate.py", "pr_create_parser.py"):
            shutil.copy(os.path.join(os.path.dirname(GATE), name), lone)
        r = subprocess.run([sys.executable, os.path.join(lone, "review-gate.py"), "--mark", "--findings", "0"],
                           cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("git_refs", r.stderr + r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "claude-review-ok")))

    def test_the_hook_never_runs_git_refs(self):
        """NFX-108: the agy adapter hashes only the gate and the parser, so git_refs.py (unhashed) must not run in the
        hook or in `check`; only --mark and run-check import it."""
        lone = os.path.join(self.tmp, "lone-hook")
        os.makedirs(lone)
        for name in ("review-gate.py", "pr_create_parser.py"):
            shutil.copy(os.path.join(os.path.dirname(GATE), name), lone)
        sentinel = os.path.join(self.tmp, "git-refs-ran")
        with open(os.path.join(lone, "git_refs.py"), "w", encoding="utf-8") as f:
            f.write("open(%r, 'w').close()\n" % sentinel)
        for command in ("ls", "gh pr create"):
            self.run_gate(command, gate=os.path.join(lone, "review-gate.py"))
        self.assertFalse(os.path.exists(sentinel))
        subprocess.run([sys.executable, os.path.join(lone, "review-gate.py"), "--mark", "--findings", "0"],
                       cwd=self.repo, capture_output=True, text=True)
        self.assertTrue(os.path.exists(sentinel))  # the mark does load it

    def test_a_check_that_forges_a_mark_and_fails_leaves_none(self):
        py = sys.executable.replace("\\", "/")
        forge = ("import json,subprocess,sys; t=subprocess.check_output(['git','write-tree']).decode().strip(); "
                 "json.dump({'tree':t,'findings_open':0,'skipped':None,'check':{'exit':0}}, "
                 "open('.git/claude-review-ok','w')); sys.exit(1)")
        self.write_check(self.repo, "%s -c \"%s\"" % (py, forge.replace('"', '\\"')))
        self.commit(self.repo)
        self.assertNotEqual(self.mark("--findings", "0").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "claude-review-ok")))

    def test_run_check_shows_the_commands_output(self):
        self.write_check(self.repo, "echo FAILED_test_\"\"login; exit 1")  # the output differs from the command text
        self.commit(self.repo)
        self.set_base(self.repo)
        r = self.run_check("--ref", "refs/remotes/origin/main")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FAILED_test_login", r.stdout + r.stderr)

    def test_run_check_prints_a_positive_confirmation(self):
        self.set_base(self.repo)
        self.assertIn("Check passed.", self.run_check("--ref", "refs/remotes/origin/main").stdout)

    def test_run_check_refuses_a_ref_that_looks_like_an_option(self):
        self.assertNotEqual(self.run_check("--ref", "-p").returncode, 0)

    def test_timeout_kills_the_whole_process_group(self):
        marker = os.path.join(self.tmp, "orphan-ran")
        py = sys.executable.replace("\\", "/")
        self.write_check(self.repo, "(%s -c 'import time; time.sleep(3); open(\"%s\", \"w\").close()' &) ; sleep 30"
                         % (py, marker.replace("\\", "/")), timeout=1)
        self.commit(self.repo)
        self.assertNotEqual(self.mark("--findings", "0").returncode, 0)
        time.sleep(4)
        self.assertFalse(os.path.exists(marker))

    def test_no_check_and_no_skip_refuses_the_mark(self):
        self.write_check(self.repo, None)
        self.commit(self.repo)
        r = self.mark("--findings", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(".nousfx/project.toml", r.stderr)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_skip_needs_no_check(self):
        self.write_check(self.repo, None)
        self.commit(self.repo)
        self.assertEqual(self.mark("--skip", "approved").returncode, 0)
        self.add_origin()  # NFX-20: the create asks tier.py for a base
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)

    def test_check_that_changes_files_is_refused(self):
        self.write_check(self.repo, "echo more >> f.txt")
        self.commit(self.repo)
        self.assertNotEqual(self.mark("--findings", "0").returncode, 0)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_old_marker_without_a_check_result_blocks(self):
        self.mark("--findings", "0")
        path = os.path.join(self.repo, ".git", "claude-review-ok")
        with open(path, encoding="utf-8") as f:
            marker = json.load(f)
        del marker["check"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(marker, f)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_background_child_is_killed_after_a_normal_exit(self):
        marker = os.path.join(self.tmp, "late-edit")
        py = sys.executable.replace("\\", "/")
        self.write_check(self.repo, "(%s -c 'import time; time.sleep(2); open(\"%s\", \"w\").close()' &) ; exit 0"
                         % (py, marker.replace("\\", "/")))
        self.commit(self.repo)
        self.assertEqual(self.mark("--findings", "0").returncode, 0)
        time.sleep(3)
        self.assertFalse(os.path.exists(marker))

    def test_check_that_times_out_is_refused(self):
        self.write_check(self.repo, "%s -c 'import time; time.sleep(30)'" % sys.executable.replace("\\", "/"), timeout=1)
        self.commit(self.repo)
        self.assertNotEqual(self.mark("--findings", "0").returncode, 0)

    def test_broken_config_refuses_the_mark(self):
        cfg = os.path.join(self.repo, ".nousfx", "project.toml")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("[check\n")
        self.assertNotEqual(self.mark("--findings", "0").returncode, 0)


class Marker(Base):
    origin = False

    def test_mark_alone_is_refused_and_records_nothing(self):
        r = self.mark()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('--skip "', r.stdout + r.stderr)  # NFX-101: no ready-made skip command
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_mark_with_bad_findings_is_refused(self):
        for args in (["--findings", "-1"], ["--findings", "x"], ["--findings"], ["--skip", ""], ["--skip"]):
            with self.subTest(args=args):
                self.assertNotEqual(self.mark(*args).returncode, 0)
                self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_old_marker_without_findings_blocks(self):
        self.mark("--findings", "0")
        path = os.path.join(self.repo, ".git", "claude-review-ok")
        with open(path, encoding="utf-8") as f:
            marker = json.load(f)
        marker["findings_open"] = None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(marker, f)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_zero_findings_passes(self):
        self.assertEqual(self.mark("--findings", "0").returncode, 0)
        self.add_origin()  # NFX-20: the create asks tier.py for a base
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)

    def test_open_findings_block(self):
        self.mark("--findings", "2")
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_skip_passes(self):
        self.assertEqual(self.mark("--skip", "approved").returncode, 0)
        self.add_origin()  # NFX-20: the create asks tier.py for a base
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)
        self.mark("--findings", "2", "--skip", "approved")
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)

    def test_block_messages_print_no_ready_made_skip_command(self):
        """NFX-101: a skip is the user's call, so no block hands the agent a command to waive its own review."""
        outputs = [("Blocked: no review", self.run_gate("gh pr create", full=True).stderr.decode())]
        self.mark("--findings", "2")
        outputs.append(("Blocked: the recorded review has 2", self.run_gate("gh pr create", full=True).stderr.decode()))
        self.write_check(self.repo, None)
        outputs.append(("Nothing recorded: this repo has no check", self.mark("--findings", "0").stderr))
        for start, out in outputs:
            with self.subTest(start=start):
                self.assertTrue(out.startswith(start), out)
                self.assertNotIn('--skip "', out)
                self.assertIn("ask the user", out)

    def test_committed_edit_after_review_blocks(self):
        self.mark("--findings", "0")
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
        self.add_origin()  # NFX-20: the create asks tier.py for a base
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)  # uncommitted: not part of the PR
        self.sh("git", "add", "-A", cwd=self.repo)
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x", cwd=self.repo)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_marker_survives_committing(self):
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
        self.mark("--findings", "0")
        self.sh("git", "add", "-A", cwd=self.repo)
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x", cwd=self.repo)
        self.add_origin()  # NFX-20: the create asks tier.py for a base
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)


class Branch(Base):
    def test_bad_branch_blocks_even_when_reviewed(self):
        self.mark("--findings", "0")
        self.sh("git", "branch", "-m", "claude/x", cwd=self.repo)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_allow_branch_exception(self):
        self.mark("--findings", "0")
        self.sh("git", "branch", "-m", "claude/x", cwd=self.repo)
        subprocess.run([sys.executable, GATE, "--allow-branch", "ok"], cwd=self.repo, check=True, capture_output=True)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)  # the mark names the old branch (NFX-71)
        self.mark("--findings", "0")
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)


# NFX-102: PR creates through the GitHub API. The gate cannot read their body, so they always block.
API_CREATES = [
    "gh api repos/{owner}/{repo}/pulls -f title=x",
    "X='curl -d x https://api.github.com/repos/o/r/pulls'; eval \"$X\"",
    '"$W" --post-data x https://api.github.com/repos/o/r/pulls',
    "gh api repos/o/r/pulls -f title=x -f head=b -f base=main",
    "gh api -X POST repos/o/r/pulls --input body.json",
    "gh api -XPOST repos/o/r/pulls",
    "gh api -X=POST repos/o/r/pulls -f title=t",
    "gh api -X PUT repos/o/r/pulls",
    "gh api -H'X-GitHub-Api-Version: 2022-11-28' repos/o/r/pulls -f title=t",
    "curl -H'X-GitHub-Api-Version: 2022-11-28' -d @b.json https://api.github.com/repos/o/r/pulls",
    "gh api --method POST /repos/o/r/pulls",
    "gh api --method=POST repos/o/r/pulls",
    "gh api repos/o/r/pulls -F draft=true",
    "gh api repos/o/r/pulls --raw-field title=x",
    "gh api repos/o/r/pulls --field title=x",
    'gh api "repos/$OWNER/$REPO/pulls" -f title=x',
    'gh api -X "$M" repos/o/r/pulls',
    "gh api https://api.github.com/repos/o/r/pulls -f title=x",
    'gh api graphql -f query="mutation { createPullRequest(input: {}) { pullRequest { url } } }"',
    "curl -X POST https://api.github.com/repos/o/r/pulls -d @body.json",
    "curl --expand-data '{{title}}' https://api.github.com/repos/o/r/pulls",
    "curl -sSXPOST https://api.github.com/repos/o/r/pulls",
    "curl --request POST https://api.github.com/repos/o/r/pulls",
    "curl --request=POST https://api.github.com/repos/o/r/pulls",
    "curl https://api.github.com/repos/o/r/pulls -d '{\"title\":\"x\"}'",
    "curl https://api.github.com/repos/o/r/pulls --data-raw x",
    "curl https://api.github.com/repos/o/r/pulls --data-binary @b.json",
    "curl https://api.github.com/repos/o/r/pulls --json '{}'",
    "curl -s https://ghe.example.com/api/v3/repos/o/r/pulls -d x",
    "wget --method=POST https://api.github.com/repos/o/r/pulls",
    "wget --post-data x https://api.github.com/repos/o/r/pulls",
    "wget --post-file=b.json https://api.github.com/repos/o/r/pulls",
    "wget --method POST --body-data x https://api.github.com/repos/o/r/pulls",
    "wget --meth=POST https://api.github.com/repos/o/r/pulls",
    "wget --post-d=x https://api.github.com/repos/o/r/pulls",
    "curl -T b.json -X POST -G https://api.github.com/repos/o/r/pulls",
    "curl -d '{\"query\":\"mutation{createPullRequest(input:{})}\"}' https://api.github.com/graphql",
    "echo 'curl -d @b https://api.github.com/repos/o/r/pulls' | sh",
    'eval "$X"; echo repos/o/r/pulls',
    '"$CURL" -d x https://api.github.com/repos/o/r/pulls',
    "$GH api repos/o/r/pulls -f title=x",
    "curl -d @b.json -A -XGET https://api.github.com/repos/o/r/pulls",
    "gh api repos/o/r/pulls -f title=x -f head=b -f base=main -t -XGET",
    "wget --post-file=b.json -U --method=GET https://api.github.com/repos/o/r/pulls",
    "curl -X GET https://api.github.com/repos/o/r/pulls --next -d @b.json https://api.github.com/repos/o/r/pulls",
    "curl -G -d state=open https://api.github.com/repos/o/r/pulls",
    "curl -d @b.json https://api.github.com/repos/o/r/./pulls",
    "curl -d @b.json https://api.github.com/repos/o/r/x/../pulls",
    "curl -d @b.json https://api.github.com//repos/o/r/pulls",
    "curl -d @b.json 'https://api.github.com/repos/o/r/{pulls}'",
    "curl -d @b.json https://api.github.com/repositories/123/pulls",
    "curl --js '{}' https://api.github.com/repos/o/r/pulls",
]

# Reads, updates and other endpoints: not a create.
API_READS = [
    "gh api repos/{owner}/{repo}/pulls/12/comments/3/replies -f body=x",
    "gh api repos/{owner}/{repo}/issues/5/comments -f body=x",
    "gh api -X PATCH repos/{owner}/{repo}/pulls/12 -f title=x",
    'eval "$(ssh-agent -s)" && gh api repos/o/r/pulls --method GET',
    '"$PY" tool.py repos/o/r/pulls',
    "gh api repos/o/r/pulls",
    "gh api repos/o/r/pulls --method GET -f state=open",
    "gh api -X GET repos/o/r/pulls -f state=open",
    "gh api -X=GET repos/o/r/pulls -f state=open",
    "curl -I https://api.github.com/repos/o/r/pulls",
    "curl -H'Accept: application/vnd.github+json' https://api.github.com/repos/o/r/pulls",
    "gh api -H'Accept: application/vnd.github+json' repos/o/r/pulls",
    "gh api -H Accept:x -q .[].number repos/o/r/pulls",
    'gh api repos/o/r/pulls/12/comments/3/replies -f body="Fixed in abc"',
    '"$PY" tool.py && gh api repos/o/r/pulls',
    "curl -A -d https://api.github.com/repos/o/r/pulls",
    "gh api repos/o/r/pulls/12",
    "gh api repos/o/r/pulls/12/comments -f body=x",
    "gh api -X PATCH repos/o/r/pulls/12 -f title=x",
    "gh api repos/o/r/issues -f title=x",
    "gh api graphql -f query='{ viewer { login } }'",
    "curl https://api.github.com/repos/o/r/pulls",
    "curl -s -H 'Accept: application/vnd.github+json' https://api.github.com/repos/o/r/pulls?state=open",
    "wget https://api.github.com/repos/o/r/pulls",
    "echo gh api repos/o/r/pulls -f title=x",
    "echo curl -d x https://api.github.com/repos/o/r/pulls",
]


def load_gate():
    """review-gate.py imported in this process (its name has a hyphen, so no plain import)."""
    spec = importlib.util.spec_from_file_location("review_gate_under_test", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApiCreates(Base):
    """NFX-102: `gh api`, `curl` and `wget` creates of a PR block, reviewed or not."""

    def test_api_creates_block(self):
        for command in API_CREATES:
            with self.subTest(command=command):
                r = self.run_gate(command, full=True)
                self.assertEqual(r.returncode, BLOCKED)
                self.assertIn(b"gh pr create", r.stderr)
                self.assertIn(b"--method GET", r.stderr)

    def test_api_create_blocks_even_after_a_review(self):
        self.mark("--findings", "0")
        self.assertEqual(self.run_gate("gh api repos/o/r/pulls -f title=x"), BLOCKED)

    def test_api_creates_block_outside_a_repo(self):
        """Unlike `gh pr create`, an API call names its repo itself, so it needs no checkout."""
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        for command in ("gh api repos/o/r/pulls -f title=x", "curl -d x https://api.github.com/repos/o/r/pulls"):
            with self.subTest(command=command):
                self.assertEqual(self.run_gate(command, cwd=plain), BLOCKED)

    def test_api_reads_pass(self):
        for command in API_READS:
            with self.subTest(command=command):
                self.assertEqual(self.run_gate(command), ALLOWED)


class Payloads(Base):
    """NFX-102: a hook payload the gate cannot read blocks unless it cannot be a create."""

    def raw_gate(self, data, *args):
        return subprocess.run([sys.executable, GATE, *args], input=data, capture_output=True, cwd=self.tmp)

    def payload(self, tool, tool_input):
        return json.dumps({"tool_name": tool, "cwd": self.repo, "tool_input": tool_input}).encode()

    def test_non_string_command_blocks(self):
        for tool in ("Bash", "PowerShell"):
            for command in (["gh", "pr", "create"], 12, {"x": "gh pr create"}, None):
                with self.subTest(tool=tool, command=command):
                    r = self.raw_gate(self.payload(tool, {"command": command}))
                    self.assertEqual(r.returncode, BLOCKED)
                    self.assertIn(b"could not read", r.stderr)

    def test_unreadable_payload_blocks(self):
        for data in (b"not json", b"[1, 2]", b'"text"', b"\xff\xfe\x00", self.payload("Bash", "gh pr create"),
                     json.dumps({"tool_name": "Bash", "cwd": 7, "tool_input": {"command": "ls"}}).encode()):
            with self.subTest(data=data):
                self.assertEqual(self.raw_gate(data).returncode, BLOCKED)

    def test_unreadable_payload_of_a_tool_that_cannot_create_passes(self):
        for data in (self.payload("Read", ["x"]), self.payload("Grep", {"command": 12})):
            with self.subTest(data=data):
                self.assertEqual(self.raw_gate(data).returncode, ALLOWED)

    def test_readable_payloads_still_pass(self):
        self.assertEqual(self.raw_gate(self.payload("Bash", {"command": "git status"})).returncode, ALLOWED)
        self.assertEqual(self.raw_gate(self.payload("Bash", {})).returncode, ALLOWED)


class Budget(Base):
    """NFX-102: a slow git makes the gate block within its budget; a hook that times out would let it pass."""

    def slow_git(self):
        stub = os.path.join(self.tmp, "slow-bin")
        os.makedirs(stub)
        path = os.path.join(stub, "git")
        with open(path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\nexec sleep 30\n")  # exec: the killed process is the one holding the pipes
        os.chmod(path, 0o755)
        return {**os.environ, "PATH": stub + os.pathsep + os.environ["PATH"]}

    def test_slow_git_blocks_within_the_budget(self):
        payload = json.dumps({"tool_name": "Bash", "cwd": self.repo,
                              "tool_input": {"command": "gh pr create --body x"}}).encode()
        start = time.monotonic()
        r = subprocess.run([sys.executable, GATE, "--budget", "1"], input=payload, capture_output=True,
                           env=self.slow_git(), cwd=self.tmp)
        self.assertEqual(r.returncode, BLOCKED)
        self.assertLess(time.monotonic() - start, 8)
        self.assertIn(b"gate timed out; retry or review at Tier 3", r.stderr)

    def test_check_entry_takes_the_budget_too(self):
        start = time.monotonic()
        r = subprocess.run([sys.executable, GATE, "check", "--cwd", self.repo, "--command", "gh pr create",
                            "--budget", "1"], capture_output=True, env=self.slow_git(), cwd=self.tmp)
        self.assertEqual(r.returncode, BLOCKED)
        self.assertLess(time.monotonic() - start, 8)
        self.assertIn(b"gate timed out", r.stderr)

    def test_default_budget_stays_under_the_old_hook_timeout(self):
        self.assertLess(load_gate().DEFAULT_BUDGET, 30)

    def test_a_bad_budget_falls_back_to_the_default(self):
        gate = load_gate()
        for argv in (["x", "--budget", "soon"], ["x", "--budget"], ["x", "--budget", "-3"], ["x"]):
            with self.subTest(argv=argv):
                self.assertEqual(gate.budget(argv), gate.DEFAULT_BUDGET)
        self.assertEqual(gate.budget(["x", "--budget", "55"]), 55)


class TreeOnce(Base):
    """NFX-102: one evaluation computes the working tree once, however many checks need it."""

    def test_worktree_tree_runs_once_per_evaluation(self):
        self.mark("--findings", "0")
        gate = load_gate()
        real, calls = gate.worktree_tree, []

        def counting(cwd):
            calls.append(cwd)
            return real(cwd)

        with mock.patch.object(gate, "worktree_tree", counting), contextlib.redirect_stderr(io.StringIO()):
            # a body with no Review-Gate line: the review check, the body check and the attest lines all run
            self.assertEqual(gate.evaluate("Bash", "gh pr create --body nothing", self.repo), BLOCKED)
            self.assertEqual(len(calls), 1)
            self.assertEqual(gate.evaluate("Bash", "gh pr create --body nothing", self.repo), BLOCKED)
            self.assertEqual(len(calls), 2)  # nothing is kept from one evaluation to the next


class FakeProc:
    """A Popen stand-in whose first wait() times out, like a check that never ends."""
    pid = 4242

    def __init__(self):
        self.kill = mock.Mock()
        self.calls = 0

    def wait(self, timeout=None):
        self.calls += 1
        if self.calls == 1:
            raise subprocess.TimeoutExpired("check", timeout)
        return 1


class WindowsStop(unittest.TestCase):
    """NFX-115: on Windows a timed-out check is stopped with its whole process tree, not just cmd.exe."""
    CHECK = {"command": "gradlew test", "timeout": 1}

    def run_timed_out(self, taskkill):
        """run_check with os.name 'nt' and a check that times out; `taskkill` is the stub for subprocess.run."""
        gate, proc = load_gate(), FakeProc()
        fake_os = mock.Mock(wraps=os, name="nt")
        fake_os.name = "nt"
        fake_os.environ = {}
        with mock.patch.object(gate, "os", fake_os), mock.patch.object(gate, "git", return_value="/repo"), \
                mock.patch.object(gate.subprocess, "Popen", return_value=proc), \
                mock.patch.object(gate.subprocess, "run", taskkill) as run:
            ok, why = gate.run_check("/repo", self.CHECK)
        return ok, why, proc, run

    def test_a_timeout_runs_taskkill_on_the_tree(self):
        ok, why, proc, run = self.run_timed_out(mock.Mock(return_value=subprocess.CompletedProcess([], 0)))
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/F", "/PID", "4242"])
        proc.kill.assert_not_called()
        self.assertFalse(ok)  # a timeout never passes, so a child that outlives taskkill still refuses the mark
        self.assertEqual(why, "did not finish within 1 seconds")

    def test_a_missing_taskkill_falls_back_to_kill_and_says_so(self):
        ok, why, proc, _ = self.run_timed_out(mock.Mock(side_effect=FileNotFoundError("taskkill")))
        proc.kill.assert_called_once_with()
        self.assertFalse(ok)
        self.assertIn("did not finish within 1 seconds", why)
        self.assertIn("taskkill", why)
        self.assertIn("only the shell", why)

    def test_a_failing_taskkill_falls_back_to_kill_and_says_so(self):
        ok, why, proc, _ = self.run_timed_out(mock.Mock(return_value=subprocess.CompletedProcess([], 128)))
        proc.kill.assert_called_once_with()
        self.assertFalse(ok)
        self.assertIn("only the shell", why)

    def test_a_hung_taskkill_falls_back_to_kill(self):
        _, why, proc, _ = self.run_timed_out(mock.Mock(side_effect=subprocess.TimeoutExpired("taskkill", 30)))
        proc.kill.assert_called_once_with()
        self.assertIn("only the shell", why)


if __name__ == "__main__":
    unittest.main(verbosity=1)
