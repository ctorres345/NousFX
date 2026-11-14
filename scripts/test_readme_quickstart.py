#!/usr/bin/env python3
"""NFX-25: the README "Meet Via" section matches the fresh-user test, its links resolve and its config table is real."""
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import fresh_user_test  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def section(text, heading):
    """The body of a '## heading' section, up to the next '## ' heading."""
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(heading), text, re.S | re.M)
    return m.group(1) if m else ""


class ReadmeQuickstartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = read("README.md")
        cls.meet = section(cls.readme, "Meet Via")

    def test_section_exists(self):
        self.assertTrue(self.meet, "README has no '## Meet Via' section")

    def test_quickstart_commands_match_the_fresh_user_test(self):
        quick = re.search(r"^### Quickstart\n(.*?)(?=^### |\Z)", self.meet, re.S | re.M)
        self.assertTrue(quick, "Meet Via has no '### Quickstart' subsection")
        lines = []
        for block in re.findall(r"```[a-z]*\n(.*?)```", quick.group(1), re.S):
            lines += [l.strip() for l in block.splitlines() if l.strip()]
        self.assertEqual(lines, fresh_user_test.QUICKSTART, "README quickstart differs from fresh_user_test.QUICKSTART")

    def test_marketplace_commands_name_a_real_plugin(self):
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")) as f:
            market = json.load(f)
        names = [p["name"] for p in market["plugins"]]
        self.assertIn("via", names)
        self.assertIn("via@" + market["name"], fresh_user_test.QUICKSTART[1])

    def test_version_matches_plugin_json(self):
        with open(os.path.join(ROOT, "plugins", "via", ".claude-plugin", "plugin.json")) as f:
            version = json.load(f)["version"]
        self.assertIn("version %s" % version, self.meet)

    def test_linked_files_exist(self):
        for target in re.findall(r"\]\((?!https?:|#)([^)#]+)", self.meet):
            self.assertTrue(os.path.exists(os.path.join(ROOT, target)), "README links a missing file: " + target)

    def test_contributing_is_linked_and_exists(self):
        self.assertIn("](CONTRIBUTING.md)", self.readme)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "CONTRIBUTING.md")))

    def test_the_slow_first_launch_is_handled_not_left_to_a_manual_rerun(self):
        """NFX-148: neither the README row nor the fresh-user report tells the user to rerun by hand."""
        row = re.search(r"^\| A first browser launch \|(.*)\|$", self.meet, re.M)
        self.assertTrue(row, "README has no 'A first browser launch' row")
        report = read("docs", "fresh-user-test.md")
        finding = re.search(r"^1\. \*\*The first capture.*$", report, re.M)
        self.assertTrue(finding, "fresh-user-test.md has no finding 1")
        for name, text in (("README row", row.group(1)), ("finding 1", finding.group(0))):
            for stale in ("Run it again", "Retry, or", "VIA_SHOT_TIMEOUT=180"):
                self.assertNotIn(stale, text, "%s still says: %s" % (name, stale))
        self.assertIn("NFX-148", finding.group(0))
        self.assertIn("first launch is slow", row.group(1))

    def test_config_table_keys_are_real(self):
        with open(os.path.join(ROOT, "plugins", "via", "assets", "via.example.json")) as f:
            example = json.load(f)
        real = set()
        for top, val in example.items():
            if isinstance(val, dict) and top not in ("integrations", "outbound_allowlist"):
                real |= {"%s.%s" % (top, k) for k in val}
            else:
                real.add(top)
        table = section(self.meet.replace("### ", "## "), "Config")
        keys = re.findall(r"^\| `([a-z_.]+)` \|", table, re.M)
        self.assertGreaterEqual(len(keys), 8)
        for key in keys:
            self.assertIn(key, real, "config key not in via.example.json: " + key)
        self.assertEqual(real - set(keys) - {"integrations"}, set(), "example keys missing from the table")


if __name__ == "__main__":
    unittest.main()
