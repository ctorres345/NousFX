#!/usr/bin/env python3
"""NFX-192: after-merge ends with a retro step that reads the pinned retro skill as a file; the NousFX extras sit in a reference."""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


SKILL = read("claude", "skills", "after-merge", "SKILL.md")
REF = read("claude", "skills", "after-merge", "references", "retro.md")


def step(text, number):
    m = re.search(r"^" + str(number) + r"\. \*\*(.*?)\*\*(.*?)(?=^\d+\. \*\*|\n\n|\Z)", text, re.M | re.S)
    return (m.group(1) + m.group(2)) if m else ""


class RetroStep(unittest.TestCase):
    def test_retro_comes_after_done_and_the_report_and_before_handoff(self):
        titles = re.findall(r"^\d+\. \*\*(.*?)\*\*", SKILL, re.M)
        want = ["Move the ticket to Done.", "Render the report.", "Run the retro.", "Run `/handoff`."]
        positions = [next((i for i, t in enumerate(titles) if t == w), -1) for w in want]
        self.assertNotIn(-1, positions, titles)
        self.assertEqual(positions, sorted(positions))

    def test_trigger_is_the_wave_folder(self):
        s = step(SKILL, 5)
        for phrase in ("waves/", "wave of one", "closed in Jira (Done, Won't Do or Cancelled)", "name the tickets still open",
                       "take the newest", "names shaped like a key"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, s)

    def test_wave_members_come_from_briefs_and_result_files(self):
        # NFX-192 round 2: a ticket's brief exists from its start, its result file only once its PR opens.
        s = step(SKILL, 5)
        self.assertIn("every ticket with a `<KEY>.brief.md` or `<KEY>.md` in that folder is in the wave", s)
        self.assertIn("waves/*/<KEY>.brief.md", s)

    def test_a_retro_runs_once(self):
        # NFX-192 round 3: two late merges, or a follow-up PR, must not run the same retro twice.
        s = step(SKILL, 5)
        self.assertIn("already exists, skip and say so", s)
        self.assertIn("write that file first to claim the run", s)

    def test_the_pinned_skill_is_checked_before_the_claim(self):
        # NFX-192 round 4: a claim written before a missing-skill stop would block the retro for good.
        s = step(SKILL, 5)
        self.assertLess(s.find("is missing, say so, skip the retro"), s.find("write that file first to claim the run"))
        self.assertIn("dead claim: delete it to run again", s)

    def test_unattended_skips_and_says_so(self):
        s = step(SKILL, 5)
        self.assertIn("Unattended, or if step 3 left this ticket open or could not read the tracker, skip it and say so.", s)

    def test_skips_while_this_ticket_is_open_or_jira_is_unreadable(self):
        s = step(SKILL, 5)
        self.assertIn("if step 3 left this ticket open or could not read the tracker", s)
        self.assertIn("A status Jira cannot read counts as not Done", s)

    def test_final_line_counts_every_step(self):
        self.assertIn("Do all six unasked.", SKILL)


class PinnedSkillIsReadNotEdited(unittest.TestCase):
    def test_step_reads_the_pinned_skill_as_a_file(self):
        s = step(SKILL, 5)
        self.assertIn("retro/SKILL.md", s)
        self.assertIn("as a file", s)
        self.assertIn("references/retro.md", s)

    def test_step_never_edits_it_and_survives_a_missing_file(self):
        s = step(SKILL, 5)
        self.assertIn("never edit it", s)
        self.assertIn("is missing, say so, skip the retro and go on to step 6", s)
        self.assertIn("no rule or skill is edited without approval", s)
        self.assertIn("An approved finding becomes a ticket: the retro never edits a rule or skill", s)

    def test_the_retro_skill_is_still_pinned_not_forked(self):
        lock = json.loads(read("harness", "skills.lock.json"))
        entry = lock["skills"]["retro"] if "skills" in lock else lock["retro"]
        self.assertEqual(entry["path"], "skills/engineering/retro")
        self.assertFalse(os.path.exists(os.path.join(ROOT, "claude", "skills", "retro")))


class ReferenceExtras(unittest.TestCase):
    def test_fix_order_runs_from_structure_to_prose(self):
        order = [REF.lower().find(w.lower()) for w in ("API or structure", "types", "lint or check", "test", "prose rule")]
        self.assertNotIn(-1, order)
        self.assertEqual(order, sorted(order))
        self.assertIn("only for judgement calls", REF)

    def test_class_proof_and_worked_line(self):
        for phrase in ("happened twice", "fails on a real past instance", "how we tell it worked",
                       "why a higher level cannot"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, REF)

    def test_inputs_are_named(self):
        for phrase in ("review comments", "Habits in each PR body", "session_report.py", "followups.md",
                       "in two different PRs or tickets"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, REF)

    def test_filing_rule(self):
        for phrase in ("Cesar's yes", "at most 3", "`needs-triage`", "`agent-filed`", "never `ready-for-agent`",
                       "Search Jira", "comment there instead", "docs/agents/ticket-template.md"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, REF)

    def test_nothing_is_edited_automatically(self):
        self.assertIn("Never edit rules or skills from a retro", REF)

    def test_classes_read_and_feed_the_watch_ledger(self):
        # NFX-272: a mistake seen once per ticket becomes a class across tickets and projects.
        classes = REF[REF.index("## Classes"):REF.index("## Fix order")]
        for phrase in ("python3 -B ~/.claude/skills/review-loop/scripts/session_report.py watch list",
                       "the installed copy", "Before grouping", "count it against that slug", "watch add",
                       "--harness", "the fix belongs in NousFX", "an agent habit", "--project", "--new",
                       "inside NousFX every row is a harness row",
                       "has no `watch`, say so and go on"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, classes)
        self.assertLess(classes.index("watch list"), classes.index("Group findings"))


if __name__ == "__main__":
    unittest.main()
