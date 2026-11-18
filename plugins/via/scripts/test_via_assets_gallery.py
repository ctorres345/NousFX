#!/usr/bin/env python3
"""The /via assets review gallery (NFX-163). Run: python3 -B plugins/via/scripts/test_via_assets_gallery.py

Tests reach via_assets_review.py only through its command line, in a throwaway home and git project. The image
approve test needs the real Pillow environment from /via setup and skips loudly without it; SVG work needs none.
The Argon test uses a stub agy, as test_via_second_opinion.py does.
"""
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import struct
import zlib

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import nousfx_testhome as th  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "via_assets_review.py")
VIA = os.path.join(HERE, "..", "skills", "via")
KEY = "github.com/acme/app"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#1A73E8"/></svg>'
SVG2 = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect width="20" height="20" fill="#1A73E8"/></svg>'
TEXT_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><text x="2" y="20" fill="#1A73E8">A</text>'
            b'</svg>')
BAD_SVG =b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script>alert(1)</script></svg>'
JPEG = bytes.fromhex(  # a 2x2 baseline JPEG
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e"
    "1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080002000201011100ffc4001f0000010501"
    "010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131"
    "410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a"
    "535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6"
    "b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd3ff"
    "d9")


def png(w=2, h=2):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


AGY = """#!/bin/sh
ls > "$HOME/agy-files.log"
echo '{"conversation_id": "abcdef12-3456", "status": "SUCCESS", "response": "NO FINDINGS", "usage": {}}'
"""
SPEC = '''palette = ["#1A73E8", "#FFFFFF"]

[[asset]]
name = "home_logo_main"
kind = "svg"
source = "design/assets/home_logo_main.svg"
platforms = ["android", "web"]
size = "24x24"
usage = "App bar logo <b>"

[[asset]]
name = "home_hero_main"
kind = "image"
source = "design/assets/home_hero_main.png"
platforms = ["android", "web"]
size = "2x2"
usage = "Empty state"
'''
REAL_PILLOW = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".local", "share", "nousfx", "via", "venv", "bin",
                           "python")
PILLOW = REAL_PILLOW if os.path.isfile(REAL_PILLOW) and subprocess.run(
    [REAL_PILLOW, "-I", "-c", "import PIL"], capture_output=True).returncode == 0 else None
if not PILLOW:
    print("SKIPPED the image approve test: no working Pillow environment at %s (run /via setup)" % REAL_PILLOW,
          file=sys.stderr)


class GalleryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.home = os.path.join(self.root, "home")
        self.project = os.path.join(self.root, "app")
        for d in (self.home, self.project):
            os.makedirs(d)
        self.git("init", "-q")
        self.git("remote", "add", "origin", "git@github.com:%s.git" % KEY)
        self.write(os.path.join(self.project, "design", "assets.toml"), SPEC.encode())
        self.config = os.path.join(self.home, ".config", "nousfx", "via.json")
        os.makedirs(os.path.dirname(self.config))
        with open(self.config, "w") as f:
            json.dump({"version": 1, "assets": {"python": PILLOW},
                       "outbound_allowlist": {KEY: {"destinations": ["agy"]}}}, f)
        self.raw = os.path.join(self.home, ".local", "share", "nousfx", "via", "app", "APP-7")

    def git(self, *args):
        return subprocess.run(["git", "-C", self.project, *args], check=True, capture_output=True, text=True).stdout

    def write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def raw_file(self, name, data):
        return self.write(os.path.join(self.raw, name), data)

    def run_cli(self, *args, env=None):
        return subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, args[0], "--project", self.project,
                               "--key", "APP-7", *args[1:]], capture_output=True, text=True,
                              env=th.env(self.home, **(env or {})), timeout=120)

    def ok(self, *args, env=None):
        r = self.run_cli(*args, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def refused(self, *args, code=1, env=None):
        r = self.run_cli(*args, env=env)
        self.assertEqual(r.returncode, code, (r.stdout, r.stderr))
        self.assertIn("Stopped:", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return r.stderr

    def review(self, *parts):
        return os.path.join(self.project, "design", "asset-review", "APP-7", *parts)

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def add_logo(self, data=SVG, name="logo.svg"):
        return self.ok("add", "--name", "home_logo_main", "--file", self.raw_file(name, data),
                       "--conversation-id", "abcdef12")

    # --- the page and the state file ---

    def test_an_empty_gallery_has_its_empty_state_and_ignores_itself_in_git(self):
        out = self.ok("status")
        self.assertEqual(out["counts"], {"pending": 0, "approved": 0, "rejected": 0})
        page = self.read(self.review("gallery.html"))
        self.assertIn("No assets yet", page)
        self.assertEqual(self.read(self.review(".gitignore")).splitlines()[-1], "*")
        self.assertEqual(self.git("status", "--porcelain", "-uall", "design/asset-review"), "")

    def test_add_makes_a_pending_card_with_the_spec_row_escaped(self):
        out = self.add_logo()
        self.assertEqual(out["assets"]["home_logo_main"], {"kind": "svg", "state": "pending", "note": None,
                                                           "attempt": 1})
        with open(self.review("home_logo_main-1.svg"), "rb") as f:
            self.assertEqual(f.read(), SVG)
        page = self.read(self.review("gallery.html"))
        self.assertIn('src="home_logo_main-1.svg"', page)
        self.assertIn("App bar logo &lt;b&gt;", page)
        self.assertNotIn("<b>", page)
        self.assertIn("svg · android, web · 24x24", page)
        self.assertIn('data-copy="approve home_logo_main"', page)
        self.assertIn("default-src 'none'", page)  # no network from the page
        self.assertIsNone(re.search(r"(src|href)=\"(https?:)?//", page))
        log = self.read(os.path.join(self.project, "design", "asset-log", "APP-7.md"))
        self.assertIn("`home_logo_main` attempt 1 (svg), agy conversation abcdef12: pending.", log)

    # --- serve: the gallery over 127.0.0.1, for a browser pane that snapshots file:// pages ---

    CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"

    def get(self, url):
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, dict(err.headers), b""

    def test_serve_loads_the_gallery_and_its_images_with_the_csp_unchanged(self):
        self.add_logo()
        self.write(self.review("notes.txt"), b"x")
        self.write(self.review("other.png"), b"x")  # a stray image, not a candidate
        self.write(self.review("sub", "gallery.html"), b"<p>sub</p>")
        self.write(os.path.join(self.project, "design", "asset-review", "APP-8", "x-1.svg"), SVG)
        os.symlink(self.review("home_logo_main-1.svg"), self.review("link.svg"))
        p = subprocess.Popen([th.python(self.home), "-I", "-B", SCRIPT, "serve", "--project", self.project, "--key",
                              "APP-7", "--minutes", "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env=th.env(self.home))
        try:
            first = json.loads(p.stdout.readline())
            self.assertEqual(first["pid"], p.pid)
            url = first["url"]
            self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/gallery\.html$")
            status, headers, page = self.get(url)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Security-Policy"], self.CSP)  # the same policy the page carries
            self.assertIn(('content="%s"' % self.CSP).encode(), page)
            src = re.search(rb'<img src="([^"]+)"', page).group(1).decode()
            status, headers, data = self.get(url.rsplit("/", 1)[0] + "/" + src)  # the way the page loads it
            self.assertEqual((status, headers["Content-Type"]), (200, "image/svg+xml"))
            self.assertEqual(data, SVG)
            self.assertNotIn("script-src", headers["Content-Security-Policy"])  # an SVG opened alone runs nothing
            root = url.rsplit("/", 1)[0]
            idle = __import__("socket").create_connection(("127.0.0.1", int(url.split(":")[2].split("/")[0])))
            try:  # a browser's idle preconnect must not hold the server
                self.assertEqual(self.get(url)[0], 200)
            finally:
                idle.close()
            for path in ("/state.json", "/notes.txt", "/other.png", "/.gitignore", "/link.svg", "/sub/gallery.html",
                         "/../APP-8/x-1.svg", "/%2e%2e/APP-8/x-1.svg", "/..%2fAPP-8%2fx-1.svg",
                         "/..\\APP-8\\x-1.svg"):
                self.assertEqual(self.get(root + path)[0], 404, path)
            import urllib.request
            rebound = urllib.request.Request(url, headers={"Host": "evil.example:80"})
            self.assertEqual(self.get(rebound)[0], 404)  # a DNS-rebinding Host gets nothing
        finally:
            p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()

    def test_serve_refuses_without_a_gallery_or_with_a_bad_port(self):
        self.assertIn("no gallery", self.refused("serve"))
        self.add_logo()
        self.assertIn("--port must be", self.refused("serve", "--port", "70000"))
        self.assertIn("--port must be", self.refused("serve", "--port", "80"))  # Host would lack the port

    def test_the_gallery_csp_is_unchanged(self):
        self.add_logo()
        self.assertIn('content="%s"' % self.CSP, self.read(self.review("gallery.html")))

    def test_reject_needs_a_note_and_regenerate_changes_only_that_asset(self):
        self.add_logo()
        self.ok("add", "--name", "home_hero_main", "--file", self.raw_file("hero.jpg", JPEG))
        self.refused("reject", "--name", "home_logo_main", "--note", "  ")
        out = self.ok("reject", "--name", "home_logo_main", "--note", "Make it square")
        self.assertEqual(out["assets"]["home_logo_main"]["state"], "rejected")
        self.assertIn("Make it square", self.read(self.review("gallery.html")))
        out = self.ok("add", "--name", "home_logo_main", "--file", self.raw_file("logo2.svg", SVG2))
        self.assertEqual(out["assets"]["home_logo_main"], {"kind": "svg", "state": "pending", "note": None,
                                                           "attempt": 2})
        self.assertEqual(out["assets"]["home_hero_main"]["attempt"], 1)  # the other asset is untouched
        state = json.loads(self.read(self.review("state.json")))
        self.assertEqual(state["assets"]["home_logo_main"]["history"][0]["note"], "Make it square")
        self.assertTrue(os.path.exists(self.review("home_logo_main-1.svg")))  # the old candidate stays for the record
        log = self.read(os.path.join(self.project, "design", "asset-log", "APP-7.md"))
        self.assertIn("attempt 1: rejected. Note: Make it square", log)

    def test_approve_copies_into_the_platform_folders_and_commits_nothing(self):
        self.add_logo()
        head = subprocess.run(["git", "-C", self.project, "rev-parse", "-q", "--verify", "HEAD"],
                              capture_output=True).returncode
        out = self.ok("approve", "--name", "home_logo_main")
        self.assertEqual(out["assets"]["home_logo_main"]["state"], "approved")
        self.assertEqual(out["source"], "design/assets/home_logo_main.svg")
        self.assertEqual(sorted(out["written"]), ["app/src/main/res/drawable/home_logo_main.xml",
                                                  "public/images/home_logo_main.svg"])
        with open(os.path.join(self.project, "design", "assets", "home_logo_main.svg"), "rb") as f:
            self.assertEqual(f.read(), SVG)
        self.assertIn("<vector", self.read(os.path.join(self.project, out["written"][0])))
        self.assertEqual(subprocess.run(["git", "-C", self.project, "rev-parse", "-q", "--verify", "HEAD"],
                                        capture_output=True).returncode, head)  # still no commit
        self.assertNotIn("A ", self.git("status", "--porcelain"))  # nothing staged either
        self.refused("approve", "--name", "home_logo_main")  # approved is final
        self.refused("add", "--name", "home_logo_main", "--file", self.raw_file("logo3.svg", SVG2))

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_an_approved_jpeg_becomes_the_png_source_the_spec_names(self):
        self.ok("add", "--name", "home_hero_main", "--file", self.raw_file("hero.jpg", JPEG))
        out = self.ok("approve", "--name", "home_hero_main")
        with open(os.path.join(self.project, "design", "assets", "home_hero_main.png"), "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertIn("app/src/main/res/drawable-xxxhdpi/home_hero_main.png", out["written"])

    # --- transparent: key out a flat background on approve (NFX-173) ---

    def pillow(self, code, *args):
        r = subprocess.run([PILLOW, "-I", "-B", "-c", code, *args], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    DISC = "d.ellipse((12, 12, 52, 52), fill=(200, 60, 40), outline=(20, 20, 20), width=3)"

    def keyed_candidate(self, art=DISC, background=(0, 255, 0), name="frame.jpg"):
        """A 64x64 image with the art drawn on a flat background (a JPEG unless name says .png)."""
        with open(os.path.join(self.project, "design", "assets.toml"), "a") as f:
            f.write('\n[[asset]]\nname = "home_frame_main"\nkind = "image"\nsource = "design/assets/home_frame_main.png"'
                    '\nplatforms = ["android", "web"]\nsize = "32x32"\nusage = "Frame"\ntransparent = "#00FF00"\n')
        path = os.path.join(self.raw, name)
        os.makedirs(self.raw, exist_ok=True)
        self.pillow("import sys\nfrom PIL import Image, ImageDraw\nim = Image.new(%r, (64, 64), %r)\n"
                    "d = ImageDraw.Draw(im)\n%s\nim.save(sys.argv[1], **({'quality': 90} if sys.argv[1].endswith('.jpg') else {}))"
                    % ("RGBA" if len(background) == 4 else "RGB", background, art), path)
        return self.ok("add", "--name", "home_frame_main", "--file", path)

    def pixels(self, path):
        """[corner alphas, center alpha, visible green px, visible magenta px] of an image."""
        return json.loads(self.pillow(
            "import sys, json\nfrom PIL import Image\nim = Image.open(sys.argv[1]).convert('RGBA')\nw, h = im.size\n"
            "px = list(im.get_flattened_data())\n"
            "print(json.dumps([[im.getpixel(p)[3] for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))],"
            " im.getpixel((w // 2, h // 2))[3], sum(1 for r, g, b, a in px if a > 0 and g > r + 60 and g > b + 60),"
            " sum(1 for r, g, b, a in px if a > 0 and r > g + 60 and b > g + 60)]))", path))

    def frame_paths(self):
        return [os.path.join(self.project, p) for p in (
            "design/assets/home_frame_main.png", "public/images/home_frame_main.webp",
            "app/src/main/res/drawable-mdpi/home_frame_main.png")]

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_a_green_backed_jpeg_becomes_a_png_with_transparent_corners_and_an_opaque_center(self):
        self.keyed_candidate()
        state = json.loads(self.read(self.review("state.json")))
        self.assertEqual(state["assets"]["home_frame_main"]["preview"], "home_frame_main-1.keyed.png")
        page = self.read(self.review("gallery.html"))
        self.assertIn('<img src="home_frame_main-1.keyed.png"', page)  # the tiles show the cut-out
        self.assertIn("Background #00FF00 keyed out", page)
        self.assertTrue(os.path.isfile(self.review("home_frame_main-1.keyed.png")))
        out = self.ok("approve", "--name", "home_frame_main")
        self.assertEqual(out["assets"]["home_frame_main"]["state"], "approved")
        self.assertIn("public/images/home_frame_main.webp", out["written"])
        for path in self.frame_paths():
            corners, center, green, magenta = self.pixels(path)
            self.assertEqual(corners, [0, 0, 0, 0], path)  # transparent corners
            self.assertEqual(center, 255, path)  # opaque center
            self.assertEqual((green, magenta), (0, 0), path)  # no green or magenta fringe
        with open(self.frame_paths()[0], "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def assert_key_refused(self, why):
        page = self.read(self.review("gallery.html"))
        self.assertIn('<p class="note">Key-out: ', page)  # the card warns before anyone approves
        self.assertIn(why, page)
        self.assertIn('<img src="home_frame_main-1.', page)  # the candidate, no preview
        self.assertIn(why, self.refused("approve", "--name", "home_frame_main"))
        for path in self.frame_paths():
            self.assertFalse(os.path.exists(path), path)  # nothing half written
        self.assertEqual(self.ok("status")["assets"]["home_frame_main"]["state"], "pending")

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_a_frame_opening_is_keyed_out_and_light_art_gets_no_fringe(self):
        for art in ("d.rectangle((8, 8, 56, 56), fill=(150, 100, 50))\nd.rectangle((20, 20, 44, 44), fill=(0, 255, 0))",
                    "d.ellipse((12, 12, 52, 52), fill=(255, 255, 255))"):
            with self.subTest(art):
                self.setUp()
                self.keyed_candidate(art)
                self.ok("approve", "--name", "home_frame_main")
                corners, center, green, magenta = self.pixels(self.frame_paths()[0])
                self.assertEqual(corners, [0, 0, 0, 0])
                self.assertEqual(center, 0 if "rectangle" in art else 255)  # the opening is see-through
                self.assertEqual((green, magenta), (0, 0))

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_an_imported_png_that_is_already_cut_out_keeps_its_alpha(self):
        self.keyed_candidate(background=(0, 0, 0, 0), name="frame.png")
        self.ok("approve", "--name", "home_frame_main")
        corners, center, _, _ = self.pixels(self.frame_paths()[0])
        self.assertEqual((corners, center), ([0, 0, 0, 0], 255))

    def test_without_pillow_a_transparent_card_says_to_fix_setup_not_to_reject(self):
        with open(self.config, "w") as f:
            json.dump({"version": 1, "assets": {"python": None}}, f)
        with open(os.path.join(self.project, "design", "assets.toml"), "a") as f:
            f.write('\n[[asset]]\nname = "home_frame_main"\nkind = "image"\nsource = "design/assets/home_frame_main.png"'
                    '\nplatforms = ["web"]\nsize = "2x2"\nusage = "Frame"\ntransparent = "#00FF00"\n')
        self.ok("add", "--name", "home_frame_main", "--file", self.raw_file("frame.jpg", JPEG))
        page = self.read(self.review("gallery.html"))
        self.assertIn("No cut-out preview", page)
        self.assertIn("/via setup", page)
        self.assertNotIn("Key-out:", page)

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_a_row_that_drops_transparent_shows_the_plain_candidate(self):
        self.keyed_candidate()
        path = os.path.join(self.project, "design", "assets.toml")
        self.write(path, self.read(path).replace('transparent = "#00FF00"\n', "").encode())
        self.ok("status")
        page = self.read(self.review("gallery.html"))
        self.assertIn('<img src="home_frame_main-1.jpg"', page)
        self.assertNotIn("keyed out", page)

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_a_changed_key_color_hides_the_old_preview(self):
        self.keyed_candidate()
        path = os.path.join(self.project, "design", "assets.toml")
        self.write(path, self.read(path).replace('"#00FF00"', '"#FF00FF"').encode())
        self.ok("status")
        page = self.read(self.review("gallery.html"))
        self.assertIn('<img src="home_frame_main-1.jpg"', page)
        self.assertNotIn("keyed out", page)
        self.assertIn("No cut-out preview for background #FF00FF yet", page)
        self.ok("add", "--name", "home_frame_main", "--file", os.path.join(self.raw, "frame.jpg"))
        page = self.read(self.review("gallery.html"))
        self.assertNotIn("No cut-out preview for background", page)
        self.assertIn("Key-out: no #FF00FF background touches the edge", page)

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_an_image_too_large_to_key_out_refuses(self):
        self.keyed_candidate(name="frame.png")
        big = os.path.join(self.raw, "big.png")
        self.pillow("import sys\nfrom PIL import Image\nImage.new('RGB', (4097, 4096), (0, 255, 0)).save(sys.argv[1])",
                    big)
        self.ok("add", "--name", "home_frame_main", "--file", big)
        self.assertIn('<p class="note">Key-out: 4097x4096 is too large to key out',
                      self.read(self.review("gallery.html")))
        self.assertIn("too large to key out", self.refused("approve", "--name", "home_frame_main"))
        for path in self.frame_paths():
            self.assertFalse(os.path.exists(path), path)

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_a_key_color_inside_the_art_refuses_approve(self):
        dots = "\n".join("d.rectangle((%d, %d, %d, %d), fill=(0, 255, 0))" % (x, y, x + 1, y + 1)
                         for x in range(20, 44, 5) for y in range(20, 44, 5))
        self.keyed_candidate(self.DISC + "\n" + dots, name="frame.png")  # a JPEG would blur 2 px dots away
        self.assert_key_refused("also covers")

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_no_key_color_at_all_refuses_approve(self):
        self.keyed_candidate(background=(255, 255, 255))
        self.assert_key_refused("no #00FF00 background touches the edge")

    def test_approve_without_pillow_leaves_the_image_pending_and_writes_no_source(self):
        with open(self.config, "w") as f:
            json.dump({"version": 1, "assets": {"python": None}}, f)
        self.ok("add", "--name", "home_hero_main", "--file", self.raw_file("hero.jpg", JPEG))
        self.assertIn("stays pending", self.refused("approve", "--name", "home_hero_main"))
        self.assertFalse(os.path.exists(os.path.join(self.project, "design", "assets", "home_hero_main.png")))
        self.assertEqual(self.ok("status")["assets"]["home_hero_main"]["state"], "pending")

    def test_an_svg_convert_skips_is_not_approved(self):
        self.add_logo(TEXT_SVG, "text.svg")  # safe, but outside the VectorDrawable subset
        self.assertIn("no VectorDrawable", self.refused("approve", "--name", "home_logo_main"))
        for path in ("design/assets/home_logo_main.svg", "public/images/home_logo_main.svg",
                     "app/src/main/res/drawable/home_logo_main.xml"):
            self.assertFalse(os.path.exists(os.path.join(self.project, path)), path)  # nothing half-written
        self.assertEqual(self.ok("status")["assets"]["home_logo_main"]["state"], "pending")

    def test_a_missing_candidate_stops_approve_cleanly(self):
        self.add_logo()
        os.unlink(self.review("home_logo_main-1.svg"))
        self.assertIn("stays pending", self.refused("approve", "--name", "home_logo_main"))
        self.assertFalse(os.path.exists(os.path.join(self.project, "design", "assets", "home_logo_main.svg")))

    def test_a_state_file_it_did_not_write_is_refused_without_a_traceback(self):
        self.ok("status")
        for bad in ("[]", '{"version": 1, "key": "APP-7", "assets": {"home_logo_main": {"state": "pending", '
                          '"file": "../x-1.svg", "attempt": 1}}}',
                    '{"version": 1, "key": "APP-7", "assets": {"home_logo_main": {"state": "pending", '
                    '"file": "home_logo_main-1.svg", "attempt": "1"}}}',
                    '{"version": 1, "key": "APP-7", "assets": {"home_logo_main": {"state": "pending", '
                    '"file": "home_logo_main-1.svg", "attempt": 1}}}'):  # no kind
            with self.subTest(bad=bad[:30]):
                self.write(self.review("state.json"), bad.encode())
                self.assertIn("not a review state", self.refused("status"))

    def test_add_takes_only_generated_files_of_the_right_kind_for_a_declared_name(self):
        outside = self.write(os.path.join(self.root, "elsewhere.svg"), SVG)
        link = os.path.join(self.raw, "link.svg")
        os.makedirs(self.raw, exist_ok=True)
        os.symlink(outside, link)
        for name, path in (("home_logo_main", outside), ("home_logo_main", link),
                           ("home_logo_main", self.raw_file("x.jpg", JPEG)),
                           ("home_hero_main", self.raw_file("y.svg", SVG)),
                           ("home_other_main", self.raw_file("z.svg", SVG)),
                           ("home_logo_main", self.raw_file("bad.svg", BAD_SVG))):
            with self.subTest(name=name, path=os.path.basename(path)):
                self.refused("add", "--name", name, "--file", path)
        self.assertFalse(os.path.exists(self.review("state.json")))

    def test_an_unattended_run_never_approves_or_rejects(self):
        self.add_logo()
        for args in (("approve", "--name", "home_logo_main"), ("reject", "--name", "home_logo_main", "--note", "x")):
            with self.subTest(args[0]):
                for value in ("1", "true"):
                    self.assertIn("unattended", self.refused(*args, env={"VIA_UNATTENDED": value}))
        self.assertEqual(self.ok("status")["counts"]["pending"], 1)

    def test_a_secret_shaped_note_is_refused(self):
        self.add_logo()
        self.refused("reject", "--name", "home_logo_main", "--note", "key AKIAABCDEFGHIJKLMNOP", code=2)

    # --- images made outside Via (NFX-170) ---

    def outside(self, name, data):
        return self.write(os.path.join(self.root, "downloads", name), data)

    def test_an_imported_image_is_the_pending_candidate_and_shows_its_origin(self):
        for ext, data in ((".png", png()), (".jpg", JPEG)):
            with self.subTest(ext=ext):
                self.setUp()
                out = self.ok("import", "--name", "home_hero_main", "--file", self.outside("frame" + ext, data),
                              "--origin", "Gemini app <script>x</script>")
                self.assertEqual(out["assets"]["home_hero_main"], {"kind": "image", "state": "pending",
                                                                   "note": None, "attempt": 1})
                with open(self.review("home_hero_main-1" + ext), "rb") as f:
                    self.assertEqual(f.read(), data)
                state = json.loads(self.read(self.review("state.json")))
                self.assertEqual(state["assets"]["home_hero_main"]["origin"], "Gemini app <script>x</script>")
                page = self.read(self.review("gallery.html"))
                self.assertIn("From: Gemini app &lt;script&gt;x&lt;/script&gt;", page)
                self.assertNotIn("<script>x", page)
                log = self.read(os.path.join(self.project, "design", "asset-log", "APP-7.md"))
                self.assertIn("`home_hero_main` attempt 1 (image), imported from Gemini app <script>x</script>: "
                              "pending.", log)

    def test_an_imported_candidate_rejects_regenerates_and_approves_like_a_generated_one(self):
        self.ok("import", "--name", "home_logo_main", "--file", self.outside("logo.svg", SVG), "--origin", "by hand")
        self.ok("reject", "--name", "home_logo_main", "--note", "Too round")
        out = self.ok("add", "--name", "home_logo_main", "--file", self.raw_file("logo2.svg", SVG2))  # agy is back
        self.assertEqual(out["assets"]["home_logo_main"]["attempt"], 2)
        state = json.loads(self.read(self.review("state.json")))
        self.assertIsNone(state["assets"]["home_logo_main"]["origin"])
        self.assertEqual(state["assets"]["home_logo_main"]["history"][0]["origin"], "by hand")
        self.ok("reject", "--name", "home_logo_main", "--note", "Still round")
        out = self.ok("import", "--name", "home_logo_main", "--file", self.outside("logo3.svg", SVG2),
                      "--origin", "Gemini app")
        self.assertEqual(out["assets"]["home_logo_main"]["attempt"], 3)
        out = self.ok("approve", "--name", "home_logo_main")
        self.assertEqual(out["assets"]["home_logo_main"]["state"], "approved")
        with open(os.path.join(self.project, "design", "assets", "home_logo_main.svg"), "rb") as f:
            self.assertEqual(f.read(), SVG2)

    def import_reject_reimport(self):
        self.ok("import", "--name", "home_hero_main", "--file", self.outside("a.png", png()), "--origin", "Gemini app")
        self.ok("reject", "--name", "home_hero_main", "--note", "Warmer gold")
        out = self.ok("import", "--name", "home_hero_main", "--file", self.outside("b.jpg", JPEG),
                      "--origin", "Gemini app")
        self.assertEqual(out["assets"]["home_hero_main"], {"kind": "image", "state": "pending", "note": None,
                                                           "attempt": 2})

    def test_an_imported_png_rejects_and_reimports(self):
        self.import_reject_reimport()

    @unittest.skipUnless(PILLOW, "needs the Pillow environment from /via setup")
    def test_an_imported_image_approves_into_the_platform_folders(self):
        self.import_reject_reimport()
        out = self.ok("approve", "--name", "home_hero_main")
        with open(os.path.join(self.project, "design", "assets", "home_hero_main.png"), "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")
        self.assertIn("app/src/main/res/drawable-xxxhdpi/home_hero_main.png", out["written"])

    def test_import_refuses_the_same_files_add_does_and_non_images(self):
        good = self.outside("frame.png", png())
        link = os.path.join(self.root, "downloads", "link.png")
        os.symlink(good, link)
        big = self.outside("big.png", png())
        with open(big, "r+b") as f:
            f.truncate(16 * 1024 * 1024 + 1)
        for name, path in (("home_other_main", good),                          # not in assets.toml
                           ("home_logo_main", good),                           # an svg row
                           ("home_hero_main", self.outside("x.svg", SVG)),     # an image row
                           ("home_hero_main", link),                           # a symlink
                           ("home_hero_main", big),                            # oversized
                           ("home_hero_main", self.outside("fake.png", b"not an image at all")),
                           ("home_hero_main", self.outside("fake.jpg", png())),  # a PNG named .jpg
                           ("home_hero_main", self.outside("bomb.png", png(1)[:16] + struct.pack(">II", 50000, 50000)
                                                           + png(1)[24:])),  # a header that claims 50000x50000
                           ("home_hero_main", os.path.join(self.root, "missing.png")),
                           ("home_logo_main", self.outside("bad.svg", BAD_SVG))):
            with self.subTest(name=name, path=os.path.basename(path)):
                self.refused("import", "--name", name, "--file", path, "--origin", "Gemini app")
        locked = self.outside("locked.png", png())
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o600)
        if os.access(locked, os.R_OK):
            print("SKIPPED the unreadable-file case: running as a user who can read mode 000", file=sys.stderr)
        else:
            self.assertIn("cannot read", self.refused("import", "--name", "home_hero_main", "--file", locked,
                                                      "--origin", "Gemini app"))
        self.refused("import", "--name", "home_hero_main", "--file", good, "--origin", "  ")
        self.refused("import", "--name", "home_hero_main", "--file", good, "--origin",
                     "key AKIAABCDEFGHIJKLMNOP", code=2)
        self.assertFalse(os.path.exists(self.review("state.json")))

    # --- Argon through a Via run ---

    def test_the_argon_step_reaches_the_second_opinion_through_a_run_with_the_assets_as_kept_shots(self):
        self.add_logo()
        self.ok("add", "--name", "home_hero_main", "--file", self.raw_file("hero.jpg", JPEG))
        run = subprocess.run([th.python(self.home), "-I", "-B", os.path.join(HERE, "via_run.py"), "init",
                              "--project", self.project], capture_output=True, text=True, env=th.env(self.home))
        self.assertEqual(run.returncode, 0, run.stderr)
        run_dir = json.loads(run.stdout)["run_dir"]
        out = self.ok("shots", "--run-dir", run_dir)
        self.assertEqual(out["shots"], ["asset-home_hero_main-1.jpg"])
        self.assertEqual(out["capture"], ["home_logo_main"])  # SVGs go through a gallery capture
        record_path = os.path.join(run_dir, "findings.json")
        rec = json.loads(self.read(record_path))
        self.assertEqual((rec["mode"], rec["scope"]), ("check", ["assets"]))
        self.assertNotIn("review", rec["screenshots"][0])  # Claude looks before anything is kept
        rec["screenshots"][0]["review"] = "kept"
        with open(record_path, "w") as f:
            json.dump(rec, f)
        agy = self.write(os.path.join(self.home, ".local", "bin", "agy"), AGY.encode())
        os.chmod(agy, os.stat(agy).st_mode | stat.S_IXUSR)
        r = subprocess.run([th.python(self.home), "-I", "-B", os.path.join(HERE, "via_second_opinion.py"),
                            "--run-dir", run_dir, "--project", self.project, "--config", self.config,
                            "--shot", os.path.join(run_dir, "asset-home_hero_main-1.jpg"),
                            "--brief", "Generated app assets for APP-7"], capture_output=True, text=True,
                           cwd=self.root, env={"PATH": "/usr/bin:/bin", **th.env(self.home)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((json.loads(r.stdout)["who"], json.loads(r.stdout)["route"]), ("argon", "agy"))
        self.assertIn("shot0.jpg", self.read(os.path.join(self.home, "agy-files.log")))
        again = self.ok("shots", "--run-dir", run_dir)  # a second call lists nothing twice
        self.assertEqual(len(json.loads(self.read(record_path))["screenshots"]), 1, again)

    def test_shots_needs_a_run_folder_made_by_init(self):
        self.add_logo()
        self.refused("shots", "--run-dir", self.root)

    # --- the mode doc and route ---

    def test_the_mode_is_routed_and_its_doc_names_only_real_commands(self):
        skill = self.read(os.path.join(VIA, "SKILL.md"))
        self.assertIn("| `assets` | `modes/assets.md` |", skill)
        doc = self.read(os.path.join(VIA, "modes", "assets.md"))
        self.assertTrue(doc.startswith("# /via assets <ticket>\n"))
        used = set(re.findall(r"via_assets_review\.py\" (\w+)", doc)) | set(
            re.findall(r"`via_assets_review\.py (\w+)", doc))
        self.assertEqual(used, {"add", "import", "approve", "reject", "shots", "status", "serve"})
        self.assertIn("## 3b. Images from elsewhere", doc)
        self.assertIn("--ref", doc)
        for phrase in ("never approves", "Never commit"):
            self.assertIn(phrase.lower(), doc.lower())
        self.assertIn("`/via assets <ticket>`", self.read(os.path.join(VIA, "modes", "spec.md")))


if __name__ == "__main__":
    unittest.main()
