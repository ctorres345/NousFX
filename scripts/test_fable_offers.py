#!/usr/bin/env python3
"""NFX-66: Fable is offered at three points (plan check, tie-breaker, final Tier 3 pass), never by default."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


SKILL = read("claude", "skills", "review-loop", "SKILL.md")


def section(text, title):
    m = re.search(r"^## " + re.escape(title) + r".*?\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


class FableOffers(unittest.TestCase):
    def test_old_optional_deep_pass_is_gone(self):
        for rel in (("claude", "skills", "review-loop", "SKILL.md"), ("README.md",),
                    ("harness", "overlays", "claude.overlay.md")):
            with self.subTest(rel=rel):
                self.assertNotIn("Optional deep pass", read(*rel))

    def test_section_names_the_three_offers(self):
        s = section(SKILL, "Fable offers")
        self.assertTrue(s, "missing '## Fable offers' section")
        self.assertIn("`architect`", s)
        self.assertIn("first time the tier is 3", s)
        self.assertIn("tie-breaker", s)
        self.assertIn("final pass", s)
        self.assertIn("ends clean at Tier 3", s)
        self.assertIn("still disagree with the reviewer on a `high`", s)
        self.assertGreaterEqual(s.count("`reviewer-deep`"), 2)

    def test_section_states_the_limits(self):
        s = section(SKILL, "Fable offers")
        for phrase in ("never every round", "Tier 0 or 1", "unattended", "/goal", "only after the user says yes",
                       "one line", "at most once per loop"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, s)

    def test_loop_points_to_the_tie_breaker(self):
        loop = section(SKILL, "Loop")
        self.assertGreaterEqual(loop.count("Fable offers"), 2)
        self.assertRegex(loop, r"same finding ID returns[^\n]*Fable offers")

    def test_tier_step_points_to_the_plan_check(self):
        self.assertIn("Fable offers", section(SKILL, "Step 1"))

    def test_overlay_offers_plan_check_at_kickoff(self):
        line = next((l for l in read("harness", "overlays", "claude.overlay.md").splitlines()
                     if l.startswith("- Fable")), "")
        self.assertTrue(line, "missing the Fable line in the Claude overlay")
        self.assertIn("`fable-review`", line)
        self.assertIn("`architect`", line)
        self.assertIn("clear yes", line)
        self.assertIn("`/goal`", line)
        self.assertIn("Never offer or run", line)


if __name__ == "__main__":
    unittest.main()
