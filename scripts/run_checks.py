#!/usr/bin/env python3
"""Run every test suite of this repo. This is the check command in .nousfx/project.toml (NFX-54).

Each suite runs as its own process with this interpreter and -B (no __pycache__, which would change the
tree hash). Prints one line per suite and exits 1 if any failed. Output of a failing suite is shown.
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = ["claude/hooks/test_*.py", "scripts/test_*.py", "plugins/via/scripts/test_*.py",
            "claude/skills/*/scripts/test_*.py"]


def main():
    suites = sorted(p for pat in PATTERNS for p in glob.glob(os.path.join(ROOT, pat)))
    if not suites:
        print("FAIL: no test suites found")
        return 1
    failed = []
    for path in suites:
        rel = os.path.relpath(path, ROOT)
        r = subprocess.run([sys.executable, "-B", path], cwd=ROOT, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {rel}", flush=True)
        if r.returncode:
            failed.append(rel)
            print((r.stdout + r.stderr)[-4000:], flush=True)
    print(f"{len(suites) - len(failed)}/{len(suites)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
