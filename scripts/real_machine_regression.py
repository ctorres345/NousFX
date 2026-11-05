#!/usr/bin/env python3
"""Real-machine regression for nousfx_sync. Not run in CI.

Generates the Claude rules into a throwaway home, using the real repo sources and the real local
file, then diffs the result against the real ~/.claude/CLAUDE.md and writes the diff to --out.

    python3 -B scripts/real_machine_regression.py --out DIR

Safety: the real home is only read (CLAUDE.md and the state home's local.md). The sync runs with
--home pointing at a temp dir, so nothing real is written. A --home equal to the real home is refused.
"""
import argparse
import difflib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nousfx_sync  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="folder for the diff file (for example a scratchpad)")
    ap.add_argument("--real-home", help="home to compare against (default: from the user record)")
    args = ap.parse_args()

    real = os.path.realpath(args.real_home or nousfx_sync.user_home())
    state = os.path.join(real, ".config", "nousfx")
    real_rules = os.path.join(real, ".claude", "CLAUDE.md")
    with open(real_rules, encoding="utf-8") as f:
        current = f.read()

    with tempfile.TemporaryDirectory(prefix="nousfx-regress-") as tmp:
        if os.path.realpath(tmp) == real:
            sys.exit("refusing: temp home equals the real home")
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "nousfx_sync.py"),
                            "--home", tmp, "--state-home", state, "--dry-run"],
                           capture_output=True, text=True)
        # Dry-run never writes, even to the temp home or the real state home. Rebuild the rules text
        # through the same function the command uses.
        repo = os.path.dirname(HERE)
        generated = nousfx_sync.build_rules(repo, state, nousfx_sync.commit_of(repo))
        if r.returncode != 0:
            sys.exit(f"sync dry-run failed: {r.stderr}")

    diff = "".join(difflib.unified_diff(current.splitlines(keepends=True), generated.splitlines(keepends=True),
                                        fromfile=real_rules, tofile="generated CLAUDE.md"))
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "claude-rules.diff")
    with open(out, "w", encoding="utf-8") as f:
        f.write(diff)
    print(f"{len(diff.splitlines())} diff lines written to {out}" if diff else f"no difference; empty diff at {out}")


if __name__ == "__main__":
    main()
