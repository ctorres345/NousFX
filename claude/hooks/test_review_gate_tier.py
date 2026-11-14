#!/usr/bin/env python3
"""Tests for the tier check in `--mark` (NFX-68): the gate runs tier.py and refuses a lower --tier.
Run: python3 -B claude/hooks/test_review_gate_tier.py

Each test copies the gate into a throwaway layout (`hooks/` next to `skills/review-loop/scripts/`), so a fake
tier.py can stand in for the real one. Exit 0 ok, 2 refused.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_gate_check import GATE, Base  # noqa: E402

HOOKS = os.path.dirname(GATE)


class TierCheck(Base):
    def layout(self, tier=None, body=None, code=0):
        """Copy the gate into tmp/lay/hooks. With `tier` (or a raw `body`) add a fake tier.py that prints it and
        exits `code`. Returns the copied gate path."""
        lay = os.path.join(self.tmp, "lay")
        shutil.rmtree(lay, ignore_errors=True)
        hooks = os.path.join(lay, "hooks")
        os.makedirs(hooks)
        for name in ("review-gate.py", "pr_create_parser.py", "git_refs.py"):
            shutil.copy(os.path.join(HOOKS, name), hooks)
        if tier is not None or body is not None:
            scripts = os.path.join(lay, "skills", "review-loop", "scripts")
            os.makedirs(scripts)
            self.sentinel = os.path.join(self.tmp, "tier-ran")
            if body is None:
                body = json.dumps({"tier": tier, "reasons": ["Tier %d: fake" % tier], "ui": False, "fallback": False, "stats": {}})
            with open(os.path.join(scripts, "tier.py"), "w", encoding="utf-8") as f:
                f.write("import sys\nopen(%r, 'w').close()\nprint(%r)\nsys.exit(%d)\n" % (self.sentinel, body, code))
        return os.path.join(hooks, "review-gate.py")

    def gate(self, gate, *args):
        return subprocess.run([sys.executable, "-B", gate, *args], cwd=self.repo, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)

    def attest(self, gate):
        return self.gate(gate, "attest")

    def test_lower_tier_is_refused_and_names_the_computed_tier(self):
        gate = self.layout(tier=3)
        r = self.gate(gate, "--mark", "--findings", "0", "--tier", "0")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Tier 3", r.stderr)
        self.assertIn("ask the user", r.stderr)
        self.assertNotIn('--tier-override "', r.stderr)  # NFX-101: no ready-made override command
        self.assertEqual(self.attest(gate).returncode, 2)  # nothing recorded

    def test_a_refusal_removes_an_older_good_mark(self):
        gate = self.layout(tier=3)
        self.assertEqual(self.gate(gate, "--mark", "--findings", "0", "--tier", "3").returncode, 0)
        self.assertEqual(self.gate(gate, "--mark", "--findings", "0", "--tier", "1").returncode, 2)
        self.assertEqual(self.attest(gate).returncode, 2)

    def test_equal_or_higher_tier_passes_without_an_override_line(self):
        for computed, given in ((0, 0), (2, 2), (1, 3), (0, 3)):
            with self.subTest(computed=computed, given=given):
                gate = self.layout(tier=computed)
                r = self.gate(gate, "--mark", "--findings", "0", "--tier", str(given), "--reasons", "why")
                self.assertEqual(r.returncode, 0, r.stderr)
                out = self.attest(gate).stdout
                self.assertIn("Review-Tier: %d (why)" % given, out)
                self.assertNotIn("override", out)

    def test_override_records_the_reason_and_the_computed_tier(self):
        gate = self.layout(tier=2)
        r = self.gate(gate, "--mark", "--findings", "0", "--tier", "1", "--tier-override", "docs only,\nCesar ok")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = self.attest(gate).stdout.splitlines()[1]
        self.assertEqual(line, "Review-Tier: 1 override: computed 2, docs only, Cesar ok")

    def test_override_with_reasons_keeps_both(self):
        gate = self.layout(tier=2)
        self.gate(gate, "--mark", "--findings", "0", "--tier", "0", "--reasons", "tiny", "--tier-override", "typo")
        self.assertEqual(self.attest(gate).stdout.splitlines()[1],
                         "Review-Tier: 0 (tiny) override: computed 2, typo")

    def test_override_is_dropped_when_the_tier_is_not_lower(self):
        gate = self.layout(tier=1)
        r = self.gate(gate, "--mark", "--findings", "0", "--tier", "2", "--tier-override", "stale")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.attest(gate).stdout
        self.assertNotIn("override", out)
        self.assertNotIn("stale", out)

    def test_bad_override_records_nothing(self):
        gate = self.layout(tier=3)
        for args in (["--tier-override", "why"], ["--tier", "1", "--tier-override"], ["--tier", "1", "--tier-override", " "],
                     ["--tier", "1", "--tier-override", "--findings"]):
            with self.subTest(args=args):
                r = self.gate(gate, "--mark", "--findings", "0", *args)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(self.attest(gate).returncode, 2)

    def test_override_with_a_skip_is_refused(self):
        gate = self.layout(tier=3)
        r = self.gate(gate, "--mark", "--skip", "docs", "--tier", "1", "--tier-override", "why")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.attest(gate).returncode, 2)

    def test_missing_tier_py_counts_as_tier_2(self):
        gate = self.layout()
        refused = self.gate(gate, "--mark", "--findings", "0", "--tier", "1")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("Tier 2", refused.stderr)
        self.assertIn("tier.py", refused.stderr)
        ok = self.gate(gate, "--mark", "--findings", "0", "--tier", "2")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("tier.py", ok.stderr)  # a note, so the cause is not silent
        self.assertEqual(self.gate(gate, "--mark", "--findings", "0", "--tier", "1", "--tier-override", "x").returncode, 0)

    def test_a_broken_tier_py_counts_as_tier_2(self):
        for label, kwargs in (("exit 2", dict(body="{}", code=2)), ("exit 1", dict(body='{"tier": 0}', code=1)),
                              ("not json", dict(body="oops")), ("no tier key", dict(body="{}")),
                              ("tier 7", dict(body='{"tier": 7}')), ("bool tier", dict(body='{"tier": true}')),
                              ("string tier", dict(body='{"tier": "0"}'))):
            with self.subTest(label):
                gate = self.layout(**kwargs)
                self.assertEqual(self.gate(gate, "--mark", "--findings", "0", "--tier", "1").returncode, 2)
                self.assertEqual(self.gate(gate, "--mark", "--findings", "0", "--tier", "2").returncode, 0)

    def test_tier_py_only_runs_with_a_tier_and_not_for_a_skip(self):
        gate = self.layout(tier=3)
        self.assertEqual(self.gate(gate, "--mark", "--findings", "0").returncode, 0)
        self.assertFalse(os.path.exists(self.sentinel))
        self.assertEqual(self.gate(gate, "--mark", "--skip", "docs", "--tier", "0").returncode, 0)
        self.assertFalse(os.path.exists(self.sentinel))
        self.gate(gate, "--mark", "--findings", "0", "--tier", "3")
        self.assertTrue(os.path.exists(self.sentinel))

    def test_old_marks_without_a_tier_still_attest(self):
        self.mark("--findings", "0")
        r = subprocess.run([sys.executable, "-B", GATE, "attest"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(r.stdout.splitlines()), 1)

    def test_the_real_tier_py_runs_and_a_repo_without_a_merge_base_is_tier_2(self):
        self.sh("git", "update-ref", "-d", "refs/remotes/origin/main")  # Base adds it for the Via rule (NFX-20)
        r = self.mark("--findings", "0", "--tier", "0")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Tier 2", r.stderr)
        self.assertEqual(self.mark("--findings", "0", "--tier", "2").returncode, 0)


if __name__ == "__main__":
    unittest.main()
