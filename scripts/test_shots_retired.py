#!/usr/bin/env python3
"""NFX-236: PR shots live in monthly evidence releases; no rule, skill or hook tells an agent to make a -shots branch."""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SHOTS_BRANCH = re.compile(r"[\w>}]-shots\b")  # <KEY>-shots, {prefix}/<KEY>-shots; not `outbound-shots`
NOTE = re.compile(r"legacy|retired|NFX-236", re.I)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class ShotsRetiredTest(unittest.TestCase):
    def test_every_mention_of_a_shots_branch_is_a_legacy_or_retirement_note(self):
        offenders = []
        for top in ("claude", "harness", "scripts"):
            for here, dirs, files in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
                for name in files:
                    if name.startswith("test_") or name.endswith((".pyc", ".png")):
                        continue
                    path = os.path.join(here, name)
                    with open(path, encoding="utf-8", errors="replace") as f:
                        for n, line in enumerate(f, 1):
                            if SHOTS_BRANCH.search(line.replace("outbound-shots", "")) and not NOTE.search(line):
                                offenders.append("%s:%d" % (os.path.relpath(path, ROOT), n))
        self.assertEqual(offenders, [])

    def test_after_merge_sweeps_old_evidence_and_keeps_its_six_steps(self):
        skill = read("claude", "skills", "after-merge", "SKILL.md")
        step2 = skill[skill.index("2. **"):skill.index("3. **")]
        self.assertIn("pr_evidence.py sweep", step2)
        self.assertNotIn("<prefix>/<KEY>` and `<prefix>/<KEY>-shots", step2)
        self.assertIn("Do all six unasked.", skill)

    def test_the_branch_rule_points_shots_at_the_evidence_release(self):
        rule = [line for line in read("harness", "AGENTS.md").splitlines() if line.startswith("- Branch `<prefix>/<KEY>`")]
        self.assertEqual(len(rule), 1)
        self.assertIn("pr_evidence.py", rule[0])
        self.assertNotIn("-shots", rule[0])

    def test_the_pre_push_hook_and_gate_no_longer_wave_a_shots_branch_through(self):
        self.assertNotIn("check-shots", read("claude", "githooks", "pre-push"))
        self.assertNotIn("def check_shots", read("claude", "hooks", "review-gate.py"))


if __name__ == "__main__":
    unittest.main()
