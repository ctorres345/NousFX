#!/usr/bin/env python3
"""Tests for via_assets.py. Run: python3 -B plugins/via/scripts/test_via_assets.py

One seam: each test runs the script's command line as a subprocess in a throwaway project and checks only
what a caller sees (exit codes, stdout JSON, files written). Convert tests need the Pillow environment that
`/via setup` creates; without it they skip and say so by name.
"""
import ast
import hashlib
import json
import os
import pwd
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nousfx_testhome as th  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "via_assets.py")
VIA_RUN = os.path.join(HERE, "via_run.py")
REAL_PILLOW = os.environ.get("VIA_TEST_PILLOW") or os.path.join(  # the override lets a test prove the loud skip
    pwd.getpwuid(os.getuid()).pw_dir, ".local", "share", "nousfx", "via", "venv", "bin", "python")
CHECK_MD = os.path.join(HERE, "..", "skills", "via", "modes", "check.md")


def pillow_python():
    """The real Pillow environment's interpreter when it works, else None."""
    if not os.path.isfile(REAL_PILLOW):
        return None
    r = subprocess.run([REAL_PILLOW, "-I", "-c", "import PIL"], capture_output=True)
    return REAL_PILLOW if r.returncode == 0 else None


PILLOW = pillow_python()
UMASK = os.umask(0)
os.umask(UMASK)
NO_PILLOW = "SKIPPED convert tests: no working Pillow environment at %s (run /via setup)" % REAL_PILLOW
if not PILLOW:
    print(NO_PILLOW, file=sys.stderr)


def png(width, height, rgb=(26, 115, 232)):
    """A solid PNG built with the standard library."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


def sizes(paths):
    """Image sizes read by Pillow: an oracle independent of the script's own header parser."""
    code = "import json,sys;from PIL import Image;print(json.dumps([Image.open(p).size for p in sys.argv[1:]]))"
    r = subprocess.run([PILLOW, "-I", "-c", code] + list(paths), capture_output=True, text=True, check=True)
    return [tuple(s) for s in json.loads(r.stdout)]


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<rect x="2" y="2" width="20" height="20" rx="4" fill="#1A73E8"/>'
        '<circle cx="12" cy="12" r="5" fill="#fff"/>'
        '<g stroke="#202124" stroke-width="2" fill="none" stroke-linecap="round">'
        '<line x1="4" y1="20" x2="20" y2="20"/><polygon points="12,3 15,8 9,8"/></g>'
        '<ellipse cx="12" cy="18" rx="3" ry="1" fill="#202124" fill-opacity="0.5"/>'
        '<path d="M 6 6 L 8 6\n L 8 8 Z" fill="#1a73e8" fill-rule="evenodd"/></svg>')

GOLDEN = """<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp"
    android:height="24dp"
    android:viewportWidth="24"
    android:viewportHeight="24">
    <path
        android:fillColor="#1A73E8"
        android:pathData="M6,2 H18 A4,4 0 0 1 22,6 V18 A4,4 0 0 1 18,22 H6 A4,4 0 0 1 2,18 V6 A4,4 0 0 1 6,2 Z"/>
    <path
        android:fillColor="#FFFFFF"
        android:pathData="M7,12 A5,5 0 1,0 17,12 A5,5 0 1,0 7,12 Z"/>
    <path
        android:strokeColor="#202124"
        android:strokeWidth="2"
        android:strokeLineCap="round"
        android:pathData="M4,20 L20,20"/>
    <path
        android:strokeColor="#202124"
        android:strokeWidth="2"
        android:strokeLineCap="round"
        android:pathData="M12,3 L15,8 L9,8 Z"/>
    <path
        android:fillColor="#80202124"
        android:pathData="M9,18 A3,1 0 1,0 15,18 A3,1 0 1,0 9,18 Z"/>
    <path
        android:fillColor="#1A73E8"
        android:fillType="evenOdd"
        android:pathData="M 6 6 L 8 6 L 8 8 Z"/>
</vector>
"""

SPEC_HEAD = 'palette = ["#1A73E8", "#FFFFFF", "#202124"]\n'


def asset(name, kind, source, platforms, size, usage="test"):
    return ('\n[[asset]]\nname = "%s"\nkind = "%s"\nsource = "%s"\nplatforms = %s\nsize = "%s"\nusage = "%s"\n'
            % (name, kind, source, json.dumps(platforms), size, usage))


class AssetsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.project = os.path.join(self.root, "proj")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.project)
        os.makedirs(self.home)
        self.config(PILLOW)

    def config(self, python):
        path = os.path.join(self.home, ".config", "nousfx", "via.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"version": 1, "assets": {"python": python}}, f)

    def write(self, rel, data):
        path = os.path.join(self.project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if isinstance(data, bytes) else "w") as f:
            f.write(data)
        return path

    def spec(self, *assets, head=SPEC_HEAD):
        self.write("design/assets.toml", head + "".join(assets))

    def run_cli(self, *args, script=SCRIPT):
        r = subprocess.run([th.python(self.home), "-I", "-B", script] + list(args) + ["--project", self.project],
                           capture_output=True, text=True, env=th.env(self.home), timeout=120)
        self.assertNotIn("Traceback", r.stderr)
        return r

    def ok(self, *args):
        r = self.run_cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def rules(self, report):
        return sorted((f["rule"], f["location"]["file"]) for f in report["findings"])

    def tree(self):
        out = {}
        for folder, _, files in os.walk(self.project):
            for name in files:
                path = os.path.join(folder, name)
                out[os.path.relpath(path, self.project)] = digest(path)
        return out

    # --- the ticket's guarantees about this suite and the docs ---

    def test_convert_tests_skip_loudly_without_pillow(self):
        env = dict(os.environ, VIA_TEST_PILLOW=os.path.join(self.root, "no-python"))
        r = subprocess.run([sys.executable, "-B", os.path.abspath(__file__),
                            "AssetsTest.test_convert_flags_upscaling"], capture_output=True, text=True, env=env,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SKIPPED convert tests", r.stderr)
        self.assertIn("skipped=1", r.stderr)

    def test_suite_reaches_the_script_only_through_its_command_line(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imported = {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
        imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("via_assets", imported)

    def test_check_mode_doc_runs_the_audit(self):
        with open(CHECK_MD, encoding="utf-8") as f:
            self.assertIn('scripts/via_assets.py" audit', f.read())

    # --- the spec file ---

    def test_missing_spec_stops_with_a_clear_message(self):
        r = self.run_cli("audit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("design/assets.toml is missing", r.stderr)

    def test_malformed_toml_names_the_line(self):
        self.write("design/assets.toml", SPEC_HEAD + "\n[[asset]]\nname = oops\n")
        r = self.run_cli("audit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 4", r.stderr)

    def test_a_bad_asset_entry_names_its_line(self):
        self.spec(asset("home_logo_main", "svg", "design/a.svg", ["ios"], "24x24"))
        r = self.run_cli("audit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 3", r.stderr)
        self.assertIn("platforms", r.stderr)

    def test_a_commented_asset_header_still_names_its_line(self):
        self.spec(asset("home_logo_main", "svg", "design/a.svg", ["ios"], "24x24").replace("[[asset]]",
                                                                                         "[[ asset ]]  # logo"))
        r = self.run_cli("audit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("line 3", r.stderr)

    def test_a_source_outside_the_project_is_refused(self):
        self.spec(asset("home_logo_main", "svg", "../secret.svg", ["web"], "24x24"))
        r = self.run_cli("audit")
        self.assertEqual(r.returncode, 1)
        self.assertIn("inside the project", r.stderr)

    # --- audit ---

    def test_audit_flags_missing_targets_and_sources(self):
        self.spec(asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["android", "web"], "24x24"),
                  asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["flutter"], "100x50"))
        self.write("design/src/home_logo_main.svg", ICON)
        rep = self.ok("audit")
        self.assertEqual(self.rules(rep), [
            ("missing", "app/src/main/res/drawable/home_logo_main.xml"),
            ("missing", "assets/images/2.0x/home_hero_wide.png"),
            ("missing", "assets/images/3.0x/home_hero_wide.png"),
            ("missing", "assets/images/home_hero_wide.png"),
            ("missing", "public/images/home_logo_main.svg"),
            ("missing-source", "design/src/home_hero_wide.png")])

    def test_audit_findings_follow_the_convention(self):
        self.spec(asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["web"], "24x24"))
        rep = self.ok("audit")
        f = rep["findings"][0]
        self.assertEqual((f["screen"], f["source"], f["severity"]), ("assets", "via-assets", "high"))
        self.assertTrue(f["issue"] and f["fix"] and f["rule"])

    def test_audit_flags_wrong_size_extra_and_upscale(self):
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["flutter"], "100x50"),
                  head=SPEC_HEAD + 'ignore = ["keep_*"]\n[paths]\nflutter = "lib/img"\n')
        self.write("design/src/home_hero_wide.png", png(200, 100))  # 3x needs 300x150: upscaled
        self.write("lib/img/home_hero_wide.png", png(100, 50))
        self.write("lib/img/2.0x/home_hero_wide.png", png(150, 50))  # wrong
        self.write("lib/img/3.0x/home_hero_wide.png", png(300, 150))
        self.write("lib/img/old_thing_unused.png", png(4, 4))  # extra
        self.write("lib/img/keep_me_too.png", png(4, 4))  # ignored
        self.write("lib/img/notes.txt", "not an asset")
        rep = self.ok("audit")
        self.assertEqual(self.rules(rep), [
            ("extra", "lib/img/old_thing_unused.png"),
            ("upscale", "design/src/home_hero_wide.png"),
            ("wrong-size", "lib/img/2.0x/home_hero_wide.png")])

    @unittest.skipUnless(PILLOW, NO_PILLOW)
    def test_audit_reads_jpeg_and_webp_sources(self):
        for fmt, name in (("JPEG", "home_hero_jpg"), ("WEBP", "home_hero_webp")):
            src = os.path.join(self.project, "design", "src", name + "." + fmt.lower())
            os.makedirs(os.path.dirname(src), exist_ok=True)
            subprocess.run([PILLOW, "-I", "-c", "import sys;from PIL import Image;Image.new('RGB',(64,32)).save(sys.argv[1],sys.argv[2])",
                            src, fmt], check=True)
        self.spec(asset("home_hero_jpg", "image", "design/src/home_hero_jpg.jpeg", ["web"], "64x32"),
                  asset("home_hero_webp", "image", "design/src/home_hero_webp.webp", ["web"], "64x32"))
        rules = {r for r, _ in self.rules(self.ok("audit"))}
        self.assertIn("upscale", rules)  # 2x of 64x32 is 128x64: the headers were read
        self.assertNotIn("missing-source", rules)

    def test_audit_flags_the_naming_rule_without_renaming(self):
        self.spec(asset("HomeLogo", "svg", "design/src/HomeLogo.svg", ["web"], "24x24"))
        self.write("design/src/HomeLogo.svg", ICON)
        rep = self.ok("audit")
        self.assertEqual(self.rules(rep), [("naming", "design/src/HomeLogo.svg")])
        self.assertTrue(os.path.isfile(os.path.join(self.project, "design/src/HomeLogo.svg")))

    def test_audit_svg_rules(self):
        cases = {
            "home_noview_main": ('<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4" fill="#fff"/></svg>',
                                 "svg-viewbox"),
            "home_script_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><script>alert(1)</script></svg>',
                                 "svg-script"),
            "home_onload_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4" onload="x()"></svg>',
                                 "svg-script"),
            "home_image_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><image href="a.png"/></svg>',
                                "svg-embedded-image"),
            "home_data_main": ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                               'viewBox="0 0 4 4"><rect xlink:href="data:text/html,x" width="4" height="4" fill="#fff"/></svg>', "svg-embedded-image"),
            "home_link_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect href="https://x.test/a" '
                               'width="4" height="4" fill="#fff"/></svg>', "svg-embedded-image"),
            "home_url_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4" '
                              'style="fill:#fff;filter:url( https://x.test/f)"/></svg>', "svg-embedded-image"),
            "home_sheet_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><style>@import "x.css";</style>'
                                '</svg>', "svg-embedded-image"),
            "home_color_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4" '
                                'style="fill:#ff0000"/></svg>', "svg-color"),
            "home_default_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4"/></svg>',
                                  "svg-color"),
            "home_gradient_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><defs><linearGradient id="g"/>'
                                   '</defs><rect width="4" height="4" fill="url(#g)"/></svg>', "svg-unsupported"),
            "home_text_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><text fill="#fff">A</text></svg>',
                               "svg-unsupported"),
            "home_doctype_main": ('<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "y">]><svg viewBox="0 0 4 4"/>',
                                  "svg-doctype"),
            "home_broken_main": ('<svg viewBox="0 0 4 4"><rect></svg>', "svg-parse"),
            "home_escape_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4" '
                                 'style="fill:#fff;filter:\\75 rl(https://x.test/f)"/></svg>', "svg-embedded-image"),
            "home_imageset_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4" '
                                   'fill="#fff" cursor="image-set(&quot;https://x.test/c&quot; 1x)"/></svg>',
                                   "svg-embedded-image"),
            "home_xhtml_main": ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:h="http://www.w3.org/1999/xhtml" '
                                'viewBox="0 0 4 4"><h:img src="https://x.test/i.png"/></svg>',
                                "svg-embedded-image"),
            "home_meta_main": ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:h="http://www.w3.org/1999/xhtml" '
                               'viewBox="0 0 4 4"><metadata><h:script>x()</h:script></metadata></svg>',
                               {"svg-script", "svg-embedded-image"}),
            "home_metastyle_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><metadata><style>'
                                    '@import url(https://x.test/a.css);</style></metadata></svg>', "svg-embedded-image"),
            "home_metaset_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><desc><set href="#r" '
                                  'attributeName="fill" to="url(https://x.test/p.svg#p)"/></desc></svg>',
                                  {"svg-script", "svg-embedded-image"}),
            "home_pi_main": ('<?xml version="1.0"?><?xml-stylesheet type="text/css" href="https://x.test/x.css"?>'
                             '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"/>', "svg-script"),
            "home_set_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><a><set attributeName="href" '
                              'to="javascript:alert(1)"/><rect width="4" height="4" fill="#fff"/></a></svg>', "svg-script"),
            "home_inject_main": ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><path fill="#fff" '
                                 'd="M0 0&quot;/&gt;&lt;x a=&quot;"/></svg>', "svg-parse"),
        }
        for name, (text, rule) in cases.items():
            with self.subTest(name):
                self.spec(asset(name, "svg", "design/src/%s.svg" % name, ["android"], "24x24"))
                self.write("design/src/%s.svg" % name, text)
                got = {r for r, f in self.rules(self.ok("audit")) if f.startswith("design/")}
                self.assertEqual(got, rule if isinstance(rule, set) else {rule})

    def test_currentcolor_and_none_are_allowed_and_unsupported_is_android_only(self):
        text = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><text fill="currentColor">A</text>'
                '<rect width="4" height="4" fill="none" stroke="currentColor"/></svg>')
        self.spec(asset("home_mark_main", "svg", "design/src/home_mark_main.svg", ["web"], "vector"))
        self.write("design/src/home_mark_main.svg", text)
        self.write("public/images/home_mark_main.svg", text)
        self.assertEqual(self.ok("audit")["findings"], [])

    def test_a_missing_palette_is_reported_not_passed(self):
        self.spec(asset("home_red_main", "svg", "design/src/home_red_main.svg", ["web"], "24x24"), head="")
        red = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4" fill="red"/></svg>'
        self.write("design/src/home_red_main.svg", red)
        self.write("public/images/home_red_main.svg", red)
        self.assertEqual(self.rules(self.ok("audit")), [("palette-missing", "design/assets.toml")])

    def test_audit_runs_without_pillow_and_writes_nothing(self):
        self.config(None)
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["android"], "10x10"))
        self.write("design/src/home_hero_wide.png", png(40, 40))
        before = self.tree()
        rep = self.ok("audit")
        self.assertEqual(len(rep["findings"]), 5)  # five density folders missing
        self.assertEqual(self.tree(), before)

    def test_audit_output_passes_the_findings_merge(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        self.spec(asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["android"], "24x24"),
                  asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["web"], "10x10"))
        self.write("design/src/home_logo_main.svg", ICON.replace("#fff", "#ff0000"))
        findings = self.ok("audit")["findings"]
        self.assertGreaterEqual(len(findings), 3)
        r = self.run_cli("init", script=VIA_RUN)
        self.assertEqual(r.returncode, 0, r.stderr)
        run_dir = json.loads(r.stdout)["run_dir"]
        with open(os.path.join(run_dir, "findings.json"), "w") as f:
            json.dump({"version": 1, "mode": "check", "scope": ["assets"], "platforms": ["android", "web"],
                       "findings": findings}, f)
        r = self.run_cli("merge", run_dir, script=VIA_RUN)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.project, "docs", "design", "via.md")) as f:
            self.assertIn("VIA-assets-", f.read())

    # --- convert ---

    @unittest.skipUnless(PILLOW, NO_PILLOW)
    def test_convert_writes_every_platform_size_and_leaves_sources(self):
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["android", "flutter", "web"], "40x20"))
        src = self.write("design/src/home_hero_wide.png", png(400, 200))
        before = digest(src)
        rep = self.ok("convert")
        self.assertEqual(rep["flags"], [])
        want = {"app/src/main/res/drawable-%s/home_hero_wide.png" % d: sz for d, sz in (
            ("mdpi", (40, 20)), ("hdpi", (60, 30)), ("xhdpi", (80, 40)), ("xxhdpi", (120, 60)), ("xxxhdpi", (160, 80)))}
        want.update({"assets/images/home_hero_wide.png": (40, 20), "assets/images/2.0x/home_hero_wide.png": (80, 40),
                     "assets/images/3.0x/home_hero_wide.png": (120, 60),
                     "public/images/home_hero_wide.webp": (40, 20), "public/images/home_hero_wide@2x.webp": (80, 40)})
        got = sizes(os.path.join(self.project, p) for p in want)
        self.assertEqual(dict(zip(want, got)), want)
        self.assertEqual(sorted(rep["written"]), sorted(want))
        mode = os.stat(os.path.join(self.project, "public/images/home_hero_wide.webp")).st_mode & 0o777
        self.assertEqual(mode, 0o666 & ~UMASK)  # not mkstemp's private 0600
        self.assertEqual(digest(src), before)
        self.assertEqual(self.ok("audit")["findings"], [])  # convert's output passes its own audit

    def test_convert_dry_run_writes_nothing(self):  # dry-run never starts Pillow
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["android"], "40x20"),
                  asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["android", "web"], "24x24"))
        self.write("design/src/home_hero_wide.png", png(400, 200))
        self.write("design/src/home_logo_main.svg", ICON)
        before = self.tree()
        rep = self.ok("convert", "--dry-run")
        self.assertTrue(rep["dry_run"])
        self.assertEqual(len(rep["would_write"]), 7)
        self.assertEqual(self.tree(), before)

    def test_a_zero_height_image_is_unreadable_not_a_crash(self):
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["web"], "100x50"))
        self.write("design/src/home_hero_wide.png", png(400, 200)[:16] + struct.pack(">II", 400, 0) + png(400, 200)[24:])
        self.assertIn("missing-source", {r for r, _ in self.rules(self.ok("audit"))})

    @unittest.skipUnless(PILLOW, NO_PILLOW)
    def test_a_rotated_jpeg_is_measured_and_exported_upright(self):
        xmp = '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:Description tiff:Orientation="%d"/></x:xmpmeta>'
        cases = {"exif": "ex[0x0112] = 6; kw = {'exif': ex}",
                 "exif_then_xmp": "ex[0x0112] = 6; kw = {'exif': ex, 'xmp': %r}" % (xmp % 1).encode(),
                 "xmp_only": "kw = {'xmp': %r}" % (xmp % 6).encode()}
        for case, setup in cases.items():
            with self.subTest(case):
                src = os.path.join(self.project, "design/src/home_photo_main.jpg")
                os.makedirs(os.path.dirname(src), exist_ok=True)
                subprocess.run([PILLOW, "-I", "-c", "import sys; from PIL import Image; im = Image.new('RGB', (200, 100)); "
                                "ex = Image.Exif(); %s; im.save(sys.argv[1], **kw)" % setup, src], check=True)
                self.spec(asset("home_photo_main", "image", "design/src/home_photo_main.jpg", ["web"], "50x100"))
                shutil.rmtree(os.path.join(self.project, "public"), ignore_errors=True)
                self.assertEqual({f["rule"] for f in self.ok("audit")["findings"]}, {"missing"})  # no aspect/upscale
                self.config(PILLOW)
                self.assertEqual(self.ok("convert")["flags"], [])
                self.assertEqual(self.ok("audit")["findings"], [])

    def test_a_nested_svg_is_unsupported_on_android(self):
        text = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><svg x="2" viewBox="0 0 2 2">'
                '<rect width="2" height="2" fill="#fff"/></svg></svg>')
        self.spec(asset("home_inner_main", "svg", "design/src/home_inner_main.svg", ["android"], "24x24"))
        self.write("design/src/home_inner_main.svg", text)
        self.assertIn("svg-unsupported", {r for r, _ in self.rules(self.ok("audit"))})

    def test_a_stroked_line_has_no_default_fill(self):
        text = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><line x1="0" y1="0" x2="4" y2="4" '
                'stroke="#ffffff"/></svg>')
        self.spec(asset("home_rule_main", "svg", "design/src/home_rule_main.svg", ["web"], "vector"))
        self.write("design/src/home_rule_main.svg", text)
        self.write("public/images/home_rule_main.svg", text)
        self.assertEqual(self.ok("audit")["findings"], [])

    def test_a_different_aspect_ratio_is_flagged_as_a_crop(self):
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["web"], "100x50"))
        self.write("design/src/home_hero_wide.png", png(400, 400))
        self.assertIn("aspect", {r for r, _ in self.rules(self.ok("audit"))})
        self.config(None)
        rep = self.ok("convert", "--dry-run")
        self.assertEqual([f["issue"].split(":")[0] for f in rep["flags"]], ["cropped"])

    @unittest.skipUnless(PILLOW, NO_PILLOW)
    def test_convert_flags_upscaling(self):
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["web"], "100x100"))
        self.write("design/src/home_hero_wide.png", png(150, 150))
        rep = self.ok("convert")
        self.assertEqual(len(rep["flags"]), 1)
        self.assertIn("upscaled", rep["flags"][0]["issue"])
        self.assertIn("home_hero_wide@2x.webp", rep["flags"][0]["issue"])

    def test_svg_to_vectordrawable_matches_the_golden_file(self):
        self.spec(asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["android", "flutter"], "24x24"))
        src = self.write("design/src/home_logo_main.svg", ICON)
        self.config(None)  # SVG work needs no Pillow
        rep = self.ok("convert")
        self.assertEqual(rep["flags"], [])
        with open(os.path.join(self.project, "app/src/main/res/drawable/home_logo_main.xml")) as f:
            self.assertEqual(f.read(), GOLDEN)
        self.assertEqual(digest(os.path.join(self.project, "assets/images/home_logo_main.svg")), digest(src))
        self.assertEqual(self.ok("audit")["findings"], [])

    def test_vector_size_comes_from_the_viewbox_and_its_origin_is_moved(self):
        self.spec(asset("home_dot_main", "svg", "design/src/home_dot_main.svg", ["android"], "vector"))
        self.write("design/src/home_dot_main.svg", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="2 2 48 32">'
                                                   '<circle cx="10" cy="10" r="2" fill="#fff"/></svg>')
        self.config(None)
        self.ok("convert")
        with open(os.path.join(self.project, "app/src/main/res/drawable/home_dot_main.xml")) as f:
            text = f.read()
        self.assertIn('android:width="48dp"', text)
        self.assertIn('android:viewportHeight="32"', text)
        self.assertIn('<group android:translateX="-2" android:translateY="-2">', text)

    def test_unsupported_svg_gets_no_vectordrawable_but_a_flag(self):
        self.spec(asset("home_grad_main", "svg", "design/src/home_grad_main.svg", ["android", "web"], "24x24"))
        self.write("design/src/home_grad_main.svg",
                   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><g transform="scale(2)">'
                   '<rect width="2" height="2" fill="#fff"/></g></svg>')
        self.config(None)
        rep = self.ok("convert")
        self.assertEqual(len(rep["flags"]), 1)
        self.assertIn("no VectorDrawable", rep["flags"][0]["issue"])
        self.assertFalse(os.path.exists(os.path.join(self.project, "app/src/main/res/drawable/home_grad_main.xml")))
        self.assertTrue(os.path.isfile(os.path.join(self.project, "public/images/home_grad_main.svg")))
        rules = {r for r, _ in self.rules(self.ok("audit"))}
        self.assertEqual(rules, {"svg-unsupported", "missing"})

    def test_inkscape_metadata_is_not_drawing(self):
        text = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" '
                'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 4 4">'
                '<sodipodi:namedview pagecolor="#ffffff"><inkscape:page x="0" y="0"/></sodipodi:namedview>'
                '<metadata><rdf:RDF><rdf:Description/></rdf:RDF></metadata>'
                '<rect width="4" height="4" fill="#fff"/></svg>')
        self.spec(asset("home_ink_main", "svg", "design/src/home_ink_main.svg", ["android"], "24x24"))
        self.write("design/src/home_ink_main.svg", text)
        self.config(None)
        self.assertEqual(self.ok("convert")["flags"], [])
        self.assertEqual(self.ok("audit")["findings"], [])

    def test_a_copy_without_its_source_is_missing_source_not_a_crash(self):
        self.spec(asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["web"], "vector"))
        self.write("public/images/home_logo_main.svg", ICON)
        self.assertEqual({r for r, _ in self.rules(self.ok("audit"))}, {"missing-source"})

    def test_an_edited_svg_copy_is_stale(self):
        self.spec(asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["web"], "vector"))
        self.write("design/src/home_logo_main.svg", ICON)
        self.write("public/images/home_logo_main.svg", ICON.replace("</svg>", "<script>x()</script></svg>"))
        self.assertEqual({r for r, _ in self.rules(self.ok("audit"))}, {"stale"})

    def test_a_utf16_svg_is_refused_and_never_copied(self):
        text = ('<?xml version="1.0" encoding="UTF-16"?><?xml-stylesheet type="text/css" href="https://x.test/x.css"?>'
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"/>')
        self.spec(asset("home_wide_main", "svg", "design/src/home_wide_main.svg", ["web"], "24x24"))
        self.write("design/src/home_wide_main.svg", text.encode("utf-16"))
        self.assertIn("svg-parse", {r for r, _ in self.rules(self.ok("audit"))})
        self.config(None)
        self.assertIn("not converted", self.ok("convert")["flags"][0]["issue"])
        self.assertFalse(os.path.exists(os.path.join(self.project, "public/images/home_wide_main.svg")))

    def test_unsafe_svg_is_never_copied(self):
        self.spec(asset("home_bad_main", "svg", "design/src/home_bad_main.svg", ["web"], "24x24"))
        self.write("design/src/home_bad_main.svg",
                   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><script>x()</script></svg>')
        self.config(None)
        rep = self.ok("convert")
        self.assertIn("not converted", rep["flags"][0]["issue"])
        self.assertFalse(os.path.exists(os.path.join(self.project, "public/images/home_bad_main.svg")))

    def test_convert_skips_a_bad_name_and_never_renames(self):
        self.spec(asset("Home-Logo", "svg", "design/src/x.svg", ["web"], "24x24"))
        self.write("design/src/x.svg", ICON)
        rep = self.ok("convert")
        self.assertIn("snake_case", rep["flags"][0]["issue"])
        self.assertFalse(os.path.isdir(os.path.join(self.project, "public")))

    def test_convert_without_pillow_stops_with_the_setup_fix(self):
        self.config(None)
        self.spec(asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["web"], "10x10"))
        self.write("design/src/home_hero_wide.png", png(40, 40))
        before = self.tree()
        r = self.run_cli("convert")
        self.assertEqual(r.returncode, 1)
        self.assertIn("/via setup", r.stderr)
        self.assertEqual(self.tree(), before)

    def test_only_limits_convert_and_rejects_unknown_names(self):
        self.config(None)
        self.spec(asset("home_logo_main", "svg", "design/src/home_logo_main.svg", ["web"], "24x24"),
                  asset("home_hero_wide", "image", "design/src/home_hero_wide.png", ["web"], "10x10"))
        self.write("design/src/home_logo_main.svg", ICON)
        rep = self.ok("convert", "--only", "home_logo_main")
        self.assertEqual(rep["written"], ["public/images/home_logo_main.svg"])
        r = self.run_cli("convert", "--only", "nope_nope_nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("nope_nope_nope", r.stderr)


if __name__ == "__main__":
    unittest.main()
