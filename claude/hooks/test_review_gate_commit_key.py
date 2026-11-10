#!/usr/bin/env python3
"""Tests for the commit key check (NFX-86): a commit subject must name the branch's Jira key.
Run: python3 -B claude/hooks/test_review_gate_commit_key.py

Local: the commit-msg hook and the Claude hook only warn (exit 0). CI: `check-commits` fails on a wrong or
missing key. Merge commits are skipped everywhere. Throwaway git repos under a temp dir.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from test_review_gate_check import GATE, Base  # noqa: E402

COMMIT_MSG = os.path.join(ROOT, "claude", "githooks", "commit-msg")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "review-gate.yml")


def load_gate():
    spec = importlib.util.spec_from_file_location("review_gate_commit_key_test", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Rule(unittest.TestCase):
    """commit_key_problem(subject, branch): a message, or None when the subject is fine or not judged."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def problem(self, subject, branch="cesar/NFX-83"):
        return self.gate.commit_key_problem(subject, branch)

    def test_matching_key_passes(self):
        self.assertIsNone(self.problem("NFX-83: fix it"))

    def test_multi_key_passes_when_it_holds_the_branch_key(self):
        self.assertIsNone(self.problem("NFX-29, NFX-83: both"))
        self.assertIsNone(self.problem("NFX-83, NFX-29: both"))

    def test_other_key_is_a_problem_and_names_both(self):
        msg = self.problem("NFX-10: x")
        self.assertIn("NFX-10", msg)
        self.assertIn("NFX-83", msg)

    def test_multi_key_without_the_branch_key_is_a_problem(self):
        self.assertTrue(self.problem("NFX-10, NFX-11: x"))

    def test_no_key_is_a_problem(self):
        self.assertTrue(self.problem("fix typo"))

    def test_key_not_at_the_start_does_not_count(self):
        self.assertTrue(self.problem("fix NFX-83 typo"))

    def test_prefix_of_a_longer_number_does_not_match(self):
        self.assertTrue(self.problem("NFX-830: x"))

    def test_one_letter_key_prefix_is_not_a_key_so_the_branch_is_not_judged(self):
        self.assertIsNone(self.problem("anything", "cesar/A-1"))

    def test_key_without_colon_is_a_problem(self):
        self.assertTrue(self.problem("NFX-83 fix it"))

    def test_branch_without_a_key_is_not_judged(self):
        self.assertIsNone(self.problem("anything", "main"))
        self.assertIsNone(self.problem("anything", "HEAD"))
        self.assertIsNone(self.problem("anything", "feature/no-key"))

    def test_git_generated_subjects_are_not_judged(self):
        for subject in ("Merge branch 'main' into cesar/NFX-83", "Merge pull request #5 from x/y",
                        "Revert \"NFX-83: x\"", "fixup! NFX-83: x", "squash! NFX-83: x"):
            self.assertIsNone(self.problem(subject), subject)


class CheckCommits(Base):
    """`check-commits BRANCH --base REF`: the CI step. Exit 1 on a bad subject, merges skipped."""

    def setUp(self):
        super().setUp()
        self.sh("git", "switch", "-q", "-c", "cesar/NFX-83")
        self.base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True,
                                   text=True, check=True).stdout.strip()

    def commit(self, subject):
        self.sh("git", "commit", "-q", "--allow-empty", "--no-verify", "-m", subject)

    def run_check(self, branch="cesar/NFX-83"):
        return subprocess.run([sys.executable, "-B", GATE, "check-commits", branch, "--base", self.base],
                              cwd=self.repo, capture_output=True, text=True)

    def test_matching_commits_pass(self):
        self.commit("NFX-83: one")
        self.commit("NFX-29, NFX-83: two")
        r = self.run_check()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Commit keys checked.", r.stdout)

    def test_wrong_key_fails_and_names_the_commit(self):
        self.commit("NFX-83: one")
        self.commit("NFX-10: wrong")
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        out = r.stdout + r.stderr
        self.assertIn("NFX-10: wrong", out)
        self.assertNotIn("NFX-83: one", out)

    def test_keyless_commit_fails(self):
        self.commit("fix typo")
        self.assertEqual(self.run_check().returncode, 1)

    def test_merge_commit_is_skipped(self):
        self.commit("NFX-83: one")
        self.sh("git", "switch", "-q", "-c", "side", self.base)
        self.commit("NFX-83: side")
        self.sh("git", "switch", "-q", "cesar/NFX-83")
        self.sh("git", "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")
        r = self.run_check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_commits_pass(self):
        self.assertEqual(self.run_check().returncode, 0)

    def test_branch_without_a_key_fails_with_a_message(self):
        r = self.run_check("feature/x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("feature/x", r.stdout + r.stderr)

    def test_bad_usage_fails(self):
        r = subprocess.run([sys.executable, "-B", GATE, "check-commits", "cesar/NFX-83"], cwd=self.repo,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)

    def test_unknown_base_fails_closed(self):
        r = subprocess.run([sys.executable, "-B", GATE, "check-commits", "cesar/NFX-83", "--base", "nope"],
                           cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


class CommitMsgHook(Base):
    """claude/githooks/commit-msg: advisory, always exit 0."""

    def setUp(self):
        super().setUp()
        self.sh("git", "switch", "-q", "-c", "cesar/NFX-83")

    def run_hook(self, text):
        path = os.path.join(self.tmp, "MSG")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return subprocess.run(["sh", COMMIT_MSG, path], cwd=self.repo, capture_output=True, text=True)

    def test_matching_key_is_silent(self):
        r = self.run_hook("NFX-83: x\n")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_wrong_key_warns_and_exits_0(self):
        r = self.run_hook("NFX-10: x\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("commit-msg: warning", r.stderr)
        self.assertIn("NFX-10", r.stderr)
        self.assertIn("NFX-83", r.stderr)

    def test_comment_lines_are_skipped(self):
        r = self.run_hook("# comment\nNFX-83: x\n")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_multi_key_matches(self):
        r = self.run_hook("NFX-29, NFX-83: x\n")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_keyless_warns(self):
        r = self.run_hook("fix typo\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("warning", r.stderr)

    def test_merge_in_progress_is_skipped(self):
        with open(os.path.join(self.repo, ".git", "MERGE_HEAD"), "w", encoding="utf-8") as f:
            f.write("0" * 40 + "\n")
        r = self.run_hook("Merge branch 'x'\n")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_detached_head_and_keyless_branch_are_skipped(self):
        self.sh("git", "switch", "-q", "-c", "scratch")
        self.assertEqual(self.run_hook("anything\n").stderr, "")
        self.sh("git", "switch", "-q", "--detach")
        self.assertEqual(self.run_hook("anything\n").stderr, "")

    def test_one_letter_key_prefix_agrees_with_python(self):
        self.sh("git", "switch", "-q", "-c", "cesar/A-1")
        self.assertEqual(self.run_hook("anything\n").stderr, "")

    def test_real_commit_warns_but_still_commits(self):
        hooks = os.path.join(self.repo, ".git", "hooks")
        os.makedirs(hooks, exist_ok=True)
        target = os.path.join(hooks, "commit-msg")
        with open(COMMIT_MSG, encoding="utf-8") as src, open(target, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        os.chmod(target, 0o755)
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
                            "-m", "NFX-10: x"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("commit-msg: warning", r.stderr)


class ClaudeHook(Base):
    """The Claude PreToolUse hook warns on a literal `git commit -m` subject and never blocks."""

    def setUp(self):
        super().setUp()
        self.sh("git", "switch", "-q", "-c", "cesar/NFX-83")

    def hook(self, command, cwd=None, tool="Bash"):
        payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd or self.repo})
        return subprocess.run([sys.executable, "-B", GATE], input=payload, capture_output=True, text=True)

    def warning(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_wrong_key_warns(self):
        out = self.warning(self.hook('git commit -m "NFX-10: x"'))
        data = json.loads(out)
        self.assertIn("NFX-10", json.dumps(data))
        self.assertIn("NFX-83", json.dumps(data))

    def test_matching_key_is_silent(self):
        self.assertEqual(self.warning(self.hook('git commit -m "NFX-83: x"')), "")

    def test_all_message_forms_are_read(self):
        for cmd in ("git commit -am 'NFX-10: x'", "git commit --message='NFX-10: x'",
                    "git commit --allow-empty --message 'NFX-10: x'", "git add -A && git commit -m 'NFX-10: x'",
                    "git commit -m \"$(cat <<'EOF'\nNFX-10: x\n\nbody\nEOF\n)\""):
            self.assertIn("NFX-10", self.warning(self.hook(cmd)), cmd)

    def test_unreadable_subject_is_silent(self):
        for cmd in ("git commit -F msg.txt", "git commit", 'git commit -m "$MSG"', "git commit --amend --no-edit",
                    "git commit -C HEAD"):
            self.assertEqual(self.warning(self.hook(cmd)), "", cmd)

    def test_other_commands_are_silent(self):
        for cmd in ("git status", "echo git commit -m 'NFX-10: x'", "git log -m 'NFX-10: x'"):
            self.assertEqual(self.warning(self.hook(cmd)), "", cmd)

    def test_cd_before_commit_is_silent(self):
        self.assertEqual(self.warning(self.hook('cd /tmp && git commit -m "NFX-10: x"')), "")

    def test_outside_a_repo_is_silent(self):
        self.assertEqual(self.warning(self.hook('git commit -m "NFX-10: x"', cwd=self.tmp)), "")

    def test_only_shell_tools_are_judged(self):
        self.assertEqual(self.warning(self.hook('git commit -m "NFX-10: x"', tool="Read")), "")

    def test_a_commit_with_a_pr_create_is_still_gated(self):
        r = self.hook('git commit -m "NFX-83: x" && gh pr create --fill')
        self.assertEqual(r.returncode, 2)


class Workflow(unittest.TestCase):
    def test_commit_step_runs_between_the_branch_check_and_the_attestation(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        branch = text.index("Check the branch name")
        commits = text.index("check-commits")
        attest = text.index("Verify the review attestation")
        self.assertLess(branch, commits)
        self.assertLess(commits, attest)
        self.assertRegex(text, r"origin/\$BASE_REF:claude/hooks/review-gate\.py")

    def test_commit_step_skips_when_the_base_gate_lacks_it(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        step = text[text.index("Check the commit subjects"):text.index("Verify the review attestation")]
        self.assertRegex(step, r"::warning::.*check-commits")
        self.assertIn("branch-exception", step)


if __name__ == "__main__":
    unittest.main()
