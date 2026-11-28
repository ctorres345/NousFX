#!/usr/bin/env python3
"""Shared skills name ~/.agents/skills, not ~/.claude/skills (NFX-315). Run: python3 -B scripts/test_skill_paths_neutral.py

An agy-only machine has no ~/.claude/skills. Sync links every NousFX skill at ~/.agents/skills/<name>, so a skill
that runs a sibling script names that path. ALLOWED lists the files that may still name ~/.claude/skills, with
how many times and why, so a new hit in an allowed file still fails.
"""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = [os.path.join("claude", "skills"), os.path.join("plugins", "via", "skills")]
SUFFIXES = (".md", ".py", ".json", ".toml", ".sh")
OLD = "~/.claude/skills"
NAMED = re.compile(r"~/\.agents/skills/([a-z0-9-]+)/([A-Za-z0-9_/-]+\.(?:py|md))")

ALLOWED = {
    "claude/skills/project-setup/SKILL.md": (1, "names what it copies from Claude's install; out of scope"),
    "claude/skills/review-loop/scripts/gate_lookup_cases.py": (1, "a case for the Claude link layout itself"),
    "claude/skills/review-loop/scripts/tier.py": (1, "explains how the Claude links resolve into the repo"),
}


def hits():
    """{repo-relative path: count of OLD} for every skill file that names it."""
    found = {}
    for src in SOURCES:
        for dirpath, _, files in os.walk(os.path.join(ROOT, src)):
            for name in files:
                if not name.endswith(SUFFIXES):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8", errors="replace") as f:
                    n = f.read().count(OLD)
                if n:
                    found[os.path.relpath(path, ROOT).replace(os.sep, "/")] = n
    return found


class SkillPathsNeutral(unittest.TestCase):
    def test_no_claude_skills_path_outside_allow_list(self):
        extra = {p: n for p, n in hits().items() if ALLOWED.get(p, (None,))[0] != n}
        self.assertEqual(extra, {}, f"use ~/.agents/skills/ instead of {OLD} (or allow-list with a reason)")

    def test_named_agents_skills_files_exist(self):
        with open(os.path.join(ROOT, "harness", "skills.lock.json"), encoding="utf-8") as f:
            outside = set(json.load(f)["skills"])
        named, missing = set(), []
        for src in SOURCES:
            for dirpath, _, files in os.walk(os.path.join(ROOT, src)):
                for name in files:
                    if not name.endswith(SUFFIXES):
                        continue
                    with open(os.path.join(dirpath, name), encoding="utf-8", errors="replace") as f:
                        for skill, rel in NAMED.findall(f.read()):
                            named.add(skill)
                            if skill in outside:
                                continue
                            if not any(os.path.isfile(os.path.join(ROOT, s, skill, rel)) for s in SOURCES):
                                missing.append(f"{skill}/{rel}")
        self.assertEqual(missing, [], "a skill names a ~/.agents/skills file that is neither in the repo nor in a pinned outside skill")
        self.assertLessEqual({"review-loop", "gemini-review", "jira-sync", "retro"}, named)

    def test_allow_list_is_current(self):
        found = hits()
        stale = [p for p in ALLOWED if p not in found]
        self.assertEqual(stale, [], "allow-listed files no longer name the old path: drop them")


if __name__ == "__main__":
    unittest.main()
