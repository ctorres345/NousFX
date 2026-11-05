#!/usr/bin/env python3
"""Tests for review-gate.py. Run: python3 claude/hooks/test_review_gate.py

Each test runs the hook as a subprocess on a throwaway git repo. BLOCKED is exit 2, ALLOWED exit 0.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

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
]


class Base(unittest.TestCase):
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
        return path

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

    def run_gate(self, command=None, cwd=None, tool="Bash", env=None, gate=GATE):
        payload = {"tool_name": tool, "cwd": cwd or self.repo, "tool_input": {}}
        if command is not None:
            payload["tool_input"]["command"] = command
        return subprocess.run([sys.executable, gate], input=json.dumps(payload).encode(),
                              capture_output=True, env={**os.environ, **(env or {})},
                              cwd=self.tmp).returncode


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
        self.mark("--findings", "0")
        for command in RUNS_CREATE:
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

    def test_bad_payload_passes(self):
        r = subprocess.run([sys.executable, GATE], input=b"not json", capture_output=True)
        self.assertEqual(r.returncode, ALLOWED)

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
    def test_mark_alone_is_refused_and_records_nothing(self):
        self.assertNotEqual(self.mark().returncode, 0)
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
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)

    def test_open_findings_block(self):
        self.mark("--findings", "2")
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    def test_skip_passes(self):
        self.assertEqual(self.mark("--skip", "approved").returncode, 0)
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)
        self.mark("--findings", "2", "--skip", "approved")
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)

    def test_committed_edit_after_review_blocks(self):
        self.mark("--findings", "0")
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
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
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)


if __name__ == "__main__":
    unittest.main(verbosity=1)
