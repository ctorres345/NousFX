#!/usr/bin/env python3
"""Warn before an Edit, Write or NotebookEdit while `testfail.py run` is testing this work tree (NFX-271).

PreToolUse hook. testfail.py writes <git dir>/claude-testfail-running while it runs; an edit made meanwhile changes the
tree, so the run is void and its double suite run is wasted. When that marker is fresh and its process is alive, this
prints a warning the agent sees before it edits. It never blocks: exit 0 always, and any error prints nothing.

The git dir is found by walking up from the file for `.git` (a folder, or a `gitdir:` file in a linked worktree), not
by running git, so the hook costs one small file read. A stale marker (killed run) is told by `expires` and, on
POSIX, a dead pid; on Windows only `expires`, because os.kill(pid, 0) would terminate the process there.
Python 3.11+, standard library only; run as `python3 -I -B testfail-running.py`.
"""
import json
import math
import os
import sys
import time

MARKER = "claude-testfail-running"
BUDGET_SECONDS = 2.0
IS_WINDOWS = os.name == "nt"


def git_dir_for(path, deadline):
    """The git dir of the work tree holding `path` (which may not exist yet), or None, also past `deadline`."""
    folder = os.path.dirname(os.path.abspath(path))
    while time.monotonic() < deadline:
        dot_git = os.path.join(folder, ".git")
        if os.path.isdir(dot_git):
            return dot_git
        if os.path.isfile(dot_git):
            with open(dot_git, encoding="utf-8") as f:
                first = f.readline().strip()
            if not first.startswith("gitdir:"):
                return None
            target = first[len("gitdir:"):].strip()
            return target if os.path.isabs(target) else os.path.normpath(os.path.join(folder, target))
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent
    return None


def alive(pid):
    if IS_WINDOWS:
        return True  # os.kill(pid, 0) terminates the process on Windows: only `expires` counts there
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def warning(path, deadline):
    git_dir = git_dir_for(path, deadline)
    if not git_dir or time.monotonic() >= deadline:
        return None
    with open(os.path.join(git_dir, MARKER), encoding="utf-8") as f:
        marker = json.load(f)
    tree, pid, start, expires = (marker.get(k) for k in ("tree", "pid", "start", "expires"))
    numbers = all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n)
                  for n in (pid, start, expires))
    if not (isinstance(tree, str) and numbers and isinstance(pid, int) and pid > 0):
        return None
    if expires <= time.time() or not alive(pid):
        return None
    hhmm = time.strftime("%H:%M", time.localtime(start))
    return (f"testfail.py run (pid {pid}, started {hhmm}) is testing tree {tree[:12]}; this edit voids it. "
            "Wait for it to finish, or stop it and run it again after your last edit.")


def main():
    deadline = time.monotonic() + BUDGET_SECONDS
    try:
        data = json.load(sys.stdin)
        tool_input = data.get("tool_input") or {}
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(path, str) or not path:
            return 0
        if not os.path.isabs(path):
            path = os.path.join(data.get("cwd") or os.getcwd(), path)
        text = warning(path, deadline)
        if text:
            print(json.dumps({"systemMessage": text,
                              "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
