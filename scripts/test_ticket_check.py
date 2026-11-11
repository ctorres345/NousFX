#!/usr/bin/env python3
"""Tests for the ticket-check skill. Run: python3 -B scripts/test_ticket_check.py

Reads the real claude/skills/ticket-check/SKILL.md and checks its frontmatter, both briefs,
the no-code rule on the Paraphrase brief and that no brief asks for a builder summary.
"""
import os
import re
import sys
import unittest

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "claude", "skills", "ticket-check", "SKILL.md")


def text():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


def frontmatter(src):
    m = re.match(r"---\n(.*?)\n---\n", src, re.S)
    assert m, "no frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


class TicketCheck(unittest.TestCase):
    def test_file_exists(self):
        self.assertTrue(os.path.isfile(SKILL))

    def test_frontmatter(self):
        src = text()
        self.assertTrue(src.startswith("---"))
        fm = frontmatter(src)
        self.assertEqual(fm.get("name"), "ticket-check")
        desc = fm.get("description", "")
        self.assertTrue(desc)
        first = desc.split(". ")[0]
        self.assertIn("before coding a ticket", first)

    def test_both_briefs(self):
        src = text()
        self.assertIn("Design and readiness", src)
        self.assertIn("Paraphrase", src)

    def test_paraphrase_forbids_code(self):
        src = text()
        m = re.search(r"### Brief: Paraphrase.*?(?=\n##|\Z)", src, re.S)
        self.assertTrue(m)
        section = m.group(0)
        self.assertIn("no code access", section)
        self.assertIn("must not read code or files", section)

    def test_no_builder_summary_requested(self):
        src = text()
        hits = [m.start() for m in re.finditer(r"builder summary", src, re.I)]
        self.assertTrue(hits)
        for pos in hits:
            before = src[max(0, pos - 10):pos]
            self.assertRegex(before, r"No $", "builder summary must only appear negated")


if __name__ == "__main__":
    unittest.main()
