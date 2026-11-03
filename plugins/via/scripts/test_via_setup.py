#!/usr/bin/env python3
"""Tests for via_setup.py. Run: python3 plugins/via/scripts/test_via_setup.py

Each test runs the script as a subprocess with a throwaway HOME and a PATH holding only fake binaries.
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "via_setup.py")
KEY_LIKE = re.compile(r"(AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{20,})")
INTEGRATIONS = {"gemini", "atlassian", "figma", "obsidian", "simulators", "browser", "skills"}
FAKE_KEY = "AQ.FAKEKEYFAKEKEYFAKEKEYFAKEKEY99"


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


class ViaSetupTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.bin = tempfile.mkdtemp()
        self.config = os.path.join(self.home, ".claude", "via.json")

    def fake_gemini(self, source="keyring"):
        path = os.path.join(self.home, ".local", "bin", "gemini-api")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = {"credentials": {"api-key": {"source": source, "value": FAKE_KEY[:2] + "******99"}}}
        with open(path, "w") as f:
            f.write("#!/bin/sh\necho '%s'\n" % json.dumps(body))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def run_setup(self, *args):
        env = {"HOME": self.home, "PATH": self.bin}
        return subprocess.run([sys.executable, SCRIPT, *args], env=env, capture_output=True, text=True)

    def report(self, *args):
        r = self.run_setup(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_nothing_configured_lists_every_integration_as_missing_with_a_fix_link(self):
        rep = self.report()
        self.assertEqual(set(rep["integrations"]), INTEGRATIONS)
        for name, item in rep["integrations"].items():
            self.assertEqual(item["status"], "missing", name)
            self.assertTrue(item["why"], name)
            self.assertTrue(item["fix_url"].startswith("https://"), name)
        self.assertIn("summary", rep)

    def test_config_is_written_with_the_required_fields_and_no_secrets(self):
        self.fake_gemini()
        self.report()
        text = read(self.config)
        cfg = json.loads(text)
        for field in ("outbound_allowlist", "obsidian", "figma"):
            self.assertIn(field, cfg)
        self.assertEqual(cfg["outbound_allowlist"], {})
        self.assertIn("vault_path", cfg["obsidian"])
        self.assertIn("state", cfg["figma"])
        self.assertIsNone(KEY_LIKE.search(text))
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o600)

    def test_rerun_with_no_changes_leaves_the_config_untouched(self):
        self.fake_gemini()
        first = self.report()
        self.assertEqual(first["config"], "written")
        before = (read(self.config, "rb"), os.stat(self.config).st_mtime_ns)
        second = self.report()
        self.assertEqual(second["config"], "unchanged")
        self.assertEqual(before, (read(self.config, "rb"), os.stat(self.config).st_mtime_ns))

    def test_user_set_values_survive_a_rerun(self):
        self.report()
        cfg = json.loads(read(self.config))
        vault = os.path.join(self.home, "vault")
        os.makedirs(os.path.join(vault, ".obsidian"))
        cfg["obsidian"]["vault_path"] = vault
        cfg["outbound_allowlist"] = {"acme/app": ["gemini"]}
        with open(self.config, "w") as f:
            json.dump(cfg, f)
        rep = self.report()
        after = json.loads(read(self.config))
        self.assertEqual(after["obsidian"]["vault_path"], vault)
        self.assertEqual(after["outbound_allowlist"], {"acme/app": ["gemini"]})
        self.assertEqual(rep["integrations"]["obsidian"]["status"], "present")

    def test_gemini_key_in_keyring_is_present_and_never_echoed(self):
        self.fake_gemini("keyring")
        r = self.run_setup()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["integrations"]["gemini"]["status"], "present")
        self.assertNotIn("******", r.stdout + read(self.config))

    def test_gemini_cli_without_a_key_is_reported_as_missing_key(self):
        self.fake_gemini("unset")
        self.assertEqual(self.report()["integrations"]["gemini"]["status"], "no-key")

    def test_flags_from_claude_set_the_mcp_browser_and_skill_integrations(self):
        rep = self.report("--atlassian", "present", "--figma", "needs-auth", "--browser", "chrome",
                          "--skills", "material-3:material-3,adaptive")
        i = rep["integrations"]
        self.assertEqual(i["atlassian"]["status"], "present")
        self.assertEqual(i["figma"]["status"], "needs-auth")
        self.assertEqual(i["browser"]["status"], "present")
        self.assertEqual(i["skills"]["status"], "partial")
        self.assertEqual(json.loads(read(self.config))["figma"]["state"], "needs-auth")

    def test_a_symlinked_config_is_refused(self):
        os.makedirs(os.path.dirname(self.config))
        target = os.path.join(self.home, "elsewhere.json")
        with open(target, "w") as f:
            f.write("{}")
        os.symlink(target, self.config)
        r = self.run_setup()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(read(target), "{}")

    def test_a_secret_shaped_value_is_never_written(self):
        r = self.run_setup("--skills", FAKE_KEY)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.config))


if __name__ == "__main__":
    unittest.main()
