#!/usr/bin/env python3
"""Run every test suite of this repo. This is the check command in .nousfx/project.toml (NFX-54).

Each suite runs as its own process with this interpreter and -B (no __pycache__, which would change the
tree hash). Suites run in parallel (NFX-97), largest file first so the slow ones start early; `-j N` sets the
number at once (`-j 1` runs them one by one). Prints one line per suite as it finishes and exits 1 if any
failed. Output of a failing suite is shown. Suites must not write inside the repo: they run side by side, and
some read the real tree (test_ui_review_retired.py greps it).
"""
import argparse
import concurrent.futures
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = ["claude/hooks/test_*.py", "scripts/test_*.py", "plugins/via/scripts/test_*.py",
            "claude/skills/*/scripts/test_*.py"]


def jobs(argv):
    parser = argparse.ArgumentParser(description="Run every test suite of this repo.")
    parser.add_argument("-j", type=int, default=min(8, os.cpu_count() or 1), help="suites at once (default: up to 8)")
    n = parser.parse_args(argv[1:]).j
    if n < 1:
        parser.error("-j needs a positive number")
    return n


def run(path):
    start = time.monotonic()
    r = subprocess.run([sys.executable, "-B", path], cwd=ROOT, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    return path, r, time.monotonic() - start


def main(argv):
    suites = sorted(p for pat in PATTERNS for p in glob.glob(os.path.join(ROOT, pat)))
    if not suites:
        print("FAIL: no test suites found")
        return 1
    suites.sort(key=os.path.getsize, reverse=True)
    failed = []
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs(argv)) as pool:
        for future in concurrent.futures.as_completed([pool.submit(run, p) for p in suites]):
            path, r, took = future.result()
            rel = os.path.relpath(path, ROOT)
            print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {rel} ({took:.0f}s)", flush=True)
            if r.returncode:
                failed.append(rel)
                print((r.stdout + r.stderr)[-4000:], flush=True)
    print(f"{len(suites) - len(failed)}/{len(suites)} suites passed in {time.monotonic() - start:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
