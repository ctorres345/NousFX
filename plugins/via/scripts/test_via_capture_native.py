#!/usr/bin/env python3
"""Tests for via_capture_native.py. Run: python3 -B plugins/via/scripts/test_via_capture_native.py

Each test runs the script as a subprocess on a throwaway project. Fake `gradlew`, `flutter` and `adb` programs
stand in for the real tools. The tests check what a caller sees: exit codes, stdout JSON and the files left behind.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "via_capture_native.py")


def write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = os.path.realpath(tmp.name)

    def p(self, *parts):
        return os.path.join(self.project, *parts)

    def cli(self, *args, env=None):
        full_env = {"PATH": os.environ["PATH"], **(env or {})}
        return subprocess.run([sys.executable, "-I", "-B", SCRIPT, *args], capture_output=True, text=True, env=full_env)


class DetectTest(Base):
    def detect(self):
        r = self.cli("detect", "--project", self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_flutter_project_is_found_by_its_pubspec(self):
        write(self.p("pubspec.yaml"), "name: app\nflutter:\n  uses-material-design: true\n")
        self.assertEqual(self.detect(), {"platforms": ["flutter"], "roots": {"flutter": "."}})

    def test_a_dart_package_without_flutter_is_not_flutter(self):
        write(self.p("pubspec.yaml"), "name: tool\nenvironment:\n  sdk: ^3.5.0\n")
        self.assertEqual(self.detect(), {"platforms": [], "roots": {}})

    def test_compose_project_is_found_in_an_app_module(self):
        write(self.p("settings.gradle.kts"), 'include(":app")\n')
        write(self.p("app", "build.gradle.kts"), 'dependencies { implementation("androidx.compose.material3:material3") }\n')
        self.assertEqual(self.detect(), {"platforms": ["compose"], "roots": {"compose": "."}})

    def test_kmp_compose_is_found_by_its_composeapp_module(self):
        write(self.p("settings.gradle.kts"), 'include(":composeApp")\n')
        self.assertEqual(self.detect()["platforms"], ["compose"])

    def test_a_gradle_project_without_compose_is_not_compose(self):
        write(self.p("settings.gradle.kts"), 'include(":app")\n')
        write(self.p("app", "build.gradle.kts"), 'dependencies { implementation("androidx.appcompat:appcompat") }\n')
        self.assertEqual(self.detect()["platforms"], [])

    def test_a_project_with_both_is_reported_per_platform_with_its_folder(self):
        write(self.p("mobile", "pubspec.yaml"), "name: m\nflutter:\n")
        write(self.p("settings.gradle.kts"), 'include(":composeApp")\n')
        self.assertEqual(self.detect(), {"platforms": ["compose", "flutter"], "roots": {"compose": ".", "flutter": "mobile"}})

    def test_hidden_folders_are_ignored(self):
        write(self.p(".via", "runs", "x", "pubspec.yaml"), "flutter:\n")
        self.assertEqual(self.detect()["platforms"], [])

    def test_a_missing_project_folder_exits_1(self):
        r = self.cli("detect", "--project", self.p("nope"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a folder", r.stderr)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def make_png(width, height, text=b""):
    """A real RGBA PNG where pixel (x, y) is (x, y, 7, 255), with an optional tEXt chunk of metadata."""
    rows = b"".join(b"\x00" + b"".join(bytes((x, y, 7, 255)) for x in range(width)) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + (png_chunk(b"tEXt", text) if text else b"") + png_chunk(b"IDAT", zlib.compress(rows))
            + png_chunk(b"IEND", b""))


def read_png(path):
    """(width, height, chunk kinds, pixel rows as lists of RGBA tuples) of a PNG written with filter 0 or by make_png."""
    with open(path, "rb") as f:
        data = f.read()
    pos, kinds, idat = 8, [], b""
    while pos < len(data):
        size = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + size]
        kinds.append(kind.decode())
        if kind == b"IHDR":
            width, height = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + size
    raw, stride = zlib.decompress(idat), 1 + width * 4
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        assert line[0] == 0, "the test reader only reads filter 0"
        rows.append([tuple(line[1 + 4 * x:5 + 4 * x]) for x in range(width)])
    return width, height, kinds, rows


SCREEN = make_png(6, 10, b"Author\x00/Users/me on Pixel serial R5CT123ABC")

# A stand-in for `flutter`: logs its call, then writes one PNG per entry of the plan it finds in the generated test.
FAKE_FLUTTER = '''#!%(python)s
import json, os, re, sys
log = os.environ.get("VIA_FAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "shot_dir": os.environ.get("VIA_SHOT_DIR"),
                            "test_exists": os.path.isfile(sys.argv[-1])}) + "\\n")
if os.environ.get("VIA_FAKE_FAIL"):
    sys.stderr.write("Error: compilation failed\\n")
    sys.exit(1)
text = open(sys.argv[-1]).read()
plan = json.loads(re.search(r"_plan = r\\'\\'\\'(.*?)\\'\\'\\'", text, re.S).group(1))
for entry in plan:
    with open(os.path.join(os.environ["VIA_SHOT_DIR"], entry["path"]), "wb") as f:
        f.write(%(png)r)
'''

HOME_PREVIEWS = """import 'package:flutter/material.dart';
import 'package:flutter/widget_previews.dart';

@Preview(name: 'Home')
Widget homePreview() => const Text('Home');

@Preview(name: 'Home empty')
Widget homeEmptyPreview() => const Text('Nothing yet');
"""


class FlutterBase(Base):
    def setUp(self):
        super().setUp()
        self.bin = tempfile.mkdtemp(prefix="via-fake-bin-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.bin, ignore_errors=True))
        self.log = os.path.join(self.bin, "flutter.log")
        self.flutter = os.path.join(self.bin, "flutter")
        write(self.flutter, FAKE_FLUTTER % {"python": sys.executable, "png": PNG})
        os.chmod(self.flutter, 0o755)
        write(self.p("pubspec.yaml"), "name: demo_app\nflutter:\n  uses-material-design: true\n")
        write(self.p("lib", "home.dart"), HOME_PREVIEWS)
        subprocess.run(["git", "-C", self.project, "init", "-q"], check=True)
        subprocess.run([sys.executable, "-B", os.path.join(HERE, "via_run.py"), "init", "--project", self.project],
                       check=True, capture_output=True)
        runs = self.p(".via", "runs")
        self.run_dir = os.path.join(runs, sorted(os.listdir(runs))[0])

    def tree(self):
        """Every file in the project outside .via, with its bytes: what a restore must leave untouched."""
        out = {}
        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in (".via", ".git")]
            for name in files:
                path = os.path.join(root, name)
                with open(path, "rb") as f:
                    out[os.path.relpath(path, self.project)] = f.read()
        return out

    def capture(self, *extra, env=None):
        return self.cli("capture", "--run-dir", self.run_dir, "--project", self.project, *extra,
                        env={"VIA_FLUTTER": self.flutter, "VIA_FAKE_LOG": self.log, **(env or {})})

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]


class FlutterPreviewTest(FlutterBase):
    def test_previews_become_shots_with_state_and_matrix_fields(self):
        before = self.tree()
        r = self.capture()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        shots = {s["path"]: s for s in out["screenshots"]}
        self.assertEqual(sorted(shots), [
            "flutter-home-dark-large.png", "flutter-home-dark-phone.png", "flutter-home-empty-dark-phone.png", "flutter-home-empty-light-phone.png",
            "flutter-home-light-large.png", "flutter-home-light-phone-font.png", "flutter-home-light-phone.png"])
        for name in shots:
            self.assertTrue(os.path.getsize(os.path.join(self.run_dir, name)) > 0, name)
        one = shots["flutter-home-light-phone-font.png"]
        keys = ("screen", "theme", "size", "redacted", "source", "state", "font_scale", "direction", "platform")
        self.assertEqual({k: one[k] for k in keys},
                         {"screen": "home", "theme": "light", "size": "phone", "redacted": False, "source": "preview",
                          "state": "default", "font_scale": "largest", "direction": "ltr", "platform": "flutter"})
        self.assertEqual(shots["flutter-home-empty-dark-phone.png"]["state"], "empty")
        self.assertEqual(self.tree(), before, "the project must be left exactly as it was")

    def test_the_route_runs_flutter_test_in_the_flutter_folder(self):
        self.capture()
        (call,) = self.calls()
        self.assertEqual(call["args"][0], "test")
        self.assertTrue(call["test_exists"])
        self.assertEqual(os.path.realpath(call["cwd"]), self.project)
        self.assertFalse(os.path.exists(self.p("test", "zz_via_shots_test.dart")))

    def coverage(self, out, entry):
        (item,) = [c for c in out["coverage"] if c["entry"] == entry]
        return item

    def test_coverage_says_what_was_not_captured_and_why(self):
        out = json.loads(self.capture().stdout)
        self.assertEqual(self.coverage(out, "state:empty"), {"platform": "flutter", "screen": "home", "entry": "state:empty", "kind": "state", "captured": True})
        for state in ("loading", "error", "offline", "long"):
            item = self.coverage(out, "state:" + state)
            self.assertEqual((item["captured"], item["reason"]), (False, "no preview for this state"))
        self.assertEqual(self.coverage(out, "rtl")["reason"], "no right-to-left translation found")
        self.assertTrue(self.coverage(out, "font-largest")["captured"])
        self.assertEqual(self.coverage(out, "large, font and rtl for the other states")["reason"], "not in the default matrix")

    def test_right_to_left_is_shot_when_the_app_is_localized(self):
        write(self.p("l10n.yaml"), "arb-dir: lib/l10n\n")
        write(self.p("lib", "l10n", "app_ar.arb"), "{}\n")
        out = json.loads(self.capture().stdout)
        shots = {s["path"]: s for s in out["screenshots"]}
        self.assertEqual(shots["flutter-home-light-phone-rtl.png"]["direction"], "rtl")
        self.assertTrue(self.coverage(out, "rtl")["captured"])

    def test_an_arb_with_a_right_to_left_locale_inside_counts(self):
        write(self.p("lib", "l10n", "strings.arb"), '{"@@locale": "he"}\n')
        self.assertTrue(self.coverage(json.loads(self.capture().stdout), "rtl")["captured"])

    def test_translations_in_left_to_right_languages_only_do_not_plan_a_right_to_left_shot(self):
        write(self.p("l10n.yaml"), "arb-dir: lib/l10n\n")
        write(self.p("lib", "l10n", "app_en.arb"), "{}\n")
        write(self.p("lib", "l10n", "app_es.arb"), "{}\n")
        out = json.loads(self.capture().stdout)
        self.assertFalse(any(s["direction"] == "rtl" for s in out["screenshots"]))
        self.assertFalse(self.coverage(out, "rtl")["captured"])

    def test_private_and_parameter_previews_are_reported_and_not_called(self):
        write(self.p("lib", "more.dart"), HOME_PREVIEWS.replace("homePreview", "_hiddenPreview").replace("homeEmptyPreview", "needsArgPreview()")
              .replace("needsArgPreview()", "needsArgPreview(int x)"))
        out = json.loads(self.capture().stdout)
        reasons = {c["entry"]: c["reason"] for c in out["coverage"] if c["entry"].startswith("preview:")}
        self.assertIn("private", reasons["preview:_hiddenPreview"])
        self.assertIn("parameters", reasons["preview:needsArgPreview"])
        self.assertEqual(sorted({s["screen"] for s in out["screenshots"]}), ["home"])

    def test_a_failing_route_keeps_nothing_and_restores_the_project(self):
        before = self.tree()
        r = self.capture(env={"VIA_FAKE_FAIL": "1"})
        self.assertEqual(r.returncode, 4)
        self.assertIn("compilation failed", r.stderr)
        self.assertIn("No screenshots were kept", r.stderr)
        self.assertEqual([n for n in os.listdir(self.run_dir)], [])
        self.assertEqual(self.tree(), before)

    def test_an_existing_shot_stops_the_run_before_flutter_runs(self):
        old = os.path.join(self.run_dir, "flutter-home-light-phone.png")
        write(old, "reviewed")
        r = self.capture()
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)
        self.assertEqual(self.calls(), [])
        with open(old) as f:
            self.assertEqual(f.read(), "reviewed")

    def test_a_missing_flutter_exits_3(self):
        r = self.capture(env={"VIA_FLUTTER": self.p("no-such-flutter")})
        self.assertEqual(r.returncode, 3)
        self.assertIn("Native capture was skipped", r.stderr)


# A stand-in for `gradlew`: logs its call, then writes one PNG per entry of the plan it finds in the generated test.
FAKE_GRADLEW = '''#!%(python)s
import glob, json, os, re, sys
log = os.environ.get("VIA_FAKE_LOG")
tests = glob.glob(os.path.join(os.getcwd(), "**", "ZzViaShotsTest.kt"), recursive=True)
init = sys.argv[sys.argv.index("--init-script") + 1] if "--init-script" in sys.argv else None
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "test_files": tests,
                            "init_exists": bool(init and os.path.isfile(init)),
                            "android_home": os.environ.get("ANDROID_HOME")}) + "\\n")
if os.environ.get("VIA_FAKE_HANG"):
    import time
    time.sleep(60)
if os.environ.get("VIA_FAKE_FAIL"):
    sys.stderr.write("FAILURE: Build failed with an exception\\n")
    sys.exit(1)
text = open(tests[0]).read()
plan = json.loads(re.search(r'PLAN = """(.*?)"""', text, re.S).group(1))
stage = re.search(r'STAGE = "(.*?)"', text).group(1)
for entry in plan:
    with open(os.path.join(stage, entry["path"]), "wb") as f:
        f.write(%(png)r)
'''

HOME_KT = """package dev.demo

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview
@Composable
fun HomePreview() { Home() }

@Preview(showBackground = true)
@Composable
fun HomeEmptyPreview() { Home() }
"""


class ComposeBase(Base):
    def setUp(self):
        super().setUp()
        self.bin = tempfile.mkdtemp(prefix="via-fake-bin-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.bin, ignore_errors=True))
        self.sdk = os.path.join(self.bin, "sdk")
        os.makedirs(self.sdk)
        self.log = os.path.join(self.bin, "gradle.log")
        self.gradlew = os.path.join(self.bin, "gradlew")
        write(self.gradlew, FAKE_GRADLEW % {"python": sys.executable, "png": PNG})
        os.chmod(self.gradlew, 0o755)
        write(self.p("settings.gradle.kts"), 'include(":app")\n')
        write(self.p("app", "build.gradle.kts"),
              'plugins { id("com.android.application") }\nandroid { namespace = "dev.demo" }\ndependencies { implementation("androidx.compose.material3:material3") }\n')
        write(self.p("app", "src", "main", "java", "dev", "demo", "Home.kt"), HOME_KT)
        subprocess.run(["git", "-C", self.project, "init", "-q"], check=True)
        subprocess.run([sys.executable, "-B", os.path.join(HERE, "via_run.py"), "init", "--project", self.project],
                       check=True, capture_output=True)
        runs = self.p(".via", "runs")
        self.run_dir = os.path.join(runs, sorted(os.listdir(runs))[0])

    tree = FlutterBase.tree
    calls = FlutterBase.calls

    def capture(self, *extra, env=None):
        full = {"VIA_GRADLEW": self.gradlew, "VIA_FAKE_LOG": self.log, "ANDROID_HOME": self.sdk, **(env or {})}
        return self.cli("capture", "--run-dir", self.run_dir, "--project", self.project, "--platform", "compose",
                        *extra, env=full)


class ComposePreviewTest(ComposeBase):
    def test_previews_become_shots_with_state_and_matrix_fields(self):
        before = self.tree()
        r = self.capture()
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        shots = {s["path"]: s for s in out["screenshots"]}
        self.assertEqual(sorted(shots), [
            "compose-home-dark-large.png", "compose-home-dark-phone.png", "compose-home-empty-dark-phone.png", "compose-home-empty-light-phone.png",
            "compose-home-light-large.png", "compose-home-light-phone-font.png", "compose-home-light-phone.png"])
        for name in shots:
            self.assertTrue(os.path.getsize(os.path.join(self.run_dir, name)) > 0, name)
        one = shots["compose-home-light-phone-font.png"]
        keys = ("screen", "theme", "size", "redacted", "source", "state", "font_scale", "direction", "platform")
        self.assertEqual({k: one[k] for k in keys},
                         {"screen": "home", "theme": "light", "size": "phone", "redacted": False, "source": "preview",
                          "state": "default", "font_scale": "largest", "direction": "ltr", "platform": "compose"})
        self.assertEqual(self.tree(), before, "the project must be left exactly as it was")

    def test_the_route_runs_the_module_test_task_with_an_init_script(self):
        self.capture()
        (call,) = self.calls()
        self.assertIn(":app:testDebugUnitTest", call["args"])
        self.assertIn("--tests", call["args"])
        self.assertIn("via.ZzViaShotsTest", call["args"])
        self.assertTrue(call["init_exists"])
        self.assertEqual(len(call["test_files"]), 1)
        self.assertEqual(os.path.realpath(call["cwd"]), self.project)
        self.assertEqual(call["android_home"], self.sdk)
        self.assertEqual(subprocess.run(["find", self.project, "-name", "ZzViaShotsTest.kt"], capture_output=True,
                                        text=True).stdout.strip(), "")

    def test_the_build_files_are_never_edited(self):
        before = self.tree()
        self.capture()
        for name in ("settings.gradle.kts", os.path.join("app", "build.gradle.kts")):
            self.assertEqual(self.tree()[name], before[name])

    def coverage(self, out, entry):
        (item,) = [c for c in out["coverage"] if c["entry"] == entry]
        return item

    def test_coverage_says_what_was_not_captured_and_why(self):
        out = json.loads(self.capture().stdout)
        self.assertEqual(self.coverage(out, "state:empty"),
                         {"platform": "compose", "screen": "home", "entry": "state:empty", "kind": "state", "captured": True})
        self.assertEqual(self.coverage(out, "state:loading")["reason"], "no preview for this state")
        self.assertEqual(self.coverage(out, "rtl")["reason"], "no right-to-left translation found")

    def test_translations_in_left_to_right_languages_only_do_not_plan_a_right_to_left_shot(self):
        write(self.p("app", "src", "main", "res", "values-es", "strings.xml"), "<resources/>\n")
        out = json.loads(self.capture().stdout)
        self.assertFalse(any(s["direction"] == "rtl" for s in out["screenshots"]))

    def test_right_to_left_is_shot_when_the_app_is_localized(self):
        write(self.p("app", "src", "main", "res", "values-ar", "strings.xml"), "<resources/>\n")
        out = json.loads(self.capture().stdout)
        shots = {s["path"]: s for s in out["screenshots"]}
        self.assertEqual(shots["compose-home-light-phone-rtl.png"]["direction"], "rtl")

    def test_private_parameter_and_nested_previews_are_reported_and_not_called(self):
        write(self.p("app", "src", "main", "java", "dev", "demo", "More.kt"), """package dev.demo

@Preview
@Composable
private fun HiddenPreview() { Home() }

@Preview
@Composable
fun NeedsArgPreview(x: Int) { Home() }

class Holder {
    @Preview
    @Composable
    fun NestedPreview() { Home() }
}
""")
        out = json.loads(self.capture().stdout)
        reasons = {c["entry"]: c["reason"] for c in out["coverage"] if c["entry"].startswith("preview:")}
        self.assertIn("private", reasons["preview:HiddenPreview"])
        self.assertIn("parameters", reasons["preview:NeedsArgPreview"])
        self.assertIn("inside a class", reasons["preview:NestedPreview"])
        self.assertEqual(sorted({s["screen"] for s in out["screenshots"]}), ["home"])

    def test_a_failing_route_keeps_nothing_and_restores_the_project(self):
        before = self.tree()
        r = self.capture(env={"VIA_FAKE_FAIL": "1"})
        self.assertEqual(r.returncode, 4)
        self.assertIn("Build failed", r.stderr)
        self.assertIn("No screenshots were kept", r.stderr)
        self.assertEqual(os.listdir(self.run_dir), [])
        self.assertEqual(self.tree(), before)

    def test_an_existing_shot_stops_the_run_before_gradle_runs(self):
        old = os.path.join(self.run_dir, "compose-home-light-phone.png")
        write(old, "reviewed")
        r = self.capture()
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)
        self.assertEqual(self.calls(), [])

    def test_an_existing_generated_test_file_stops_the_run(self):
        write(self.p("app", "src", "test", "kotlin", "via", "ZzViaShotsTest.kt"), "mine")
        r = self.capture()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_a_missing_gradlew_exits_3(self):
        r = self.capture(env={"VIA_GRADLEW": self.p("no-such-gradlew")})
        self.assertEqual(r.returncode, 3)
        self.assertIn("Native capture was skipped", r.stderr)

    def test_a_missing_android_sdk_exits_3(self):
        r = self.capture(env={"ANDROID_HOME": "", "HOME": self.bin})
        self.assertEqual(r.returncode, 3)
        self.assertIn("no Android SDK", r.stderr)

    def test_sdk_dir_in_local_properties_is_enough(self):
        write(self.p("local.properties"), "sdk.dir=%s\n" % self.sdk)
        r = self.capture(env={"ANDROID_HOME": "", "HOME": self.bin})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_compose_multiplatform_project_without_an_android_module_is_reported(self):
        write(self.p("app", "build.gradle.kts"), 'plugins { id("org.jetbrains.compose") }\n')
        write(self.p("settings.gradle.kts"), 'include(":composeApp")\n')
        out = json.loads(self.capture().stdout)
        self.assertEqual(out["screenshots"], [])
        self.assertIn("not supported", out["coverage"][0]["reason"])
        self.assertEqual(out["coverage"][0]["kind"], "note")
        self.assertEqual(self.calls(), [])


class ComposeMultiplatformTest(ComposeBase):
    """A Kotlin Multiplatform module keeps previews in androidDebug/androidMain/commonMain and Android tests in androidUnitTest."""

    def setUp(self):
        super().setUp()
        os.remove(self.p("app", "src", "main", "java", "dev", "demo", "Home.kt"))
        write(self.p("app", "src", "androidDebug", "kotlin", "dev", "demo", "Home.kt"), HOME_KT)
        write(self.p("app", "src", "androidUnitTest", "kotlin", "KeepMe.kt"), "// a test the app already has\n")

    def test_previews_in_android_debug_are_captured(self):
        before = self.tree()
        r = self.capture()
        self.assertEqual(r.returncode, 0, r.stderr)
        screens = {s["screen"] for s in json.loads(r.stdout)["screenshots"]}
        self.assertEqual(screens, {"home"})
        self.assertEqual(self.tree(), before, "the project must be left exactly as it was")

    def test_the_test_is_written_to_android_unit_test_and_removed_afterwards(self):
        self.capture()
        (call,) = self.calls()
        (path,) = call["test_files"]
        self.assertTrue(path.endswith(os.path.join("src", "androidUnitTest", "kotlin", "via", "ZzViaShotsTest.kt")), path)
        self.assertFalse(os.path.exists(self.p("app", "src", "test")))

    def test_previews_in_android_main_and_common_main_are_found(self):
        os.remove(self.p("app", "src", "androidDebug", "kotlin", "dev", "demo", "Home.kt"))
        write(self.p("app", "src", "androidMain", "kotlin", "dev", "demo", "Home.kt"), HOME_KT)
        write(self.p("app", "src", "commonMain", "kotlin", "dev", "demo", "Detail.kt"),
              HOME_KT.replace("Home", "Detail"))
        out = json.loads(self.capture().stdout)
        self.assertEqual(sorted({s["screen"] for s in out["screenshots"]}), ["detail", "home"])

    def test_an_android_plugin_applied_through_a_version_catalog_alias_counts(self):
        write(self.p("app", "build.gradle.kts"),
              'plugins {\n    alias(libs.plugins.kotlinMultiplatform)\n    alias(libs.plugins.androidApplication)\n}\n'
              'dependencies { implementation("androidx.compose.material3:material3") }\n')
        out = json.loads(self.capture().stdout)
        self.assertEqual({s["screen"] for s in out["screenshots"]}, {"home"})

    def test_a_kmp_module_without_an_android_unit_test_folder_still_gets_its_test_there(self):
        import shutil
        shutil.rmtree(self.p("app", "src", "androidUnitTest"))
        before = self.tree()
        self.capture()
        (call,) = self.calls()
        self.assertTrue(call["test_files"][0].endswith(os.path.join("src", "androidUnitTest", "kotlin", "via", "ZzViaShotsTest.kt")))
        self.assertEqual(self.tree(), before)

    def test_the_same_screen_in_two_source_sets_is_shot_once(self):
        write(self.p("app", "src", "commonMain", "kotlin", "dev", "other", "Home.kt"),
              HOME_KT.replace("package dev.demo", "package dev.other"))
        out = json.loads(self.capture().stdout)
        paths = [s["path"] for s in out["screenshots"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual({s["screen"] for s in out["screenshots"]}, {"home"})

    def test_a_root_build_file_that_only_declares_the_android_plugin_is_not_a_module(self):
        write(self.p("build.gradle.kts"), 'plugins {\n    alias(libs.plugins.androidApplication) apply false\n}\n// com.android\n')
        self.capture()
        (call,) = self.calls()
        self.assertEqual(len(call["test_files"]), 1)

    def test_an_existing_generated_test_file_in_android_unit_test_stops_the_run(self):
        write(self.p("app", "src", "androidUnitTest", "kotlin", "via", "ZzViaShotsTest.kt"), "mine")
        r = self.capture()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls(), [])


# A stand-in for `adb`: a phone whose settings live in a JSON file, so a test can see what was changed and restored.
FAKE_ADB = '''#!%(python)s
import json, os, sys
state_path = os.environ["VIA_FAKE_ADB_STATE"]
with open(state_path) as f:
    state = json.load(f)
args = sys.argv[1:]
state["calls"].append(args)
serial = None
if args[:1] == ["-s"]:
    serial, args = args[1], args[2:]
out = ""
if args == ["devices"]:
    out = "List of devices attached\\n" + "".join("%%s\\tdevice\\n" %% d for d in state["devices"])
elif args[:2] == ["exec-out", "screencap"]:
    if state.get("screencap_hangs"):
        with open(state_path, "w") as f:
            json.dump(state, f)
        open(state_path + ".hanging", "w").close()
        import time
        time.sleep(60)
    if state.get("screencap_fails"):
        sys.stderr.write("screencap: failed\\n")
        sys.exit(1)
    sys.stdout.buffer.write(%(png)r)
elif args[:1] == ["shell"]:
    cmd = args[1:]
    if cmd == ["cmd", "uimode", "night"]:
        out = "Night mode: %%s\\n" %% state["night"]
    elif cmd[:3] == ["cmd", "uimode", "night"]:
        state["night"] = cmd[3]
    elif cmd == ["settings", "get", "system", "font_scale"]:
        out = "%%s\\n" %% state["font_scale"]
    elif cmd[:4] == ["settings", "put", "system", "font_scale"]:
        state["font_scale"] = cmd[4]
    elif cmd[:4] == ["settings", "delete", "system", "font_scale"]:
        state["font_scale"] = "null"
    elif cmd[:3] == ["settings", "get", "global"]:
        out = "%%s\\n" %% state["global"].get(cmd[3], "null")
    elif cmd[:3] == ["settings", "put", "global"]:
        state["global"][cmd[3]] = cmd[4]
    elif cmd[:3] == ["settings", "delete", "global"]:
        state["global"].pop(cmd[3], None)
    elif cmd[:4] == ["am", "broadcast", "-a", "com.android.systemui.demo"]:
        if state["global"].get("sysui_demo_allowed") != "1":
            pass  # SystemUI ignores demo commands until they are allowed
        elif cmd[5:7] == ["command", "enter"]:
            state["demo"] = []
        elif cmd[5:7] == ["command", "exit"]:
            state["demo"] = None
            if state.get("exit_pauses"):  # the first restore is slow, so a test can signal again during it
                open(state_path + ".restoring", "w").close()
                import time
                time.sleep(2)
        elif state.get("demo") is not None:
            state["demo"].append(" ".join(w for w in cmd[6:] if w != "-e"))
    elif cmd[:3] == ["cmd", "notification", "set_dnd"]:
        state["global"]["zen_mode"] = "0" if cmd[3] in ("off", "all") else "2"
    elif cmd == ["dumpsys", "window", "displays"]:
        out = "".join(l + "\\n" for l in state["windows"].splitlines() if "mCurrentFocus" in l)
        if state.get("focus_null_reads", 0) > 0:  # the app is being relaunched: nothing has focus yet
            state["focus_null_reads"] -= 1
            out = "  mCurrentFocus=null\\n"
    elif cmd == ["dumpsys", "window", "windows"]:
        out = "".join(l + "\\n" for l in state["windows"].splitlines() if "mCurrentFocus" not in l)
    elif cmd[:2] == ["dumpsys", "activity"]:
        state["dumpsys_calls"] = state.get("dumpsys_calls", 0) + 1
        out = state["dumpsys_later"] if state.get("dumpsys_later") and state["dumpsys_calls"] > 1 else state["dumpsys"]
if args[:2] == ["exec-out", "screencap"]:
    state["shots_seen"] = state.get("shots_seen", []) + [[state["night"], state["font_scale"]]]
    state["demo_seen"] = state.get("demo_seen", []) + [[state.get("demo"), state["global"].get("zen_mode")]]
with open(state_path, "w") as f:
    json.dump(state, f)
sys.stdout.write(out)
'''

DUMPSYS = "  topResumedActivity=ActivityRecord{1a2b u0 dev.demo/.MainActivity t12}\n"


def windows(frame="[0,0][6,10]", focus="dev.demo/dev.demo.MainActivity", form="Frames: parent=[0,0][6,10] frame=%s last=[0,0][0,0]"):
    """`dumpsys window` for a status bar, the focused window and a navigation bar."""
    return ("WINDOW MANAGER WINDOWS (dumpsys window windows)\n"
            "  Window #0 Window{11 u0 StatusBar}:\n    Frames: parent=[0,0][6,10] frame=[0,0][6,1] last=[0,0][0,0]\n"
            "  Window #1 Window{22 u0 %s}:\n    mAttrs={(0,0)(fillxfill)}\n    %s\n"
            "  Window #2 Window{33 u0 NavigationBar0}:\n    Frames: parent=[0,0][6,10] frame=[0,9][6,10] last=[0,0][0,0]\n"
            "  mCurrentFocus=Window{22 u0 %s}\n" % (focus, form % frame, focus))


class FallbackBase:
    """Mixed into the Compose and Flutter test classes: a fake adb and a running device."""

    def start_adb(self, devices=("emulator-5554",), **state):
        self.adb = os.path.join(self.bin, "adb")
        write(self.adb, FAKE_ADB % {"python": sys.executable, "png": SCREEN})
        os.chmod(self.adb, 0o755)
        self.adb_state = os.path.join(self.bin, "adb-state.json")
        write(self.adb_state, json.dumps({"devices": list(devices), "night": "no", "font_scale": "1.15",
                                          "dumpsys": DUMPSYS, "windows": windows(), "calls": [],
                                          "global": {"sysui_demo_allowed": "0", "zen_mode": "0"}, **state}))
        return {"VIA_ADB": self.adb, "VIA_FAKE_ADB_STATE": self.adb_state, "VIA_FAKE_FAIL": "1", "VIA_ADB_SETTLE": "0"}

    def device(self):
        with open(self.adb_state) as f:
            return json.load(f)


class ComposeFallbackTest(FallbackBase, ComposeBase):
    def test_a_failing_route_falls_back_to_the_running_device(self):
        before = self.tree()
        r = self.capture(env=self.start_adb())
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        shots = {s["path"]: s for s in out["screenshots"]}
        self.assertEqual(sorted(shots), ["compose-main-dark-phone.png", "compose-main-light-phone-font.png", "compose-main-light-phone.png"])
        for s in shots.values():
            self.assertEqual((s["source"], s["state"], s["size"], s["redacted"], s["platform"]),
                             ("device", "default", "phone", False, "compose"))
            self.assertTrue(os.path.getsize(os.path.join(self.run_dir, s["path"])) > 0)
        self.assertEqual(shots["compose-main-light-phone-font.png"]["font_scale"], "largest")
        self.assertEqual(shots["compose-main-dark-phone.png"]["theme"], "dark")
        self.assertTrue(any("emulator" in w for w in out["warnings"]), out["warnings"])
        self.assertTrue(any("Build failed" in w for w in out["warnings"]), out["warnings"])
        gaps = {c["entry"] for c in out["coverage"] if not c["captured"]}
        self.assertTrue({"large-light", "large-dark", "rtl", "other states"} <= gaps, gaps)
        self.assertTrue(all(c["kind"] in ("matrix", "note") for c in out["coverage"]), out["coverage"])
        self.assertEqual({c["entry"] for c in out["coverage"] if c["captured"]}, {"phone-light", "phone-dark", "font-largest"})
        self.assertEqual(self.tree(), before)

    def test_the_device_settings_are_set_per_shot_and_put_back(self):
        self.capture(env=self.start_adb(night="auto"))
        dev = self.device()
        self.assertEqual(dev["shots_seen"], [["no", "1.0"], ["yes", "1.0"], ["no", "2.0"]])
        self.assertEqual((dev["night"], dev["font_scale"]), ("auto", "1.15"))

    def test_the_settings_are_put_back_when_screencap_fails(self):
        r = self.capture(env=self.start_adb(screencap_fails=True))
        self.assertEqual(r.returncode, 4)
        dev = self.device()
        self.assertEqual((dev["night"], dev["font_scale"]), ("no", "1.15"))
        self.assertEqual(os.listdir(self.run_dir), [])

    def test_every_shot_is_taken_in_demo_mode_with_do_not_disturb_on(self):
        r = self.capture(env=self.start_adb())
        self.assertEqual(r.returncode, 0, r.stderr)
        seen = self.device()["demo_seen"]
        self.assertEqual(len(seen), 3)
        for demo, zen in seen:
            self.assertEqual(zen, "2")
            self.assertIn("notifications visible false", demo)
            self.assertTrue(any(c.startswith("clock hhmm") for c in demo), demo)

    def test_demo_mode_and_do_not_disturb_are_put_back(self):
        self.capture(env=self.start_adb())
        dev = self.device()
        self.assertIsNone(dev.get("demo"))
        self.assertEqual(dev["global"], {"sysui_demo_allowed": "0", "zen_mode": "0"})

    def test_demo_mode_is_put_back_when_screencap_fails(self):
        r = self.capture(env=self.start_adb(screencap_fails=True))
        self.assertEqual(r.returncode, 4)
        dev = self.device()
        self.assertIsNone(dev.get("demo"))
        self.assertEqual(dev["global"], {"sysui_demo_allowed": "0", "zen_mode": "0"})

    def test_a_terminated_or_hung_up_run_still_puts_the_device_back(self):
        import signal
        import time
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig):
                env = {**os.environ, **self.start_adb(screencap_hangs=True), "VIA_GRADLEW": self.p("no-such-gradlew")}
                proc = subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, "capture", "--run-dir", self.run_dir,
                                         "--project", self.project, "--platform", "compose"], env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.addCleanup(proc.kill)
                for _ in range(200):
                    if os.path.exists(self.adb_state + ".hanging"):
                        break
                    time.sleep(0.05)
                self.assertTrue(os.path.exists(self.adb_state + ".hanging"), "screencap never started")
                os.remove(self.adb_state + ".hanging")
                proc.send_signal(sig)
                proc.wait(timeout=20)
                dev = self.device()
                self.assertIsNone(dev.get("demo"))
                self.assertEqual(dev["global"], {"sysui_demo_allowed": "0", "zen_mode": "0"})
                self.assertEqual((dev["night"], dev["font_scale"]), ("no", "1.15"))
                self.assertEqual(os.listdir(self.run_dir), [])

    def test_a_second_signal_during_the_restores_waits_until_they_finish(self):
        import signal
        import time
        env = {**os.environ, **self.start_adb(screencap_hangs=True, exit_pauses=True),
               "VIA_GRADLEW": self.p("no-such-gradlew")}
        proc = subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, "capture", "--run-dir", self.run_dir,
                                 "--project", self.project, "--platform", "compose"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        for marker in (".hanging", ".restoring"):
            for _ in range(200):
                if os.path.exists(self.adb_state + marker):
                    break
                time.sleep(0.05)
            self.assertTrue(os.path.exists(self.adb_state + marker), marker)
            proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=20), 143)
        dev = self.device()
        self.assertEqual(dev["global"], {"sysui_demo_allowed": "0", "zen_mode": "0"})
        self.assertEqual((dev["night"], dev["font_scale"]), ("no", "1.15"))

    def test_settings_that_were_never_set_are_deleted_again(self):
        self.capture(env=self.start_adb(**{"global": {"zen_mode": "0"}}))
        self.assertEqual(self.device()["global"], {"zen_mode": "0"})

    def test_do_not_disturb_and_demo_mode_that_were_already_on_are_left_on(self):
        self.capture(env=self.start_adb(demo=["clock hhmm 0930"], **{"global": {"sysui_demo_allowed": "1",
                                                                                "sysui_tuner_demo_on": "1",
                                                                                "zen_mode": "1"}}))
        dev = self.device()
        self.assertIsNotNone(dev["demo"])
        self.assertEqual(dev["global"], {"sysui_demo_allowed": "1", "sysui_tuner_demo_on": "1", "zen_mode": "1"})

    def test_a_shot_is_cropped_to_the_app_window_and_loses_its_metadata(self):
        r = self.capture(env=self.start_adb(windows=windows("[1,2][5,9]")))
        self.assertEqual(r.returncode, 0, r.stderr)
        width, height, kinds, rows = read_png(os.path.join(self.run_dir, "compose-main-light-phone.png"))
        self.assertEqual((width, height), (4, 7))
        self.assertEqual(rows[0][0], (1, 2, 7, 255))
        self.assertEqual(rows[-1][-1], (4, 8, 7, 255))
        self.assertEqual(kinds, ["IHDR", "IDAT", "IEND"])

    def test_a_full_screen_app_window_keeps_the_whole_shot(self):
        r = self.capture(env=self.start_adb())
        self.assertEqual(r.returncode, 0, r.stderr)
        width, height, kinds, rows = read_png(os.path.join(self.run_dir, "compose-main-dark-phone.png"))
        self.assertEqual((width, height), (6, 10))
        self.assertEqual(rows[9][5], (5, 9, 7, 255))
        self.assertNotIn("tEXt", kinds)

    def test_the_older_android_window_frame_is_read(self):
        r = self.capture(env=self.start_adb(windows=windows("[0,1][6,10]", form="mFrame=%s mLastFrame=[0,0][0,0]")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(read_png(os.path.join(self.run_dir, "compose-main-light-phone.png"))[:2], (6, 9))

    def test_a_focused_window_of_another_app_is_never_shot(self):
        r = self.capture(env=self.start_adb(windows=windows(focus="com.google.android.gm/com.google.ConversationList")))
        self.assertEqual(r.returncode, 4)
        self.assertIn("window", r.stderr)
        self.assertEqual(os.listdir(self.run_dir), [])
        self.assertIsNone(self.device().get("demo"))

    def test_the_app_window_is_waited_for_while_the_app_is_relaunched(self):
        r = self.capture(env=self.start_adb(focus_null_reads=2))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(os.listdir(self.run_dir)), 3)

    def test_an_unreadable_focus_or_window_frame_is_never_shot_uncropped(self):
        for dump, message in (("no windows here\n", "focused window could not be read"),
                              (windows(form="Frames: parent=%s"), "frame could not be read")):
            with self.subTest(message=message):
                r = self.capture(env=self.start_adb(windows=dump))
                self.assertEqual(r.returncode, 4)
                self.assertIn(message, r.stderr)
                self.assertEqual(os.listdir(self.run_dir), [])
                self.assertIsNone(self.device().get("demo"))

    def test_a_font_scale_that_was_never_set_is_deleted_again(self):
        self.capture(env=self.start_adb(font_scale="null"))
        self.assertEqual(self.device()["font_scale"], "null")
        self.assertTrue(any("delete" in c for c in self.device()["calls"]))

    def test_no_device_keeps_the_route_error_and_says_why(self):
        r = self.capture(env=self.start_adb(devices=()))
        self.assertEqual(r.returncode, 4)
        self.assertIn("Build failed", r.stderr)
        self.assertIn("no running device", r.stderr)
        self.assertEqual(os.listdir(self.run_dir), [])

    def test_several_devices_are_not_guessed(self):
        r = self.capture(env=self.start_adb(devices=("emulator-5554", "emulator-5556")))
        self.assertEqual(r.returncode, 4)
        self.assertIn("ANDROID_SERIAL", r.stderr)
        self.assertEqual([c for c in self.device()["calls"] if c != ["devices"]], [])

    def test_android_serial_picks_one_of_several_devices(self):
        r = self.capture(env=self.start_adb(devices=("emulator-5554", "emulator-5556")) | {"ANDROID_SERIAL": "emulator-5556"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(all(c[:2] == ["-s", "emulator-5556"] for c in self.device()["calls"] if c != ["devices"]))

    def test_no_adb_keeps_the_route_error_and_says_why(self):
        r = self.capture(env={"VIA_ADB": self.p("no-such-adb"), "VIA_FAKE_FAIL": "1"})
        self.assertEqual(r.returncode, 4)
        self.assertIn("no `adb`", r.stderr)

    def test_a_route_that_works_never_touches_adb(self):
        env = self.start_adb()
        del env["VIA_FAKE_FAIL"]
        r = self.capture(env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.device()["calls"], [])
        self.assertNotIn("warnings", json.loads(r.stdout))

    def test_a_missing_gradlew_also_falls_back(self):
        env = self.start_adb()
        del env["VIA_FAKE_FAIL"]
        r = self.capture(env={**env, "VIA_GRADLEW": self.p("no-such-gradlew")})
        self.assertEqual(r.returncode, 0, r.stderr)
        shot = json.loads(r.stdout)["screenshots"][0]
        self.assertEqual((shot["source"], shot["emulator"]), ("device", True))

    def test_a_device_showing_another_app_is_never_shot(self):
        env = self.start_adb(dumpsys="  topResumedActivity=ActivityRecord{1a2b u0 com.google.android.gm/.ConversationList t9}\n")
        r = self.capture(env=env)
        self.assertEqual(r.returncode, 4)
        self.assertIn("not showing the app", r.stderr)
        self.assertEqual([c[1:] if c[:1] == ["-s"] else c for c in self.device()["calls"] if "screencap" in c or "put" in c], [])
        self.assertEqual(os.listdir(self.run_dir), [])

    def test_the_app_must_still_be_on_screen_for_every_shot(self):
        later = "  topResumedActivity=ActivityRecord{1a2b u0 com.android.launcher/.Launcher t1}\n"
        r = self.capture(env=self.start_adb(dumpsys_later=later))
        self.assertEqual(r.returncode, 4)
        self.assertIn("not showing the app", r.stderr)
        dev = self.device()
        self.assertEqual((dev["night"], dev["font_scale"]), ("no", "1.15"))
        self.assertEqual(os.listdir(self.run_dir), [])

    def test_the_route_reason_is_kept_when_a_device_read_fails(self):
        r = self.capture(env={**self.start_adb(dumpsys=""), "VIA_GRADLEW": self.p("no-such-gradlew")})
        self.assertIn("no `gradlew` found", r.stderr)

    def test_the_older_android_form_of_the_resumed_activity_is_read(self):
        old = "  mResumedActivity: ActivityRecord{1a2b u0 dev.demo/.MainActivity t12}\n"
        r = self.capture(env=self.start_adb(dumpsys=old))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_settle_time_that_is_not_a_number_is_refused_with_a_message(self):
        r = self.capture(env=self.start_adb() | {"VIA_ADB_SETTLE": "soon"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("VIA_ADB_SETTLE", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_failure_before_the_device_is_touched_keeps_the_route_exit_code(self):
        r = self.capture(env={**self.start_adb(dumpsys="", font_scale="null"), "VIA_GRADLEW": self.p("no-such-gradlew")})
        self.assertEqual(r.returncode, 3)

    def test_an_app_whose_id_cannot_be_found_is_not_shot_on_a_guess(self):
        write(self.p("app", "build.gradle.kts"), 'plugins { id("com.android.application") }\ndependencies { implementation("androidx.compose.material3:material3") }\n')
        r = self.capture(env=self.start_adb())
        self.assertEqual(r.returncode, 4)
        self.assertIn("applicationId", r.stderr)

    def test_a_physical_phone_is_not_used_unless_asked(self):
        r = self.capture(env=self.start_adb(devices=("R5CT123ABC",)))
        self.assertEqual(r.returncode, 4)
        self.assertIn("VIA_ADB_ALLOW_DEVICE", r.stderr)
        self.assertEqual([c for c in self.device()["calls"] if c != ["devices"]], [])

    def test_a_physical_phone_is_used_when_allowed_or_picked_by_serial(self):
        for extra in ({"VIA_ADB_ALLOW_DEVICE": "1"}, {"ANDROID_SERIAL": "R5CT123ABC"}):
            with self.subTest(extra=extra):
                r = self.capture(env=self.start_adb(devices=("R5CT123ABC",)) | extra)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual({s["emulator"] for s in json.loads(r.stdout)["screenshots"]}, {False})
                for name in os.listdir(self.run_dir):
                    os.remove(os.path.join(self.run_dir, name))

    def test_no_fallback_keeps_the_route_exit_code(self):
        r = self.capture(env={**self.start_adb(devices=()), "VIA_GRADLEW": self.p("no-such-gradlew")})
        self.assertEqual(r.returncode, 3)

    def test_a_fallback_that_failed_after_touching_the_device_exits_4(self):
        r = self.capture(env={**self.start_adb(screencap_fails=True), "VIA_GRADLEW": self.p("no-such-gradlew")})
        self.assertEqual(r.returncode, 4)

    def test_a_terminated_run_still_restores_the_project(self):
        import time
        before = self.tree()
        env = {"PATH": os.environ["PATH"], "VIA_GRADLEW": self.gradlew, "VIA_FAKE_LOG": self.log,
               "ANDROID_HOME": self.sdk, "VIA_FAKE_HANG": "1"}
        proc = subprocess.Popen([sys.executable, "-I", "-B", SCRIPT, "capture", "--run-dir", self.run_dir,
                                 "--project", self.project, "--platform", "compose"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        for _ in range(100):
            if os.path.exists(self.log):
                break
            time.sleep(0.1)
        self.assertTrue(os.path.exists(self.log), "gradle never started")
        proc.terminate()
        proc.wait(timeout=20)
        self.assertEqual(self.tree(), before, "the generated test file must be gone")
        self.assertEqual(os.listdir(self.run_dir), [], "the stage folder must be gone")

    def test_an_input_error_never_falls_back(self):
        write(os.path.join(self.run_dir, "compose-home-light-phone.png"), "reviewed")
        r = self.capture(env=self.start_adb())
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.device()["calls"], [])


class FlutterFallbackTest(FallbackBase, FlutterBase):
    def capture(self, *extra, env=None):
        return super().capture("--platform", "flutter", *extra, env=env)

    def test_a_flutter_project_with_an_android_folder_falls_back(self):
        write(self.p("android", "app", "build.gradle"), 'android { defaultConfig { applicationId "dev.demo" } }\n')
        r = self.capture(env=self.start_adb())
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual({s["source"] for s in out["screenshots"]}, {"device"})
        self.assertEqual({s["platform"] for s in out["screenshots"]}, {"flutter"})

    def test_a_flutter_project_without_an_android_folder_does_not_fall_back(self):
        r = self.capture(env=self.start_adb())
        self.assertEqual(r.returncode, 4)
        self.assertIn("no android/ folder", r.stderr)
        self.assertEqual(self.device()["calls"], [])


class BothPlatformsTest(ComposeBase):
    def test_flutter_and_compose_screens_with_the_same_name_do_not_collide(self):
        flutter = os.path.join(self.bin, "flutter")
        write(flutter, FAKE_FLUTTER % {"python": sys.executable, "png": PNG})
        os.chmod(flutter, 0o755)
        write(self.p("pubspec.yaml"), "name: demo_app\nflutter:\n  uses-material-design: true\n")
        write(self.p("lib", "home.dart"), HOME_PREVIEWS)
        r = self.cli("capture", "--run-dir", self.run_dir, "--project", self.project,
                     env={"VIA_GRADLEW": self.gradlew, "VIA_FLUTTER": flutter, "ANDROID_HOME": self.sdk})
        self.assertEqual(r.returncode, 0, r.stderr)
        paths = [s["path"] for s in json.loads(r.stdout)["screenshots"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertTrue(any(p.startswith("flutter-home") for p in paths) and any(p.startswith("compose-home") for p in paths))
        for path in paths:
            self.assertTrue(os.path.getsize(os.path.join(self.run_dir, path)) > 0, path)


class DartTemplateTest(unittest.TestCase):
    def test_right_to_left_is_forced_on_the_wrapper_not_left_to_the_locale(self):
        sys.path.insert(0, HERE)
        import via_capture_native
        self.assertIn("Directionality(", via_capture_native.DART_TEST)
        self.assertIn("TextDirection.rtl", via_capture_native.DART_TEST)


def filtered_png(width, height, filters):
    """An RGBA PNG like make_png, but row y is written with PNG filter filters[y % len(filters)], as screencap does."""
    def paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if pa <= pb and pa <= pc else b if pb <= pc else c
    raw, prior = b"", bytes(width * 4)
    for y in range(height):
        line = bytes(b for x in range(width) for b in ((x * 37 + y) % 256, (y * 53) % 256, (x * y) % 256, 255))
        kind, out = filters[y % len(filters)], bytearray()
        for i, value in enumerate(line):
            a, b, c = (line[i - 4] if i >= 4 else 0), prior[i], (prior[i - 4] if i >= 4 else 0)
            out.append((value - (0, a, b, (a + b) >> 1, paeth(a, b, c))[kind]) % 256)
        raw, prior = raw + bytes((kind,)) + bytes(out), line
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))


class CropTest(unittest.TestCase):
    """crop_png on PNGs that use every filter, as real `screencap -p` output does (adaptive filtering)."""

    def setUp(self):
        sys.path.insert(0, HERE)
        import via_capture_native
        self.crop = via_capture_native.crop_png

    def test_every_filter_is_undone_before_the_crop(self):
        width, height, box = 9, 15, (2, 3, 8, 14)
        with tempfile.TemporaryDirectory() as tmp:
            for name, filters in (("plain", [0]), ("each", [0, 1, 2, 3, 4])):
                paths = {}
                for kind, source in (("want", [0]), ("got", filters)):
                    paths[kind] = os.path.join(tmp, name + kind + ".png")
                    with open(paths[kind], "wb") as f:
                        f.write(self.crop(filtered_png(width, height, source), box))
                self.assertEqual(read_png(paths["got"]), read_png(paths["want"]), name)
            _, _, _, rows = read_png(paths["got"])
            self.assertEqual(rows[0][0], ((2 * 37 + 3) % 256, (3 * 53) % 256, 6, 255))

    def test_an_unknown_filter_is_refused(self):
        data = filtered_png(3, 2, [0])
        raw = bytearray(zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]))
        raw[13] = 9  # the second row's filter byte
        bad = data[:data.index(b"IDAT") - 4] + png_chunk(b"IDAT", zlib.compress(bytes(raw))) + png_chunk(b"IEND", b"")
        with self.assertRaisesRegex(ValueError, "unknown PNG filter 9"):
            self.crop(bad, (0, 0, 3, 2))

    def test_short_image_data_is_refused(self):
        data = filtered_png(3, 4, [0])
        raw = zlib.decompress(data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8])
        short = data[:data.index(b"IDAT") - 4] + png_chunk(b"IDAT", zlib.compress(raw[:20])) + png_chunk(b"IEND", b"")
        with self.assertRaisesRegex(ValueError, "shorter than its header"):
            self.crop(short, (0, 0, 3, 4))


class VocabularyTest(unittest.TestCase):
    """The shot words live in three places: the capture script, the record validator and the JSON schema."""

    def test_the_three_copies_agree(self):
        sys.path.insert(0, HERE)
        import via_capture_native
        import via_run
        with open(os.path.join(HERE, "..", "assets", "findings.schema.json"), encoding="utf-8") as f:
            schema = json.load(f)["properties"]
        shot = schema["screenshots"]["items"]["properties"]
        self.assertEqual(set(via_capture_native.STATES), via_run.SHOT_STATES)
        for key, words in (("state", via_run.SHOT_STATES), ("font_scale", via_run.FONT_SCALES),
                           ("direction", via_run.DIRECTIONS), ("source", via_run.SOURCES)):
            self.assertEqual(set(shot[key]["enum"]), words, key)
        self.assertEqual(set(schema["coverage"]["items"]["properties"]["kind"]["enum"]), via_run.COVERAGE_KINDS)
        self.assertEqual({e["font_scale"] for e in via_capture_native.build_plan(
            "compose", [{"screen": "a", "state": "default", "ref": "a"}], True)[0]}, via_run.FONT_SCALES)


if __name__ == "__main__":
    unittest.main()
