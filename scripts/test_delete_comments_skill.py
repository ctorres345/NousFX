#!/usr/bin/env python3
"""Tests for the delete-comments skill (NFX-228). Run: python3 -B scripts/test_delete_comments_skill.py

Reads the real claude/skills/delete-comments/SKILL.md and checks that it names the four kinds and their actions,
the ADR bar, the one keep list, the verify step, the report path and the batch rule.
"""
import os
import re
import sys
import unittest

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "claude", "skills", "delete-comments", "SKILL.md")
SCRIPT = "~/.claude/skills/review-loop/scripts/comment_check.py"


def text():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


def section(src, title):
    """The text under a `## title` heading, up to the next `## `."""
    m = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", src, re.S | re.M)
    assert m, f"no ## {title} section"
    return m.group(1)


class DeleteCommentsSkill(unittest.TestCase):
    def test_frontmatter_names_the_skill_and_when_to_use_it(self):
        m = re.match(r"---\nname: (.+)\ndescription: (.+)\n", text())
        self.assertTrue(m, "frontmatter must start with name and description")
        self.assertEqual(m.group(1), "delete-comments")
        self.assertIn("Use when", m.group(2))

    def test_it_calls_the_review_loop_script_for_inventory_and_verify(self):
        src = text()
        self.assertIn(f"{SCRIPT} --all", src)
        self.assertIn(f"{SCRIPT} verify", src)

    def test_the_keep_list_lives_only_in_the_script(self):
        src = text()
        self.assertIn("--keep-list", src)
        self.assertIn("KEEP", src)
        self.assertNotIn("SPDX", src, "the keep list must not be copied into the skill")

    def test_each_kind_has_its_action(self):
        kinds = section(text(), "3. Act on each kind")
        rows = {
            "Tautology": ["delete"],
            "Stale": ["delete"],
            "Excuse": ["one-line reason", "ticket key", "one ticket per root cause", "needs-triage", "agent-filed"],
            "Decision record": ["ADR", "pointer"],
        }
        for kind, needles in rows.items():
            line = next((l for l in kinds.splitlines() if l.startswith(f"- **{kind}**")), "")
            self.assertTrue(line, f"no bullet for {kind}")
            for needle in needles:
                self.assertIn(needle, line, f"{kind} must say {needle!r}")

    def test_the_adr_bar_has_all_three_parts(self):
        kinds = section(text(), "3. Act on each kind")
        for part in ("hard to reverse", "surprising", "real trade-off"):
            self.assertIn(part, kinds)
        self.assertIn("ADR-FORMAT", kinds)
        self.assertIn("NousFX ADR-NNNN", kinds)

    def test_verify_runs_before_the_suite(self):
        step = section(text(), "4. Verify")
        self.assertLess(step.index("verify"), step.index("suite"))
        self.assertIn("exit 2", step.lower())

    def test_it_names_the_test_docstring_rule_and_the_bare_ticket_tag_kind(self):
        step = section(text(), "2. Classify")
        rule = next((l for l in step.splitlines() if "flag:test-docstring" in l), "")
        self.assertTrue(rule, "Classify must name flag:test-docstring")
        for needle in ("only repeats the test name", "Tautology", "kept"):
            self.assertIn(needle, rule)
        tag = next((l for l in step.splitlines() if "flag:ticket-tag" in l), "")
        self.assertTrue(tag, "Classify must name flag:ticket-tag")
        for needle in ("# NFX-100", "Stale", "git log"):
            self.assertIn(needle, tag)

    def test_a_classifier_miss_adds_a_corpus_entry_in_the_same_pr(self):
        """NFX-276: each cleanup's miss is pinned in comment_check's corpus, not left to the next reviewer."""
        rule = section(text(), "Classifier misses")
        for needle in ("claude/skills/review-loop/scripts/comment_corpus/", "same PR", "known", "reduced from"):
            self.assertIn(needle, rule)

    def test_the_report_path_and_batch_rule(self):
        src = text()
        self.assertIn("~/Releases/<project>/reports/<date>-<KEY>/comments.md", src)
        self.assertIn("PR body", src)
        self.assertRegex(src, r"800 deleted lines")


if __name__ == "__main__":
    unittest.main()
