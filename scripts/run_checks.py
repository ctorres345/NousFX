#!/usr/bin/env python3
"""Run every test suite of this repo. This is the check command in .nousfx/project.toml (NFX-54).

Each suite runs as its own process with this interpreter and -B (no __pycache__, which would change the
tree hash). Suites run in parallel (NFX-97), cheapest first (NFX-134): by the seconds recorded on the last run in the
git dir (nousfx-check-times.json, outside the tree), else by file size. A failing fast suite then shows up
before the slow ones finish. `-j N` sets the number at once (`-j 1` runs them one by one). Prints one line per
suite as it finishes and exits 1 if any failed. Output of a failing suite is shown. `--fail-fast` stops after
the first failure (a quick local loop); the review gate never passes it, so a stamp is always a full pass. Suites must not write inside the repo: they run side by side, and
some read the real tree (test_ui_review_retired.py greps it).
"""
import argparse
import concurrent.futures
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = ["claude/hooks/test_*.py", "scripts/test_*.py", "plugins/via/scripts/test_*.py",
            "claude/skills/*/scripts/test_*.py"]


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run every test suite of this repo.")
    parser.add_argument("-j", type=int, default=min(8, os.cpu_count() or 1), help="suites at once (default: up to 8)")
    parser.add_argument("--fail-fast", action="store_true", help="stop after the first failing suite")
    args = parser.parse_args(argv[1:])
    if args.j < 1:
        parser.error("-j needs a positive number")
    return args


def times_path():
    """Where last run's seconds per suite live: the git dir, so the tree hash is untouched. None outside git."""
    r = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=ROOT, capture_output=True, text=True)
    return os.path.join(r.stdout.strip(), "nousfx-check-times.json") if r.returncode == 0 else None


def load_times(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def save_times(path, times):
    try:
        with open(path, "w") as f:
            json.dump(times, f, indent=1, sort_keys=True)
    except OSError:
        pass


def order(suites, times):
    """Cheapest first. Seconds from the last run win; without a record the file size stands in (smaller first).
    Only the order changes, never which suites run."""
    def cost(p):
        recorded = times.get(os.path.basename(p))
        return (0, recorded) if recorded is not None else (1, os.path.getsize(p))
    return sorted(suites, key=cost)


def run(path):
    start = time.monotonic()
    r = subprocess.run([sys.executable, "-B", path], cwd=ROOT, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True)
    return path, r, time.monotonic() - start


def main(argv):
    args = parse_args(argv)
    suites = sorted(p for pat in PATTERNS for p in glob.glob(os.path.join(ROOT, pat)))
    if not suites:
        print("FAIL: no test suites found")
        return 1
    tpath = times_path()
    times = load_times(tpath) if tpath else {}
    suites = order(suites, times)
    failed, seen = [], 0
    start = time.monotonic()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.j)
    futures = [pool.submit(run, p) for p in suites]
    try:
        for future in concurrent.futures.as_completed(futures):
            path, r, took = future.result()
            rel = os.path.relpath(path, ROOT)
            seen += 1
            times[os.path.basename(path)] = round(took, 2)
            print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {rel} ({took:.0f}s)", flush=True)
            if r.returncode:
                failed.append(rel)
                print((r.stdout + r.stderr)[-4000:], flush=True)
                if args.fail_fast:
                    break
    finally:  # on a fail-fast break this cancels the suites that have not started
        pool.shutdown(wait=True, cancel_futures=True)
    if tpath and not (args.fail_fast and failed):
        save_times(tpath, times)  # a stopped run is a partial picture; only full runs refresh the record
    if args.fail_fast and failed:
        print(f"stopped after first failure: {seen}/{len(suites)} suites run")
        return 1
    print(f"{len(suites) - len(failed)}/{len(suites)} suites passed in {time.monotonic() - start:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
