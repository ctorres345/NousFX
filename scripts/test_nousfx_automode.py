#!/usr/bin/env python3
"""Tests for the auto-mode allow entry sync writes (scripts/nousfx_automode.py, NFX-117, NFX-118).
Run: python3 -B scripts/test_nousfx_automode.py"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nousfx_automode  # noqa: E402
import nousfx_sync  # noqa: E402
from test_nousfx_sync import Base, read, write  # noqa: E402

RULES = [nousfx_automode.rule("ana")]
ENV = ["### User-specific", "**Trusted repo**: /Users/me/Projects/app"]


class Rules(unittest.TestCase):
    def test_sync_owns_the_two_nfx_117_bash_entries(self):
        self.assertEqual(list(nousfx_automode.LEGACY),
                         ["Bash(git branch -D cesar/*)", "Bash(git push origin --delete cesar/*)"])
        self.assertNotIn(nousfx_automode.DEFAULTS, RULES)


class Merge(unittest.TestCase):
    def merge(self, cfg):
        nousfx_automode.merge(cfg, nousfx_sync.SyncError, "settings.json", "ana")
        return cfg

    def test_missing_list_starts_with_defaults(self):
        self.assertEqual(self.merge({}), {"autoMode": {"allow": ["$defaults"] + RULES}})
        cfg = self.merge({"autoMode": {"environment": ENV}})
        self.assertEqual(cfg["autoMode"]["allow"], ["$defaults"] + RULES)

    def test_defaults_user_rules_and_environment_are_kept(self):
        cfg = self.merge({"autoMode": {"allow": ["$defaults", "Bash(adb:*)"], "environment": list(ENV),
                                       "soft_deny": ["x"]}, "model": "opus"})
        self.assertEqual(cfg["autoMode"]["allow"], ["$defaults", "Bash(adb:*)"] + RULES)
        self.assertEqual(cfg["autoMode"]["environment"], ENV)
        self.assertEqual(cfg["autoMode"]["soft_deny"], ["x"])
        self.assertEqual(cfg["model"], "opus")

    def test_hand_added_copies_stay_in_place_once(self):
        current = ["$defaults", "Bash(adb:*)"] + RULES + ["Bash(later)"]
        cfg = self.merge({"autoMode": {"allow": list(current)}})
        self.assertEqual(cfg["autoMode"]["allow"], current)

    def test_twice_is_the_same(self):
        once = self.merge({"autoMode": {"allow": ["Bash(adb:*)"]}})
        self.assertEqual(once["autoMode"]["allow"], ["Bash(adb:*)"] + RULES)  # no $defaults forced back in
        self.assertEqual(self.merge(json.loads(json.dumps(once))), once)

    def test_a_removed_rule_comes_back_at_the_end(self):
        cfg = {"autoMode": {"allow": ["$defaults", "Bash(b)"]}}
        self.assertEqual(self.merge(cfg)["autoMode"]["allow"], ["$defaults", "Bash(b)"] + RULES)

    def test_bad_shapes_stop_the_run(self):
        for cfg in ({"autoMode": []}, {"autoMode": {"allow": "Bash(x)"}}, {"autoMode": {"allow": [1]}}):
            with self.assertRaises(nousfx_sync.SyncError):
                self.merge(cfg)


class Sync(Base):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.state, "local.toml"), '[branch]\nprefix = "ana"\n')

    def test_sync_adds_rules_and_leaves_environment(self):
        before = {"autoMode": {"allow": ["$defaults", "Bash(adb:*)"], "environment": ENV},
                  "permissions": {"allow": ["Bash(git status)"]}}
        write(self.settings, json.dumps(before))
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(read(self.settings))
        self.assertEqual(cfg["autoMode"]["allow"], ["$defaults", "Bash(adb:*)"] + RULES)
        self.assertEqual(cfg["autoMode"]["environment"], ENV)
        self.assertEqual(cfg["permissions"]["allow"], ["Bash(git status)"])

    def test_fresh_settings_and_second_run(self):
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        first = read(self.settings)
        self.assertEqual(json.loads(first)["autoMode"], {"allow": ["$defaults"] + RULES})
        r = self.sync()
        self.assertIn("nothing to change", r.stdout)
        self.assertEqual(read(self.settings), first)
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_check_flags_a_removed_rule(self):
        self.sync()
        cfg = json.loads(read(self.settings))
        cfg["autoMode"]["allow"].remove(RULES[0])
        write(self.settings, json.dumps(cfg))
        r = self.sync("--check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("settings.json", r.stdout)

    def test_bad_auto_mode_fails_before_writing(self):
        write(self.settings, json.dumps({"autoMode": {"allow": "x"}}))
        r = self.sync()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("autoMode.allow", r.stderr)


if __name__ == "__main__":
    unittest.main()
