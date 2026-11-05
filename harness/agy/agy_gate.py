#!/usr/bin/env python3
"""agy PreToolUse hook: block a pull request create until a review is recorded.

    python3 -I -B agy_gate.py --expect <sha256 adapter>,<sha256 review-gate.py>,<sha256 pr_create_parser.py>

agy runs this before a shell command (tool run_command) or a create-PR tool and sends JSON on stdin:
  {"toolCall": {"name": "run_command", "args": {"CommandLine": "..."}}, "workspacePaths": ["/repo"], ...}
The folder a command runs in is not always sent, so every workspace path is checked, and so is any
folder-like argument (Cwd, WorkingDirectory ...) when agy sends one. The decision itself is made by the
gate's plain `check` entry (review-gate.py, in this folder), the same one every non-Claude adapter uses.

Integrity: sync writes the hashes of the three files into the hook command in hooks.json, which agy treats as
a sensitive file. A file that no longer matches is a deny, and so is a shell command that names the plugin
folder (a model editing the gate to disarm it). An edited adapter could skip both checks; hooks.json still
carries the hashes, and every sync run re-tests the installed hook.

Answer: a JSON line {"decision": "deny", "reason": ...} to block. To allow, print nothing: agy's own
permission prompt then still applies (an explicit "allow" could skip it). Anything unreadable or
unexpected for a tool this hook is meant for is a deny, because agy treats silence as "no decision" and
would run the call. A tool name the matcher should never send is left to agy.
Exit code is always 0 once an answer is given; a crash makes agy error the call, which also blocks.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "review-gate.py")
PARSER = os.path.join(HERE, "pr_create_parser.py")
TOTAL_SECONDS = 25  # one deadline for every check; hooks.json allows 30
# An argument naming the folder a command runs in (whole names only: IsDirect and RedirectOutput are not folders)
FOLDER_KEY = re.compile(r"cwd|(work(ing)?_?)?(dir(ectory)?|folder)|location|path", re.I)
MCP_CREATE = re.compile(r"create_pull_request", re.I)
CREATE_CANDIDATE = re.compile(r"\bpr\b|pull", re.I)  # a command that might open a PR
PLUGIN_FOLDER = re.compile(r"plugins[/\\]nousfx", re.I)  # where sync puts this hook and the gate
# GIT_DIR and friends would point git at another repo (or none) and hide the real one from the gate
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


class Refused(Exception):
    """A deny whose message is already the reason to show."""


def deny(reason):
    print(json.dumps({"decision": "deny", "reason": reason}))
    return 0


def shells_for(osname):
    """Parsers to try on a shell command. On Windows run_command may be PowerShell or bash: block if either
    finds a create, because the two parse quoting differently."""
    return ("Bash", "PowerShell") if osname == "nt" else ("Bash",)


def verify_files(argv):
    """Deny unless this adapter, the gate and its parser match the hashes sync wrote into hooks.json."""
    if "--expect" not in argv[:-1]:
        raise Refused("Blocked: hooks.json carries no file hashes for the review gate. Run nousfx_sync again.")
    want = argv[argv.index("--expect") + 1].lower().split(",")
    paths = (os.path.abspath(__file__), GATE, PARSER)
    if len(want) != len(paths):
        raise Refused("Blocked: the review gate hashes in hooks.json are malformed. Run nousfx_sync again.")
    for path, expected in zip(paths, want):
        try:
            with open(path, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            actual = None
        if actual != expected:
            raise Refused(f"Blocked: {os.path.basename(path)} does not match the version sync installed, so the "
                          "review gate may have been changed. Run nousfx_sync again.")


def read_call():
    """(tools, command, folders) to check, or None when this hook is not meant for the tool.
    Raises ValueError on anything unreadable and Refused on a call that must not run."""
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    call = payload["toolCall"]
    name = call["name"]
    if not isinstance(name, str):
        raise ValueError("no tool name")
    shell = "run_command" in name  # the matcher is an unanchored regex, so any name holding it is a shell call
    if not shell and not MCP_CREATE.search(name):
        return None
    args = call.get("args") or {}
    if not isinstance(args, dict):
        raise ValueError("tool arguments are not an object")
    command = args.get("CommandLine") if shell else name
    if not isinstance(command, str) or not command.strip():
        raise ValueError("no command")
    if shell and PLUGIN_FOLDER.search(command):
        raise Refused("Blocked: this command touches the review gate's own files. Change the sources in the "
                      "nousfx repo and run nousfx_sync instead.")
    workspaces = payload["workspacePaths"]
    if not isinstance(workspaces, list) or not all(isinstance(w, str) for w in workspaces):
        raise ValueError("workspacePaths is not a list of folder names")
    workspaces = [w for w in workspaces if os.path.isdir(w)]  # a removed root has nothing to check
    if not workspaces:
        raise ValueError("no usable workspace folder")
    folders = list(workspaces)
    candidate = not shell or bool(CREATE_CANDIDATE.search(command))
    for key, value in args.items():
        if FOLDER_KEY.fullmatch(key) and value not in ("", None):
            folder = os.path.join(workspaces[0], value) if isinstance(value, str) else None  # absolute stays
            if folder and os.path.isdir(folder):
                folders.append(folder)
            elif candidate:  # a folder that does not exist cannot run anything, but a create must not slip by
                raise ValueError(f"argument {key} is not an existing folder")
    return (shells_for(os.name) if shell else (name,)), command, folders


def first_block(tools, command, folders, total=TOTAL_SECONDS):
    """The gate's reason for the first check that blocks, or None. One deadline covers all checks."""
    deadline = time.monotonic() + total
    for folder in dict.fromkeys(folders):
        for tool in tools:
            left = deadline - time.monotonic()
            if left <= 0:
                return f"Blocked: the review gate took longer than {total} s."
            try:
                r = subprocess.run([sys.executable, "-I", "-B", GATE, "check", "--cwd", folder, "--command", command,
                                    "--tool", tool, "--allow-non-repo"], capture_output=True, text=True, timeout=left,
                                   env=CLEAN_ENV)
            except (OSError, subprocess.SubprocessError) as e:
                return f"Blocked: the review gate did not answer ({type(e).__name__})."
            if r.returncode != 0:
                reason = r.stderr.strip()[:1500] or f"Blocked: the review gate exited {r.returncode}."
                return f"[{tool} parser] {reason}" if len(tools) > 1 else reason
    return None


def main(argv):
    try:
        verify_files(argv)
        call = read_call()
    except Refused as e:
        return deny(str(e))
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        return deny(f"Blocked: the review gate could not read agy's tool call ({type(e).__name__}: {e}).")
    if call is None:
        return 0
    reason = first_block(*call)
    return deny(reason) if reason else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
