#!/usr/bin/env python3
"""via_assets.py generate through agy (NFX-162). Run: python3 -B plugins/via/scripts/test_via_assets_generate.py

A stub `agy` on PATH stands in for the Antigravity CLI. It records the arguments and the prompt it got, and answers
in the JSON shape the real agy 1.5 gives (probed 2026-11-17): an SVG comes back as text in "response"; an image's
path appears only inside the "response" prose, under ~/.gemini/antigravity-cli/brain/<conversation_id>/.
Tests reach the script only through its command line, in a throwaway home and git project.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import nousfx_testhome as th  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "via_assets.py")
KEY = "github.com/acme/app"
CONV = "0a1b2c3d-1111-2222-3333-444455556666"
BAD_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" onload="alert(1)"><rect width="1" height="1"/></svg>'
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" fill="#1A73E8"/></svg>'

STUB = r'''#!%(python)s
import json, os, sys, time
here = os.path.dirname(os.path.realpath(__file__))
with open(os.path.join(here, "mode")) as f:
    mode = f.read().strip()
with open(os.path.join(here, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": sorted(os.environ)}) + "\n")
conv = "%(conv)s"
brain = os.path.join(os.environ["HOME"], ".gemini", "antigravity-cli", "brain", conv)
os.makedirs(brain, exist_ok=True)
def reply(text, status="SUCCESS"):
    print(json.dumps({"conversation_id": conv, "status": status, "response": text, "duration_seconds": 1.0,
                      "num_turns": 1, "usage": {}}))
if mode == "image":
    path = os.path.join(brain, "blue_plane_1.jpg")
    with open(path, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0fake-jpeg")
    reply("I generated the image. It is saved at %%s." %% path)
elif mode == "svg":
    reply("```svg\n%(svg)s\n```")
elif mode == "svg-prose":
    reply("Here is the <svg> markup you asked for:\n```xml\n%(svg)s\n```\nThe <svg> root has a viewBox.")
elif mode == "svg-script":
    reply("%(bad_svg)s")
elif mode == "svg-broken":
    reply("<svg viewBox=\"0 0 1 1\"><g></svg>")
elif mode == "status-error":
    reply("the model refused", status="ERROR")
elif mode == "symlink-out":
    outside = os.path.join(os.environ["HOME"], "secret.png")
    with open(outside, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\nsecret")
    link = os.path.join(brain, "x.png")
    os.symlink(outside, link)
    reply("Saved at %%s" %% link)
elif mode == "not-an-image":
    path = os.path.join(brain, "page.png")
    with open(path, "wb") as f:
        f.write(b"<html>error</html>")
    reply("Saved at %%s" %% path)
elif mode == "fifo":
    path = os.path.join(brain, "pipe.png")
    os.mkfifo(path)
    reply("Saved at %%s" %% path)
elif mode == "svg-surrogate":
    print('{"conversation_id": "%%s", "status": "SUCCESS", "response": "<svg viewBox=\\"0 0 1 1\\">\\ud800</svg>"}'
          %% conv)
elif mode == "fast-with-helper":
    import subprocess
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # holds our stdout open
    reply("```svg\n%(svg)s\n```")
elif mode == "fail-echo":
    print("I drew a login screen for you")
    sys.exit(2)
elif mode == "two-svgs":
    reply("%(svg)s and %(svg)s")
elif mode == "no-image":
    reply("I could not make that image.")
elif mode == "stale":
    path = os.path.join(brain, "old.jpg")
    with open(path, "wb") as f:
        f.write(b"old")
    os.utime(path, (time.time() - 3600, time.time() - 3600))
    reply("Saved at %%s" %% path)
elif mode == "outside":
    path = os.path.join(os.environ["HOME"], "elsewhere.jpg")
    with open(path, "wb") as f:
        f.write(b"x")
    reply("Saved at %%s" %% path)
elif mode == "fail":
    print("internal error: model unavailable", file=sys.stderr)
    sys.exit(3)
elif mode == "signed-out":
    print("Error: not authenticated. Please sign in.", file=sys.stderr)
    sys.exit(1)
elif mode == "limit":
    print("Error: quota exceeded for this plan", file=sys.stderr)
    sys.exit(1)
elif mode == "bad-json":
    print("not json at all")
elif mode == "new-shape":
    print(json.dumps({"id": conv, "result": "something"}))
elif mode == "slow":
    import subprocess
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3); open(%%r, 'w').write('late')"
                      %% os.path.join(here, "helper-finished")])
    time.sleep(60)
'''


class GenerateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.home = os.path.join(self.root, "home")
        self.project = os.path.join(self.root, "app")
        self.bin = os.path.join(self.root, "bin")
        for d in (self.home, self.project, self.bin):
            os.makedirs(d)
        subprocess.run(["git", "-C", self.project, "init", "-q"], check=True)
        subprocess.run(["git", "-C", self.project, "remote", "add", "origin", "git@github.com:acme/app.git"],
                       check=True)
        stub = os.path.join(self.bin, "agy")
        with open(stub, "w") as f:
            f.write(STUB % {"python": sys.executable, "conv": CONV, "svg": SVG.replace('"', '\\"'),
                             "bad_svg": BAD_SVG.replace('"', '\\"')})
        os.chmod(stub, 0o755)
        self.allow(["agy"])
        self.mode("image")

    def allow(self, destinations):
        path = os.path.join(self.home, ".config", "nousfx", "via.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"version": 1, "outbound_allowlist": {KEY: {"destinations": destinations}}}, f)

    def mode(self, mode):
        with open(os.path.join(self.bin, "mode"), "w") as f:
            f.write(mode)

    def calls(self):
        path = os.path.join(self.bin, "calls.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]

    def raw_dir(self):
        return os.path.join(self.home, ".local", "share", "nousfx", "via", "app", "APP-7")

    def run_cli(self, *args, path=None):
        env = th.env(self.home, PATH=path if path is not None else self.bin + os.pathsep + "/usr/bin:/bin")
        return subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, "generate", "--project", self.project,
                               "--key", "APP-7", "--name", "home_plane_main", *args],
                              capture_output=True, text=True, env=env, timeout=120)

    def ok(self, *args):
        r = self.run_cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        return json.loads(r.stdout)

    def stopped(self, *args, code=1, path=None):
        r = self.run_cli(*args, path=path)
        self.assertEqual(r.returncode, code, (r.stdout, r.stderr))
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("Stopped:", r.stderr)
        self.assertFalse(os.path.exists(self.raw_dir()), "nothing may be written on failure")
        return r.stderr

    def test_an_image_is_copied_to_the_raw_output_folder_and_its_path_printed(self):
        rep = self.ok("--kind", "image", "--prompt", "a blue paper plane")
        self.assertEqual(os.path.dirname(rep["path"]), self.raw_dir())
        self.assertTrue(os.path.basename(rep["path"]).startswith("home_plane_main-"))
        self.assertTrue(rep["path"].endswith("-%s.jpg" % CONV[:8]))  # the conversation id keeps names unique
        with open(rep["path"], "rb") as f:
            self.assertEqual(f.read(), b"\xff\xd8\xff\xe0fake-jpeg")
        self.assertEqual(rep["conversation_id"], CONV)
        call = self.calls()[0]
        self.assertIn("--output-format", call["argv"])
        self.assertEqual(call["argv"][call["argv"].index("--output-format") + 1], "json")
        self.assertNotEqual(os.path.realpath(call["cwd"]), self.project)  # agy never runs in the repo
        self.assertFalse(call["cwd"].startswith(self.project))
        self.assertFalse(os.path.exists(call["cwd"]), "the temp folder is removed")

    def test_api_keys_and_tokens_are_not_passed_to_agy(self):
        env = th.env(self.home, PATH=self.bin + os.pathsep + "/usr/bin:/bin", GEMINI_API_KEY="k", GOOGLE_API_KEY="k",
                     GH_TOKEN="t", GOOGLE_GENAI_USE_VERTEXAI="1", LANG="C")
        r = subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, "generate", "--project", self.project,
                            "--key", "APP-7", "--name", "home_plane_main", "--kind", "image", "--prompt", "x"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        seen = self.calls()[0]["env"]
        self.assertIn("LANG", seen)
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GH_TOKEN", "GOOGLE_GENAI_USE_VERTEXAI"):
            self.assertNotIn(name, seen)

    def test_the_prompt_tells_agy_to_run_no_commands(self):
        self.ok("--kind", "image", "--prompt", "a blue paper plane")
        argv = self.calls()[0]["argv"]
        prompt = argv[argv.index("-p") + 1]
        self.assertIn("Do not run shell commands", prompt)
        self.assertIn("a blue paper plane", prompt)

    def test_a_model_written_svg_is_saved_as_text_with_its_findings(self):
        self.mode("svg")
        rep = self.ok("--kind", "svg", "--prompt-file", self.prompt_file("a blue dot"))
        self.assertEqual(os.path.dirname(rep["path"]), self.raw_dir())
        with open(rep["path"], encoding="utf-8") as f:
            self.assertEqual(f.read(), SVG + "\n")
        self.assertEqual(rep["findings"], [])

    def test_the_svg_comes_from_the_code_block_when_the_prose_names_the_tag(self):
        self.mode("svg-prose")
        rep = self.ok("--kind", "svg", "--prompt", "a blue dot")
        with open(rep["path"], encoding="utf-8") as f:
            self.assertEqual(f.read(), SVG + "\n")

    def test_a_helper_holding_agys_output_open_does_not_stall_a_finished_call(self):
        self.mode("fast-with-helper")
        start = time.time()
        self.ok("--kind", "svg", "--prompt", "x", "--timeout", "20")
        self.assertLess(time.time() - start, 15)

    def test_a_sigterm_while_agy_runs_stops_agy_and_its_helpers(self):
        self.mode("slow")
        env = th.env(self.home, PATH=self.bin + os.pathsep + "/usr/bin:/bin")
        proc = subprocess.Popen([th.python(self.home), "-I", "-B", SCRIPT, "generate", "--project", self.project,
                                 "--key", "APP-7", "--name", "home_plane_main", "--kind", "image", "--prompt", "x"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        for _ in range(100):
            if self.calls():
                break
            time.sleep(0.1)
        time.sleep(0.5)
        proc.terminate()
        proc.communicate(timeout=30)
        self.assertNotEqual(proc.returncode, 0)
        time.sleep(4)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "helper-finished")), "agy's helper outlived SIGTERM")

    def test_an_unsafe_or_broken_svg_is_never_written(self):
        for mode, words in (("svg-script", "safety rules (svg-script)"), ("svg-broken", "no single <svg>"),
                            ("svg-surrogate", "no single <svg>")):
            with self.subTest(mode):
                self.mode(mode)
                self.assertIn(words, self.stopped("--kind", "svg", "--prompt", "x"))

    def prompt_file(self, text):
        path = os.path.join(self.root, "prompt.txt")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_nothing_is_sent_when_the_project_does_not_allow_agy(self):
        self.allow(["gemini"])
        err = self.stopped("--kind", "image", "--prompt", "x", code=20)
        self.assertIn("does not allow agy", err)
        self.assertEqual(self.calls(), [])

    def test_agy_missing_is_a_clear_error(self):
        err = self.stopped("--kind", "image", "--prompt", "x", path="/usr/bin:/bin")
        self.assertIn("agy is not installed", err)

    def test_each_agy_failure_is_a_clear_error_and_writes_nothing(self):
        cases = {"fail": "agy failed (exit 3)", "signed-out": "not signed in", "limit": "subscription limit",
                 "bad-json": "did not return JSON", "new-shape": "not the expected shape",
                 "no-image": "no image file", "stale": "no image file", "outside": "no image file",
                 "symlink-out": "no image file", "not-an-image": "no image file",
                 "status-error": "status 'ERROR'", "fail-echo": "agy failed (exit 2)", "fifo": "no image file"}
        for mode, words in cases.items():
            with self.subTest(mode):
                self.mode(mode)
                self.assertIn(words, self.stopped("--kind", "image", "--prompt", "x"))

    def test_raw_reply_is_shown_when_the_shape_changes(self):
        self.mode("new-shape")
        self.assertIn('"result": "something"', self.stopped("--kind", "image", "--prompt", "x"))

    def test_an_svg_reply_needs_exactly_one_svg(self):
        self.mode("two-svgs")
        self.assertIn("no single <svg>", self.stopped("--kind", "svg", "--prompt", "x"))

    def test_a_timeout_is_a_clear_error(self):
        self.mode("slow")
        start = time.time()
        self.assertIn("did not answer within 1 s", self.stopped("--kind", "image", "--prompt", "x", "--timeout", "1"))
        self.assertLess(time.time() - start, 30)
        time.sleep(4)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "helper-finished")), "agy's helper outlived the timeout")

    def test_bad_names_and_keys_are_refused_before_anything_is_sent(self):
        for args in (["--name", "../evil"], ["--key", "../x"], ["--prompt", " "], ["--timeout", "0"],
                     ["--timeout", "1801"]):
            with self.subTest(args):
                r = subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, "generate", "--project", self.project,
                                    "--kind", "image", "--key", "APP-7", "--name", "home_plane_main", "--prompt", "x",
                                    *args], capture_output=True, text=True,
                                   env=th.env(self.home, PATH=self.bin + os.pathsep + "/usr/bin:/bin"))
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("Stopped:", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_a_missing_prompt_file_is_a_clear_error(self):
        err = self.stopped("--kind", "image", "--prompt-file", os.path.join(self.root, "nope.txt"))
        self.assertIn("cannot read the prompt file", err)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
