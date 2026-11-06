#!/usr/bin/env python3
"""NFX-65: every reviewer agent, the review-loop skill and the README /goal text use one finding format.

Format: ID | severity | confidence | file:line | problem | fix | quoted code
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORMAT = "`ID | severity | confidence | file:line | problem | fix | quoted code`"
FORMAT_PLAIN = "ID | severity | confidence | file:line | problem | fix | quoted code"
COUNT = "--findings <open high count + open med at 60 or more>"
NAMES = ("reviewer", "reviewer-light", "reviewer-high", "reviewer-deep")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def body(text):
    """Text after the closing frontmatter fence."""
    return text.split("\n---\n", 1)[1] if text.startswith("---") else text


class ReviewerAgents(unittest.TestCase):
    def test_every_copy_uses_the_new_format(self):
        for folder in ("harness", "claude"):
            for name in NAMES:
                with self.subTest(folder=folder, name=name):
                    text = read(folder, "agents", name + ".md")
                    self.assertIn(FORMAT, text)
                    self.assertNotIn("suggested fix", text)
                    self.assertIn("confidence is", text)
                    self.assertIn("quoted code is", text)
                    self.assertIn("output exactly `NO FINDINGS`", text)

    def test_both_copies_share_one_body(self):
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(body(read("harness", "agents", name + ".md")),
                                 body(read("claude", "agents", name + ".md")))

    def test_all_reviewers_define_the_same_format_bullets(self):
        bullets = {}
        for name in NAMES:
            text = read("harness", "agents", name + ".md")
            bullets[name] = [l for l in text.splitlines()
                             if l.startswith(("- confidence is", "- quoted code is"))]
        self.assertEqual(len({tuple(v) for v in bullets.values()}), 1, bullets)
        self.assertEqual(len(bullets["reviewer"]), 2)


class ReviewLoopSkill(unittest.TestCase):
    def setUp(self):
        self.text = read("claude", "skills", "review-loop", "SKILL.md")

    def test_step_two_asks_for_the_new_format(self):
        self.assertIn(FORMAT_PLAIN, self.text)
        self.assertNotIn("suggested fix", self.text)

    def test_low_confidence_med_is_listed_not_fixed(self):
        self.assertIn("A `med` under 60 is listed, not fixed", self.text)
        self.assertIn("skipped: low confidence", self.text)

    def test_high_is_verified_and_evidence_is_asked_before_rejecting(self):
        self.assertIn("Verify every `high`", self.text)
        self.assertRegex(self.text, r"(?i)before rejecting a `high`.{0,200}evidence")

    def test_findings_count_excludes_low_confidence_med(self):
        self.assertIn(COUNT, self.text)
        self.assertNotIn("high/med", self.text)

    def test_corrected_quote_reopens_a_rejection(self):
        self.assertIn("a corrected quote that is in the file or the diff counts", self.text)

    def test_explains_why_the_main_model_stays(self):
        self.assertIn("opusplan", self.text)
        self.assertIn("models.toml", self.text)


class ReadmeGoalText(unittest.TestCase):
    def test_goal_block_matches(self):
        readme = read("README.md")
        goal = next((l for l in readme.splitlines() if l.startswith("/goal Review-fix loop")), "")
        self.assertTrue(goal, "no /goal review-fix line in README.md")
        self.assertIn(FORMAT_PLAIN, goal)
        self.assertIn(COUNT, goal)
        self.assertIn("missing or non-numeric confidence counts as 60 or more", goal)
        self.assertIn("stays rejected", goal)
        self.assertIn("a corrected quote counts", goal)
        self.assertIn("is not evidence against the high", goal)
        self.assertIn("quote is not found", goal)
        self.assertIn("If SendMessage is not available", goal)
        self.assertIn("real med findings at confidence 60 or more", readme)
        self.assertIn("no med at confidence 60 or more, other than findings already rejected", readme)
        self.assertNotIn("no high or medium findings", readme)
        self.assertNotIn("high/med", readme)
        self.assertRegex(goal, r"under 60")
        self.assertRegex(goal, r"(?i)evidence")


class GateMessages(unittest.TestCase):
    def test_gate_text_uses_the_same_count(self):
        gate = read("claude", "hooks", "review-gate.py")
        self.assertIn(COUNT, gate)
        self.assertNotIn("high/med", gate)


if __name__ == "__main__":
    unittest.main()
