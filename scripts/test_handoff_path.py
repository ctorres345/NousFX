#!/usr/bin/env python3
"""One handoff note per repo, beside its Claude Code memory and outside the repo, for writes and for the
session-start read (NFX-153, NFX-158): the desktop app blocks worktree sessions from the main checkout's .claude/.

Run: python3 -B scripts/test_handoff_path.py
"""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class HandoffPath(unittest.TestCase):
    def test_skill_writes_beside_the_memory_folder(self):
        skill = read("claude", "skills", "handoff", "SKILL.md")
        self.assertIn("`~/.claude/projects/<key>/handoff.md`", skill)
        self.assertIn('= .git ]; then root="$(dirname "$common")"', skill)
        self.assertIn("sed 's/[^A-Za-z0-9]/-/g')/handoff.md\"", skill)  # Claude Code's project key
        self.assertIn("Re-read it just before writing", skill)  # merge, never overwrite
        self.assertIn("Never fall back to a worktree path", skill)

    def test_recipe_key_matches_memory_upkeep(self):
        import re
        import subprocess
        import sys
        import tempfile
        sys.path.insert(0, os.path.join(ROOT, "claude", "skills", "memory-upkeep", "scripts"))
        import check_memory
        skill = read("claude", "skills", "handoff", "SKILL.md")
        recipe = re.search(r"```bash\n(.*?)```", skill, re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "My.Repo_1")
            os.makedirs(os.path.join(repo, "sub"))
            git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", repo]
            subprocess.run(["git", "init", "-q", repo], check=True)
            subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)
            tree = os.path.join(repo, ".claude", "worktrees", "w")
            subprocess.run(git + ["worktree", "add", "-q", "-b", "w", tree], check=True)
            want = os.path.join(tmp, ".claude", "projects", check_memory.slug(repo), "handoff.md")
            for cwd in (repo, os.path.join(repo, "sub"), tree):  # a worktree resolves to the main checkout's key
                out = subprocess.run(["bash", "-c", recipe + 'printf %s "$note"'], cwd=cwd, check=True,
                                     capture_output=True, text=True, env={**os.environ, "HOME": tmp}).stdout
                self.assertEqual(out, want, cwd)

    def test_durable_files_rule_points_at_the_doc(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "docs", "worktrees.md")))
        agents = read("harness", "AGENTS.md")
        self.assertIn("## Durable files (NousFX `docs/worktrees.md`)", agents)
        for path in ("`~/Releases/<project>/", "`~/.config/nousfx/secrets/<project>/`", "the main checkout is read-only"):
            self.assertIn(path, agents)
        self.assertIn("~/.config/nousfx/secrets/<project>/", read("claude", "skills", "project-setup", "SKILL.md"))

    def test_session_start_read_names_the_main_checkout(self):
        rule = [l for l in read("harness", "overlays", "claude.overlay.md").splitlines() if l.startswith("- Handoff:")]
        self.assertEqual(len(rule), 1)
        self.assertIn("read `handoff.md` beside your memory folder", rule[0])


if __name__ == "__main__":
    unittest.main()
