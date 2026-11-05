#!/usr/bin/env python3
"""Each rule lives in one source file, and the always-loaded Claude rules stay short (NFX-58).

Run: python3 -B scripts/test_rules_single_copy.py

Sources compared: harness/AGENTS.md against each harness/overlays/*.md (overlays are never loaded together,
so not with each other), and the rules against the skills and agents they point to. A rule counts as repeated when the same sentence (6 words or
more, case and spacing ignored) appears in two different files.
"""
import glob
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import nousfx_sync  # noqa: E402
import real_machine_regression as regression  # noqa: E402

MIN_WORDS = 6
CLAUDE_RULES_BUDGET = 4096  # bytes: AGENTS.md + Claude overlay, what every Claude session loads


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def rule_files():
    """The always-loaded rules: harness/AGENTS.md plus each overlay."""
    return [os.path.join(ROOT, "harness", "AGENTS.md")] + sorted(glob.glob(os.path.join(ROOT, "harness", "overlays", "*.md")))


def on_use_files():
    """Where moved rules live: skills and agents."""
    return sorted(glob.glob(os.path.join(ROOT, "claude", "skills", "*", "SKILL.md"))
                  + glob.glob(os.path.join(ROOT, "claude", "skills", "*", "assets", "*.md"))
                  + glob.glob(os.path.join(ROOT, "claude", "agents", "*.md")))


def sentences(path):
    return regression.sentences(read(path), MIN_WORDS)


class SingleCopy(unittest.TestCase):
    def repeats(self, first, others):
        seen = set().union(*(sentences(p) for p in first))
        return [f"{os.path.relpath(p, ROOT)}: {s[:90]}" for p in others for s in sorted(sentences(p) & seen)]

    def test_no_sentence_in_agents_md_and_an_overlay(self):
        neutral, *overlays = rule_files()
        self.assertEqual(self.repeats([neutral], overlays), [], "the same rule is written in more than one file")

    def test_no_sentence_in_both_the_rules_and_a_skill_or_agent(self):
        self.assertEqual(self.repeats(rule_files(), on_use_files()), [], "a moved rule is still in the rules")

    def test_always_loaded_claude_rules_fit_the_budget(self):
        size = 0
        for rel in ("harness/AGENTS.md", "harness/overlays/claude.overlay.md"):
            size += len(read(os.path.join(ROOT, rel)).encode("utf-8"))
        self.assertLessEqual(size, CLAUDE_RULES_BUDGET,
                             f"Claude rules are {size} bytes; move on-use rules into their skill or hook")

    def test_built_rules_use_only_the_listed_sources(self):
        # build_rules reads the same two files (plus the optional local files, absent here).
        built = nousfx_sync.build_rules(ROOT, os.path.join(ROOT, "no-such-state"))
        for rel in ("harness/AGENTS.md", "harness/overlays/claude.overlay.md"):
            self.assertIn(read(os.path.join(ROOT, rel)).strip("\n"), built)


if __name__ == "__main__":
    unittest.main()
