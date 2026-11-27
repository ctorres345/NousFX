#!/usr/bin/env python3
"""NFX-233: a real comment-check flag is a `med` finding at confidence 60, no longer warn-only."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR = os.path.join(ROOT, "claude", "skills", "review-loop")


def read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as f:
        return f.read()


def section(text, heading):
    level = len(heading) - len(heading.lstrip("#"))
    body = text.split(heading + "\n", 1)[1]
    return re.split(r"\n#{1,%d} " % level, body, maxsplit=1)[0]


class SkillStep(unittest.TestCase):
    def test_the_skill_names_the_comment_check_as_a_med_finding(self):
        step = section(read(LOOP_DIR, "SKILL.md"), "## Comment check")
        for word in ("references/comments.md", "after Step 0", "`med` finding", "confidence 60", "keep list"):
            self.assertIn(word, step)
        self.assertNotIn("warn-only", step.lower())


class Reference(unittest.TestCase):
    def setUp(self):
        self.ref = read(LOOP_DIR, "references", "comments.md")

    def test_a_real_flag_is_a_med_finding_the_loop_fixes_or_rejects(self):
        severity = section(self.ref, "## Severity")
        for word in ("`med` finding at confidence 60", "`fix-now`", "The fix is the change the finding names",
                     "after which the comment it replaces goes", "delete a comment only when it is a Tautology or Stale",
                     "old comments never fail a PR"):
            self.assertIn(word, severity)
        self.assertNotIn("warn-only", self.ref.lower())
        self.assertNotIn("`low` finding", self.ref)

    def test_the_reviewer_is_told_a_real_flag_is_med_at_60(self):
        self.assertIn("it is a finding: `med` at confidence 60", section(self.ref, "## What the reviewer gets"))

    def test_the_reviewer_may_reject_a_flag_naming_its_keep_list_item_or_external_cause(self):
        severity = section(self.ref, "## Severity")
        self.assertIn("Besides \"quote not found\" (step 4) and \"not an added comment\"", severity)
        self.assertIn("rejects a comment finding only with a reason", severity)
        for word in ("names the keep-list item", "names the external cause", "for kind `ticket` or `suppression`",
                     "says why the named fix cannot be made", "the comment then stays"):
            self.assertIn(word, severity)


class Docs(unittest.TestCase):
    def test_the_gate_doc_says_med_not_warn_only(self):
        doc = section(read(ROOT, "docs", "review-gate.md"), "### Comment check (NFX-196)")
        self.assertIn("`med` finding", doc)
        self.assertNotIn("warn-only", doc)
        self.assertNotIn("`low` finding", doc)

    def test_re_sync_prs_are_told_to_say_review_gets_stricter(self):
        setup = read(ROOT, "claude", "skills", "project-setup", "SKILL.md")
        self.assertIn("A re-sync PR body names each NousFX CHANGELOG entry or `changelog.d/` fragment since the project's last sync", setup)
        self.assertIn("that makes its review stricter", setup)
        self.assertIn("makes its review stricter", section(read(ROOT, "docs", "sync.md"), "## Project mode"))


if __name__ == "__main__":
    unittest.main()
