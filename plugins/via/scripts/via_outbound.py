#!/usr/bin/env python3
"""via-outbound: asks the personal config whether this project may send anything to a destination.

Usage:
  via_outbound.py check --dest DEST [--project DIR] [--config FILE]
  via_outbound.py paths --dest gemini [--project DIR] [--config FILE]

DEST is one of gemini, jira, figma, artifacts, obsidian.
The project key is host/owner/repo (lowercase), taken from the `origin` remote of the git repo at --project
(default: the current folder). An old two-part owner/repo config key is read as github.com/owner/repo. A remote with
no host (a local path or file:// URL) gives no key. The config is --config, else
~/.config/nousfx/via.json (home taken from the system user record; no environment variable moves it). It must be
a real file inside ~/.config/nousfx, and that folder must not be in a git work tree: a config kept in a project
is refused, so a repo cannot allow-list itself. While the new file is absent, the old ~/.claude/via.json is read
(one release). Its `outbound_allowlist` maps a key
to {"destinations": [...], "gemini_code_paths": [...]}. A project that is not listed, has no remote, or has
no config is local-only. There is no exemption for public repos.

`check` prints JSON {"allowed": bool, "project": key-or-null, "dest": DEST, "reason": ..., "gemini_integration":
the status /via setup stored, or null}.
`paths` prints gemini_code_paths, each followed by a NUL byte, and nothing when gemini is not allowed.

Exit codes:
  0   allowed.
  20  local-only: nothing may leave the machine for this destination. Use the local output.
  1   bad input or a malformed config. This fails closed: never read it as allowed.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import via_home  # noqa: E402

DESTINATIONS = ("gemini", "jira", "figma", "artifacts", "obsidian")
LOCAL_ONLY = {
    "gemini": "use the fresh-context Claude reviewer",
    "jira": "write local markdown tickets in docs/design/tickets/",
    "figma": "skip the export; the prototype stays a local HTML file",
    "artifacts": "save the prototype as a local HTML file in the run folder",
    "obsidian": "write no note",
}
ALLOWED, LOCAL, BAD = 0, 20, 1


class Bad(Exception):
    pass


def project_key(project):
    """host/owner/repo from the origin remote, lowercased, or None when there is no usable remote.
    ssh, scp-style and https forms of one remote give the same key; user, port, .git and ?query are dropped."""
    try:
        r = subprocess.run(["git", "-C", project, "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    url = r.stdout.strip().rstrip("/")
    url = re.sub(r"[?#].*$", "", url)
    url = re.sub(r"\.git$", "", url)
    scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://([^/]*)(.*)$", url)
    scp = re.match(r"^(?:[^/@:]+@)?([^/@:]+):(?!//)(.*)$", url)
    if scheme:
        if scheme.group(1).lower() == "file" or not scheme.group(2):
            return None  # a file URL names no host, so it cannot be the allow-listed project
        host = re.sub(r":\d*$", "", scheme.group(2).rpartition("@")[2])
        path = scheme.group(3)
    elif scp:
        host, path = scp.group(1), scp.group(2)
    else:
        return None  # a local path names no host
    parts = [p for p in path.split("/") if p]
    if not host or len(parts) < 2:
        return None
    return ("%s/%s/%s" % (host, parts[-2], parts[-1])).lower()


def config_path(arg):
    """--config, else ~/.config/nousfx/via.json (the old ~/.claude/via.json is read while that is absent).
    It must be a real file inside ~/.config/nousfx, outside any git work tree. No environment variable moves it."""
    try:
        return via_home.config_path(arg)
    except via_home.Refused as e:
        raise Bad(str(e))


def normalize_key(key):
    """Lowercase key. An old two-part owner/repo key means github.com/owner/repo; host/owner/repo is kept as is."""
    key = key.strip().lower()
    return "github.com/" + key if key.count("/") == 1 else key


def load_allowlist(path):
    """(validated allow-list with lowercased keys, Gemini status from /via setup). ({}, None) when there is no config."""
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        raise Bad("cannot read %s (%s)" % (path, e))
    try:
        cfg = json.loads(raw)
    except ValueError:
        raise Bad("%s is not valid JSON" % path)
    if not isinstance(cfg, dict):
        raise Bad("%s is not a JSON object" % path)
    allow = cfg.get("outbound_allowlist", {})
    if not isinstance(allow, dict):
        raise Bad("outbound_allowlist in %s must be an object" % path)
    clean = {}
    for key, entry in allow.items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("destinations"), list)
                or not all(isinstance(d, str) and d in DESTINATIONS for d in entry["destinations"])):
            raise Bad("outbound_allowlist entry \"%s\" needs \"destinations\", a list of: %s"
                      % (key, ", ".join(DESTINATIONS)))
        paths = entry.get("gemini_code_paths", [])
        if not isinstance(paths, list) or not all(isinstance(p, str) and p for p in paths):
            raise Bad("outbound_allowlist entry \"%s\": gemini_code_paths must be a list of paths" % key)
        norm = normalize_key(key)
        if norm in clean:
            raise Bad("outbound_allowlist lists \"%s\" twice (an old owner/repo key counts as github.com/owner/repo)" % norm)
        clean[norm] = {"destinations": entry["destinations"], "gemini_code_paths": paths}
    integrations = cfg.get("integrations")
    return clean, (integrations.get("gemini") if isinstance(integrations, dict) else None)


def decide(project, dest, config=None):
    """(exit code, result dict). Raises Bad for invalid input or a malformed config."""
    if dest not in DESTINATIONS:
        raise Bad("unknown destination \"%s\". Use one of: %s" % (dest, ", ".join(DESTINATIONS)))
    allow, gemini = load_allowlist(config_path(config))
    key = project_key(project)
    result = {"allowed": False, "project": key, "dest": dest, "gemini_integration": gemini}
    if key is None:
        result["reason"] = "no git remote, so the project is not on the allow-list; %s" % LOCAL_ONLY[dest]
        return LOCAL, result
    entry = allow.get(key)
    if entry is None:
        result["reason"] = "%s is not on the allow-list; %s" % (key, LOCAL_ONLY[dest])
        return LOCAL, result
    if dest not in entry["destinations"]:
        result["reason"] = "%s does not allow %s; %s" % (key, dest, LOCAL_ONLY[dest])
        return LOCAL, result
    result["allowed"] = True
    result["reason"] = "%s allows %s" % (key, dest)
    result["gemini_code_paths"] = entry["gemini_code_paths"]
    return ALLOWED, result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["check", "paths"])
    ap.add_argument("--dest", required=True)
    ap.add_argument("--project", default=".")
    ap.add_argument("--config")
    args = ap.parse_args(argv)
    try:
        if args.command == "paths" and args.dest != "gemini":
            raise Bad("paths only applies to --dest gemini")
        code, result = decide(args.project, args.dest, args.config)
    except Bad as e:
        print("Stopped: %s. Nothing was sent." % e, file=sys.stderr)
        return BAD
    if args.command == "check":
        print(json.dumps({k: v for k, v in result.items() if k != "gemini_code_paths"}))
    elif code == ALLOWED:
        sys.stdout.write("".join(p + "\0" for p in result["gemini_code_paths"]))
    return code


if __name__ == "__main__":
    sys.exit(main())
