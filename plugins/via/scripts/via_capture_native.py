#!/usr/bin/env python3
"""via-capture-native: screenshots of Compose and Flutter screens for a Via run.

Usage: via_capture_native.py detect  --project DIR
       via_capture_native.py capture --run-dir RUN_DIR [--project DIR] [--platform flutter|compose]

detect prints {"platforms": [...], "roots": {platform: folder}} (folders relative to the project). Flutter is a
pubspec.yaml with a top-level `flutter:` key. Compose is a Gradle build file that mentions `androidx.compose` or
`composeApp`, in a folder or in one of its modules. The project folder is looked at first, then each folder directly
under it (hidden and build folders are skipped). A project with both is reported per platform.

capture takes the screens from the app's own previews (a public function marked @Preview, with no parameters).
A preview name says its state: HomeEmptyPreview is the "empty" state of the "home" screen. Via never invents a state.
It adds a temporary test file to the project, runs it, and puts every file back as it found it, also after a failure.
Matrix: the default state is shot on a phone and a large screen, light and dark, at the largest font and, when the app
has a right-to-left translation (ar, he, fa, ur), right to left. Every other state is shot on a phone, light and dark.
Stdout is JSON: {"platforms": [...], "screenshots": [...], "coverage": [...]}. A shot has the run record's fields plus
platform, state, font_scale, direction and source ("preview" or "device"), and "redacted": false: native capture has no mask,
so Claude must look at each shot and set its `review` before anything leaves the run folder. A coverage entry says
what was captured and, for what was not, why.
Flutter: $VIA_FLUTTER if set, else `flutter` on PATH.
Compose: $VIA_GRADLEW if set, else the project's `gradlew`; an Android SDK (ANDROID_HOME, local.properties or the usual
folder). A preview must be public or internal and at the top of its file. Roborazzi and Robolectric come in through a Gradle
init script in the run folder, so no build file is edited; only the temporary test file is written, then removed. Gradle's own
build/ and .gradle/ folders stay, as after any build. The first run downloads the libraries and can take minutes.
$VIA_ROBORAZZI and $VIA_ROBOLECTRIC pick other library versions (an app on a much newer Compose may need a newer Roborazzi).
Emulator fallback: when a route fails (tool missing, build failed) and exactly one device is running ($ANDROID_SERIAL
picks one of several), Via shoots the screen now on it with `adb`: default state, phone light and dark, and at the
largest font. It never starts an emulator. Night mode and font scale are read first and put back, also after a failure.
Shots get source "device", and the output gets a "warnings" list. It shoots only when the foreground package matches an
applicationId or namespace of the project. A physical phone needs $VIA_ADB_ALLOW_DEVICE=1 or $ANDROID_SERIAL. Claude must
still look at each shot. A Flutter project with no android/ folder, no adb ($VIA_ADB, PATH or the SDK's platform-tools)
and no or several devices all keep the route's error and exit code, with the reason added.
Exit 0 success, 1 invalid input, 3 tool not found, 4 the capture failed (no shots are kept).
"""
import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from via_capture import check_run_dir, die  # noqa: E402

SKIP = {"build", "node_modules", "Pods"}
COMPOSE_RE = re.compile(r"androidx\.compose|composeApp")
FLUTTER_RE = re.compile(r"^flutter:", re.MULTILINE)
GRADLE_FILES = ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts")

STATES = ("default", "empty", "loading", "error", "offline", "long")  # in plan order; a test keeps it equal to via_run.SHOT_STATES
STATE_WORDS = {"empty": {"empty"}, "loading": {"loading"}, "error": {"error", "failure"}, "offline": {"offline"},
               "long": {"long", "overflow"}}
FILLER_WORDS = {"content", "text", "state", "data"}  # HomeLongContentPreview: "content" belongs to the state, not the screen
DEFAULT_MATRIX = (("light", "phone", "default", "ltr"), ("dark", "phone", "default", "ltr"),
                  ("light", "large", "default", "ltr"), ("dark", "large", "default", "ltr"),
                  ("light", "phone", "largest", "ltr"), ("light", "phone", "default", "rtl"))
MATRIX_NAMES = ("phone-light", "phone-dark", "large-light", "large-dark", "font-largest", "rtl")
OTHER_MATRIX = (("light", "phone", "default", "ltr"), ("dark", "phone", "default", "ltr"))
RUN_TIMEOUT = 600


# --- detect ---

def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def is_flutter(folder):
    return bool(FLUTTER_RE.search(read(os.path.join(folder, "pubspec.yaml"))))


def is_compose(folder):
    files = [os.path.join(folder, name) for name in GRADLE_FILES]
    files += glob.glob(os.path.join(folder, "*", "build.gradle")) + glob.glob(os.path.join(folder, "*", "build.gradle.kts"))
    return any(COMPOSE_RE.search(read(path)) for path in files)


def candidate_folders(project):
    yield "."
    for name in sorted(os.listdir(project)):
        if name.startswith(".") or name in SKIP or not os.path.isdir(os.path.join(project, name)):
            continue
        yield name


def detect(project):
    roots = {}
    for rel in candidate_folders(project):
        folder = os.path.join(project, rel)
        if "flutter" not in roots and is_flutter(folder):
            roots["flutter"] = rel
        if "compose" not in roots and is_compose(folder):
            roots["compose"] = rel
    return {"platforms": sorted(roots), "roots": dict(sorted(roots.items()))}


# --- previews and the matrix ---

def words(name):
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)]


def classify(function):
    """HomeEmptyPreview -> ("home", "empty"). A name with no state word is the default state."""
    parts = [w for w in words(function) if w not in ("preview", "previews")]
    state = next((s for w in parts for s, names in STATE_WORDS.items() if w in names), "default")
    if state != "default":
        parts = [w for w in parts if w not in STATE_WORDS[state] and w not in FILLER_WORDS]
    return "-".join(parts) or "screen", state


def shot_path(screen, state, theme, size, font, direction):
    name = "%s-%s-%s" % (screen if state == "default" else "%s-%s" % (screen, state), theme, size)
    return name + ("-font" if font == "largest" else "") + ("-rtl" if direction == "rtl" else "") + ".png"


def build_plan(platform, previews, rtl):
    """The shots to take and the coverage report, from the previews found. Nothing is made up."""
    screens = {}
    for p in previews:
        screens.setdefault(p["screen"], {}).setdefault(p["state"], p)
    plan, coverage = [], []

    def note(screen, entry, captured, reason=None, kind="matrix"):
        item = {"platform": platform, "screen": screen, "entry": entry, "kind": kind, "captured": captured}
        if reason:
            item["reason"] = reason
        coverage.append(item)

    for screen in sorted(screens):
        found = screens[screen]
        for state in STATES:
            preview = found.get(state)
            if preview is None:
                note(screen, "state:" + state, False, "no preview for this state", "state")
                continue
            note(screen, "state:" + state, True, kind="state")
            for theme, size, font, direction in (DEFAULT_MATRIX if state == "default" else OTHER_MATRIX):
                if direction == "rtl" and not rtl:
                    continue
                plan.append({"path": platform + "-" + shot_path(screen, state, theme, size, font, direction), "screen": screen,
                             "state": state, "theme": theme, "size": size, "font_scale": font, "direction": direction,
                             "ref": preview["ref"]})
        if "default" in found:
            for name in MATRIX_NAMES:
                if name == "rtl" and not rtl:
                    note(screen, name, False, "no right-to-left translation found")
                else:
                    note(screen, name, True)
        else:
            for name in MATRIX_NAMES:
                note(screen, name, False, "no default-state preview")
        if len(found) > ("default" in found):
            note(screen, "large, font and rtl for the other states", False, "not in the default matrix", "note")
    return plan, coverage


# --- Flutter ---

def balanced(text, start):
    """Index just after the parenthesis that closes the one at text[start]."""
    depth = 0
    for i in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0:
            return i + 1
    return -1


PREVIEW_AFTER_RE = re.compile(r"\s*(?:@\w+(?:\([^)]*\))?\s*)*[\w<>?]+\s+(\w+)\s*\(\s*(\)?)")


def flutter_previews(root):
    """Public, parameterless @Preview functions under lib/. Returns (previews, skipped)."""
    package = (re.search(r"^name:\s*(\S+)", read(os.path.join(root, "pubspec.yaml")), re.MULTILINE) or [None, "app"])[1]
    previews, skipped = [], []
    for path in sorted(glob.glob(os.path.join(root, "lib", "**", "*.dart"), recursive=True)):
        text = read(path)
        rel = os.path.relpath(path, os.path.join(root, "lib")).replace(os.sep, "/")
        for m in re.finditer(r"@Preview\(", text):
            end = balanced(text, m.end() - 1)
            after = PREVIEW_AFTER_RE.match(text, end) if end > 0 else None
            if not after:
                continue
            function = after.group(1)
            if function.startswith("_"):
                skipped.append((function, "the preview is private; make it public so Via can call it"))
            elif not after.group(2):
                skipped.append((function, "the preview takes parameters"))
            else:
                screen, state = classify(function)
                previews.append({"screen": screen, "state": state, "function": function,
                                 "import": "package:%s/%s" % (package, rel), "ref": None})
    for i, p in enumerate(previews):
        p["alias"] = "p%d" % i
        p["ref"] = "%s.%s" % (p["alias"], p["function"])
    return previews, skipped


RTL_LANGUAGES = "ar|he|iw|fa|ur"  # Arabic, Hebrew (old and new code), Persian, Urdu
ARB_RTL_RE = re.compile(r"_(%s)(?:_[A-Za-z]+)?\.arb$|\"@@locale\"\s*:\s*\"(%s)" % (RTL_LANGUAGES, RTL_LANGUAGES))


def flutter_has_rtl(root):
    """True when the app ships a translation in a right-to-left language, so a right-to-left shot is a real one."""
    for path in glob.glob(os.path.join(root, "lib", "**", "*.arb"), recursive=True):
        if ARB_RTL_RE.search(path) or ARB_RTL_RE.search(read(path)):
            return True
    return False


DART_TEST = """// Generated by Via (via_capture_native.py) for one screenshot run. Via removes this file afterwards.
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
__IMPORTS__

const String _plan = r'''__PLAN__''';

final Map<String, Widget Function()> _previews = {
__PREVIEWS__
};

Future<void> _font(String family, List<String> files) async {
  final loader = FontLoader(family);
  for (final file in files) {
    final bytes = File(file).readAsBytesSync();
    loader.addFont(Future.value(ByteData.view(bytes.buffer)));
  }
  await loader.load();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final out = Platform.environment['VIA_SHOT_DIR']!;
  final fonts = '${Platform.environment['FLUTTER_ROOT_DIR']}/bin/cache/artifacts/material_fonts';
  setUpAll(() async {
    await _font('MaterialIcons', ['$fonts/MaterialIcons-Regular.otf']);
    await _font('Roboto', Directory(fonts).listSync().whereType<File>().map((f) => f.path)
        .where((p) => p.contains('Roboto-')).toList());
  });
  for (final entry in (jsonDecode(_plan) as List).cast<Map<String, dynamic>>()) {
    testWidgets(entry['path'] as String, (t) async {
      final large = entry['size'] == 'large';
      final dark = entry['theme'] == 'dark';
      t.view.physicalSize = large ? const Size(1280, 800) : const Size(390, 844);
      t.view.devicePixelRatio = 1;
      t.platformDispatcher.platformBrightnessTestValue = dark ? Brightness.dark : Brightness.light;
      t.platformDispatcher.textScaleFactorTestValue = entry['font_scale'] == 'largest' ? 2.0 : 1.0;
      t.platformDispatcher.localesTestValue = [Locale(entry['direction'] == 'rtl' ? 'ar' : 'en')];
      addTearDown(() {
        t.view.reset();
        t.platformDispatcher.clearAllTestValues();
      });
      final widget = _previews[entry['ref']]!();
      final app = (widget is MaterialApp || widget is CupertinoApp)
          ? widget
          : MaterialApp(
              debugShowCheckedModeBanner: false,
              builder: (context, child) => Directionality(
                  textDirection: entry['direction'] == 'rtl' ? TextDirection.rtl : TextDirection.ltr, child: child!),
              theme: ThemeData(useMaterial3: true, brightness: dark ? Brightness.dark : Brightness.light),
              home: widget);
      await t.pumpWidget(RepaintBoundary(key: const Key('via-shot'), child: app));
      await t.pump(const Duration(milliseconds: 100));
      await t.runAsync(() async {
        final boundary = t.renderObject<RenderRepaintBoundary>(find.byKey(const Key('via-shot')));
        final image = await boundary.toImage(pixelRatio: large ? 1 : 2);
        final data = await image.toByteData(format: ui.ImageByteFormat.png);
        File('$out/${entry['path']}').writeAsBytesSync(data!.buffer.asUint8List());
      });
    });
  }
}
"""


class Touched:
    """Files a run writes into the project. restore() puts everything back as it was, also after a failure."""

    def __init__(self):
        self.originals = {}
        self.created_dirs = []

    def write(self, path, content):
        if path not in self.originals:
            self.originals[path] = read_bytes(path)
        folder = os.path.dirname(path)
        missing = []
        while not os.path.isdir(folder):
            missing.append(folder)
            folder = os.path.dirname(folder)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.created_dirs.extend(missing)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def restore(self):
        """Put every file back. One that cannot be restored is named on stderr, and the rest still are."""
        left = []
        for path, original in self.originals.items():
            try:
                if original is None:
                    if os.path.lexists(path):
                        os.remove(path)
                else:
                    with open(path, "wb") as f:
                        f.write(original)
            except OSError:
                left.append(path)
        for folder in self.created_dirs:  # innermost last, so remove in reverse
            shutil.rmtree(folder, ignore_errors=True)
        if left:
            print("Warning: could not restore %s. Check the project." % ", ".join(left), file=sys.stderr)


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def find_flutter():
    """$VIA_FLUTTER if set (and then only that), else `flutter` on PATH."""
    tool = os.environ.get("VIA_FLUTTER")
    tool = tool if tool is not None else shutil.which("flutter")
    if not tool or not os.path.isfile(tool) or not os.access(tool, os.X_OK):
        fail("no `flutter` found (set VIA_FLUTTER to its path). Native capture was skipped.", 3)
    return tool


class RouteFailed(Exception):
    """The preview route could not produce shots (tool missing, build failed). The emulator fallback may still run."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def fail(message, code=4):
    raise RouteFailed(message, code)


def tail(text, lines=6):
    errors = [l for l in text.splitlines() if l.startswith("e: ")]  # Kotlin compiler errors
    if errors:
        return " | ".join(e[:300] for e in errors[:4])
    if "* What went wrong:" in text:  # Gradle: the cause, not the help footer after it
        text = text.split("* What went wrong:", 1)[1].split("* Try:", 1)[0]
    return " | ".join(line.strip() for line in text.strip().splitlines()[-lines:] if line.strip())


def run_tool(cmd, cwd, env, what, timeout=RUN_TIMEOUT):
    try:
        done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        fail("%s failed to run (%s). No screenshots were kept." % (what, type(e).__name__), 4)
    if done.returncode != 0:
        fail("%s failed (exit %d): %s. No screenshots were kept." % (what, done.returncode, tail(done.stderr or done.stdout)), 4)


def refuse_existing(run_dir, paths):
    for path in paths:
        if os.path.lexists(os.path.join(run_dir, path)):  # a shot Claude already reviewed must never be swapped
            die("%s already exists in the run folder. Start a new run folder with `via_run.py init`. "
                "Nothing was written." % path)


def skipped_coverage(platform, skipped):
    return [{"platform": platform, "screen": "-", "entry": "preview:" + function, "kind": "preview", "captured": False,
             "reason": reason}
            for function, reason in skipped]


def finish_shots(platform, tool, plan, stage):
    """The run record's shot entries for a finished route, after checking that the tool wrote every file."""
    for entry in plan:
        path = os.path.join(stage, entry["path"])
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            fail("%s wrote nothing for %s. No screenshots were kept." % (tool, entry["path"]), 4)
    return [{**{k: v for k, v in e.items() if k != "ref"}, "platform": platform, "redacted": False, "source": "preview"}
            for e in plan]


def capture_flutter(project, rel_root, stage, run_dir):
    root = os.path.normpath(os.path.join(project, rel_root))
    previews, skipped = flutter_previews(root)
    plan, coverage = build_plan("flutter", previews, flutter_has_rtl(root))
    coverage += skipped_coverage("flutter", skipped)
    if not plan:
        return [], coverage, []
    refuse_existing(run_dir, [e["path"] for e in plan])
    flutter = find_flutter()
    test_file = os.path.join(root, "test", "zz_via_shots_test.dart")
    if os.path.lexists(test_file):
        die("%s already exists. Delete it and run again. Nothing was written." % os.path.relpath(test_file, project))
    wanted = {e["ref"] for e in plan}
    imports = "\n".join("import '%s' as %s;" % (p["import"], p["alias"]) for p in previews if p["ref"] in wanted)
    refs = "\n".join("  '%s': %s," % (p["ref"], p["ref"]) for p in previews if p["ref"] in wanted)
    code = (DART_TEST.replace("__IMPORTS__", imports).replace("__PLAN__", json.dumps(plan)).replace("__PREVIEWS__", refs))
    touched = Touched()
    env = {**os.environ, "VIA_SHOT_DIR": stage,
           "FLUTTER_ROOT_DIR": os.path.dirname(os.path.dirname(os.path.realpath(flutter)))}
    try:
        touched.write(test_file, code)
        run_tool([flutter, "test", os.path.relpath(test_file, root)], root, env, "flutter test")
    finally:
        touched.restore()
    return finish_shots("flutter", "flutter test", plan, stage), coverage, []


# --- Compose ---

# Roborazzi must not be newer than the app's Compose: 1.77.0 needs Kotlin 2.3 and a recent Compose runtime and fails on an
# app with Kotlin 2.1 / Compose BOM 2024.10 (NoSuchMethodError in Composer). 1.43.0 is verified on that app. An older
# library on a newer Compose is the safe direction. Override with $VIA_ROBORAZZI and $VIA_ROBOLECTRIC.
ROBORAZZI = "1.43.0"
ROBOLECTRIC = "4.17"  # 4.18 is still a beta
GRADLE_TIMEOUT = 1800  # the first run downloads Roborazzi and Robolectric
TEST_CLASS = "via.ZzViaShotsTest"
KOTLIN_PREVIEW_RE = re.compile(r"@Preview\b(?!\w)")
KOTLIN_AFTER_RE = re.compile(r"\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*((?:\w+\s+)*)fun\s+(?:<[^>]*>\s*)?(\w+)\s*\(([^)]*)\)")

INIT_SCRIPT = """// Generated by Via (via_capture_native.py). Adds Roborazzi and Robolectric to a screenshot run without editing a build file.
allprojects {
    def setup = { project ->
        ["io.github.takahirom.roborazzi:roborazzi:%(rr)s", "io.github.takahirom.roborazzi:roborazzi-compose:%(rr)s",
         "io.github.takahirom.roborazzi:roborazzi-junit-rule:%(rr)s", "org.robolectric:robolectric:%(rl)s",
         "androidx.compose.ui:ui-test-junit4", "androidx.test.ext:junit:1.2.1", "junit:junit:4.13.2"].each {
            project.dependencies.add("testImplementation", it)
        }
        project.dependencies.add("debugImplementation", "androidx.compose.ui:ui-test-manifest")
        project.extensions.getByName("android").testOptions.unitTests.includeAndroidResources = true
        project.tasks.withType(Test).configureEach {
            systemProperty "roborazzi.test.record", "true"
            systemProperty "robolectric.pixelCopyRenderMode", "hardware"
            outputs.upToDateWhen { false }
        }
    }
    plugins.withId("com.android.application") { setup(project) }
    plugins.withId("com.android.library") { setup(project) }
}
"""

KOTLIN_TEST = """// Generated by Via (via_capture_native.py) for one screenshot run. Via removes this file afterwards.
package via

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.github.takahirom.roborazzi.captureRoboImage
import java.io.File
import org.json.JSONArray
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

private const val PLAN = \"\"\"__PLAN__\"\"\"
private const val STAGE = "__STAGE__"

@Composable
private fun ViaPreview(ref: String) {
    when (ref) {
__BRANCHES__
        else -> error("unknown preview " + ref)
    }
}

@RunWith(AndroidJUnit4::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34])
class ZzViaShotsTest {
    @Test
    fun shots() {
        val plan = JSONArray(PLAN)
        for (i in 0 until plan.length()) {
            val e = plan.getJSONObject(i)
            val dark = e.getString("theme") == "dark"
            val large = e.getString("size") == "large"
            val rtl = e.getString("direction") == "rtl"
            val fontScale = if (e.getString("font_scale") == "largest") 2f else 1f
            val size = if (large) "w1280dp-h800dp" else "w390dp-h844dp"
            val density = if (large) "mdpi" else "xhdpi"
            val locale = if (rtl) "ar-ldrtl-" else ""
            RuntimeEnvironment.setQualifiers("$locale$size${if (dark) "-night" else ""}-$density")
            captureRoboImage(File(STAGE, e.getString("path"))) {
                val base = LocalDensity.current
                CompositionLocalProvider(LocalDensity provides Density(base.density, fontScale)) {
                    Box(Modifier.fillMaxSize().background(if (dark) Color(0xFF121212) else Color.White)) {
                        ViaPreview(e.getString("ref"))
                    }
                }
            }
        }
    }
}
"""


def compose_modules(root):
    """Android modules (their build file mentions com.android) in the root folder or up to two folders below it."""
    found = []
    for folder, dirs, _ in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP)
        depth = 0 if folder == root else os.path.relpath(folder, root).count(os.sep) + 1
        if depth > 2:
            dirs[:] = []
            continue
        for name in ("build.gradle", "build.gradle.kts"):
            if "com.android" in read(os.path.join(folder, name)):
                found.append(os.path.relpath(folder, root))
                break
    return found


def compose_previews(module):
    """Public, parameterless, file-level @Preview functions. Returns (previews, skipped)."""
    previews, skipped = [], []
    files = []
    for variant in ("main", "debug"):
        for lang in ("java", "kotlin"):
            files += glob.glob(os.path.join(module, "src", variant, lang, "**", "*.kt"), recursive=True)
    for path in sorted(files):
        text = read(path)
        package = (re.search(r"^package\s+([\w.]+)", text, re.MULTILINE) or [None, ""])[1]
        for m in KOTLIN_PREVIEW_RE.finditer(text):
            line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
            pos = m.end()
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if text.startswith("(", pos):
                pos = balanced(text, pos)
                if pos < 0:
                    continue
            after = KOTLIN_AFTER_RE.match(text, pos)
            if not after:
                continue
            function = after.group(2)
            if "private" in after.group(1).split():
                skipped.append((function, "the preview is private; make it internal or public so Via can call it"))
            elif after.group(3).strip():
                skipped.append((function, "the preview takes parameters"))
            elif line != line.lstrip():  # indented: a member of a class or object
                skipped.append((function, "the preview is inside a class; move it to the top of the file"))
            else:
                screen, state = classify(function)
                previews.append({"screen": screen, "state": state, "function": function,
                                 "ref": "%s.%s" % (package, function) if package else function})
    return previews, skipped


def compose_has_rtl(module):
    """True when the app ships strings in a right-to-left language (values-ar, values-iw, ...)."""
    rtl = re.compile(r"values-(%s)(-r[A-Z]{2})?$" % RTL_LANGUAGES)
    return any(rtl.search(os.path.basename(os.path.dirname(p)))
               for p in glob.glob(os.path.join(module, "src", "main", "res", "values-*", "strings.xml")))


def find_gradlew(root):
    """$VIA_GRADLEW if set (and then only that), else the project's own gradlew."""
    tool = os.environ.get("VIA_GRADLEW")
    tool = tool if tool is not None else os.path.join(root, "gradlew")
    if not os.path.isfile(tool) or not os.access(tool, os.X_OK):
        fail("no `gradlew` found (set VIA_GRADLEW to its path). Native capture was skipped.", 3)
    return tool


def env_sdks():
    return [os.environ[n] for n in ("ANDROID_HOME", "ANDROID_SDK_ROOT") if os.path.isdir(os.environ.get(n) or "")]


def home_sdks():
    return [p for p in (os.path.expanduser(c) for c in ("~/Library/Android/sdk", "~/Android/Sdk")) if os.path.isdir(p)]


def find_android_sdk(root):
    """The SDK folder to export as ANDROID_HOME, or None when Gradle can find it itself (env or local.properties)."""
    if env_sdks() or re.search(r"^sdk\.dir\s*=", read(os.path.join(root, "local.properties")), re.MULTILINE):
        return None
    if home_sdks():
        return home_sdks()[0]
    fail("no Android SDK found (set ANDROID_HOME). Native capture was skipped.", 3)


def kotlin_string(text):
    return text.replace("\\", "\\\\").replace("$", "\\$").replace('"', '\\"')


def capture_compose(project, rel_root, stage, run_dir):
    root = os.path.normpath(os.path.join(project, rel_root))
    modules = compose_modules(root)
    if not modules:
        return [], [{"platform": "compose", "screen": "-", "entry": "compose-multiplatform", "kind": "note", "captured": False,
                     "reason": "no Android module found; Compose Multiplatform previews are not supported yet"}], []
    plan, coverage, jobs = [], [], []
    for rel in modules:
        module = os.path.normpath(os.path.join(root, rel))
        previews, skipped = compose_previews(module)
        module_plan, module_coverage = build_plan("compose", previews, compose_has_rtl(module))
        coverage += module_coverage + skipped_coverage("compose", skipped)
        if module_plan:
            plan += module_plan
            jobs.append((module, previews, module_plan))
    if not plan:
        return [], coverage, []
    paths = [e["path"] for e in plan]
    if len(set(paths)) != len(paths):
        die("two modules have a screen with the same name. Rename one preview. Nothing was written.")
    refuse_existing(run_dir, paths)
    gradlew = find_gradlew(root)
    sdk = find_android_sdk(root)
    tests = [os.path.join(module, "src", "test", "kotlin", "via", "ZzViaShotsTest.kt") for module, _, _ in jobs]
    for test_file in tests:
        if os.path.lexists(test_file):
            die("%s already exists. Delete it and run again. Nothing was written." % os.path.relpath(test_file, project))
    init = os.path.join(stage, "via-roborazzi.init.gradle")
    with open(init, "w", encoding="utf-8") as f:
        f.write(INIT_SCRIPT % {"rr": os.environ.get("VIA_ROBORAZZI") or ROBORAZZI,
                               "rl": os.environ.get("VIA_ROBOLECTRIC") or ROBOLECTRIC})
    cmd = [gradlew, "--console=plain", "--init-script", init]
    touched = Touched()
    try:
        for (module, previews, module_plan), test_file in zip(jobs, tests):
            wanted = {e["ref"] for e in module_plan}
            branches = "\n".join('        "%s" -> %s()' % (p["ref"], p["ref"]) for p in previews if p["ref"] in wanted)
            code = (KOTLIN_TEST.replace("__PLAN__", json.dumps(module_plan)).replace("__STAGE__", kotlin_string(stage))
                    .replace("__BRANCHES__", branches))
            touched.write(test_file, code)
            task = ":".join(["", *[p for p in os.path.relpath(module, root).split(os.sep) if p != "."], "testDebugUnitTest"])
            cmd += [task, "--tests", TEST_CLASS]
        env = {**os.environ, **({"ANDROID_HOME": sdk} if sdk else {})}
        run_tool(cmd, root, env, "gradle", GRADLE_TIMEOUT)
    finally:
        touched.restore()
    return finish_shots("compose", "gradle", plan, stage), coverage, []


# --- emulator fallback ---

ADB_TIMEOUT = 60
FONT_LARGEST = "2.0"
FALLBACK_MATRIX = (("light", "default"), ("dark", "default"), ("light", "largest"))


def find_adb():
    """$VIA_ADB if set (and then only that), else `adb` on PATH, else the SDK's platform-tools. None when there is none."""
    tool = os.environ.get("VIA_ADB")
    if tool is None:
        tool = shutil.which("adb")
    if tool is None:
        tool = next((c for c in (os.path.join(d, "platform-tools", "adb") for d in env_sdks() + home_sdks())
                     if os.path.isfile(c)), None)
    return tool if tool and os.path.isfile(tool) and os.access(tool, os.X_OK) else None


def adb(tool, serial, *args, binary=False):
    """Run adb against one device. Returns stdout (bytes when binary), or raises RouteFailed."""
    cmd = [tool, *(["-s", serial] if serial else []), *args]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=ADB_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        fail("adb %s failed to run (%s)." % (" ".join(args[:2]), type(e).__name__))
    if done.returncode != 0:
        fail("adb %s failed (exit %d): %s" % (" ".join(args[:2]), done.returncode,
                                              tail(done.stderr.decode("utf-8", "replace"))))
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def pick_device(tool):
    """(serial, None) for the one running device, or (None, why not)."""
    try:
        listing = adb(tool, None, "devices")
    except RouteFailed as e:
        return None, str(e)
    devices = [l.split("\t")[0] for l in listing.splitlines()[1:] if l.strip().endswith("device")]
    wanted = os.environ.get("ANDROID_SERIAL")
    if wanted:  # an explicit pick is allowed to be a real phone
        return (wanted, None) if wanted in devices else (None, "ANDROID_SERIAL %s is not a running device" % wanted)
    if not os.environ.get("VIA_ADB_ALLOW_DEVICE"):
        phones = [d for d in devices if not d.startswith("emulator-")]
        devices = [d for d in devices if d.startswith("emulator-")]
        if phones and not devices:
            return None, ("only a physical device is running, and its screen may show personal data; start an "
                          "emulator, or set VIA_ADB_ALLOW_DEVICE=1 or ANDROID_SERIAL to use it")
    if not devices:
        return None, "no running device or emulator (the fallback never starts one)"
    if len(devices) > 1:
        return None, "%d devices are running; set ANDROID_SERIAL to pick one" % len(devices)
    return devices[0], None


APP_ID_RE = re.compile(r"""(?:applicationId|namespace)\s*(?:=\s*)?["']([\w.]+)["']""")
RESUMED_RE = re.compile(r"(?:topResumedActivity|mResumedActivity)[=:]\s*\S+ \S+ ([\w.]+)/\.?([\w.$]+)")


def app_ids(platform, root):
    """The Android application ids (or namespaces) the project declares: what its package is called on a device."""
    if platform == "flutter":
        folders = [os.path.join(root, "android", "app")]
    else:
        folders = [os.path.normpath(os.path.join(root, rel)) for rel in compose_modules(root)]
    found = set()
    for folder in folders:
        for name in ("build.gradle", "build.gradle.kts"):
            found.update(APP_ID_RE.findall(read(os.path.join(folder, name))))
    return found


def on_screen(dump):
    """(package, screen name) of the resumed activity: dev.demo/.MainActivity -> ("dev.demo", "main")."""
    m = RESUMED_RE.search(dump)
    if not m:
        return None, "current"
    parts = [w for w in words(m.group(2).split(".")[-1].split("$")[0]) if w != "activity"]
    return m.group(1), "-".join(parts) or "current"


def capture_emulator(platform, stage, run_dir, root, why, code):
    """Shoot the screen now on the one running device: default state, phone light and dark, and at the largest font.
    Night mode and font scale are read first and put back after, also after a failure. Raises RouteFailed with the
    reason when there is nothing to fall back to: with the route's exit code `code` if the device was never touched, else 4."""
    why = why.rstrip(". ") + "."
    try:
        settle = float(os.environ.get("VIA_ADB_SETTLE") or 1.0)  # time for the device to redraw after a setting changes
    except ValueError:
        die("VIA_ADB_SETTLE must be a number of seconds.")
    if platform == "flutter" and not os.path.isdir(os.path.join(root, "android")):
        fail("%s The emulator fallback needs the project's android/ folder, and there is no android/ folder." % why, code)
    tool = find_adb()
    if not tool:
        fail("%s The emulator fallback needs `adb`, and no `adb` was found (set VIA_ADB to its path)." % why, code)
    serial, problem = pick_device(tool)
    if not serial:
        fail("%s No emulator fallback: %s." % (why, problem), code)
    ids = app_ids(platform, root)
    if not ids:
        fail("%s The device screen cannot be checked: no applicationId or namespace was found in the project's "
             "build files." % why, code)

    def foreground(exit_code):
        """The screen name and package now on the device, or a failure when it is not the app."""
        package, name = on_screen(adb(tool, serial, "shell", "dumpsys", "activity", "activities"))
        if not package or not any(package == i or package.startswith(i + ".") for i in ids):
            fail("%s The device is not showing the app (on screen: %s; the project is %s)."
                 % (why, package or "unknown", ", ".join(sorted(ids))), exit_code)
        return package, name

    try:
        package, screen = foreground(code)
        night = adb(tool, serial, "shell", "cmd", "uimode", "night").split(":")[-1].strip() or "auto"
        font_was = adb(tool, serial, "shell", "settings", "get", "system", "font_scale").strip()
    except RouteFailed as e:  # nothing on the device has changed yet
        fail(str(e) if why in str(e) else "%s %s" % (why, e), code)
    plan = [{"path": platform + "-" + shot_path(screen, "default", theme, "phone", font, "ltr"), "screen": screen,
             "state": "default",
             "theme": theme, "size": "phone", "font_scale": font, "direction": "ltr"} for theme, font in FALLBACK_MATRIX]
    refuse_existing(run_dir, [e["path"] for e in plan])
    try:
        for entry in plan:
            adb(tool, serial, "shell", "cmd", "uimode", "night", "yes" if entry["theme"] == "dark" else "no")
            adb(tool, serial, "shell", "settings", "put", "system", "font_scale",
                FONT_LARGEST if entry["font_scale"] == "largest" else "1.0")
            time.sleep(settle)
            foreground(4)  # a changed setting can restart the app, and another app may have come forward
            png = adb(tool, serial, "exec-out", "screencap", "-p", binary=True)
            if not png.startswith(b"\x89PNG"):
                fail("adb screencap did not return a PNG for %s." % entry["path"])
            with open(os.path.join(stage, entry["path"]), "wb") as f:
                f.write(png)
    finally:
        restore = (("settings", "delete", "system", "font_scale") if font_was in ("", "null")
                   else ("settings", "put", "system", "font_scale", font_was))
        for command in (("cmd", "uimode", "night", night), restore):
            try:
                adb(tool, serial, "shell", *command)
            except RouteFailed:
                print("Warning: could not restore the device setting `%s`. Check the device." % " ".join(command),
                      file=sys.stderr)
    shots = [{**e, "platform": platform, "redacted": False, "source": "device"} for e in plan]
    reason = "the emulator fallback shoots only the screen now on the device"
    taken = ("phone-light", "phone-dark", "font-largest")
    coverage = [{"platform": platform, "screen": screen, "entry": name, "kind": "matrix", "captured": name in taken,
                 **({} if name in taken else {"reason": reason})} for name in MATRIX_NAMES]
    coverage.append({"platform": platform, "screen": screen, "entry": "other states", "kind": "note",
                     "captured": False, "reason": reason})
    warnings = [why, "Fell back to the running device %s, which showed %s. Look at each shot before you keep it."
                % (serial, package)]
    return shots, coverage, warnings


# --- main ---

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("detect")
    d.add_argument("--project")
    c = sub.add_parser("capture")
    c.add_argument("--run-dir", required=True)
    c.add_argument("--project")
    c.add_argument("--platform", choices=("flutter", "compose"))
    args = ap.parse_args()
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        die("%s is not a folder." % project)
    if args.command == "detect":
        print(json.dumps(detect(project)))
        return 0
    run_dir = check_run_dir(project, args.run_dir)
    found = detect(project)
    platforms = [args.platform] if args.platform else found["platforms"]
    for name in platforms:
        if name not in found["roots"]:
            die("no %s project found in %s." % (name, project))
    if not platforms:
        die("no Flutter or Compose project found in %s." % project)
    for sig in (signal.SIGTERM, signal.SIGHUP):  # a tool timeout or a closed terminal must still run the restores
        signal.signal(sig, lambda *_: sys.exit(143))
    stage = tempfile.mkdtemp(prefix=".stage-", dir=run_dir)  # same filesystem, so os.replace is atomic
    shots, coverage, warnings = [], [], []
    try:
        for name in platforms:
            root = found["roots"][name]
            try:
                got = (capture_flutter if name == "flutter" else capture_compose)(project, root, stage, run_dir)
            except RouteFailed as route:
                try:
                    got = capture_emulator(name, stage, run_dir, os.path.normpath(os.path.join(project, root)),
                                           str(route), route.code)
                except RouteFailed as problem:
                    die(str(problem), problem.code)
            shots += got[0]
            coverage += got[1]
            warnings += got[2]
        refuse_existing(run_dir, [shot["path"] for shot in shots])  # two platforms may share a screen name
        for shot in shots:
            os.replace(os.path.join(stage, shot["path"]), os.path.join(run_dir, shot["path"]))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    print(json.dumps({"platforms": platforms, "screenshots": shots, "coverage": coverage,
                      **({"warnings": warnings} if warnings else {})}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
