#!/usr/bin/env python3
"""NFX-106: review-loop gives every finding a disposition, runs one delta round on fixed lows,
checks each Proof: against its criterion, and docs hold a /goal condition template."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def section(text, heading):
    """Text of '## heading' up to the next '## '."""
    m = re.search(r"^## " + re.escape(heading) + r"\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


SKILL = read("claude", "skills", "review-loop", "SKILL.md")


class Dispositions(unittest.TestCase):
    def setUp(self):
        self.sec = section(SKILL, "Dispositions")

    def test_section_names_the_three_dispositions(self):
        self.assertTrue(self.sec, "no '## Dispositions' section")
        for word in ("`fix-now`", "`follow-up`", "`note`"):
            self.assertIn(word, self.sec)

    def test_a_note_needs_a_reason_and_is_listed_in_the_pr(self):
        self.assertIn("A `note` needs a reason", self.sec)
        self.assertIn("the PR body lists the notes", self.sec)

    def test_required_findings_cannot_be_notes(self):
        self.assertIn("A `high`, or a `med` at 60 or more, is always `fix-now`", self.sec)
        self.assertIn("`rejected` is not a disposition", self.sec)

    def test_follow_up_is_a_draft_that_claude_never_files(self):
        self.assertIn("docs/agents/ticket-template.md", self.sec)
        self.assertIn("never create the ticket", self.sec)
        self.assertIn("Follow-ups", self.sec)


class DeltaRound(unittest.TestCase):
    def setUp(self):
        self.sec = section(SKILL, "Delta round")

    def test_section_exists_and_is_one_round_outside_the_cap(self):
        self.assertTrue(self.sec, "no '## Delta round' section")
        self.assertIn("one round", self.sec)
        self.assertIn("does not count toward the cap", self.sec)

    def test_reviewer_sees_only_the_fix_diff_built_without_stash(self):
        self.assertIn("only the fix diff", self.sec)
        self.assertIn("git diff <tree1> <tree2>", self.sec)
        self.assertIn("GIT_INDEX_FILE", self.sec)

    def test_a_bad_fix_is_reverted_to_follow_up(self):
        self.assertIn("revert that fix", self.sec)
        self.assertIn("`follow-up`", self.sec)

    def test_full_suite_still_runs(self):
        self.assertIn("full test suite", self.sec)

    def test_runs_before_record_the_review(self):
        self.assertLess(SKILL.index("## Delta round"), SKILL.index("## Record the review"))


class ProofCheck(unittest.TestCase):
    def test_reviewer_checks_each_proof(self):
        self.assertIn("a proof that cannot fail", SKILL)
        self.assertIn("`manual:` proofs are exempt", SKILL)


class FinalReport(unittest.TestCase):
    def test_per_finding_table_has_a_disposition_column(self):
        sec = section(SKILL, "Final report")
        self.assertIn("| ID | severity | disposition | reason or ticket draft |", sec)

    def test_existing_summary_items_stay(self):
        sec = section(SKILL, "Final report")
        self.assertIn("Gemini checks (calls made, findings, real, wrong, or why skipped)", sec)
        self.assertIn("Fable offers (made, accepted or declined, outcome)", sec)


class GoalTemplate(unittest.TestCase):
    def test_docs_hold_the_goal_condition(self):
        text = read("docs", "review-gate.md")
        self.assertIn("AC-Proof: n/n manual:0", text)
        self.assertIn("run_checks.py passes", text)
        self.assertIn("review recorded with 0 open", text)
        self.assertIn("at most N turns", text)

    def test_readme_points_at_the_template(self):
        self.assertIn("docs/review-gate.md", read("README.md"))


if __name__ == "__main__":
    unittest.main()
