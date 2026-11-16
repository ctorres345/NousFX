#!/usr/bin/env python3
"""The handoff note lives in the main checkout, for writes and for the session-start read (NFX-153).

Run: python3 -B scripts/test_handoff_path.py
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class HandoffPath(unittest.TestCase):
    def test_skill_resolves_the_main_checkout(self):
        skill = read("claude", "skills", "handoff", "SKILL.md")
        self.assertIn('common="$(git rev-parse --path-format=absolute --git-common-dir)"', skill)
        self.assertIn('= .git ]; then root="$(dirname "$common")"', skill)
        self.assertIn("Re-read it just before writing", skill)  # merge, never overwrite

    def test_session_start_read_names_the_main_checkout(self):
        rule = [l for l in read("harness", "overlays", "claude.overlay.md").splitlines() if l.startswith("- Handoff:")]
        self.assertEqual(len(rule), 1)
        self.assertIn("read the main checkout's `.claude/handoff.md`", rule[0])


if __name__ == "__main__":
    unittest.main()
