#!/usr/bin/env python3
"""The ticket template has one source, and the repo's own copy matches it (NFX-104).

Run: python3 -B scripts/test_ticket_template_single_copy.py

Source: claude/skills/project-setup/assets/ticket-template.md, which project-setup installs as
docs/agents/ticket-template.md. Edit the source, then copy it over the installed copy.
"""
import difflib
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(ROOT, "claude", "skills", "project-setup", "assets", "ticket-template.md")
COPY = os.path.join(ROOT, "docs", "agents", "ticket-template.md")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TicketTemplate(unittest.TestCase):
    def test_copies_are_identical(self):
        diff = "".join(difflib.unified_diff(read(SOURCE).splitlines(True), read(COPY).splitlines(True), "source", "copy"))
        self.assertEqual(diff, "", "docs/agents/ticket-template.md differs from the project-setup asset: copy the asset over it")

    def test_template_asks_for_proofs_and_failure_modes(self):
        text = read(SOURCE)
        self.assertIn("## Failure modes", text)
        self.assertIn("Proof:", text)
        self.assertIn("Proof: manual:", text)


if __name__ == "__main__":
    unittest.main()
