#!/usr/bin/env python3
"""Tests for via_prototype.py. Run: python3 -B plugins/via/scripts/test_via_prototype.py

The CLI tests run the script as a subprocess on a throwaway git repo with a throwaway home (see nousfx_testhome),
and check only what a caller sees: exit codes, stdout JSON and the files left behind.
"""
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

import nousfx_testhome as th

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import via_prototype  # noqa: E402

SCRIPT = os.path.join(HERE, "via_prototype.py")
RUN_SCRIPT = os.path.join(HERE, "via_run.py")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
FAKE_KEY = "AQ.FAKEKEYFAKEKEYFAKEKEYFAKEKEY99"
CSS = """:root{--surface:#ffffff;--on-surface:#1b1b1f;--primary:#0a7b5c;--radius-md:12px;--space-4:16px;
--font-size-body:15px;--font-weight-medium:500}
@media (prefers-color-scheme: dark){:root{--surface:#101820;--primary:#7fe0c0}}
"""
BUTTON = ('<div style="padding: var(--space-4); background: var(--surface)">'
          '<button style="background: var(--primary); border-radius: var(--radius-md); font-size: var(--font-size-body)">Start</button></div>')


def write(path, content, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(content)


def finding(fid="VIA-home-1a2b3c4d", screen="home", **extra):
    base = {"id": fid, "screen": screen, "severity": "high", "location": {"file": "styles.css", "line": 3},
            "rule": "touch-target", "issue": "The Start button is 28 pt tall.", "fix": "Make it 48.",
            "source": "design:accessibility-review", "status": "new"}
    base.update(extra)
    return base


def variant(label="Taller button", html=BUTTON, **extra):
    return {"label": label, "rationale": "Meets the 48 pt target.", "html": html, **extra}


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
        self.shot = "home-light.png"
        self.record = {"version": 1, "mode": "audit", "scope": "all", "platforms": ["web"],
                       "merged_at": "2026-01-01T00:00:00",
                       "screenshots": [{"path": self.shot, "screen": "home", "theme": "light", "size": "390x844",
                                        "redacted": True, "review": "kept"}],
                       "findings": [finding()]}
        write(os.path.join(self.run_dir, self.shot), PNG, "wb")
        self.save_record()
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [variant("Taller button"), variant("Wider button")]}])

    def save_record(self):
        write(os.path.join(self.run_dir, "findings.json"), json.dumps(self.record))

    def save_proto(self, findings):
        write(os.path.join(self.run_dir, "prototype.json"), json.dumps({"version": 1, "findings": findings}))

    def allow(self, destinations=("artifacts",)):
        subprocess.run(["git", "-C", self.project, "remote", "add", "origin", "https://github.com/acme/app.git"], check=True)
        write(os.path.join(self.home, ".config", "nousfx", "via.json"), json.dumps(
            {"version": 1, "outbound_allowlist": {"github.com/acme/app": {"destinations": list(destinations), "gemini_code_paths": []}}}))

    def cli(self, *args, script=SCRIPT):
        cmd = [th.python(self.home), "-I", "-B", script, *args, "--project", self.project]
        if script == SCRIPT:
            cmd += ["--config", os.path.join(self.home, ".config", "nousfx", "via.json")]
        return subprocess.run(cmd, capture_output=True, text=True, env={"PATH": os.environ["PATH"], **th.env(self.home)})

    def build(self):
        return self.cli("build", self.run_dir)

    def page(self):
        with open(os.path.join(self.run_dir, "prototype.html"), encoding="utf-8") as f:
            return f.read()

    def rec(self):
        with open(os.path.join(self.run_dir, "findings.json"), encoding="utf-8") as f:
            return json.load(f)

    def assert_refused(self, r, code=1, text=None):
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "prototype.html")))
        if text:
            self.assertIn(text, r.stderr)


class Build(Base):
    def test_page_has_before_variants_spec_and_toggle(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        page = self.page()
        self.assertIn("data:image/png;base64,", page)
        self.assertEqual(page.count('class="vp-variant"'), 2)
        self.assertIn('id="f1-v1"', page)
        self.assertIn('id="f1-v2"', page)
        self.assertIn('id="vp-dark"', page)
        self.assertIn("Taller button", page)
        self.assertIn("home \u00b7 touch-target</h2>", page)  # people scan by screen and rule; the ID is small print
        self.assertIn('<p class="vp-id">VIA-home-1a2b3c4d</p>', page)
        self.assertNotIn("<script", page.lower())
        self.assertEqual(page.count('class="vp-fragment" inert'), 2)  # mock buttons must not become tab stops
        self.assertIn("default-src 'none'", page)

    def test_spec_values_come_from_the_projects_tokens(self):
        self.build()
        page = self.page()
        for expected in ("#0a7b5c", "#7fe0c0", "12px", "16px", "15px"):  # primary light/dark, radius, space, type size
            self.assertIn(expected, page)
        self.assertIn(".vp-scope{--", page)
        self.assertIn("--primary:#7fe0c0", page)  # the dark scope

    def test_a_missing_token_shows_no_token(self):
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [
            variant(html='<p style="color: var(--invented)">x</p>'), variant()]}])
        self.build()
        self.assertIn("--invented", self.page())
        self.assertIn("no token in this project", self.page())

    def test_records_anchors_and_the_local_page(self):
        r = self.build()
        out = json.loads(r.stdout)
        self.assertEqual(out["findings"], [{"id": "VIA-home-1a2b3c4d", "anchor": "f1", "variants": ["f1-v1", "f1-v2"]}])
        rec = self.rec()
        self.assertEqual(rec["findings"][0]["prototype"], {"anchor": "f1", "variants": ["f1-v1", "f1-v2"]})
        self.assertEqual((rec["prototype"]["path"], rec["prototype"]["published"], rec["prototype"]["url"]),
                         ("prototype.html", False, None))

    def test_off_the_allow_list_stays_local(self):
        out = json.loads(self.build().stdout)
        self.assertFalse(out["publish_allowed"])
        self.assertIn("local HTML file", out["reason"])
        r = self.cli("link", self.run_dir, "--url", "https://claude.ai/artifact/x")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.rec()["prototype"]["published"])

    def test_allow_listed_project_may_publish_and_link(self):
        self.allow()
        self.assertTrue(json.loads(self.build().stdout)["publish_allowed"])
        r = self.cli("link", self.run_dir, "--url", "https://claude.ai/artifact/x")
        self.assertEqual(r.returncode, 0, r.stderr)
        proto = self.rec()["prototype"]
        self.assertEqual((proto["published"], proto["url"]), (True, "https://claude.ai/artifact/x"))

    def test_link_refuses_other_destinations_and_bad_urls(self):
        self.allow(destinations=("jira",))
        self.build()
        self.assertEqual(self.cli("link", self.run_dir, "--url", "https://claude.ai/a").returncode, 1)

    def test_link_refuses_a_non_https_url(self):
        self.allow()
        self.build()
        for bad in ("http://claude.ai/a", "javascript:alert(1)", "https://a b"):
            self.assertEqual(self.cli("link", self.run_dir, "--url", bad).returncode, 1, bad)

    def test_a_malformed_config_fails_closed(self):
        write(os.path.join(self.home, ".config", "nousfx", "via.json"), "{not json")
        out = json.loads(self.build().stdout)
        self.assertFalse(out["publish_allowed"])

    def test_dark_screenshot_is_used_when_there_is_one(self):
        write(os.path.join(self.run_dir, "home-dark.png"), PNG + b"0", "wb")
        self.record["screenshots"].append({"path": "home-dark.png", "screen": "home", "theme": "dark", "size": "390x844",
                                           "redacted": True, "review": "kept"})
        self.save_record()
        self.build()
        self.assertIn('class="vp-shot-dark"', self.page())

    def test_the_phone_shot_is_preferred_over_the_desktop_one(self):
        write(os.path.join(self.run_dir, "home-light-desktop.png"), PNG + b"DESKTOP", "wb")
        self.record["screenshots"].insert(0, {"path": "home-light-desktop.png", "screen": "home", "theme": "light",
                                              "size": "desktop", "redacted": True, "review": "kept"})
        self.save_record()
        self.build()
        self.assertNotIn(base64.b64encode(PNG + b"DESKTOP").decode(), self.page())
        self.assertIn(base64.b64encode(PNG).decode(), self.page())

    def test_a_single_theme_shot_says_so(self):
        self.build()
        self.assertIn("Captured in the light theme only", self.page())

    def test_merge_afterwards_puts_anchors_in_the_ledger(self):
        self.record.pop("merged_at")
        self.record["findings"] = [{k: v for k, v in finding().items() if k not in ("id", "status")}]
        self.save_record()
        self.assertEqual(self.cli("merge", self.run_dir, script=RUN_SCRIPT).returncode, 0)
        fid = self.rec()["findings"][0]["id"]
        self.save_proto([{"id": fid, "variants": [variant(), variant("B")]}])
        self.assertEqual(self.build().returncode, 0)
        self.assertEqual(self.cli("merge", self.run_dir, script=RUN_SCRIPT).returncode, 0)
        with open(os.path.join(self.project, "docs", "design", "via.md"), encoding="utf-8") as f:
            ledger = f.read()
        self.assertIn('"anchor": "f1"', ledger)
        self.assertNotIn("https://", ledger)

    def test_a_rebuild_drops_links_of_findings_no_longer_shown(self):
        self.record["findings"].append(finding("VIA-home-ffffffff", rule="contrast"))
        self.save_record()
        self.save_proto([{"id": "VIA-home-ffffffff", "variants": [variant(), variant("B")]}])
        self.build()
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [variant(), variant("B")]}])
        self.build()
        by_id = {f["id"]: f for f in self.rec()["findings"]}
        self.assertIn("prototype", by_id["VIA-home-1a2b3c4d"])
        self.assertNotIn("prototype", by_id["VIA-home-ffffffff"])

    def test_literal_values_are_warned_about(self):
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [
            variant(html='<div style="color: #ff0000; border-radius: 20px">x</div>'), variant()]}])
        warnings = json.loads(self.build().stdout)["warnings"]
        self.assertTrue(any("color" in w for w in warnings))
        self.assertTrue(any("border-radius" in w for w in warnings))

    def test_no_tokens_in_the_project_warns(self):
        os.remove(os.path.join(self.project, "styles.css"))
        warnings = json.loads(self.build().stdout)["warnings"]
        self.assertTrue(any("No theme tokens" in w for w in warnings))


class Refusals(Base):
    def test_unmerged_run(self):
        self.record.pop("merged_at")
        self.save_record()
        self.assert_refused(self.build(), text="merge")

    def test_unknown_finding_id(self):
        self.save_proto([{"id": "VIA-home-00000000", "variants": [variant(), variant("B")]}])
        self.assert_refused(self.build(), text="not a finding")

    def test_no_kept_screenshot(self):
        self.record["screenshots"][0]["review"] = "dropped"
        self.save_record()
        self.assert_refused(self.build(), text="no kept screenshot")

    def test_unreviewed_screenshot_is_not_used(self):
        self.record["screenshots"][0].pop("review")
        self.save_record()
        self.assert_refused(self.build(), text="no kept screenshot")

    def test_screenshot_outside_the_run_folder(self):
        outside = os.path.join(self.home, "secret.png")
        write(outside, PNG, "wb")
        self.record["screenshots"][0]["path"] = outside
        self.save_record()
        self.assert_refused(self.build(), text="inside the run folder")

    def test_screenshot_that_is_not_an_image(self):
        write(os.path.join(self.run_dir, self.shot), "not a png")
        self.assert_refused(self.build(), text="PNG or JPEG")

    def test_symlinked_screenshot(self):
        target = os.path.join(self.home, "real.png")
        write(target, PNG, "wb")
        os.remove(os.path.join(self.run_dir, self.shot))
        os.symlink(target, os.path.join(self.run_dir, self.shot))
        self.assert_refused(self.build(), text="inside the run folder")

    def test_too_many_findings(self):
        self.save_proto([{"id": "VIA-x-%08d" % i, "variants": [variant(), variant("B")]} for i in range(6)])
        self.assert_refused(self.build(), text="1 to 5")

    def test_variant_count(self):
        for count in (1, 4):
            self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [variant("V%d" % i) for i in range(count)]}])
            self.assert_refused(self.build(), text="2 or 3")

    def test_duplicate_ids(self):
        entry = {"id": "VIA-home-1a2b3c4d", "variants": [variant(), variant("B")]}
        self.save_proto([entry, entry])
        self.assert_refused(self.build(), text="repeats")

    def test_unknown_keys_and_states(self):
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "extra": 1, "variants": [variant(), variant("B")]}])
        self.assert_refused(self.build())
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [variant(states={"hover": "x"}), variant("B")]}])
        self.assert_refused(self.build(), text="states")

    def test_secret_in_prototype_json(self):
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [variant(label=FAKE_KEY), variant("B")]}])
        self.assert_refused(self.build(), code=2)

    def test_missing_prototype_json(self):
        os.remove(os.path.join(self.run_dir, "prototype.json"))
        self.assert_refused(self.build(), text="prototype.json")

    def test_not_a_run_folder(self):
        r = self.cli("build", self.project)
        self.assertEqual(r.returncode, 1)

    def test_a_refused_build_leaves_the_record_alone(self):
        before = self.rec()
        self.save_proto([{"id": "VIA-home-1a2b3c4d", "variants": [variant(html="<script>x</script>"), variant("B")]}])
        self.assert_refused(self.build())
        self.assertEqual(self.rec(), before)


class Sanitizer(unittest.TestCase):
    def clean(self, source):
        return via_prototype.clean_fragment(source, "t")

    def refused(self, source):
        with self.assertRaises(via_prototype.Problem):
            self.clean(source)

    def test_allowed_markup_is_rewritten(self):
        out, used, _ = self.clean('<div class="card" style="gap: var(--space-4)" role="group"><p>Hi <b>there</b></p><br></div>')
        self.assertEqual(out, '<div class="card" style="gap: var(--space-4)" role="group"><p>Hi <b>there</b></p><br></div>')
        self.assertEqual(used, {"--space-4"})

    def test_text_is_escaped_and_entities_survive(self):
        out, _, _ = self.clean("<p>1 &lt; 2 &amp; 3</p>")
        self.assertEqual(out, "<p>1 &lt; 2 &amp; 3</p>")

    def test_attribute_quotes_cannot_break_out(self):
        out, _, _ = self.clean('<div title=\'a" onmouseover="x\'>t</div>')
        self.assertNotIn('" onmouseover', out)
        self.assertIn("&quot;", out)

    def test_refuses_dangerous_markup(self):
        for source in (
            "<script>alert(1)</script>", "<style>p{}</style>", "<iframe></iframe>", "<form></form>", "<input>",
            "<a href='https://x'>x</a>", "<img src='data:image/png;base64,AA'>", "<svg></svg>", "<object></object>",
            "<link rel=stylesheet>", "<base href='x'>", "<h1>Second title</h1>", "<h2>Second title</h2>", "<template></template>", "<textarea></textarea>",
            '<div onclick="x()">t</div>', '<div id="f1">t</div>', '<div data-x="1">t</div>', '<div srcdoc="x">t</div>',
            "<!-- hi --><div>t</div>", "<!DOCTYPE html>", "<![CDATA[x]]>", "<?php ?>",
            '<div class="vp-frame">t</div>', '<div class="a b!">t</div>',
        ):
            with self.subTest(source=source):
                self.refused(source)

    def test_refuses_nesting_a_browser_would_rebuild(self):
        # Chrome turns each of these into a different tree, so a closing tag could end the page's own wrappers.
        for source in (
            "<button><div><button></button></div>X</button>",
            "<li><div><li></li></div>X</li>",
            "<ul><li><div><li>a</li></div></li></ul>",
            "<p><div>x</div></p>", "<p><ul><li>x</li></ul></p>", "<p><h3>x</h3></p>", "<p><p>x</p></p>",
            "<h3><h4>x</h4></h3>",
            "<tr><td>x</td></tr>", "<td>x</td>", "<table><tr><td>x</td></tr></table>", "<table><div>x</div></table>",
            "<table><tbody><tr><div>x</div></tr></tbody></table>", "<table>text</table>",
            "<table><tbody><tr><td><td>x</td></td></tr></tbody></table>", "<table><tbody><tbody></tbody></tbody></table>",
        ):
            with self.subTest(source=source):
                self.refused(source)

    def test_allows_nesting_a_browser_keeps(self):
        for source in (
            "<ul><li>a<ul><li>b</li></ul></li></ul>", "<button><span>x</span></button>", "<p><b>x</b><br>y</p>",
            "<table> <thead><tr><th>h</th></tr></thead> <tbody><tr><td><div>x</div><table><tbody><tr><td>y</td></tr></tbody></table></td></tr></tbody> </table>",
            "<h3><span>x</span></h3>", "<li><div>x</div></li>".replace("<li>", "<ul><li>").replace("</li>", "</li></ul>"),
        ):
            with self.subTest(source=source):
                self.clean(source)

    def test_a_token_with_unbalanced_parentheses_is_not_put_in_the_page(self):
        tokens = {g: {} for g in via_prototype.via_tokens.GROUPS}
        tokens["spacing"] = {"--space-a": {"light": "calc(1px", "dark": "calc(1px"},
                             "--space-b": {"light": "calc(1px + 2px)", "dark": "calc(1px + 2px)"}}
        self.assertEqual(list(via_prototype.safe_tokens(tokens)["spacing"]), ["--space-b"])

    def test_refuses_dangerous_styles(self):
        for style in (
            "background: url(https://x/a.png)", "background: URL( data:x )", "background: image-set(x 1x)",
            "width: expression(alert(1))", "position: fixed", "position: sticky", "--x: 1",
            "color: red; position: fixed; width: 1px)", "width: calc(1px", "color: \\72 ed", "background: red /* c */", "@import 'x'", "behavior: x", "color: red; content: '<'",
        ):
            with self.subTest(style=style):
                self.refused('<div style="%s">t</div>' % style)

    def test_refuses_broken_nesting(self):
        for source in ("<div>", "</div>", "<div><p></div></p>", "<div></span>"):
            with self.subTest(source=source):
                self.refused(source)

    def test_position_absolute_is_allowed_because_the_frame_clips_it(self):
        self.clean('<div style="position: absolute; top: 0">t</div>')

    def test_disabled_attribute(self):
        out, _, _ = self.clean("<button disabled>x</button>")
        self.assertEqual(out, "<button disabled>x</button>")
        self.refused('<button disabled="javascript:1">x</button>')

    def test_oversized_fragment(self):
        self.refused("<p>" + "x" * 21_000 + "</p>")


if __name__ == "__main__":
    unittest.main()
