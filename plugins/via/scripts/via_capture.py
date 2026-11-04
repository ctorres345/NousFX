#!/usr/bin/env python3
"""via-capture: screenshots of web pages for a Via run, using headless Chrome or Chromium.

Usage: via_capture.py --run-dir RUN_DIR --screen NAME=PAGE [--screen NAME=PAGE ...] [--project DIR]

PAGE is an .html, .htm or .svg file inside the project (not in a hidden folder), or an http(s) URL that
answers with a page (a dev server). Each screen gets four shots in RUN_DIR: NAME-light-phone.png, NAME-dark-phone.png, NAME-light-desktop.png and NAME-dark-desktop.png.
NAME is lowercase letters, digits and hyphens. RUN_DIR is a folder made by `via_run.py init`.
Stdout is JSON: {"browser": ..., "screenshots": [...]}, with entries in the run record's shape.

Browser: $VIA_CHROME if set (and then only that), else Google Chrome or Chromium on macOS, else
google-chrome, chromium or chromium-browser on PATH.

Exit 0 success, 1 invalid input, 3 no browser found (say that visual checks were skipped),
4 a URL did not answer or the browser failed to write a shot. Shots land in RUN_DIR only when every one
succeeded.
"""
import argparse
import http.client
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

RUN_AREA = os.path.join(".via", "runs")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SIZES = {"phone": (390, 844), "desktop": (1280, 800)}
# Chrome's --blink-settings=preferredColorScheme value: 0 is dark and 1 is light (checked against real Chrome).
THEMES = {"light": 1, "dark": 0}
MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
PATH_BROWSERS = ("google-chrome", "chromium", "chromium-browser")
SHOT_TIMEOUT = 60
PROBE_TIMEOUT = 10
PAGE_SUFFIXES = (".html", ".htm", ".svg")


def die(message, code=1):
    print("Stopped: %s" % message, file=sys.stderr)
    sys.exit(code)


def real(path):
    return os.path.realpath(path)


def inside(path, root):
    path, root = real(path), real(root)
    return path != root and os.path.commonpath([path, root]) == root


def check_run_dir(project, run_arg):
    area = os.path.join(project, RUN_AREA)
    run_dir = os.path.abspath(os.path.join(project, run_arg))
    if any(os.path.islink(p) for p in (os.path.join(project, ".via"), area, run_dir)):
        die(".via or the run folder is a symlink. Nothing was written.")
    if not os.path.isdir(run_dir) or not inside(run_dir, area):
        die("%s is not a run folder made by `via_run.py init` in this project." % run_arg)
    return run_dir


def parse_screen(project, spec):
    name, sep, page = spec.partition("=")
    if not sep or not NAME_RE.match(name):
        die("--screen must look like name=page, with a name of lowercase letters, digits and hyphens: %r." % spec)
    if page.startswith(("http://", "https://")):
        return name, page
    if "://" in page:
        die("%s: only a file in the project or an http(s) URL can be captured." % name)
    path = os.path.join(project, page)
    if not os.path.isfile(path) or not inside(path, project):
        die("%s: %s is not a file inside the project." % (name, page))
    rel = os.path.relpath(real(path), real(project))
    if not rel.lower().endswith(PAGE_SUFFIXES) or any(part.startswith(".") for part in rel.split(os.sep)):
        die("%s: only .html, .htm and .svg pages outside hidden folders can be captured." % name)
    return name, pathlib.Path(real(path)).as_uri()


def problem_with(url):
    """None when the URL gives a page, else why not. Without this, Chrome shoots its own error page."""
    no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Chrome skips proxies for localhost
    try:
        with no_proxy.open(url, timeout=PROBE_TIMEOUT):
            return None
    except urllib.error.HTTPError as e:
        return "answered HTTP %d" % e.code
    except (OSError, ValueError, http.client.HTTPException) as e:
        return "could not be reached (%s). Is the dev server running?" % type(e).__name__


def find_browser():
    override = os.environ.get("VIA_CHROME")
    if override:  # an explicit choice is never replaced by a guess
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else None
    for path in MAC_BROWSERS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    for name in PATH_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def take(browser, profile, stage, shot):
    """Take one shot into the staging folder."""
    name, url = shot["path"], shot["url"]
    width, height = SIZES[shot["size"]]
    out = os.path.join(stage, name)
    cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
           "--no-default-browser-check", "--user-data-dir=" + profile,
           "--blink-settings=preferredColorScheme=%d" % THEMES[shot["theme"]],
           "--window-size=%d,%d" % (width, height), "--screenshot=" + out, url]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=SHOT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        die("the browser failed on %s (%s). No screenshots were kept." % (name, type(e).__name__), 4)
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        die("the browser wrote nothing for %s. No screenshots were kept." % name, 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--screen", action="append", default=[])
    ap.add_argument("--project")
    args = ap.parse_args()
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        die("%s is not a folder." % project)
    run_dir = check_run_dir(project, args.run_dir)
    if not args.screen:
        die("pass at least one --screen name=page.")
    screens = [parse_screen(project, s) for s in args.screen]
    if len({name for name, _ in screens}) != len(screens):
        die("each --screen name may be used once.")
    shots = [{"path": "%s-%s-%s.png" % (name, theme, size), "screen": name, "theme": theme, "size": size,
              "url": url} for name, url in screens for size in SIZES for theme in THEMES]
    for shot in shots:
        dest = os.path.join(run_dir, shot["path"])
        if os.path.islink(dest) or os.path.isdir(dest):
            die("%s in the run folder is a symlink or a folder. Nothing was written." % shot["path"])
    browser = find_browser()
    if browser is None:
        die("no Chrome or Chromium found (set VIA_CHROME to its path). Visual checks were skipped.", 3)
    for url in sorted({u for _, u in screens if u.startswith(("http://", "https://"))}):
        why = problem_with(url)
        if why:
            die("%s %s. No screenshots were taken." % (url, why), 4)
    work = tempfile.mkdtemp(prefix="via-capture-")  # holds the browser profile, so a running Chrome keeps out
    stage = tempfile.mkdtemp(prefix=".stage-", dir=run_dir)  # same filesystem, so os.replace is atomic
    try:
        for shot in shots:
            take(browser, os.path.join(work, "profile"), stage, shot)
        for shot in shots:
            os.replace(os.path.join(stage, shot["path"]), os.path.join(run_dir, shot["path"]))
    finally:
        shutil.rmtree(work, ignore_errors=True)  # a helper process may still hold files after a timeout
        shutil.rmtree(stage, ignore_errors=True)
    print(json.dumps({"browser": browser, "screenshots": [
        {**{k: v for k, v in shot.items() if k != "url"}, "redacted": False} for shot in shots]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
