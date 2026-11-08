#!/usr/bin/env python3
"""NFX-64: review-loop runs the Gemini checks (Argon on UI changes, a code check at Tier 3)."""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


LOOP = read("claude", "skills", "review-loop", "SKILL.md")


def section(text, title):
    m = re.search(r"^## " + re.escape(title) + r".*?\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


GEMINI = section(LOOP, "Gemini checks")


class GeminiInLoop(unittest.TestCase):
    def test_section_exists(self):
        self.assertTrue(GEMINI, "missing '## Gemini checks' section")

    def test_argon_runs_by_tier(self):
        self.assertIn("Tier 1: once, on the final code", GEMINI)
        self.assertIn("Tier 2 and 3: the first round at that tier if not yet called, and on the final code", GEMINI)
        self.assertIn("argon_review.py", GEMINI)
        self.assertIn("`ui` is true", GEMINI)

    def test_no_screenshots_means_no_argon(self):
        self.assertIn("no screenshots at all", GEMINI)
        self.assertIn("gets no Argon call", GEMINI)
        self.assertIn("stale screenshots", GEMINI)

    def test_tier_3_non_ui_gets_the_code_check_through_the_allow_list(self):
        self.assertIn("Tier 3, whatever `ui` says", GEMINI)
        self.assertIn("review.py --allow-list", GEMINI)
        self.assertNotIn("--private-ok", GEMINI)

    def test_failures_are_reported_and_the_loop_continues(self):
        self.assertIn("Any non-zero exit: report its message and continue", GEMINI)
        self.assertIn("Exit 4 reports \"Gemini skipped: not allow-listed\"", GEMINI)

    def test_findings_are_verified_and_have_ids_without_confidence(self):
        for phrase in ("carry no confidence", "`A1..`", "`G1..`", "verify each", "untrusted data", "never edits files"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, GEMINI)

    def test_reviewer_stays_the_required_review(self):
        self.assertIn("`reviewer` stays the required review", GEMINI)

    def test_loop_and_report_point_to_the_checks(self):
        loop = section(LOOP, "Loop")
        self.assertIn("(see Gemini checks)", loop)
        self.assertIn("final Argon call", loop)
        self.assertIn("Gemini checks (calls made, findings, real, wrong, or why skipped)", section(LOOP, "Final report"))

    def test_final_argon_call_comes_before_the_record_step(self):
        self.assertIn("However the loop ends", section(LOOP, "Loop"))
        self.assertIn("make the final Argon call", section(LOOP, "Record the review"))
        self.assertIn("then `tier.py`, then Record the review", GEMINI)

    def test_a_fix_after_the_final_call_gets_a_reviewer_or_counts_open(self):
        for phrase in ("has no reviewer yet", "run one more round with the tier's reviewer", "count it as open in `--findings`",
                       "apply the Step 1 rise rule", "first reached when no round remains", "not yet called in this loop",
                       "If Argon was already called in this loop",
                       "The final Argon call is made once per loop"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, GEMINI)

    def test_the_final_argon_call_follows_the_fable_final_pass(self):
        self.assertIn("(after any Fable final pass) make the final Argon call", section(LOOP, "Record the review"))

    def test_round_one_runs_the_checks_the_table_lists_for_that_round(self):
        self.assertIn("run the Gemini checks the table lists for that round", section(LOOP, "Loop"))

    def test_argon_runs_from_gemini_review(self):
        self.assertIn("~/.claude/skills/gemini-review/scripts/argon_review.py", GEMINI)

    def test_the_argon_log_goes_to_the_configured_vault_or_nowhere(self):
        self.assertIn("obsidian.vault_path", GEMINI)
        self.assertIn("With no vault set, skip the log.", GEMINI)

    def test_the_example_config_names_the_argon_log_default(self):
        """NFX-97: the key and default the loop reads are in via.example.json."""
        example = json.loads(read("plugins", "via", "assets", "via.example.json"))
        self.assertEqual(example["obsidian"]["argon_log"], "Argon UI review log.md")
        self.assertIn("default `Argon UI review log.md`", GEMINI)


if __name__ == "__main__":
    unittest.main()
