#!/usr/bin/env python3
"""Fresh-user test for Via (NFX-25): install it from the marketplace in a temp HOME and run it on a sample app.

  fresh_user_test.py prepare   make the temp HOME and a git-initialised copy of samples/web, back up the real
                               via.json, install Via from the marketplace, print the manual steps
  fresh_user_test.py verify    check what the manual run left behind (via.json, .via/runs/ report)
  fresh_user_test.py restore   put the real via.json back and delete the temp folders

A temp $HOME moves Claude Code's plugin state but NOT Via's config: Via reads the home from the system
user record (plugins/via/scripts/via_home.py), so `/via setup` writes the real ~/.config/nousfx/via.json
whatever $HOME says. `prepare` backs that file up and `restore` puts it back, so run `restore` when done.

QUICKSTART is the one list of commands. scripts/test_readme_quickstart.py checks the README against it.
Exit 0 on success, 1 on a failed step, 2 on bad usage.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "plugins", "via", "scripts"))
import via_home  # noqa: E402

# The README "Meet Via" block must show these, in this order. The last one runs inside Claude Code.
QUICKSTART = [
    "claude plugin marketplace add ctorres345/NousFX",
    "claude plugin install via@nousfx",
    "/via setup",
]
SHELL_STEPS = [c for c in QUICKSTART if not c.startswith("/")]
STATE_NAME = "fresh_user_state.json"
STATE_DIR = os.path.join(via_home.anchor(), "fresh-user")  # in my own config folder, not a shared /tmp
MARKER = ".nousfx-fresh-user"  # restore only deletes a work folder that holds this file


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _state_path():
    return os.path.join(STATE_DIR, STATE_NAME)


def _load():
    try:
        with open(_state_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _configs():
    """The files setup would read or write, with where their backups go."""
    return [(via_home.default_config(), os.path.join(STATE_DIR, "via.json.bak")),
            (via_home.legacy_config(), os.path.join(STATE_DIR, "legacy-via.json.bak"))]


def _save(state):
    tmp = _state_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, _state_path())


def prepare():
    if os.path.exists(_state_path()):
        print("a run is already prepared (or its state file is damaged: %s); run `restore` first" % _state_path(), file=sys.stderr)
        return 1
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    for cfg, backup in _configs():
        if os.path.exists(backup):
            print("an old backup is still here: %s. It may be your real config; restore it by hand "
                  "(copy it to %s) and delete it, then run `prepare` again" % (backup, cfg), file=sys.stderr)
            return 1
    work = tempfile.mkdtemp(prefix="via-fresh-")
    open(os.path.join(work, MARKER), "w").close()
    home = os.path.join(work, "home")
    app = os.path.join(work, "app")
    os.makedirs(home)
    shutil.copytree(os.path.join(ROOT, "samples", "web"), app)
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=fresh", "-c", "user.email=fresh@example.invalid", "commit", "-qm", "sample"]):
        subprocess.run(cmd, cwd=app, check=True, capture_output=True)
    moved = []
    for cfg, backup in _configs():
        has = os.path.isfile(cfg)
        moved.append({"path": cfg, "backup": backup if has else None, "sha": _sha(cfg) if has else None})
    # The state goes down first, so a stop at any later point still leaves `restore` something to work from.
    _save({"work": work, "home": home, "app": app, "configs": moved})
    for m in moved:
        if m["backup"]:
            shutil.copy2(m["path"], m["backup"])
            if _sha(m["backup"]) != m["sha"]:
                print("backup of %s did not copy cleanly; nothing was moved" % m["path"], file=sys.stderr)
                return 1
    for m in moved:
        if m["backup"]:
            os.remove(m["path"])  # setup must start from nothing; `restore` puts the backup back
    env = {**os.environ, "HOME": home}
    for cmd in SHELL_STEPS:
        print("$ HOME=%s %s" % (home, cmd))
        r = subprocess.run(cmd.split(), env=env, cwd=app)
        if r.returncode != 0:
            print("step failed (exit %d); run `restore` to clean up" % r.returncode, file=sys.stderr)
            return 1
    print("\nManual steps (Claude Code cannot be driven unattended):")
    print("  1. cd %s" % app)
    print("  2. HOME=%s claude      (log in once with /login)" % home)
    print("  3. /reload-plugins, then %s, then /via audit" % ", then ".join(c for c in QUICKSTART if c.startswith("/")))
    print("  4. python3 -B scripts/fresh_user_test.py verify")
    print("  5. python3 -B scripts/fresh_user_test.py restore")
    return 0


def verify():
    st = _load()
    if not st:
        print("nothing prepared", file=sys.stderr)
        return 1
    ok = True
    cfg = st["configs"][0]["path"]
    print("via.json: %s" % ("present" if os.path.isfile(cfg) else "MISSING (/via setup did not run)"))
    ok &= os.path.isfile(cfg)
    runs = os.path.join(st["app"], ".via", "runs")
    found = []
    if os.path.isdir(runs):
        for dirpath, _, files in os.walk(runs):
            found += [os.path.join(dirpath, f) for f in files]
    print(".via/runs: %d file(s)" % len(found))
    ok &= bool(found)
    for f in sorted(found)[:20]:
        print("  " + os.path.relpath(f, st["app"]))
    return 0 if ok else 1


def _restore_one(m):
    """Put one config back. Returns an error string, or None when it is done."""
    path, backup = m["path"], m["backup"]
    if path not in [c for c, _ in _configs()] or (backup and backup not in [b for _, b in _configs()]):
        return "%s is not a file this test manages" % path
    if not backup:
        if os.path.isfile(path):
            os.remove(path)  # the run created it; there was none before
            print("removed %s (it did not exist before the run)" % path)
        return None
    if not os.path.isfile(backup):
        if os.path.isfile(path) and _sha(path) == m["sha"]:
            return None  # an earlier restore already put it back
        return "backup %s is missing and %s is not your original" % (backup, path)
    if _sha(backup) != m["sha"]:
        return "backup %s changed; not restoring over your config" % backup
    os.makedirs(os.path.dirname(path), exist_ok=True)
    shutil.copy2(backup, path)
    os.remove(backup)
    print("restored %s (sha256 %s)" % (path, _sha(path)[:12]))
    return None


def restore():
    leftovers = [b for _, b in _configs() if os.path.exists(b)]
    if not os.path.exists(_state_path()):
        if leftovers:
            print("no state file, but a backup is here: %s. Copy it back to its place by hand." % ", ".join(leftovers), file=sys.stderr)
            return 1
        print("nothing to restore")
        return 0
    st = _load()
    if not st or not isinstance(st.get("configs"), list):
        print("state file %s is damaged. Backups, if any: %s. Copy them back by hand, then delete the state file."
              % (_state_path(), ", ".join(leftovers) or "none"), file=sys.stderr)
        return 1
    errors = [e for e in map(_restore_one, st["configs"]) if e]
    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 1
    work = os.path.realpath(st["work"])
    if os.path.basename(work).startswith("via-fresh-") and os.path.isfile(os.path.join(work, MARKER)):
        shutil.rmtree(work, ignore_errors=True)
    os.remove(_state_path())
    return 0


def main(argv):
    cmds = {"prepare": prepare, "verify": verify, "restore": restore}
    if len(argv) != 2 or argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    return cmds[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
