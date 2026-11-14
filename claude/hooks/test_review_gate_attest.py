#!/usr/bin/env python3
"""Tests for `attest` and `verify-attestation` (durable CI attestation).
Run: python3 -B claude/hooks/test_review_gate_attest.py

Throwaway git repos under a temp dir. Exit 0 ok, 2 refused.
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_gate_check import GATE, Base  # noqa: E402


class Attest(Base):
    def run_gate(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-B", GATE, *args], cwd=cwd or self.repo,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def tree(self):
        return subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()

    def verify(self, text, tree=None, extra=()):
        path = os.path.join(self.tmp, "body.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return self.run_gate("verify-attestation", "--tree", tree or self.tree(),
                             "--text-file", path, *extra, cwd=self.tmp)

    def test_attest_refused_without_mark(self):
        r = self.run_gate("attest")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")

    def test_attest_refused_on_a_new_branch_with_the_marked_tree(self):
        self.mark("--findings", "0")  # recorded on cesar/DEM-1
        self.sh("git", "checkout", "-q", "-b", "cesar/DEM-2")  # zero commits, same tree (NFX-71)
        r = self.run_gate("attest")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("another branch", r.stderr)

    def test_attest_refused_with_open_findings(self):
        self.mark("--findings", "2")
        self.assertEqual(self.run_gate("attest").returncode, 2)

    def test_attest_line_after_review(self):
        self.mark("--findings", "0")
        r = self.run_gate("attest")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "Review-Gate: " + self.tree())

    def test_attest_line_for_skip_carries_reason(self):
        self.mark("--skip", "docs only\nsecond line")
        r = self.run_gate("attest")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "Review-Gate: " + self.tree() + " skipped: docs only second line")

    def test_attest_prints_the_tier_on_its_own_line(self):
        self.mark("--findings", "0", "--tier", "3", "--reasons", "risk path: claude/hooks\nsecond")
        r = self.run_gate("attest")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.splitlines(),
                         ["Review-Gate: " + self.tree(), "Review-Tier: 3 (risk path: claude/hooks second)"])

    def test_attest_tier_without_reasons(self):
        self.mark("--findings", "0", "--tier", "2")  # no merge base here, so tier.py computes Tier 2 (NFX-68)
        self.assertEqual(self.run_gate("attest").stdout.splitlines()[1], "Review-Tier: 2")

    def test_attest_without_a_tier_prints_one_line(self):
        self.mark("--findings", "0")
        self.assertEqual(len(self.run_gate("attest").stdout.splitlines()), 1)

    def test_a_skip_has_no_tier_line(self):
        self.assertEqual(self.mark("--skip", "docs only", "--tier", "1").returncode, 0)
        self.assertNotIn("Review-Tier", self.run_gate("attest").stdout)

    def test_bad_tier_or_reasons_without_tier_record_nothing(self):
        for args in (["--tier", "4"], ["--tier", "-1"], ["--tier", "x"], ["--tier"], ["--reasons", "why"],
                     ["--tier", "2", "--reasons"], ["--tier", "2", "--reasons", "--findings"]):
            r = self.run_gate("--mark", "--findings", "0", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertEqual(self.run_gate("attest").returncode, 2, args)

    def test_verify_accepts_a_body_with_the_tier_line(self):
        r = self.verify("Review-Gate: " + self.tree() + "\nReview-Tier: 3 (risk path)\n")
        self.assertEqual(r.returncode, 0)

    def test_attest_refused_after_tree_changes(self):
        self.mark("--findings", "0")
        with open(os.path.join(self.repo, "f.txt"), "w", encoding="utf-8") as f:
            f.write("changed")
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "later")
        self.assertEqual(self.run_gate("attest").returncode, 2)

    def test_verify_accepts_matching_line_inside_body(self):
        r = self.verify("## Summary\nstuff\n\nReview-Gate: " + self.tree() + "\n\nmore\n")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("skipped", r.stdout)

    def test_verify_refuses_other_tree(self):
        self.assertEqual(self.verify("Review-Gate: " + "a" * 40).returncode, 2)

    def test_verify_refuses_missing_line(self):
        self.assertEqual(self.verify("nothing here").returncode, 2)

    def test_verify_refuses_inline_mention(self):
        self.assertEqual(self.verify("see Review-Gate: " + self.tree() + " in text").returncode, 2)

    def test_verify_surfaces_skip_reason(self):
        r = self.verify("Review-Gate: " + self.tree() + " skipped: docs only")
        self.assertEqual(r.returncode, 0)
        self.assertIn("skipped: docs only", r.stdout)

    def test_verify_bad_input_refuses(self):
        self.assertEqual(self.run_gate("verify-attestation", cwd=self.tmp).returncode, 2)
        r = self.run_gate("verify-attestation", "--tree", self.tree(), "--text-file",
                          os.path.join(self.tmp, "missing.txt"), cwd=self.tmp)
        self.assertEqual(r.returncode, 2)

    def test_verify_any_of_several_files(self):
        other = os.path.join(self.tmp, "other.txt")
        with open(other, "w", encoding="utf-8") as f:
            f.write("Review-Gate: " + self.tree() + "\n")
        r = self.verify("no line", extra=("--text-file", other))
        self.assertEqual(r.returncode, 0)

    def test_check_semantics_unchanged(self):
        self.assertEqual(self.check_create(), 2)


WORKFLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        ".github", "workflows", "review-gate.yml")


class Workflow(unittest.TestCase):
    """Static checks: the CI gate must not run the code under review, nor interpolate the PR body."""

    def setUp(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            self.text = f.read()

    def test_gate_script_comes_from_base_ref(self):
        self.assertIn('git show "origin/$BASE_REF:claude/hooks/review-gate.py"', self.text)
        self.assertIn('git show "origin/$BASE_REF:claude/hooks/pr_create_parser.py"', self.text)
        self.assertNotIn('python3 -B claude/hooks/review-gate.py', self.text)

    def test_check_command_comes_from_the_base_ref(self):
        self.assertIn('run-check --ref "origin/$BASE_REF"', self.text)
        self.assertIn('python3 -B "$gate_dir/review-gate.py" run-check', self.text)
        self.assertIn("grep -q 'sys.argv\\[1:2\\] == \\[\"run-check\"\\]' \"$gate_dir/review-gate.py\"", self.text)

    def test_a_skip_attestation_does_not_skip_the_check(self):
        # NFX-110: the skip-label block may add failures for a skip, but never an exit 0 or a way past the check.
        label = self.text.split("# NFX-110 skip label check")[1].split("# end NFX-110")[0]
        self.assertNotIn("exit 0", label)
        text = self.text.replace(label, "")
        via = text.split("# NFX-20 Via check")[1].split("# end NFX-20")[0]  # NFX-20: its skip arm only fails or passes on a label
        self.assertNotIn("exit 0", via)
        text = text.replace(via, "")
        self.assertNotIn("exit 0", text.split("run-check --ref")[0].split("verify-attestation")[-1])
        self.assertNotIn("skipped:", text.split("run-check --ref")[0].split("case \"$out\" in")[-1])

    def test_check_step_needs_a_positive_confirmation(self):
        self.assertIn('*"Check passed."*', self.text)

    def test_pr_body_only_via_env(self):
        runs = [l for l in self.text.splitlines() if "${{" in l and "pull_request.body" in l]
        self.assertTrue(runs)
        self.assertTrue(all(l.strip().startswith("PR_BODY:") for l in runs), runs)

    def test_read_only_permissions(self):
        self.assertIn("contents: read", self.text)

    def test_reruns_when_body_is_edited(self):
        self.assertIn("edited", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=1)
