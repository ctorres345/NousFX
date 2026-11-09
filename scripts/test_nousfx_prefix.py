#!/usr/bin/env python3
"""Tests for the ticket branch prefix (scripts/nousfx_prefix.py, NFX-118): where it comes from, and what
sync renders with it (rules and the auto-mode allow entry).
Run: python3 -B scripts/test_nousfx_prefix.py"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nousfx_automode  # noqa: E402
import nousfx_prefix  # noqa: E402
import nousfx_sync  # noqa: E402
from test_nousfx_sync import Base, read, write  # noqa: E402

LEGACY = list(nousfx_automode.LEGACY)


class FromName(unittest.TestCase):
    def test_cleans_up_a_git_name(self):
        self.assertEqual(nousfx_prefix.from_name("Cesar Torres"), "cesar-torres")
        self.assertEqual(nousfx_prefix.from_name("  ana_B.  "), "ana-b")
        self.assertEqual(nousfx_prefix.from_name("José"), "jose")

    def test_nothing_usable_is_me(self):
        for name in ("", "   ", "李", "--"):
            self.assertEqual(nousfx_prefix.from_name(name), "me")

    def test_every_result_is_valid(self):
        for name in ("Cesar Torres", "José", "Zoë O'Brien", "李", ""):
            self.assertIsNotNone(nousfx_prefix.VALID.fullmatch(nousfx_prefix.from_name(name)))


class Resolve(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="nousfx-prefix-test-")
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def resolve(self, toml=None, name="Cesar Torres"):
        if toml is not None:
            write(os.path.join(self.state, "local.toml"), toml)
        return nousfx_prefix.resolve(self.state, nousfx_sync.SyncError, git_name=lambda: name)

    def test_explicit_prefix_wins_without_a_note(self):
        self.assertEqual(self.resolve('[branch]\nprefix = "ana"\n'), ("ana", None))

    def test_missing_file_or_key_uses_the_git_name_with_a_note(self):
        for toml in (None, "", "[branch]\n", '[other]\nx = "y"\n'):
            prefix, note = self.resolve(toml)
            self.assertEqual(prefix, "cesar-torres")
            self.assertIn("cesar-torres", note)
            self.assertIn("local.toml", note)

    def test_no_git_name_is_me(self):
        self.assertEqual(self.resolve(name="")[0], "me")

    def test_explicit_invalid_value_stops_with_2(self):
        for value in ('"Cesar/"', '"a b"', '""', "3", '"Ana"', '"ana\\n"', '"-d"'):
            with self.assertRaises(nousfx_sync.SyncError) as e:
                self.resolve(f"[branch]\nprefix = {value}\n")
            self.assertEqual(e.exception.code, 2)

    def test_bad_toml_or_shape_stops_with_2(self):
        for toml in ("[branch\n", 'branch = "x"\n'):
            with self.assertRaises(nousfx_sync.SyncError) as e:
                self.resolve(toml)
            self.assertEqual(e.exception.code, 2)


class Entry(unittest.TestCase):
    def test_one_prose_entry_that_starts_with_the_marker(self):
        entry = nousfx_automode.rule("ana")
        self.assertTrue(entry.startswith(nousfx_automode.MARKER))
        self.assertNotIn("ana", nousfx_automode.MARKER)
        self.assertIn("`ana/<KEY>`", entry)
        self.assertIn("`ana/<KEY>-shots`", entry)
        self.assertNotIn("Bash(", entry)

    def merge(self, allow, prefix="ana"):
        cfg = {"autoMode": {"allow": list(allow)}}
        nousfx_automode.merge(cfg, nousfx_sync.SyncError, "settings.json", prefix)
        return cfg["autoMode"]["allow"]

    def test_legacy_bash_entries_become_the_prose_entry_in_place(self):
        allow = self.merge(["$defaults", "Bash(adb:*)"] + LEGACY + ["Bash(later)"])
        self.assertEqual(allow, ["$defaults", "Bash(adb:*)", nousfx_automode.rule("ana"), "Bash(later)"])

    def test_a_hand_added_legacy_copy_is_owned_too(self):
        allow = self.merge(["$defaults", LEGACY[1], "Bash(b)", LEGACY[0]])
        self.assertEqual(allow, ["$defaults", nousfx_automode.rule("ana"), "Bash(b)"])

    def test_a_prefix_change_replaces_the_old_entry(self):
        allow = self.merge(["$defaults", nousfx_automode.rule("ana"), "Bash(b)"], prefix="bo")
        self.assertEqual(allow, ["$defaults", nousfx_automode.rule("bo"), "Bash(b)"])


class Sync(Base):
    def setUp(self):
        super().setUp()
        self.gitconfig = os.path.join(self.tmp, "gitconfig")
        write(self.gitconfig, "[user]\n\tname = Cesar Torres\n")
        write(os.path.join(self.repo, "harness", "AGENTS.md"),
              read(os.path.join(self.repo, "harness", "AGENTS.md")) + "- Branch `<prefix>/<KEY>`.\n")

    def run_sync(self, *args):
        return self.sync(*args, env={"GIT_CONFIG_GLOBAL": self.gitconfig, "GIT_CONFIG_NOSYSTEM": "1"})

    def set_prefix(self, prefix):
        write(os.path.join(self.state, "local.toml"), f'[branch]\nprefix = "{prefix}"\n')

    def allow(self):
        return json.loads(read(self.settings))["autoMode"]["allow"]

    def test_explicit_prefix_renders_rules_and_one_entry(self):
        env = ["**Trusted repo**: /x"]
        write(self.settings, json.dumps({"autoMode": {"allow": ["$defaults", "Bash(adb:*)"], "environment": env}}))
        self.set_prefix("ana")
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- Branch `ana/<KEY>`.", read(self.rules))
        self.assertNotIn("<prefix>", read(self.rules))
        self.assertEqual(self.allow(), ["$defaults", "Bash(adb:*)", nousfx_automode.rule("ana")])
        self.assertEqual(json.loads(read(self.settings))["autoMode"]["environment"], env)
        self.assertNotIn("note:", r.stderr)

    def test_without_local_toml_the_git_name_is_used_with_a_note(self):
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("cesar-torres", r.stderr)
        self.assertIn("- Branch `cesar-torres/<KEY>`.", read(self.rules))
        write(self.gitconfig, "[user]\n\tname = José\n")
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("- Branch `jose/<KEY>`.", read(self.rules))

    def test_explicit_invalid_prefix_exits_2_and_writes_nothing(self):
        for bad in ("Cesar/", "a b"):
            self.set_prefix(bad)
            before = self.snapshot()
            r = self.run_sync()
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("prefix", r.stderr)
            self.assertEqual(self.snapshot(), before)

    def test_migration_prefix_change_rerun_and_check(self):
        write(self.settings, json.dumps({"autoMode": {"allow": ["$defaults"] + LEGACY}}))
        self.set_prefix("ana")
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(self.allow(), ["$defaults", nousfx_automode.rule("ana")])
        before = self.snapshot()
        r = self.run_sync()
        self.assertIn("nothing to change", r.stdout)
        self.assertEqual(self.snapshot(), before)
        self.set_prefix("bo")
        r = self.run_sync("--check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.rules, r.stdout)
        self.assertIn(self.settings, r.stdout)
        self.assertEqual(self.run_sync().returncode, 0)
        self.assertEqual(self.allow(), ["$defaults", nousfx_automode.rule("bo")])
        self.assertEqual(self.run_sync("--check").returncode, 0)

    def test_header_names_local_toml_and_any_change_to_it_is_drift(self):
        self.set_prefix("ana")
        self.run_sync()
        self.assertIn("local.toml", read(self.rules).splitlines()[1])
        write(os.path.join(self.state, "local.toml"), '# a comment\n[branch]\nprefix = "ana"\n')
        self.assertEqual(self.run_sync("--check").returncode, 1)


if __name__ == "__main__":
    unittest.main()
