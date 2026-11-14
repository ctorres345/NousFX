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


def prepare():
    if _load():
        print("a run is already prepared; run `restore` first", file=sys.stderr)
        return 1
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    work = tempfile.mkdtemp(prefix="via-fresh-")
    home = os.path.join(work, "home")
    app = os.path.join(work, "app")
    os.makedirs(home)
    shutil.copytree(os.path.join(ROOT, "samples", "web"), app)
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=fresh", "-c", "user.email=fresh@example.invalid", "commit", "-qm", "sample"]):
        subprocess.run(cmd, cwd=app, check=True, capture_output=True)
    cfg = via_home.default_config()
    backup = None
    if os.path.isfile(cfg):
        backup = os.path.join(work, "via.json.bak")
        shutil.copy2(cfg, backup)
        os.remove(cfg)  # setup must start from nothing; `restore` puts the backup back
    state = {"work": work, "home": home, "app": app, "config": cfg, "backup": backup,
             "backup_sha": _sha(backup) if backup else None}
    with open(_state_path(), "w") as f:
        json.dump(state, f)
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
    print("  3. /reload-plugins, then /via setup, then /via audit")
    print("  4. python3 -B scripts/fresh_user_test.py verify")
    print("  5. python3 -B scripts/fresh_user_test.py restore")
    return 0


def verify():
    st = _load()
    if not st:
        print("nothing prepared", file=sys.stderr)
        return 1
    ok = True
    cfg = st["config"]
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


def restore():
    st = _load()
    if not st:
        print("nothing to restore")
        return 0
    cfg, backup = st["config"], st["backup"]
    work = os.path.realpath(st["work"])
    if (cfg != via_home.default_config() or os.path.dirname(work) != os.path.realpath(tempfile.gettempdir())
            or not os.path.basename(work).startswith("via-fresh-")
            or (backup and os.path.dirname(os.path.realpath(backup)) != work)):
        print("state file does not look like one `prepare` wrote; not touching anything", file=sys.stderr)
        return 1
    if backup:
        if not os.path.isfile(backup) or _sha(backup) != st["backup_sha"]:
            print("backup %s changed; not restoring over your config" % backup, file=sys.stderr)
            return 1
        os.makedirs(os.path.dirname(cfg), exist_ok=True)
        shutil.copy2(backup, cfg)
        print("restored %s (sha256 %s)" % (cfg, _sha(cfg)[:12]))
    elif os.path.isfile(cfg):
        os.remove(cfg)  # the run created it; there was none before
        print("removed %s (it did not exist before the run)" % cfg)
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
