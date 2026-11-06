#!/usr/bin/env python3
"""Tests for via_tokens.py. Run: python3 plugins/via/scripts/test_via_tokens.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import via_tokens  # noqa: E402

SCRIPT = os.path.join(HERE, "via_tokens.py")


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


CSS = """
/* --commented: #000000; */
:root {
  --surface: #ffffff;
  --on-surface: #1b1b1f;
  --radius-md: 12px;
  --space-4: 16px;
  --font-size-body: 14px;
  --font-weight-bold: 700;
  --brand: var(--surface);
  --font-family: system-ui, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root { --surface: #121212; --on-surface: #e6e1e5; }
}
@media (min-width: 600px) {
  :root { --space-4: 24px; }
}
"""


class ReadTokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_a_self_referencing_value_does_not_blow_up(self):
        refs = "var(--space-a)" * 30
        write(self.root, "styles.css", ":root { --space-a: %s; --space-b: 8px; }" % refs)
        t = via_tokens.read_tokens(self.root)  # must return quickly
        self.assertEqual(t["spacing"]["--space-b"]["light"], "8px")
        self.assertLessEqual(len(t["spacing"].get("--space-a", {"light": ""})["light"] or ""), via_tokens.MAX_VALUE)

    def test_a_not_dark_selector_is_the_light_scope(self):
        write(self.root, "styles.css", ":root:not(.dark) { --surface: #ffffff; } .dark { --surface: #121212; }")
        self.assertEqual(via_tokens.read_tokens(self.root)["colors"]["--surface"], {"light": "#ffffff", "dark": "#121212"})

    def test_tokens_inside_layer_are_read(self):
        write(self.root, "styles.css", "@layer base { :root { --radius-l: 12px; } .dark { --radius-l: 8px; } }")
        self.assertEqual(via_tokens.read_tokens(self.root)["radius"]["--radius-l"], {"light": "12px", "dark": "8px"})

    def test_colors_have_light_and_dark(self):
        write(self.root, "styles.css", CSS)
        t = via_tokens.read_tokens(self.root)
        self.assertEqual(t["colors"]["--surface"], {"light": "#ffffff", "dark": "#121212"})
        self.assertEqual(t["colors"]["--on-surface"], {"light": "#1b1b1f", "dark": "#e6e1e5"})

    def test_groups_by_name_and_dark_falls_back_to_light(self):
        write(self.root, "styles.css", CSS)
        t = via_tokens.read_tokens(self.root)
        self.assertEqual(t["radius"]["--radius-md"], {"light": "12px", "dark": "12px"})
        self.assertEqual(t["spacing"]["--space-4"]["light"], "16px")
        self.assertEqual(t["type_size"]["--font-size-body"]["light"], "14px")
        self.assertEqual(t["type_weight"]["--font-weight-bold"]["light"], "700")

    def test_other_media_queries_are_ignored(self):
        write(self.root, "styles.css", CSS)
        self.assertEqual(via_tokens.read_tokens(self.root)["spacing"]["--space-4"]["light"], "16px")

    def test_var_references_resolve_per_theme(self):
        write(self.root, "styles.css", CSS)
        self.assertEqual(via_tokens.read_tokens(self.root)["colors"]["--brand"],
                         {"light": "#ffffff", "dark": "#121212"})

    def test_comments_and_non_token_values_are_skipped(self):
        write(self.root, "styles.css", CSS)
        t = via_tokens.read_tokens(self.root)
        self.assertNotIn("--commented", t["colors"])
        every = {k for group in t.values() if isinstance(group, dict) for k in group}
        self.assertNotIn("--font-family", every)

    def test_dark_class_selector(self):
        write(self.root, "a.css", ":root{--bg:#fff}\n[data-theme=\"dark\"]{--bg:#000}\n")
        self.assertEqual(via_tokens.read_tokens(self.root)["colors"]["--bg"], {"light": "#fff", "dark": "#000"})

    def test_skips_node_modules_hidden_dirs_and_symlinks(self):
        write(self.root, "node_modules/x/a.css", ":root{--leak:#123456}")
        write(self.root, ".via/runs/a.css", ":root{--leak2:#123456}")
        outside = write(tempfile.mkdtemp(), "o.css", ":root{--outside:#abcdef}")
        os.symlink(outside, os.path.join(self.root, "link.css"))
        write(self.root, "ok.css", ":root{--ok:#010101}")
        t = via_tokens.read_tokens(self.root)
        self.assertEqual(set(t["colors"]), {"--ok"})

    def test_no_tokens_gives_empty_groups_and_no_sources(self):
        t = via_tokens.read_tokens(self.root)
        self.assertEqual(t["colors"], {})
        self.assertEqual(t["sources"], [])

    def test_sources_are_relative_paths(self):
        write(self.root, "web/styles.css", ":root{--a:#fff}")
        self.assertEqual(via_tokens.read_tokens(self.root)["sources"], ["web/styles.css"])

    def test_cli_prints_json(self):
        write(self.root, "styles.css", CSS)
        r = subprocess.run([sys.executable, "-B", SCRIPT, "read", "--project", self.root],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["colors"]["--surface"]["dark"], "#121212")

    def test_cli_rejects_a_missing_project(self):
        r = subprocess.run([sys.executable, "-B", SCRIPT, "read", "--project", os.path.join(self.root, "nope")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
