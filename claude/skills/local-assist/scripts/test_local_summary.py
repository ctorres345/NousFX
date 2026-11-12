#!/usr/bin/env python3
"""Tests for local_summary.py. Run: python3 -B claude/skills/local-assist/scripts/test_local_summary.py

Each test runs the script as a subprocess with a temp state home and a fake Ollama on 127.0.0.1.
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "local_summary.py")
MODEL = "gemma4:12b"


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, body):
        cfg = self.server.cfg
        self.server.requests.append((self.command, self.path, body))
        if cfg.get("redirect"):
            self.send_response(302)
            self.send_header("Location", cfg["redirect"] + "/x")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if cfg.get("truncate"):
            self.send_response(200)
            self.send_header("Content-Length", "500")
            self.end_headers()
            self.wfile.write(b"{")
            self.wfile.flush()
            self.close_connection = True
            return
        if self.path == "/api/version":
            if cfg.get("version_delay"):
                threading.Event().wait(cfg["version_delay"])
            return self._send(200, {"version": "0.0"})
        if self.path == "/api/tags":
            return self._send(200, {"models": [{"name": n} for n in cfg.get("tags", [MODEL])]})
        if self.path == "/api/generate":
            if cfg.get("generate_delay"):
                threading.Event().wait(cfg["generate_delay"])
            if cfg.get("generate_status", 200) != 200:
                return self._send(cfg["generate_status"], {"error": "boom"})
            return self._send(200, {"response": cfg.get("reply", "two failed tests in foo.py")})
        self._send(404, {})

    def do_GET(self):
        self._handle(b"")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self._handle(self.rfile.read(n))


class LocalSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = self.tmp.name
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        self.server.daemon_threads = True
        self.server.cfg = {}
        self.server.requests = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def second_server(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        srv.daemon_threads = True
        srv.cfg, srv.requests = {}, []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, "http://127.0.0.1:%d" % srv.server_address[1]

    def config(self, url=None, model=MODEL):
        with open(os.path.join(self.state, "local.toml"), "w") as f:
            f.write('[local_assist]\nurl = "%s"\nmodel = "%s"\n' % (url or self.url, model))

    def run_script(self, code, env=None, state=True):
        e = dict(os.environ)
        e.pop("NOUSFX_STATE_HOME", None)
        e.update({"LOCAL_ASSIST_VERSION_TIMEOUT": "0.4", "LOCAL_ASSIST_GENERATE_TIMEOUT": "0.8"})
        e.update(env or {})
        e = {k: v for k, v in e.items() if v is not None}  # None in env removes the variable
        args = [sys.executable, "-I", "-B", SCRIPT]
        if state:
            args += ["--state-home", self.state]
        args += ["--", sys.executable, "-c", code]
        return subprocess.run(args, capture_output=True, text=True, env=e, timeout=30)

    def assert_fallback(self, r, requests=None):
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stdout)
        self.assertTrue(lines[0].startswith("local-assist unavailable: "), lines[0])
        self.assertNotIn("Traceback", r.stdout + r.stderr)
        if requests is not None:
            self.assertEqual(len(self.server.requests), requests)

    def test_pass_makes_no_requests(self):
        self.config()
        r = self.run_script("pass")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("PASS "), r.stdout)
        self.assertEqual(self.server.requests, [])

    def test_fail_summary_capped(self):
        self.config()
        self.server.cfg["reply"] = " ".join("w%d" % i for i in range(200))
        r = self.run_script("print('FAILED x'); raise SystemExit(5)")
        self.assertEqual(r.returncode, 5, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("FAIL exit=5"), r.stdout)
        body = r.stdout.split("\n", 1)[1]
        self.assertLessEqual(len(body.split()), 120)
        self.assertIn("w0", body)
        req = [x for x in self.server.requests if x[1] == "/api/generate"][0]
        sent = json.loads(req[2])
        self.assertEqual(sent["model"], MODEL)
        self.assertIs(sent["stream"], False)
        self.assertIs(sent["think"], False)
        self.assertEqual(sent["options"], {"num_ctx": 12288, "temperature": 0})

    def test_no_config(self):
        self.assert_fallback(self.run_script("raise SystemExit(1)"), 0)

    def test_version_timeout(self):
        self.config()
        self.server.cfg["version_delay"] = 2
        self.assert_fallback(self.run_script("raise SystemExit(1)"))

    def test_version_default_timeout_is_two_seconds(self):
        self.config()
        self.server.cfg["version_delay"] = 6
        t = time.monotonic()
        r = self.run_script("raise SystemExit(1)", env={"LOCAL_ASSIST_VERSION_TIMEOUT": None})
        self.assert_fallback(r)
        self.assertLess(time.monotonic() - t, 5.5)

    def test_truncated_reply_falls_back(self):
        self.config()
        self.server.cfg["truncate"] = True
        self.assert_fallback(self.run_script("raise SystemExit(1)"))

    def test_hostname_url_is_pinned_to_checked_address(self):
        self.config(url=self.url.replace("127.0.0.1", "localhost"))
        r = self.run_script("raise SystemExit(1)")
        self.assertTrue(r.stdout.startswith("FAIL exit=1"), r.stdout + r.stderr)
        self.assertTrue(self.server.requests)

    @unittest.skipIf(os.name == "nt", "signals")
    def test_signal_death_exits_shell_style(self):
        self.config()
        r = self.run_script("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
        self.assertEqual(r.returncode, 137, r.stdout + r.stderr)
        self.assertTrue(r.stdout.startswith("FAIL exit=137"), r.stdout)

    def test_trim_survives_one_huge_line(self):
        self.config()
        code = ("print('Error ' + 'x' * 9000)\n"
                "print('Traceback (most recent call last):')\n"
                "print('  MARKER_AFTER_HUGE_LINE')\n"
                "for i in range(1500): print('ok line %d padding padding' % i)\n"
                "raise SystemExit(1)")
        r = self.run_script(code)
        self.assertTrue(r.stdout.startswith("FAIL exit=1"), r.stdout + r.stderr)
        req = [x for x in self.server.requests if x[1] == "/api/generate"][0]
        self.assertIn("MARKER_AFTER_HUGE_LINE", json.loads(req[2])["prompt"])

    def test_non_utf8_config_falls_back(self):
        with open(os.path.join(self.state, "local.toml"), "wb") as f:
            f.write(b"# caf\xe9\n[local_assist]\n")
        self.assert_fallback(self.run_script("raise SystemExit(1)"), 0)

    def test_malformed_url_falls_back(self):
        for bad in ("http://[::1", "http://[zz]:1"):
            self.config(url=bad)
            r = self.run_script("raise SystemExit(1)")
            self.assert_fallback(r, 0)
            self.assertIn("url is invalid", r.stdout)

    def test_https_url_is_refused(self):
        self.config(url=self.url.replace("http://", "https://"))
        r = self.run_script("raise SystemExit(1)")
        self.assert_fallback(r, 0)
        self.assertIn("must be http", r.stdout)

    def test_overlay_line_and_skill_are_in_the_repo(self):
        root = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
        with open(os.path.join(root, "harness", "overlays", "claude.overlay.md")) as f:
            self.assertIn("`local-assist`, then `test-runner`, for noisy build/test output", f.read())
        with open(os.path.join(HERE, "..", "SKILL.md")) as f:
            self.assertIn("\nname: local-assist\n", f.read())

    def test_model_missing(self):
        self.config()
        self.server.cfg["tags"] = ["other:1b"]
        r = self.run_script("raise SystemExit(1)")
        self.assert_fallback(r)
        self.assertFalse([x for x in self.server.requests if x[1] == "/api/generate"])

    def test_generate_timeout(self):
        self.config()
        self.server.cfg["generate_delay"] = 3
        self.assert_fallback(self.run_script("raise SystemExit(1)"))

    def test_generate_500(self):
        self.config()
        self.server.cfg["generate_status"] = 500
        self.assert_fallback(self.run_script("raise SystemExit(1)"))

    def test_public_url_no_requests(self):
        self.config(url="http://8.8.8.8:1")
        r = self.run_script("raise SystemExit(1)")
        self.assert_fallback(r, 0)
        self.assertIn("not loopback or private", r.stdout)

    def test_redirect_is_not_followed(self):
        self.config()
        other, other_url = self.second_server()
        self.server.cfg["redirect"] = other_url
        r = self.run_script("raise SystemExit(1)")
        self.assert_fallback(r)
        self.assertEqual(other.requests, [])
        self.assertIn("HTTPError", r.stdout)

    def test_proxy_ignored(self):
        self.config()
        proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        proxy.daemon_threads = True
        proxy.cfg, proxy.requests = {}, []
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        self.addCleanup(proxy.server_close)
        self.addCleanup(proxy.shutdown)
        purl = "http://127.0.0.1:%d" % proxy.server_address[1]
        r = self.run_script("raise SystemExit(1)", env={"HTTP_PROXY": purl, "http_proxy": purl, "NO_PROXY": None, "no_proxy": None})
        self.assertTrue(r.stdout.startswith("FAIL exit=1"), r.stdout + r.stderr)
        self.assertEqual(proxy.requests, [])
        self.assertTrue(self.server.requests)

    def test_state_home_env(self):
        self.config()
        e = dict(os.environ, NOUSFX_STATE_HOME=self.state)
        r = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--", sys.executable, "-c",
                            "raise SystemExit(2)"], capture_output=True, text=True, env=e, timeout=30)
        self.assertTrue(r.stdout.startswith("FAIL exit=2"), r.stdout + r.stderr)

    def test_trim_keeps_failure_block(self):
        self.config()
        code = ("print('filler line'*3)\n"
                "for i in range(1500): print('ok line %d padding padding' % i)\n"
                "print('Traceback (most recent call last):')\n"
                "print('  MARKER_FAILURE_BLOCK here')\n"
                "for i in range(1500): print('more line %d padding padding' % i)\n"
                "raise SystemExit(1)")
        r = self.run_script(code)
        self.assertTrue(r.stdout.startswith("FAIL exit=1"), r.stdout + r.stderr)
        req = [x for x in self.server.requests if x[1] == "/api/generate"][0]
        prompt = json.loads(req[2])["prompt"]
        self.assertIn("MARKER_FAILURE_BLOCK", prompt)
        self.assertLess(len(prompt), 14000)


if __name__ == "__main__":
    unittest.main()
