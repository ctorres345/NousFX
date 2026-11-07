#!/usr/bin/env python3
"""Tests for `verify-tier` and its CI step (NFX-70): CI recomputes the tier and fails a lower Review-Tier line.
Run: python3 -B claude/hooks/test_review_gate_ci_tier.py

Throwaway files and git repos under a temp dir. Exit 0 passes, 2 blocks.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_gate_check import GATE, Base  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIER_PY = os.path.join(ROOT, "claude", "skills", "review-loop", "scripts", "tier.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "review-gate.yml")


class VerifyTier(Base):
    def verify(self, body, tier=None, reasons=None, raw=None, association="OWNER"):
        tier_json = os.path.join(self.tmp, "tier.json")
        with open(tier_json, "w", encoding="utf-8") as f:
            f.write(raw if raw is not None else json.dumps(
                {"tier": tier, "reasons": reasons or ["Tier %d: fake" % tier], "ui": False, "stats": {}}))
        body_file = os.path.join(self.tmp, "body.txt")
        with open(body_file, "w", encoding="utf-8") as f:
            f.write(body)
        assoc = [] if association is None else ["--author-association", association]
        return subprocess.run([sys.executable, "-B", GATE, "verify-tier", "--tier-json", tier_json,
                               "--text-file", body_file] + assoc, cwd=self.tmp, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def assertPasses(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Review tier checked.", r.stdout)

    def assertBlocked(self, r):
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertNotIn("Review tier checked.", r.stdout)

    def test_lower_tier_fails_and_names_the_computed_tier(self):
        r = self.verify("Summary\n\nReview-Gate: %s\nReview-Tier: 1 (Tier 1: small)\n" % ("a" * 40), tier=3,
                        reasons=["Tier 3: risk path .github/workflows/x.yml"])
        self.assertBlocked(r)
        self.assertIn("computed Tier 3", r.stderr)
        self.assertIn(".github/workflows/x.yml", r.stderr)

    def test_equal_or_higher_tier_passes(self):
        self.assertPasses(self.verify("Review-Tier: 2 (Tier 2: 300 lines, 5 file(s))\n", tier=2))
        self.assertPasses(self.verify("Review-Tier: 3\n", tier=1))

    def test_no_tier_line_passes_as_the_known_hole(self):
        r = self.verify("Review-Gate: %s\n" % ("a" * 40), tier=3)
        self.assertPasses(r)
        self.assertIn("known hole", r.stdout)
        self.assertPasses(self.verify("Review-Gate: %s\n" % ("a" * 40), tier=3, association="NONE"))

    def test_a_mention_inside_a_sentence_is_not_a_tier_line(self):
        self.assertPasses(self.verify("The Review-Tier: 0 line is new.\n", tier=3))

    def test_override_naming_the_computed_tier_passes_and_says_it_is_self_asserted(self):
        body = "Review-Tier: 1 (Tier 1: 30 lines, 2 file(s)) override: computed 3, Cesar approved a docs-only change\n"
        r = self.verify(body, tier=3)
        self.assertPasses(r)
        self.assertIn("self-asserted", r.stdout)
        self.assertIn("Cesar approved a docs-only change", r.stdout)

    def test_override_is_honored_for_owner_and_member_only(self):
        """NFX-95: anyone can write an override line, so CI honors it only from the repo owner or an org member."""
        body = "Review-Tier: 1 (Tier 1: small) override: computed 3, approved\n"
        self.assertPasses(self.verify(body, tier=3, association="MEMBER"))
        for assoc in ("COLLABORATOR", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "NONE", "owner", ""):
            r = self.verify(body, tier=3, association=assoc)
            self.assertBlocked(r)
            self.assertIn("OWNER or MEMBER", r.stderr)
        r = self.verify(body, tier=3, association=None)
        self.assertBlocked(r)
        self.assertIn("unknown", r.stderr)

    def test_a_line_at_or_above_the_tier_needs_no_trusted_author(self):
        self.assertPasses(self.verify("Review-Tier: 3\n", tier=3, association="NONE"))
        self.assertPasses(self.verify("Review-Tier: 3 override: computed 3, x\n", tier=2, association=None))

    def test_override_naming_a_lower_computed_tier_fails(self):
        r = self.verify("Review-Tier: 0 (Tier 0: tiny) override: computed 1, small\n", tier=3)
        self.assertBlocked(r)
        self.assertIn("CI computes Tier 3", r.stderr)

    def test_override_without_a_reason_is_not_an_override(self):
        self.assertBlocked(self.verify("Review-Tier: 0 override: computed 3, \n", tier=3))

    def test_any_low_line_fails(self):
        self.assertBlocked(self.verify("Review-Tier: 3\nReview-Tier: 0\n", tier=2))

    def test_a_malformed_tier_line_fails(self):
        self.assertBlocked(self.verify("Review-Tier: two\n", tier=0))
        self.assertBlocked(self.verify("Review-Tier: 4\n", tier=0))
        self.assertBlocked(self.verify("Review-Tier: 10\n", tier=0))
        self.assertBlocked(self.verify("Review-Tier: 0.5\n", tier=0))
        self.assertBlocked(self.verify("Review-Tier: 0x\n", tier=0))

    def test_a_diff_error_fallback_does_not_fail_a_low_tier(self):
        r = self.verify("Review-Tier: 0\n", tier=2, reasons=["Tier 2: could not read the diff (boom)"])
        self.assertPasses(r)
        self.assertIn("Warning", r.stdout)

    def test_a_risk_paths_error_still_compares_the_measured_tier(self):
        reasons = ["Tier 2: could not read [review] risk_paths (x)", "Tier 2: 300 lines, 5 file(s), 3 hunk(s)"]
        r = self.verify("Review-Tier: 0\n", tier=2, reasons=reasons)
        self.assertBlocked(r)
        self.assertIn("computed Tier 2", r.stderr)
        reasons = ["Tier 2: could not read [review] risk_paths (x)", "Tier 0: 3 code lines in 1 file(s)"]
        self.assertPasses(self.verify("Review-Tier: 0\n", tier=2, reasons=reasons))

    def test_tier_3_is_enforced_even_with_a_could_not_reason(self):
        reasons = ["Tier 2: could not read [review] risk_paths (bad)", "Tier 3: risk path .github/x"]
        self.assertBlocked(self.verify("Review-Tier: 0\n", tier=3, reasons=reasons))

    def test_bad_tier_json_blocks(self):
        self.assertBlocked(self.verify("Review-Tier: 3\n", raw="not json"))
        self.assertBlocked(self.verify("Review-Tier: 3\n", raw='{"tier": 7}'))
        self.assertBlocked(self.verify("Review-Tier: 3\n", raw='{"tier": true}'))
        self.assertBlocked(self.verify("Review-Tier: 3\n", raw='[]'))

    def test_usage_error_blocks(self):
        r = subprocess.run([sys.executable, "-B", GATE, "verify-tier"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2)

    def test_real_tier_py_on_a_risky_diff(self):
        """End to end: the real tier.py computes Tier 3 for a workflow change, so a Tier 0 body fails."""
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True,
                              check=True).stdout.strip()
        os.makedirs(os.path.join(self.repo, ".github", "workflows"))
        with open(os.path.join(self.repo, ".github", "workflows", "ci.yml"), "w", encoding="utf-8") as f:
            f.write("on: push\n")
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "ci")
        out = subprocess.run([sys.executable, "-B", TIER_PY, "--base", base, "--cwd", self.repo],
                             capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL).stdout
        computed = json.loads(out)
        self.assertEqual(computed["tier"], 3, out)
        self.assertBlocked(self.verify("Review-Tier: 0 (Tier 0: tiny)\n", raw=out))
        self.assertPasses(self.verify("Review-Tier: 3 (Tier 3: risk)\n", raw=out))

    def test_real_tier_py_fallback_reason_matches_what_verify_tier_reads(self):
        """Pins the coupling: tier.py's diff-error reason starts with the prefix verify-tier treats as unknown."""
        out = subprocess.run([sys.executable, "-B", TIER_PY, "--base", "0" * 40, "--cwd", self.repo],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
        self.assertTrue(json.loads(out)["reasons"][0].startswith("Tier 2: could not"), out)
        self.assertPasses(self.verify("Review-Tier: 0\n", raw=out))


class Workflow(unittest.TestCase):
    """Static checks on the CI step: base copies only, the body via env, a positive confirmation."""

    def setUp(self):
        with open(WORKFLOW, encoding="utf-8") as f:
            self.text = f.read()
        self.step = self.text.split("# The tier check (NFX-70)")[1].split("run-check --ref")[0]

    def test_tier_py_comes_from_the_base_ref(self):
        self.assertIn('git show "origin/$BASE_REF:claude/skills/review-loop/scripts/tier.py"', self.step)
        self.assertIn('python3 -B "$gate_dir/tier.py" --base "$base"', self.step)
        self.assertNotIn("python3 -B claude/", self.text)

    def test_no_labels_in_ci(self):
        self.assertNotIn("--labels", self.step)

    def test_guard_for_a_base_without_verify_tier(self):
        self.assertIn("grep -q 'sys.argv\\[1:2\\] == \\[\"verify-tier\"\\]' \"$gate_dir/review-gate.py\"", self.step)

    def test_needs_a_positive_confirmation(self):
        self.assertIn('*"Review tier checked."*', self.step)

    def test_passes_the_pr_author_association(self):
        self.assertIn("AUTHOR_ASSOCIATION: ${{ github.event.pull_request.author_association }}", self.text)
        self.assertIn('--author-association "$AUTHOR_ASSOCIATION"', self.step)
        self.assertIn("predates NFX-95", self.step)

    def test_merge_base_is_the_diff_base(self):
        self.assertIn('git merge-base "origin/$BASE_REF" HEAD', self.step)


if __name__ == "__main__":
    unittest.main()
