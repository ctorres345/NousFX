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
import time
import unittest
import xml.dom.minidom

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "via_capture.py")
VIA_RUN = os.path.join(HERE, "via_run.py")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

FAKE_BROWSER = """#!%(python)s
import http.client, json, os, subprocess, sys, time, urllib.parse
args = sys.argv[1:]
log_path = os.environ["VIA_FAKE_LOG"]
with open(log_path, "a") as log:
    log.write("\\t".join(args) + "\\n")

def get(u, path, host=None):
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    c.request("GET", path, headers={"Host": host} if host else {})
    r = c.getresponse()
    return r.status, r.getheader("Location"), r.read().decode("utf-8", "replace")

def note(entry):
    with open(log_path + ".bodies", "a") as f:
        f.write(json.dumps(entry) + "\\n")

u = urllib.parse.urlsplit(args[-1])
if u.scheme == "http":
    path = (u.path or "/") + ("?" + u.query if u.query else "")
    status, _, body = get(u, path)
    note({"url": args[-1], "status": status, "body": body})
    for probe in filter(None, os.environ.get("VIA_FAKE_PROBE", "").split(",")):
        status, location, body = get(u, probe)
        note({"probe": probe, "status": status, "location": location, "body": body})
if u.scheme == "http" and os.environ.get("VIA_FAKE_HOST"):
    status, _, body = get(u, "/index.html", host=os.environ["VIA_FAKE_HOST"])
    note({"probe": "host", "status": status, "location": None, "body": body})
if os.environ.get("VIA_FAKE_HANG"):
    child = subprocess.Popen(["sleep", "300"])
    with open(os.environ["VIA_FAKE_HANG"], "w") as f:
        f.write(str(child.pid))
    time.sleep(300)
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
        port = self.server.server_address[1]
        if self.path == "/untyped":
            self.send_response(200)  # no Content-Type: Chrome sniffs HTML
            self.end_headers()
            self.wfile.write(b"<!doctype html><input value='a@b.co'>")
            return
        if self.path in ("/refresh-away", "/refresh-same", "/redir-badport"):
            self.send_response(302 if self.path == "/redir-badport" else 200)
            port = self.server.server_address[1]
            header = {"/refresh-away": ("Refresh", "0; url=http://example.com/x"),
                      "/refresh-same": ("Refresh", "0; url=http://localhost:%d/x" % port),
                      "/redir-badport": ("Location", "http://localhost:abc/")}[self.path]
            self.send_header("Content-Type", "text/html")
            self.send_header(*header)
            self.end_headers()
            self.wfile.write(b"<!doctype html><p>x</p>")
            return
        if self.path == "/gz":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            self.wfile.write(b"\x1f\x8b not really gzip")
            return
        redirects = {"/redir-backslash": "/\\evil.example/x", "/redir-scheme": "https:evil.example/x",
                     "/redir": "http://127.0.0.1:%d/landing" % port, "/redir-alias": "http://localhost:%d/landing" % port,
                     "/redir-away": "http://example.com/landing"}
        if self.path in redirects:
            self.send_response(302)
            self.send_header("Location", redirects[self.path])
            self.end_headers()
            return
        self.send_response(404 if self.path.startswith("/missing") else 200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><p>hi</p>")

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

    def capture(self, *screens, run_dir=None, browser=None, extra_env=None, extra_args=()):
        env = {**os.environ, "VIA_CHROME": browser or self.browser, "VIA_FAKE_LOG": self.log}
        env.update(extra_env or {})
        args = ["--run-dir", run_dir or self.run_dir, "--project", self.repo, *extra_args]
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

    def test_the_browser_is_given_the_page_through_the_local_mask_server_not_as_a_file_url(self):
        self.ok("home=index.html")
        for args in self.calls():
            self.assertRegex(args[-1], r"^http://127\.0\.0\.1:\d+/index\.html$")

    def test_several_screens_are_all_captured(self):
        out = self.ok("home=index.html", "about=pages/about.html")
        self.assertEqual(len(self.pngs()), 8)
        self.assertEqual({s["screen"] for s in out["screenshots"]}, {"home", "about"})

    def test_a_dev_server_url_is_accepted(self):
        url = self.serve() + "/"
        self.ok("home=" + url)
        shown = self.shot_args("home-light-phone.png")[-1]
        self.assertRegex(shown, r"^http://127\.0\.0\.1:\d+/$")
        self.assertNotEqual(shown, url)  # Chrome talks to the mask server, which talks to the dev server

    def test_the_url_check_ignores_proxy_settings_because_chrome_bypasses_them_for_localhost(self):
        proxy = "http://127.0.0.1:9"  # nothing listens here
        env = {"http_proxy": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "HTTPS_PROXY": proxy,
               "no_proxy": "", "NO_PROXY": ""}
        self.ok("home=" + self.serve() + "/", extra_env=env)

    def test_a_shot_that_already_exists_is_never_overwritten(self):
        self.ok("home=index.html")
        before = {n: os.stat(os.path.join(self.run_dir, n)).st_mtime_ns for n in self.pngs()}
        r = self.capture("home=index.html")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("already exists", r.stderr)
        self.assertEqual({n: os.stat(os.path.join(self.run_dir, n)).st_mtime_ns for n in self.pngs()}, before)

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
            self.assertIs(s["redacted"], True)  # every shot goes through the mask server
            self.assertTrue(os.path.isfile(os.path.join(self.run_dir, s["path"])), s["path"])
            self.assertEqual(os.path.basename(s["path"]), s["path"])

    def test_the_printed_entries_are_accepted_by_via_run_merge(self):
        shots = self.ok("home=index.html")["screenshots"]
        rec = {"version": 1, "mode": "audit", "scope": ["home"], "platforms": ["web"],
               "screenshots": shots, "findings": []}
        write(os.path.join(self.run_dir, "findings.json"), json.dumps(rec))
        self.via_run("merge", self.run_dir)


    # --- NFX-9: masking personal data at capture time ---

    def bodies(self):
        path = self.log + ".bodies"
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def pages(self):
        return [b for b in self.bodies() if "url" in b]

    def probes(self):
        return {b["probe"]: b for b in self.bodies() if "probe" in b}

    def test_the_page_is_served_with_a_style_that_blurs_personal_data_fields(self):
        write(os.path.join(self.repo, "index.html"),
              "<!doctype html><html><head><title>t</title></head><body><input value='a@b.co'></body></html>")
        self.ok("home=index.html")
        body = self.pages()[0]["body"]
        self.assertIn("<input value='a@b.co'>", body)  # the page itself is untouched
        self.assertTrue(body.startswith("<!doctype html>"), "the doctype must stay first")
        for selector in ("input", "textarea", "[type=email]", 'a[href^="mailto:"]', 'a[href^="tel:"]',
                         "[data-via-redact]"):
            self.assertIn(selector, body, selector)
        self.assertRegex(body, r"blur\(\d+px\)")

    def test_a_dev_server_page_is_masked_the_same_way(self):
        self.ok("home=" + self.serve() + "/")
        body = self.pages()[0]["body"]
        self.assertIn("<p>hi</p>", body)
        self.assertIn("[data-via-redact]", body)

    def test_extra_selectors_from_the_caller_are_added_to_the_mask(self):
        self.ok("home=index.html", extra_args=["--redact-selector", ".account-email",
                                               "--redact-selector", "#api-token"])
        body = self.pages()[0]["body"]
        self.assertIn(".account-email", body)
        self.assertIn("#api-token", body)

    def test_a_selector_that_could_break_out_of_the_style_is_refused(self):
        for bad in ("a{}</style><script>x()</script>", "p; color:red", "</style>", "x{y", ""):
            with self.subTest(bad=bad):
                self.refused("home=index.html", extra_args=["--redact-selector", bad])
        self.assertEqual(self.calls(), [])

    def test_the_mask_server_serves_only_visible_project_files(self):
        write(os.path.join(self.tmp, "secret.html"), "<p>outside</p>")
        write(os.path.join(self.repo, ".env"), "TOKEN=abc\n")
        write(os.path.join(self.repo, ".hidden", "x.html"), "<p>x</p>")
        os.symlink(os.path.join(self.tmp, "secret.html"), os.path.join(self.repo, "linkout.html"))
        probe = ["/.env", "/.hidden/x.html", "/../secret.html", "/%2e%2e/secret.html", "/linkout.html",
                 "/.via/runs/", "/.git/config", "/nope.html"]
        self.ok("home=index.html", extra_env={"VIA_FAKE_PROBE": ",".join(probe)})
        got = self.probes()
        for path in probe:
            with self.subTest(path=path):
                self.assertIn(got[path]["status"], (403, 404), path)
                self.assertNotIn("outside", got[path]["body"])
                self.assertNotIn("TOKEN", got[path]["body"])

    def test_a_redirect_from_the_dev_server_stays_on_the_mask_server(self):
        upstream = self.serve()
        self.ok("home=" + upstream + "/", extra_env={"VIA_FAKE_PROBE": "/redir"})
        location = self.probes()["/redir"]["location"]
        self.assertRegex(location, r"^http://127\.0\.0\.1:\d+/landing$")
        self.assertNotIn(upstream, location)

    def test_every_extra_selector_gets_its_own_rule_so_one_bad_selector_cannot_disable_the_mask(self):
        self.ok("home=index.html", extra_args=["--redact-selector", ".account-email",
                                               "--redact-selector", ".user-name)"])
        body = self.pages()[0]["body"]
        self.assertRegex(body, r"\.account-email\s*\{[^}]*blur")
        self.assertRegex(body, r"\.user-name\)\s*\{[^}]*blur")
        self.assertRegex(body, r"\[data-via-redact\]\s*\{[^}]*blur")  # the built-in rule stands alone
        self.assertNotIn(".account-email, ", body)

    def test_a_selector_with_a_slash_is_refused_because_it_could_open_a_comment(self):
        for bad in ("/*", "a/b", "a[href^=\"https://x\"]"):
            with self.subTest(bad=bad):
                self.refused("home=index.html", extra_args=["--redact-selector", bad])

    def test_a_meta_content_security_policy_in_a_page_does_not_block_the_mask(self):
        write(os.path.join(self.repo, "index.html"),
              "<!doctype html><html><head><META http-equiv='Content-Security-Policy' content=\"style-src 'self'\">"
              "<title>t</title></head><body><input></body></html>")
        self.ok("home=index.html")
        body = self.pages()[0]["body"]
        self.assertNotIn("Content-Security-Policy", body)
        self.assertIn("<input>", body)
        self.assertIn("data-via-mask", body)

    def test_a_redirect_to_another_spelling_of_the_dev_server_stays_on_the_mask_server(self):
        self.ok("home=" + self.serve() + "/", extra_env={"VIA_FAKE_PROBE": "/redir-alias"})
        self.assertRegex(self.probes()["/redir-alias"]["location"], r"^http://127\.0\.0\.1:\d+/landing$")

    def test_a_redirect_to_a_different_host_is_not_relayed_and_fails_the_capture(self):
        paths = ["/redir-away", "/redir-backslash", "/redir-scheme"]
        r = self.capture("home=" + self.serve() + "/", extra_env={"VIA_FAKE_PROBE": ",".join(paths)})
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)  # a relay failure keeps no shots
        self.assertEqual(self.pngs(), [])
        for path in paths:
            with self.subTest(path=path):
                got = self.probes()[path]
                self.assertEqual(got["status"], 502)
                self.assertIsNone(got["location"])

    def test_a_refresh_header_is_handled_like_a_redirect(self):
        r = self.capture("home=" + self.serve() + "/", extra_env={"VIA_FAKE_PROBE": "/refresh-same,/refresh-away"})
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)  # the off-host one fails the capture
        self.assertEqual(self.probes()["/refresh-away"]["status"], 502)
        same = self.probes()["/refresh-same"]
        self.assertEqual(same["status"], 200)  # same server, so it is served, rewritten to the mask origin

    def test_a_location_the_relay_cannot_parse_fails_the_capture_instead_of_leaving_an_error_page(self):
        r = self.capture("home=" + self.serve() + "/", extra_env={"VIA_FAKE_PROBE": "/redir-badport"})
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(self.probes()["/redir-badport"]["status"], 502)
        self.assertEqual(self.pngs(), [])

    def test_a_meta_refresh_to_a_url_fails_the_capture_but_a_plain_reload_is_fine(self):
        write(os.path.join(self.repo, "go.html"),
              '<!doctype html><head><meta http-equiv="refresh" content="0; url=http://example.com/"></head>')
        r = self.capture("go=go.html")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(self.pngs(), [])
        write(os.path.join(self.repo, "live.html"),
              '<!doctype html><head><meta http-equiv="refresh" content="30"></head><p>live</p>')
        self.ok("live=live.html")

    def test_a_page_with_no_content_type_is_masked_too(self):
        self.ok("home=" + self.serve() + "/untyped")
        self.assertIn("data-via-mask", self.pages()[0]["body"])

    def test_the_mask_goes_in_the_head_when_there_is_one(self):
        write(os.path.join(self.repo, "index.html"),
              "<!doctype html><html><head><title>t</title></head><body><input></body></html>")
        self.ok("home=index.html")
        body = self.pages()[0]["body"]
        self.assertLess(body.index("data-via-mask"), body.index("</head>"))

    def test_a_compressed_dev_server_reply_fails_the_capture_instead_of_shooting_garbage(self):
        r = self.capture("home=" + self.serve() + "/gz")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(self.pngs(), [])

    def test_an_svg_with_no_closing_tag_fails_the_capture_instead_of_going_unmasked(self):
        write(os.path.join(self.repo, "bad.svg"), '<svg xmlns="http://www.w3.org/2000/svg"><rect/>')
        r = self.capture("logo=bad.svg")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertEqual(self.pngs(), [])

    def test_an_svg_page_stays_well_formed_xml_with_the_mask_inside_its_root(self):
        write(os.path.join(self.repo, "logo.svg"),
              '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="5" height="5"/></svg>')
        self.ok("logo=logo.svg")
        body = self.pages()[0]["body"]
        xml.dom.minidom.parseString(body)  # raises when not well-formed
        self.assertIn("data-via-mask", body)
        self.assertTrue(body.rstrip().endswith("</svg>"))

    def test_the_mask_server_refuses_a_request_whose_host_header_is_not_its_own(self):
        write(os.path.join(self.repo, "index.html"), "<!doctype html><p>private</p>")
        self.ok("home=index.html", extra_env={"VIA_FAKE_HOST": "evil.example"})
        got = self.probes()["host"]
        self.assertIn(got["status"], (403, 421))
        self.assertNotIn("private", got["body"])

    def test_a_browser_that_hangs_is_killed_with_its_helper_processes(self):
        pidfile = os.path.join(self.tmp, "helper.pid")
        r = self.capture("home=index.html", extra_env={"VIA_FAKE_HANG": pidfile, "VIA_SHOT_TIMEOUT": "2"})
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(self.pngs(), [])
        with open(pidfile) as f:
            pid = int(f.read())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, 9)
            self.fail("the helper process outlived the capture")

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
