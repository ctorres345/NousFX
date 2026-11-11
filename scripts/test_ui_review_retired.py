#!/usr/bin/env python3
"""NFX-18: the old UI review skill is retired in favor of Via; NFX-127 deleted its stub. Run: python3 -B scripts/test_ui_review_retired.py"""
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = "ui" + "-review"  # split so this file does not match its own search


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def grep(pattern):
    # --untracked: a new file is searched before it is committed (NFX-96). The pathspecs keep tool state out
    # (.claude/ settings, worktrees and drafts, graphify-out/) whatever this machine's exclude files say.
    # evals/review-replay/cases holds frozen past diffs (NFX-130): history, not live text, scrubbed of home paths.
    r = subprocess.run(["git", "grep", "--untracked", "-l", "-i", "-E", pattern, "--", ".", ":(exclude).claude", ":(exclude)graphify-out", ":(exclude)evals/review-replay/cases"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode in (0, 1), r.stderr  # 1 is "no match"; anything else means the search did not run
    return set(r.stdout.split())


class Retired(unittest.TestCase):
    def test_tool_state_folders_hold_no_tracked_files(self):
        # The grep skips them, so anything tracked there would go unchecked.
        r = subprocess.run(["git", "ls-files", ".claude", "graphify-out"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), [])

    def test_only_the_changelog_names_the_old_skill(self):
        self.assertEqual(grep(OLD), {"CHANGELOG.md"})

    def test_the_old_skill_folder_is_gone(self):
        folder = "claude/skills/" + OLD
        self.assertFalse(os.path.exists(os.path.join(ROOT, folder)))
        r = subprocess.run(["git", "ls-files", folder], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), [])

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
