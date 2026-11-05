#!/usr/bin/env python3
"""NousFX session-start drift warning (SessionStart hook for Claude Code). NFX-57.

Tells me when this machine's harness is behind, so the Mac and the Windows PC do not drift apart:
  1. the installed files differ from what sync would write (`nousfx_sync.py --check --summary`);
  2. my local NousFX clone is behind origin/main (the origin/main of the last fetch).
It prints one JSON line (a message for me and the same text as context for the model), or nothing.

Rules this file keeps:
  - It never fixes anything: no sync, no pull. The only write is the background `git fetch`
    that keeps origin/main fresh for the NEXT session (never waited for, at most every 10 minutes).
  - It never fails the session start: every error, timeout or odd output means "say nothing", exit 0.
  - It stays under a 2 second budget; whatever is not finished by then is skipped quietly.
  - Standard library only. The hook changes into the clone first, so on Windows a `git.exe` sitting in the
    project I opened is never the one that runs.

Arguments (sync writes them into the hook command): --repo DIR --tools claude[,agy].
Test seams: --home, --state-home (passed on to sync) and --platform posix|windows.
"""
import argparse
import json
import os
import subprocess
import sys
import time

BUDGET = 2.0  # seconds for the whole hook
GIT_WAIT = 0.6  # most the "how far behind" question may take
FETCH_EVERY = 600  # seconds between background fetches


def run(cmd, cwd, timeout):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def drift_counts(args, deadline):
    """The counts sync reports, or None when it says clean or cannot be trusted."""
    sync = os.path.join(args.repo, "scripts", "nousfx_sync.py")
    # -E -s: PYTHON* variables and user site-packages must not shadow the standard library. Not -I: sync
    # imports its sibling modules, and -I would leave its own folder off the module path.
    cmd = [sys.executable, "-E", "-s", "-B", sync, "--check", "--summary", "--tools", args.tools]
    for flag, value in (("--home", args.home), ("--state-home", args.state_home)):
        if value:
            cmd += [flag, value]
    left = deadline - time.monotonic()
    r = run(cmd, args.repo, left) if left > 0 else None
    if r is None or r.returncode != 1:  # 0 = clean; 2 or a crash = nothing to trust
        return None
    try:
        counts = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return counts if isinstance(counts, dict) else None


def commits_behind(args, deadline):
    left = min(GIT_WAIT, deadline - time.monotonic())
    r = run(["git", "rev-list", "--count", "HEAD..origin/main"], args.repo, left) if left > 0 else None
    if r is None or r.returncode != 0:
        return 0
    try:
        return max(int(r.stdout.strip()), 0)
    except ValueError:
        return 0


def start_background_fetch(args):
    """Refresh origin/main for the next session. Never waited for; skipped when one ran recently."""
    try:
        stamp = os.path.join(args.repo, ".git", "FETCH_HEAD")
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < FETCH_EVERY:
            return
        kwargs = {"cwd": args.repo, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL}
        # never ask for a password: no terminal here, and a credential helper must not pop a window
        kwargs["env"] = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        if os.name == "nt":  # the real OS, not the --platform test seam (Popen rejects creationflags on POSIX)
            kwargs["creationflags"] = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(["git", "fetch", "--quiet", "origin", "main"], **kwargs)
    except (OSError, ValueError):
        pass


def plural(n, noun):
    return f"{n} {noun}" + ("" if n == 1 else "s")


def message(args, counts, behind):
    win = args.platform == "windows" or (args.platform is None and os.name == "nt")
    sync = f"{'python' if win else 'python3'} -B scripts/nousfx_sync.py"
    if args.tools != "claude":
        sync += f" --tools {args.tools}"
    if counts:
        parts = []
        if counts.get("files"):
            parts.append(plural(counts["files"], "file"))
        if counts.get("skills"):
            parts.append(f"{plural(counts['skills'], 'skill')} {'needs' if counts['skills'] == 1 else 'need'} a decision")
        if counts.get("outside"):
            parts.append(f"{plural(counts['outside'], 'outside skill')} to fetch")
        head = "NousFX on this machine is out of date" + (f" ({', '.join(parts)})" if parts else "")
        if behind:
            head += f", and the clone is {plural(behind, 'commit')} behind origin/main"
    else:
        head = f"Your NousFX clone is {plural(behind, 'commit')} behind origin/main"
    action = f"Run git pull, then {sync}" if behind else f"Run {sync}"
    return f"{head}. {action} in {args.repo}."


def main(argv=None):
    started = time.monotonic()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tools", default="claude")
    ap.add_argument("--home")
    ap.add_argument("--state-home")
    ap.add_argument("--platform", choices=("posix", "windows"))
    args, _ = ap.parse_known_args(argv)
    if not os.path.isfile(os.path.join(args.repo, "scripts", "nousfx_sync.py")):
        return 0  # no clone on this machine: nothing to compare
    # Windows looks for a bare `git` in this process's own folder first, and Claude Code starts the hook in
    # the project I opened. Standing in the clone makes that folder one of mine.
    os.chdir(args.repo)
    start_background_fetch(args)  # first, so a slow check can never starve it; it is not waited for
    deadline = started + BUDGET
    counts = drift_counts(args, deadline)
    behind = commits_behind(args, deadline)
    if counts is None and not behind:
        return 0
    text = message(args, counts, behind)
    print(json.dumps({"systemMessage": text,
                      "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException:  # a warning must never fail or hang the start of a session
        sys.exit(0)
