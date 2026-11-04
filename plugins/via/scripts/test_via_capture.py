#!/usr/bin/env python3
"""Tests for via_capture.py. Run: python3 -B plugins/via/scripts/test_via_capture.py

Each test runs the script as a subprocess in a throwaway git repo. A fake browser (chosen with
VIA_CHROME) stands in for Chrome: it logs its arguments and writes a small PNG. The tests check what a
caller sees: exit codes, stdout JSON, the files left behind and the flags the browser was given.
"""
import http.server
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "via_capture.py")
VIA_RUN = os.path.join(HERE, "via_run.py")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

FAKE_BROWSER = """#!%(python)s
import os, sys
args = sys.argv[1:]
with open(os.environ["VIA_FAKE_LOG"], "a") as log:
    log.write("\\t".join(args) + "\\n")
if os.environ.get("VIA_FAKE_FAIL"):
    sys.exit(0)
for a in args:
    if a.startswith("--screenshot="):
        with open(a.split("=", 1)[1], "wb") as f:
            f.write(%(png)r)
"""


class Page(http.server.BaseHTTPRequestHandler):
    """A stand-in dev server: /missing answers 404, everything else 200."""

    def do_GET(self):
        self.send_response(404 if self.path.startswith("/missing") else 200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<p>hi</p>")

    def log_message(self, *args):
        pass


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ViaCaptureTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        write(os.path.join(self.repo, "index.html"), "<!doctype html><p>hi</p>\n")
        write(os.path.join(self.repo, "pages", "about.html"), "<!doctype html><p>about</p>\n")
        self.log = os.path.join(self.tmp, "browser.log")
        self.browser = os.path.join(self.tmp, "fake-chrome")
        write(self.browser, FAKE_BROWSER % {"python": sys.executable, "png": PNG})
        os.chmod(self.browser, 0o755)
        self.run_dir = self.via_run("init")["run_dir"]

    def serve(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_address[1]

    def via_run(self, *args):
        r = subprocess.run([sys.executable, VIA_RUN, *args, "--project", self.repo],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def capture(self, *screens, run_dir=None, browser=None, extra_env=None):
        env = {**os.environ, "VIA_CHROME": browser or self.browser, "VIA_FAKE_LOG": self.log}
        env.update(extra_env or {})
        args = ["--run-dir", run_dir or self.run_dir, "--project", self.repo]
        for s in screens:
            args.append("--screen=" + s)  # the = form, so a value that starts with "-" is not read as a flag
        return subprocess.run([sys.executable, CAPTURE, *args], capture_output=True, text=True, env=env)

    def ok(self, *screens, **kw):
        r = self.capture(*screens, **kw)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [line.rstrip("\n").split("\t") for line in f if line.strip()]

    def pngs(self):
        return sorted(n for n in os.listdir(self.run_dir) if n.endswith(".png"))

    def shot_args(self, name):
        for args in self.calls():
            if any(a.endswith("/" + name) and a.startswith("--screenshot=") for a in args):
                return args
        self.fail("the browser was never asked for %s" % name)

    # --- what gets captured ---

    def test_a_screen_gets_four_shots_light_and_dark_at_phone_and_desktop_width(self):
        self.ok("home=index.html")
        self.assertEqual(self.pngs(), ["home-dark-desktop.png", "home-dark-phone.png",
                                       "home-light-desktop.png", "home-light-phone.png"])

    def test_each_shot_is_taken_with_its_own_window_size_and_color_scheme(self):
        self.ok("home=index.html")
        expect = {"home-light-phone.png": ("390,844", "1"), "home-dark-phone.png": ("390,844", "0"),
                  "home-light-desktop.png": ("1280,800", "1"), "home-dark-desktop.png": ("1280,800", "0")}
        for name, (size, scheme) in expect.items():
            with self.subTest(shot=name):
                args = self.shot_args(name)
                self.assertIn("--window-size=" + size, args)
                self.assertIn("--blink-settings=preferredColorScheme=" + scheme, args)
                self.assertIn("--headless=new", args)

    def test_the_browser_gets_its_own_profile_so_it_does_not_hand_off_to_a_running_chrome(self):
        self.ok("home=index.html")
        for args in self.calls():
            self.assertTrue(any(a.startswith("--user-data-dir=") for a in args), args)

    def test_the_browser_is_given_the_page_as_a_file_url(self):
        self.ok("home=index.html")
        want = "file://" + os.path.realpath(os.path.join(self.repo, "index.html"))
        self.assertTrue(all(args[-1] == want for args in self.calls()), self.calls())

    def test_several_screens_are_all_captured(self):
        out = self.ok("home=index.html", "about=pages/about.html")
        self.assertEqual(len(self.pngs()), 8)
        self.assertEqual({s["screen"] for s in out["screenshots"]}, {"home", "about"})

    def test_a_dev_server_url_is_accepted(self):
        url = self.serve() + "/"
        self.ok("home=" + url)
        self.assertEqual(self.shot_args("home-light-phone.png")[-1], url)

    def test_the_url_check_ignores_proxy_settings_because_chrome_bypasses_them_for_localhost(self):
        proxy = "http://127.0.0.1:9"  # nothing listens here
        env = {"http_proxy": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "HTTPS_PROXY": proxy,
               "no_proxy": "", "NO_PROXY": ""}
        self.ok("home=" + self.serve() + "/", extra_env=env)

    def test_the_run_folder_holds_only_the_shots_afterwards(self):
        self.ok("home=index.html")
        self.assertEqual(len(os.listdir(self.run_dir)), 4)

    # --- what it prints ---

    def test_it_prints_one_run_record_entry_per_shot_with_a_path_inside_the_run_folder(self):
        out = self.ok("home=index.html")
        shots = out["screenshots"]
        self.assertEqual(len(shots), 4)
        self.assertEqual({(s["theme"], s["size"]) for s in shots},
                         {("light", "phone"), ("dark", "phone"), ("light", "desktop"), ("dark", "desktop")})
        for s in shots:
            self.assertEqual(s["screen"], "home")
            self.assertIs(s["redacted"], False)
            self.assertTrue(os.path.isfile(os.path.join(self.run_dir, s["path"])), s["path"])
            self.assertEqual(os.path.basename(s["path"]), s["path"])

    def test_the_printed_entries_are_accepted_by_via_run_merge(self):
        shots = self.ok("home=index.html")["screenshots"]
        rec = {"version": 1, "mode": "audit", "scope": ["home"], "platforms": ["web"],
               "screenshots": shots, "findings": []}
        write(os.path.join(self.run_dir, "findings.json"), json.dumps(rec))
        self.via_run("merge", self.run_dir)

    # --- refusals: nothing is written ---

    def refused(self, *screens, code=1, **kw):
        r = self.capture(*screens, **kw)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        self.assertEqual(self.pngs(), [])
        return r

    def test_no_browser_exits_3_and_writes_nothing(self):
        r = self.refused("home=index.html", code=3, browser=os.path.join(self.tmp, "no-such-chrome"))
        self.assertIn("skipped", r.stderr.lower())  # the audit mode reports this to the user
        self.assertEqual(self.calls(), [])

    def test_a_browser_that_writes_no_file_exits_4_and_names_the_shot(self):
        r = self.refused("home=index.html", code=4, extra_env={"VIA_FAKE_FAIL": "1"})
        self.assertIn("home-light-phone.png", r.stderr)

    def test_a_run_folder_outside_the_project_run_area_is_refused(self):
        outside = os.path.join(self.tmp, "stray")
        os.makedirs(outside)
        self.refused("home=index.html", run_dir=outside)
        self.assertEqual(os.listdir(outside), [])
        self.assertEqual(self.calls(), [])

    def test_a_page_outside_the_project_is_refused(self):
        write(os.path.join(self.tmp, "secret.html"), "<p>secret</p>")
        self.refused("home=../secret.html")
        self.assertEqual(self.calls(), [])

    def test_a_url_that_does_not_answer_with_a_page_exits_4_instead_of_shooting_an_error_page(self):
        base = self.serve()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead = "http://127.0.0.1:%d/" % sock.getsockname()[1]
        for url in (base + "/missing", dead):
            with self.subTest(url=url):
                self.refused("home=" + url, code=4)
        self.assertEqual(self.calls(), [])

    def test_only_html_and_svg_pages_outside_hidden_folders_can_be_captured(self):
        write(os.path.join(self.repo, ".env"), "TOKEN=abc\n")
        write(os.path.join(self.repo, "notes.txt"), "private\n")
        write(os.path.join(self.repo, ".git-notes", "x.html"), "<p>x</p>")
        for page in (".env", "notes.txt", ".git-notes/x.html", ".git/config"):
            with self.subTest(page=page):
                self.refused("home=" + page)
        self.assertEqual(self.calls(), [])

    def test_a_missing_page_is_refused(self):
        self.refused("home=nope.html")
        self.assertEqual(self.calls(), [])

    def test_a_screen_name_that_could_escape_the_run_folder_is_refused(self):
        for name in ("../evil", "a/b", "Home", "", "-x"):
            with self.subTest(name=name):
                self.refused("%s=index.html" % name)
        self.assertEqual(self.calls(), [])

    def test_a_non_web_url_is_refused(self):
        for url in ("ftp://example.com/x", "javascript:alert(1)", "file:///etc/passwd"):
            with self.subTest(url=url):
                self.refused("home=" + url)
        self.assertEqual(self.calls(), [])

    def test_a_screen_without_a_page_or_no_screens_at_all_is_refused(self):
        self.refused("home")
        r = self.capture()
        self.assertNotEqual(r.returncode, 0)

    def test_a_symlinked_run_area_is_refused(self):
        real = os.path.join(self.tmp, "elsewhere")
        os.makedirs(real)
        link = os.path.join(self.repo, ".via", "runs", "linked")
        os.symlink(real, link)
        self.refused("home=index.html", run_dir=link)
        self.assertEqual(os.listdir(real), [])


if __name__ == "__main__":
    unittest.main()
