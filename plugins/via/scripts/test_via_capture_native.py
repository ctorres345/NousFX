#!/usr/bin/env python3
"""Tests for via_capture_native.py. Run: python3 -B plugins/via/scripts/test_via_capture_native.py

Each test runs the script as a subprocess on a throwaway project. Fake `gradlew`, `flutter` and `adb` programs
stand in for the real tools. The tests check what a caller sees: exit codes, stdout JSON and the files left behind.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

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
    elif cmd[:2] == ["dumpsys", "activity"]:
        state["dumpsys_calls"] = state.get("dumpsys_calls", 0) + 1
        out = state["dumpsys_later"] if state.get("dumpsys_later") and state["dumpsys_calls"] > 1 else state["dumpsys"]
if args[:2] == ["exec-out", "screencap"]:
    state["shots_seen"] = state.get("shots_seen", []) + [[state["night"], state["font_scale"]]]
with open(state_path, "w") as f:
    json.dump(state, f)
sys.stdout.write(out)
'''

DUMPSYS = "  topResumedActivity=ActivityRecord{1a2b u0 dev.demo/.MainActivity t12}\n"


class FallbackBase:
    """Mixed into the Compose and Flutter test classes: a fake adb and a running device."""

    def start_adb(self, devices=("emulator-5554",), **state):
        self.adb = os.path.join(self.bin, "adb")
        write(self.adb, FAKE_ADB % {"python": sys.executable, "png": PNG})
        os.chmod(self.adb, 0o755)
        self.adb_state = os.path.join(self.bin, "adb-state.json")
        write(self.adb_state, json.dumps({"devices": list(devices), "night": "no", "font_scale": "1.15",
                                          "dumpsys": DUMPSYS, "calls": [], **state}))
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
        self.assertEqual(json.loads(r.stdout)["screenshots"][0]["source"], "device")

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
