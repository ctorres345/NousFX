#!/usr/bin/env python3
"""Tests for the deny list sync writes (scripts/nousfx_deny.py, NFX-74). Run: python3 -B scripts/test_nousfx_deny.py"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nousfx_deny  # noqa: E402
import nousfx_sync  # noqa: E402
from test_nousfx_sync import Base, read, write  # noqa: E402

RULES = list(nousfx_deny.RULES)


class Rules(unittest.TestCase):
    def test_list_covers_secrets_and_lockfiles(self):
        for rule in ("Read(//**/.env*)", "Edit(//**/.env*)", "Read(//**/*.pem)", "Edit(//**/*.key)",
                     "Read(//**/id_rsa*)", "Read(//**/id_ed25519*)", "Edit(//**/id_ecdsa*)", "Read(//**/id_dsa*)", "Edit(//**/package-lock.json)", "Edit(//**/yarn.lock)",
                     "Edit(//**/pnpm-lock.yaml)", "Edit(//**/pubspec.lock)", "Edit(//**/Podfile.lock)",
                     "Edit(//**/gradle.lockfile)"):
            self.assertIn(rule, RULES)
        self.assertNotIn("Read(//**/yarn.lock)", RULES)  # lockfiles may be read
        self.assertEqual(len(RULES), len(set(RULES)))

    def test_only_rule_names_that_match(self):
        # Write(...) and NotebookEdit(...) path rules never match in Claude Code; Edit covers them
        self.assertTrue(all(r.startswith(("Read(", "Edit(")) for r in RULES))

    def test_no_backslash_so_one_text_fits_mac_and_windows(self):
        self.assertFalse(any("\\" in r for r in RULES))


class Merge(unittest.TestCase):
    def merge(self, cfg, rules=RULES):
        nousfx_deny.merge(cfg, rules, nousfx_sync.SyncError, "settings.json")
        return cfg

    def test_user_rules_and_other_permissions_are_kept(self):
        cfg = self.merge({"permissions": {"allow": ["Bash(ls)"], "deny": ["Read(./secret.txt)"],
                                          "defaultMode": "plan"}})
        self.assertEqual(cfg["permissions"]["allow"], ["Bash(ls)"])
        self.assertEqual(cfg["permissions"]["defaultMode"], "plan")
        self.assertEqual(cfg["permissions"]["deny"], ["Read(./secret.txt)"] + RULES)

    def test_twice_is_the_same(self):
        once = self.merge({"permissions": {"deny": ["Bash(rm -rf *)"]}})
        twice = self.merge(json.loads(json.dumps(once)))
        self.assertEqual(once, twice)

    def test_rule_added_after_ours_keeps_its_place(self):
        cfg = self.merge({"permissions": {"deny": ["A"]}})
        cfg["permissions"]["deny"].append("B")
        cfg["permissions"]["deny"].remove(RULES[3])  # a hand removal comes back, in our block
        self.assertEqual(self.merge(cfg)["permissions"]["deny"], ["A"] + RULES + ["B"])

    def test_excluded_rule_is_removed_even_if_sync_wrote_it_before(self):
        cfg = self.merge({})
        cfg = self.merge(cfg, RULES[1:])
        self.assertNotIn(RULES[0], cfg["permissions"]["deny"])

    def test_nothing_wanted_leaves_no_empty_keys(self):
        self.assertEqual(self.merge({"model": "x"}, []), {"model": "x"})
        self.assertEqual(self.merge({"permissions": {"deny": RULES}}, []), {"permissions": {"deny": []}})

    def test_bad_shapes_stop_the_run(self):
        for cfg in ({"permissions": []}, {"permissions": {"deny": "Read(x)"}}, {"permissions": {"deny": [{"x": 1}]}}):
            with self.assertRaises(nousfx_sync.SyncError):
                self.merge(cfg)


class Sync(Base):
    def deny(self):
        return json.loads(read(self.settings))["permissions"]["deny"]

    def test_sync_writes_rules_and_keeps_every_setting(self):
        before = {"model": "opus", "env": {"A": "1"}, "permissions": {"allow": ["Bash(git status)"],
                  "deny": ["Read(./private/**)"]}, "hooks": {"Stop": [{"hooks": [{"type": "command",
                                                                                 "command": "mine.sh"}]}]}}
        write(self.settings, json.dumps(before))
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(read(self.settings))
        self.assertEqual(cfg["permissions"]["deny"], ["Read(./private/**)"] + RULES)
        self.assertEqual(cfg["permissions"]["allow"], ["Bash(git status)"])
        for key in ("model", "env"):
            self.assertEqual(cfg[key], before[key])
        self.assertEqual(cfg["hooks"]["Stop"], before["hooks"]["Stop"])

    def test_second_run_changes_nothing(self):
        self.sync()
        first = read(self.settings)
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing to change", r.stdout)
        self.assertEqual(read(self.settings), first)
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_check_flags_a_missing_or_edited_rule(self):
        self.sync()
        for change in (lambda d: d.remove("Read(//**/.env*)"),
                       lambda d: d.__setitem__(d.index("Edit(//**/yarn.lock)"), "Edit(//**/yarn.lockx)")):
            self.sync()
            cfg = json.loads(read(self.settings))
            change(cfg["permissions"]["deny"])
            write(self.settings, json.dumps(cfg))
            r = self.sync("--check")
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("settings.json", r.stdout)
        self.sync()
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_excluded_pattern_and_rule_are_left_out(self):
        self.sync()
        write(os.path.join(self.state, "deny.toml"), 'exclude = ["//**/*.key", "Edit(//**/pubspec.lock)"]\n')
        self.assertEqual(self.sync("--check").returncode, 1)  # a new exclusion is drift until synced
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        deny = self.deny()
        for gone in ("Read(//**/*.key)", "Edit(//**/*.key)", "Edit(//**/pubspec.lock)"):
            self.assertNotIn(gone, deny)
        self.assertIn("Read(//**/.env*)", deny)
        self.assertEqual(len(deny), len(RULES) - 3)

    def test_unknown_exclusion_fails_before_writing(self):
        write(os.path.join(self.state, "deny.toml"), 'exclude = ["**/.env.example"]\n')
        r = self.sync()
        self.assertEqual(r.returncode, 2)
        self.assertIn("matches no deny rule", r.stderr)
        self.assertFalse(os.path.exists(self.settings))

    def test_bad_deny_toml_fails(self):
        for text in ("exclude = [", 'exclude = "x"\n', 'skip = ["//**/*.key"]\n'):
            write(os.path.join(self.state, "deny.toml"), text)
            self.assertEqual(self.sync().returncode, 2, text)

    def test_windows_gets_the_same_rules(self):
        write(self.settings, json.dumps({"permissions": {"deny": ["Read(//c/Users/me/notes/**)"]}}))
        r = self.sync("--platform", "windows")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.deny(), ["Read(//c/Users/me/notes/**)"] + RULES)
        self.assertEqual(self.sync("--platform", "windows", "--check").returncode, 0)

    def test_restore_puts_back_the_old_permissions(self):
        write(self.settings, json.dumps({"permissions": {"deny": ["Read(./x)"]}}))
        self.sync()
        self.assertEqual(self.sync("--restore").returncode, 0)
        self.assertEqual(json.loads(read(self.settings)), {"permissions": {"deny": ["Read(./x)"]}})

    def test_dry_run_shows_rules_and_writes_nothing(self):
        r = self.sync("--dry-run")
        self.assertIn("Read(//**/.env*)", r.stdout)
        self.assertFalse(os.path.exists(self.settings))

    def test_settings_that_is_not_an_object_fails(self):
        write(self.settings, "[]")
        r = self.sync()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertEqual(read(self.settings), "[]")


if __name__ == "__main__":
    unittest.main()
