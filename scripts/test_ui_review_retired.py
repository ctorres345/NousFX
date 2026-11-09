#!/usr/bin/env python3
"""NFX-18: the old UI review skill is retired in favor of Via. Run: python3 -B scripts/test_ui_review_retired.py"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = "ui" + "-review"  # split so this file does not match its own search
STUB = "claude/skills/%s/SKILL.md" % OLD


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def grep(pattern):
    # --untracked: a new file is searched before it is committed (NFX-96). The pathspecs keep tool state out
    # (.claude/ settings, worktrees and drafts, graphify-out/) whatever this machine's exclude files say.
    r = subprocess.run(["git", "grep", "--untracked", "-l", "-i", "-E", pattern, "--", ".", ":(exclude).claude", ":(exclude)graphify-out"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode in (0, 1), r.stderr  # 1 is "no match"; anything else means the search did not run
    return set(r.stdout.split())


class Retired(unittest.TestCase):
    def test_tool_state_folders_hold_no_tracked_files(self):
        # The grep skips them, so anything tracked there would go unchecked.
        r = subprocess.run(["git", "ls-files", ".claude", "graphify-out"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), [])

    def test_only_the_stub_and_the_changelog_name_the_old_skill(self):
        self.assertEqual(grep(OLD) - {STUB, "CHANGELOG.md"}, set())

    def test_the_stub_points_to_via(self):
        body = read(STUB).split("---", 2)[2]
        self.assertEqual(len([l for l in body.splitlines() if l.strip()]), 1)
        for cmd in ("/via spec", "/via check", "/via audit"):
            self.assertIn(cmd, body)

    def test_the_stub_is_hidden_from_the_model(self):
        # NFX-116: it still answers when typed by name, but leaves Claude Code's skill list (until 1.2.0).
        front = read(STUB).split("---", 2)[1]
        self.assertIn("\ndisable-model-invocation: true\n", front)

    def test_the_stub_is_gone_once_1_2_0_is_cut(self):
        # The end date is enforced, not only written down: a released 1.2.0 (or later) heading with the stub
        # still in place fails here.
        released = re.findall(r"^## (\d+)\.(\d+)\.\d+(?![^\n]*unreleased)", read("CHANGELOG.md"), re.M)
        if any((int(a), int(b)) >= (1, 2) for a, b in released):
            self.assertFalse(os.path.exists(os.path.join(ROOT, os.path.dirname(STUB))))

    def test_the_old_skill_ships_no_scripts_or_assets(self):
        r = subprocess.run(["git", "ls-files", os.path.dirname(STUB)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), [STUB])

    def test_the_delegation_rules_name_via_spec_and_check_as_proactive(self):
        claude = read("harness/overlays/claude.overlay.md")
        self.assertIn("`/via spec` at kickoff, `/via check` before asking about the PR, unasked", claude)
        agy = read("harness/overlays/agy.overlay.md")
        self.assertIn("Via's `spec` mode at kickoff and its `check` mode before the PR, unasked", agy)

    def test_no_personal_or_vault_path_remains(self):
        # A real home folder (also as Claude's dash-encoded project name) or vault path;
        # not the /Users/me placeholder or a plain "Obsidian vault".
        home = r"(/Users/|/home/|-Users-|C:\\Users\\)[a-z]{3,}"
        found = grep(home + r"|Obsidian Vault/|320 Engineering|argon-repos") - {"scripts/test_ui_review_retired.py"}
        self.assertEqual(found, set())


if __name__ == "__main__":
    unittest.main()
