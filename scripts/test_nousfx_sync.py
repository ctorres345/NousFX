#!/usr/bin/env python3
"""Tests for scripts/nousfx_sync.py. Run: python3 -B scripts/test_nousfx_sync.py

The one seam is the CLI, run as a subprocess against a temp home, a temp state home and a
temp copy of the repo sources. Nothing real is touched.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SYNC = os.path.join(HERE, "nousfx_sync.py")
FIX = os.path.join(HERE, "fixtures", "sync")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="nousfx-sync-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.state = os.path.join(self.tmp, "state")
        self.repo = os.path.join(self.tmp, "repo")
        self.rules = os.path.join(self.home, ".claude", "CLAUDE.md")
        self.settings = os.path.join(self.home, ".claude", "settings.json")
        self.hook = os.path.join(self.home, ".claude", "hooks", "review-gate.py")
        os.makedirs(self.home)
        shutil.copytree(os.path.join(ROOT, "claude", "hooks"), os.path.join(self.repo, "claude", "hooks"),
                        ignore=shutil.ignore_patterns("test_*", "__pycache__"))
        write(os.path.join(self.repo, "harness", "AGENTS.md"), read(os.path.join(FIX, "AGENTS.md")))
        write(os.path.join(self.repo, "harness", "overlays", "claude.overlay.md"),
              read(os.path.join(FIX, "claude.overlay.md")))
        write(os.path.join(self.state, "local.md"), read(os.path.join(FIX, "local.md")))

    def sync(self, *args, repo=None):
        return subprocess.run(
            [sys.executable, "-B", SYNC, "--home", self.home, "--state-home", self.state,
             "--repo", repo or self.repo, *args],
            capture_output=True, text=True, cwd=self.tmp)

    def tree(self):
        out = {}
        for base, _, files in os.walk(self.home):
            for name in files:
                p = os.path.join(base, name)
                out[os.path.relpath(p, self.home)] = read(p)
        return out

    def snapshot(self):
        """Every file under home and state home, so 'changes nothing' covers backups too."""
        out = self.tree()
        for base, _, files in os.walk(self.state):
            for name in files:
                p = os.path.join(base, name)
                out["state:" + os.path.relpath(p, self.state)] = read(p)
        return out


class FirstRun(Base):
    def test_writes_expected_rules_and_registers_hook(self):
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(read(self.rules), read(os.path.join(FIX, "expected_CLAUDE.md")))  # golden
        self.assertTrue(os.path.exists(self.hook))
        self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "hooks", "pr_create_parser.py")))
        cfg = json.loads(read(self.settings))
        entries = cfg["hooks"]["PreToolUse"]
        self.assertEqual(len(entries), 1)
        self.assertIn("review-gate.py", entries[0]["hooks"][0]["command"])

    def test_second_run_changes_nothing(self):
        self.sync()
        before = self.snapshot()
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_existing_settings_are_kept_and_hook_not_duplicated(self):
        write(self.settings, json.dumps({"model": "x", "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "other.sh"}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 old/review-gate.py"}]}]}}))
        self.sync()
        self.sync()
        cfg = json.loads(read(self.settings))
        cmds = [h["command"] for e in cfg["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertEqual(cmds[0], "other.sh")
        self.assertEqual(sum("review-gate.py" in c for c in cmds), 1)
        self.assertEqual(cfg["model"], "x")

    def test_missing_local_file_is_fine(self):
        os.remove(os.path.join(self.state, "local.md"))
        self.assertEqual(self.sync().returncode, 0)
        self.assertNotIn("Fixture local rules", read(self.rules))

    def test_windows_hook_command(self):
        self.sync("--platform", "windows")
        hook = json.loads(read(self.settings))["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertTrue(hook["command"].startswith('python "'))
        self.assertNotIn("\\", hook["command"])
        self.assertEqual(hook["shell"], "powershell")


class DryRunAndCheck(Base):
    def test_dry_run_prints_diff_and_writes_nothing(self):
        write(self.rules, "old rules\n")
        before = self.snapshot()
        r = self.sync("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-old rules", r.stdout)
        self.assertIn("+# Fixture rules", r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_check_clean_after_sync(self):
        self.sync()
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_check_before_first_sync_reports_drift(self):
        self.assertNotEqual(self.sync("--check").returncode, 0)
        self.assertFalse(os.path.exists(self.rules))

    def test_check_fails_on_hand_edited_generated_file_and_writes_nothing(self):
        self.sync()
        write(self.rules, read(self.rules) + "hand edit\n")
        before = self.snapshot()
        r = self.sync("--check")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CLAUDE.md", r.stdout)
        self.assertEqual(self.snapshot(), before)


class Restore(Base):
    def test_restore_returns_files_to_pre_sync_state(self):
        write(self.rules, "my old rules\n")
        write(self.settings, json.dumps({"model": "x"}))
        self.sync()
        self.assertNotEqual(read(self.rules), "my old rules\n")
        r = self.sync("--restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(read(self.rules), "my old rules\n")
        self.assertEqual(json.loads(read(self.settings)), {"model": "x"})
        self.assertFalse(os.path.exists(self.hook))  # did not exist before the sync

    def test_restore_undoes_only_the_last_sync(self):
        self.sync()
        write(os.path.join(self.state, "local.md"), "# changed local\n")
        self.sync()
        self.assertIn("changed local", read(self.rules))
        self.sync("--restore")
        self.assertIn("Fixture local rules", read(self.rules))
        self.assertTrue(os.path.exists(self.hook))

    def test_restore_without_backup_fails(self):
        self.assertNotEqual(self.sync("--restore").returncode, 0)


class Guards(Base):
    def test_secret_in_input_fails_run_and_writes_nothing(self):
        token = "ghp_" + "a" * 36
        write(os.path.join(self.state, "local.md"), "token: " + token + "\n")
        r = self.sync()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(token, r.stdout + r.stderr)  # never echo the secret
        self.assertEqual(list(self.snapshot()), ["state:local.md"])  # only the planted input exists

    def test_broken_hook_fails_self_test(self):
        write(os.path.join(self.repo, "claude", "hooks", "review-gate.py"), "this is not python (\n")
        r = self.sync()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("self-test", r.stderr)

    def test_hook_that_always_allows_fails_self_test(self):
        write(os.path.join(self.repo, "claude", "hooks", "review-gate.py"), "import sys\nsys.exit(0)\n")
        self.assertNotEqual(self.sync().returncode, 0)

    def test_missing_neutral_rules_fail(self):
        os.remove(os.path.join(self.repo, "harness", "AGENTS.md"))
        self.assertNotEqual(self.sync().returncode, 0)


class DroppedSectionGuard(Base):
    """The regression helper must fail when a rule from today's file has no home in the new sources."""
    HELPER = os.path.join(HERE, "real_machine_regression.py")

    def regress(self, today_text):
        today = os.path.join(self.tmp, "today.md")
        write(today, today_text)
        r = subprocess.run([sys.executable, "-B", self.HELPER, "--out", os.path.join(self.tmp, "out"),
                            "--today", today, "--repo", self.repo, "--state-home", self.state,
                            "--fail-on-dropped"], capture_output=True, text=True)
        report = os.path.join(self.tmp, "out", "regression.txt")
        return r, read(report) if os.path.exists(report) else ""

    def test_every_section_placed_passes(self):
        r, _ = self.regress(read(os.path.join(FIX, "today_CLAUDE.md")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_dropped_heading_and_rule_fail(self):
        r, report = self.regress(read(os.path.join(FIX, "today_CLAUDE.md"))
                                 + "\n# Forgotten section\n- A rule nobody carried over.\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("# Forgotten section", report)
        self.assertIn("A rule nobody carried over", report)

    def test_reordered_and_reworded_do_not_fail(self):
        today = ("# Fixture local rules\n- Branch prefix: demo, so a ticket branch is `demo/KEY-1`.\n\n"
                 "# Fixture rules\n- Use a fast agent for searching code.\n")
        r, report = self.regress(today)
        self.assertEqual(r.returncode, 0, report)
        self.assertIn("reworded: 1", report)


if __name__ == "__main__":
    unittest.main()
