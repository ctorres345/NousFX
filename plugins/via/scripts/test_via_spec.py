#!/usr/bin/env python3
"""Tests for via_spec.py. Run: python3 -B plugins/via/scripts/test_via_spec.py

The tests run the script as a subprocess on a throwaway git repo with a throwaway home (see nousfx_testhome),
and check only what a caller sees: exit codes, stdout JSON and the files left behind.
"""
import json
import os
import subprocess
import tempfile
import unittest

import nousfx_testhome as th

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "via_spec.py")
FAKE_KEY = "AQ.FAKEKEYFAKEKEYFAKEKEYFAKEKEY99"
CSS = """:root{--surface:#ffffff;--on-surface:#1b1b1f;--primary:#0a7b5c;--radius-md:12px;--space-4:16px;
--font-size-body:15px;--font-weight-medium:500}
@media (prefers-color-scheme: dark){:root{--surface:#101820;--primary:#7fe0c0}}
"""
MOCK = ('<div style="padding: var(--space-4); background: var(--surface)">'
        '<button style="background: var(--primary); border-radius: var(--radius-md); min-height: 48px">Start</button></div>')
OUTPUTS = ("direction.md", "spec.md", "target.html", "ticket-update.md")


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def row(element="Start button", prop="background", **extra):
    """A token row for --primary unless extra names its own token or value."""
    return {"element": element, "property": prop, **(extra or {"token": "--primary"})}


def screen(name="home", **extra):
    base = {"screen": name, "label": "Home with a full-width Start", "rationale": "One clear action.", "html": MOCK,
            "states": {"default": "Primary fill", "pressed": "88% opacity"},
            "rows": [row(), row(prop="corner radius", token="--radius-md"),
                     row(prop="min height", value="48px", note="touch target")]}
    base.update(extra)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = os.path.realpath(t.name)
        self.project = os.path.join(self.home, "project")
        os.makedirs(self.project)
        subprocess.run(["git", "-C", self.project, "init", "-q"], check=True)
        write(os.path.join(self.project, "styles.css"), CSS)
        self.run_dir = os.path.join(self.project, ".via", "runs", "20260101T000000")
        self.spec = {"version": 1, "mode": "spec",
                     "ticket": {"key": "APP-7", "title": "Home: one clear Start action", "source": "jira"},
                     "platform": "web",
                     "direction": "Calm and direct: one primary action, the brand green, generous padding.",
                     "screens": [screen()]}
        self.save()

    def save(self, spec=None):
        write(os.path.join(self.run_dir, "spec.json"), json.dumps(self.spec if spec is None else spec))

    def allow(self):
        subprocess.run(["git", "-C", self.project, "remote", "add", "origin", "https://github.com/acme/app.git"], check=True)
        write(os.path.join(self.home, ".config", "nousfx", "via.json"), json.dumps(
            {"version": 1, "outbound_allowlist": {"github.com/acme/app": {"destinations": ["artifacts"], "gemini_code_paths": []}}}))

    def cli(self, *args):
        cmd = [th.python(self.home), "-I", "-B", SCRIPT, *args, "--project", self.project,
               "--config", os.path.join(self.home, ".config", "nousfx", "via.json")]
        return subprocess.run(cmd, capture_output=True, text=True, env={"PATH": os.environ["PATH"], **th.env(self.home)})

    def build(self):
        return self.cli("build", self.run_dir)

    def read(self, name):
        with open(os.path.join(self.run_dir, name), encoding="utf-8") as f:
            return f.read()

    def ok(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def assert_refused(self, r, code=1, text=None):
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        for name in OUTPUTS:
            self.assertFalse(os.path.exists(os.path.join(self.run_dir, name)), name)
        if text:
            self.assertIn(text, r.stderr)


class Build(Base):
    def test_writes_the_direction_note_spec_table_and_target_mock(self):
        out = self.ok()
        for name in OUTPUTS:
            self.assertTrue(os.path.isfile(os.path.join(self.run_dir, name)), name)
        self.assertEqual(out["outputs"]["target"], os.path.join(self.run_dir, "target.html"))
        self.assertIn("Calm and direct", self.read("direction.md"))
        table = self.read("spec.md")
        self.assertIn("| home | Start button | background | `--primary` | #0a7b5c | #7fe0c0 |", table)
        self.assertIn("| home | Start button | corner radius | `--radius-md` | 12px | 12px |", table)
        self.assertIn("| home | Start button | min height | literal | 48px | 48px | touch target |", table)

    def test_target_mock_is_the_sanitized_mock_in_a_themed_phone_frame(self):
        self.ok()
        page = self.read("target.html")
        self.assertIn("Home with a full-width Start", page)
        self.assertIn(">Start</button>", page)
        self.assertIn('class="vp-fragment" inert', page)  # mock buttons are not tab stops
        self.assertIn("--primary:#0a7b5c", page)  # light scope
        self.assertIn("--primary:#7fe0c0", page)  # dark scope
        self.assertIn('id="vp-dark"', page)
        self.assertIn("88% opacity", page)  # states from the spec
        self.assertIn("APP-7", page)
        self.assertIn("default-src 'none'", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("<img", page.lower())  # nothing is built yet, so there is no before shot

    def test_target_page_shows_the_spec_rows_beside_the_mock(self):
        self.ok()
        page = self.read("target.html")
        self.assertIn('<th scope="row">Start button</th><td>min height</td><td>literal</td><td>48px</td><td>48px</td>', page)
        self.assertIn("<td>touch target</td>", page)
        self.assertIn('<span class="vp-sw" style="background:#7fe0c0"></span>#7fe0c0', page)  # dark swatch of --primary

    def test_proposes_a_ticket_update_with_fidelity_criteria_and_concrete_values(self):
        out = self.ok()
        self.assertEqual(out["outputs"]["ticket_update"], os.path.join(self.run_dir, "ticket-update.md"))
        update = self.read("ticket-update.md")
        self.assertIn("- [ ] Matches the target mock for home: colors, sizes, radii, spacing, icons, "
                      "states (open/pressed/selected/empty/dark).", update)
        self.assertIn("| home | Start button | background | `--primary` | #0a7b5c | #7fe0c0 |", update)
        self.assertIn("Calm and direct", update)
        self.assertIn("88% opacity", update)

    def test_saves_the_resolved_values_in_spec_json_for_check(self):
        self.ok()
        saved = json.loads(self.read("spec.json"))
        rows = saved["screens"][0]["rows"]
        self.assertEqual((rows[0]["light"], rows[0]["dark"]), ("#0a7b5c", "#7fe0c0"))
        self.assertEqual((rows[2]["light"], rows[2]["dark"]), ("48px", "48px"))
        self.assertTrue(saved["built_at"])
        self.assertEqual(saved["outputs"], {"direction": "direction.md", "spec": "spec.md", "target": "target.html",
                                            "ticket_update": "ticket-update.md"})

    def test_a_rebuild_recomputes_the_values(self):
        self.ok()
        write(os.path.join(self.project, "styles.css"), CSS.replace("#0a7b5c", "#123456"))
        self.ok()
        self.assertEqual(json.loads(self.read("spec.json"))["screens"][0]["rows"][0]["light"], "#123456")


    def test_a_token_the_project_lacks_shows_no_token_and_warns(self):
        self.spec["screens"][0]["rows"].append(row(prop="border", token="--outline"))
        self.save()
        out = self.ok()
        self.assertIn("| home | Start button | border | `--outline` | no token | no token |", self.read("spec.md"))
        self.assertTrue(any("--outline" in w and "no token" in w for w in out["warnings"]), out["warnings"])

    def test_a_mock_token_missing_from_the_table_warns(self):
        out = self.ok()  # MOCK uses --space-4 and --surface, the rows do not
        self.assertTrue(any("--space-4" in w and "--surface" in w for w in out["warnings"]), out["warnings"])

    def test_literal_values_in_the_mock_warn(self):
        self.spec["screens"][0]["html"] = '<p style="color: #ff0000">x</p>'
        self.save()
        out = self.ok()
        self.assertIn("screens[0] (home): color is a literal value, not a token (#ff0000).", out["warnings"])

    def test_stays_local_off_the_allow_list(self):
        out = self.ok()
        self.assertFalse(out["publish_allowed"])
        self.assertIn("target.html", self.read("ticket-update.md"))

    def test_link_records_the_published_mock_in_spec_and_ticket_update(self):
        self.allow()
        out = self.ok()
        self.assertTrue(out["publish_allowed"], out)
        r = self.cli("link", self.run_dir, "--url", "https://claude.ai/artifact/abc")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.read("spec.json"))["target"], {"url": "https://claude.ai/artifact/abc", "published": True})
        self.assertIn("https://claude.ai/artifact/abc", self.read("ticket-update.md"))

    def test_link_refuses_when_not_allowed_or_not_https(self):
        self.ok()
        r = self.cli("link", self.run_dir, "--url", "https://claude.ai/artifact/abc")
        self.assertEqual(r.returncode, 1)
        self.allow()
        r = self.cli("link", self.run_dir, "--url", "http://claude.ai/artifact/abc")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("target", json.loads(self.read("spec.json")))

    def test_link_needs_a_build_first(self):
        self.allow()
        r = self.cli("link", self.run_dir, "--url", "https://claude.ai/artifact/abc")
        self.assertEqual(r.returncode, 1)
        self.assertIn("build", r.stderr)

    def test_a_markdown_ticket_is_read_from_the_project(self):
        write(os.path.join(self.project, "tickets", "APP-7.md"), "# APP-7\n")
        self.spec["ticket"].update(source="markdown", path="tickets/APP-7.md")
        self.save()
        self.ok()
        self.assertIn("tickets/APP-7.md", self.read("spec.md"))

    def test_a_literal_row_may_give_its_own_dark_value(self):
        self.spec["screens"][0]["rows"].append(row("Card", "border", value="#dddddd", value_dark="#333333"))
        self.save()
        self.ok()
        self.assertIn("| home | Card | border | literal | #dddddd | #333333 |", self.read("spec.md"))
        self.ok()  # a rebuild keeps the author's dark value
        self.assertIn("| home | Card | border | literal | #dddddd | #333333 |", self.read("spec.md"))

    def test_a_native_spec_declares_its_theme_for_the_table_and_the_mock(self):
        os.remove(os.path.join(self.project, "styles.css"))
        self.spec["platform"] = "compose"
        self.spec["theme"] = {"--primary": {"light": "#3f51b5", "dark": "#9fa8da"}, "--radius-md": {"light": "12px", "dark": "12px"},
                              "--space-4": {"light": "16px", "dark": "16px"}, "--surface": {"light": "#ffffff", "dark": "#121212"}}
        self.save()
        out = self.ok()
        self.assertIn("| home | Start button | background | `--primary` | #3f51b5 | #9fa8da |", self.read("spec.md"))
        page = self.read("target.html")
        self.assertIn("--primary:#3f51b5", page)
        self.assertIn("--primary:#9fa8da", page)
        self.assertFalse(any("No theme tokens" in w for w in out["warnings"]), out["warnings"])

    def test_on_native_a_stray_css_file_does_not_override_the_declared_theme(self):
        self.spec["platform"] = "flutter"  # styles.css (from setUp) defines --primary as #0a7b5c
        self.spec["theme"] = {"--primary": {"light": "#3f51b5", "dark": "#9fa8da"}}
        self.save()
        self.ok()
        self.assertIn("| home | Start button | background | `--primary` | #3f51b5 | #9fa8da |", self.read("spec.md"))
        self.assertNotIn("#0a7b5c", self.read("target.html"))

    def test_link_refuses_when_a_resolved_value_was_edited(self):
        self.allow()
        self.ok()
        spec = json.loads(self.read("spec.json"))
        del spec["screens"][0]["rows"][0]["light"]
        self.save(spec)
        r = self.cli("link", self.run_dir, "--url", "https://claude.ai/artifact/abc")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("changed since", r.stderr)

    def test_a_token_defined_for_one_theme_only_warns(self):
        write(os.path.join(self.project, "styles.css"), CSS + "@media (prefers-color-scheme: dark){:root{--accent:#ff9900}}")
        self.spec["screens"][0]["rows"].append(row(prop="focus ring", token="--accent"))
        self.save()
        out = self.ok()
        self.assertTrue(any("--accent" in w for w in out["warnings"]), out["warnings"])

    def test_link_refuses_when_spec_json_changed_after_build(self):
        self.allow()
        self.ok()
        spec = json.loads(self.read("spec.json"))
        spec["screens"][0]["rows"].append(row(prop="border", token="--primary"))
        self.save(spec)
        r = self.cli("link", self.run_dir, "--url", "https://claude.ai/artifact/abc")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("changed since", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


class Refusals(Base):
    def refused(self, mutate, code=1, text=None):
        spec = json.loads(json.dumps(self.spec))
        mutate(spec)
        self.save(spec)
        before = self.read("spec.json")
        self.assert_refused(self.build(), code, text)
        self.assertEqual(self.read("spec.json"), before)  # a refused build leaves the input alone

    def test_not_a_run_folder(self):
        other = os.path.join(self.project, "elsewhere")
        write(os.path.join(other, "spec.json"), json.dumps(self.spec))
        r = self.cli("build", other)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a run folder", r.stderr)

    def test_missing_spec_json(self):
        os.remove(os.path.join(self.run_dir, "spec.json"))
        self.assert_refused(self.build(), 1, "spec.json")

    def test_not_json(self):
        write(os.path.join(self.run_dir, "spec.json"), "{nope")
        self.assert_refused(self.build(), 1, "not valid JSON")

    def test_wrong_version_or_mode_or_unknown_keys(self):
        self.refused(lambda s: s.update(version=2), text="version 1")
        self.refused(lambda s: s.update(mode="audit"), text="mode")
        self.refused(lambda s: s.update(extra=1), text="extra")

    def test_ticket_needs_a_key_title_and_known_source(self):
        self.refused(lambda s: s["ticket"].pop("key"), text="ticket")
        self.refused(lambda s: s["ticket"].update(source="email"), text="source")

    def test_markdown_ticket_needs_a_path_inside_the_project(self):
        self.refused(lambda s: s["ticket"].update(source="markdown"), text="path")
        self.refused(lambda s: s["ticket"].update(source="markdown", path="../outside.md"), text="path")
        self.refused(lambda s: s["ticket"].update(source="markdown", path="tickets/missing.md"), text="path")

    def test_screen_count_and_names(self):
        self.refused(lambda s: s.update(screens=[]), text="screens")
        self.refused(lambda s: s.update(screens=[screen("s%d" % i) for i in range(6)]), text="screens")
        self.refused(lambda s: s.update(screens=[screen(), screen()]), text="repeats")

    def test_a_row_has_exactly_one_of_token_or_value(self):
        self.refused(lambda s: s["screens"][0]["rows"].append(row(token="--primary", value="1px")), text="token or a value")
        self.refused(lambda s: s["screens"][0]["rows"].append({"element": "x", "property": "y"}), text="token or a value")
        self.refused(lambda s: s["screens"][0]["rows"].append(row(token="primary")), text="--")
        self.refused(lambda s: s["screens"][0].update(rows=[]), text="rows")

    def test_a_theme_is_only_for_native_platforms_and_safe_values(self):
        self.refused(lambda s: s.update(theme={"--primary": {"light": "#000000", "dark": "#ffffff"}}), text="theme")
        self.refused(lambda s: s.update(platform="flutter", theme={"primary": {"light": "#000", "dark": "#fff"}}), text="theme")
        self.refused(lambda s: s.update(platform="flutter", theme={"--primary": {"light": "red;}body{x:y", "dark": "#fff"}}),
                     text="theme")

    def test_value_dark_only_on_a_literal_row(self):
        self.refused(lambda s: s["screens"][0]["rows"].append(row(token="--primary", value_dark="#000000")), text="value_dark")

    def test_unknown_states(self):
        self.refused(lambda s: s["screens"][0]["states"].update(hover="x"), text="states")

    def test_unsafe_mock_markup(self):
        self.refused(lambda s: s["screens"][0].update(html='<img src="https://x.test/a.png">'), text="img")
        self.refused(lambda s: s["screens"][0].update(html="<p>hi</p><script>alert(1)</script>"))

    def test_a_secret_stops_with_exit_2(self):
        self.refused(lambda s: s.update(direction="key " + FAKE_KEY), code=2, text="secret")


if __name__ == "__main__":
    unittest.main()
