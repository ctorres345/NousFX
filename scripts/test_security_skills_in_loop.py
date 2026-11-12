#!/usr/bin/env python3
"""NFX-124: review-loop runs the security skills tier.py lists (agentic-actions-auditor, sharp-edges)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR = os.path.join(ROOT, "claude", "skills", "review-loop")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.dont_write_bytecode = True

import nousfx_project  # noqa: E402


def read(*parts):
    with open(os.path.join(LOOP_DIR, *parts), encoding="utf-8") as f:
        return f.read()


SKILL = read("SKILL.md")
SECURITY = read("references", "security.md")


class SecuritySkillsInLoop(unittest.TestCase):
    def test_skill_keeps_a_stub_that_names_the_trigger(self):
        self.assertIn("## Security skills\n", SKILL)
        stub = SKILL.split("## Security skills\n")[1].split("\n")[0]
        for word in ("references/security.md", "first round", "`security`"):
            self.assertIn(word, stub)

    def test_reference_opens_with_its_trigger(self):
        first = SECURITY.split("\n")[0]
        self.assertTrue(first.startswith("Read this on the first round"))
        self.assertIn("`security`", first)

    def test_both_skills_and_their_rules_are_stated(self):
        for word in ("agentic-actions-auditor", "sharp-edges", ".github/workflows/", "SECURITY_KEYWORDS"):
            self.assertIn(word, SECURITY)

    def test_findings_are_ided_verified_and_counted(self):
        for word in ("`S1..`", "like a `high`", "`--findings`", "stays rejected"):
            self.assertIn(word, SECURITY)

    def test_security_audit_is_on_request_only(self):
        line = next(l for l in SECURITY.splitlines() if l.startswith("- `security-audit`"))
        self.assertIn("only when Cesar asks", line)

    def test_final_report_lists_the_check(self):
        self.assertIn("Security skills (run, findings, real, wrong, or why skipped)", SKILL)

    def test_reference_ships_to_projects(self):
        rel = os.path.join(".claude", "skills", "review-loop", "references", "security.md")
        self.assertIn(rel, nousfx_project.COPIES)
        self.assertIn(rel, nousfx_project.FOLLOW_SKILL)
        self.assertTrue(os.path.exists(os.path.join(ROOT, nousfx_project.COPIES[rel])))


if __name__ == "__main__":
    unittest.main()
