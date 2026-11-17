#!/usr/bin/env python3
"""Via setup doctor: detect integrations and keep the personal config in step.

Prints a JSON report on stdout. Claude passes what only it can see (loaded MCP tools, browser,
skills) as flags; this script detects the rest from the shell.

Usage: via_setup.py [--config PATH] [--atlassian present|missing]
                    [--figma authorized|needs-auth|missing] [--browser chrome|builtin|none]
                    [--skills a,b,c] [--create-pillow]

The config (default ~/.config/nousfx/via.json; an old ~/.claude/via.json is copied there once) holds statuses and paths only, never keys.
Plain runs never create or install anything. Only --create-pillow builds the Pillow environment.
With --create-pillow only the pillow status and assets.python change; the other recorded integrations are kept.
Exit 0 on success, 1 when the config is unsafe or cannot be written or when --create-pillow failed (the report is still printed), 2 on a secret-shaped argument.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import venv

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)  # run under `python3 -I`, which adds no script folder to sys.path
import via_home  # noqa: E402

TEMPLATE = os.path.join(HERE, "..", "assets", "via.example.json")

# Same pattern the gemini-review scripts use to stop on a secret.
SECRET_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{36}"
    r"|github_pat_[0-9A-Za-z_]{20,}|glpat-[0-9A-Za-z_-]{20,}|(^|[^0-9A-Za-z_-])sk-[0-9A-Za-z_-]{20,}"
    r"|[sr]k_live_[0-9A-Za-z]{10,}|xox[baprs]-[0-9A-Za-z-]{10,}"
    r"|eyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)"
)

# Design skills Via's platform sets rely on (the full per-platform list comes with later tickets).
WANTED_SKILLS = {
    "ui-ux-pro-max", "design:accessibility-review", "design:design-critique", "design:ux-copy",
    "material-3:material-3", "adaptive", "edge-to-edge", "jetpack-compose-performance",
}

FIX = {
    "gemini": ("The Argon second opinion needs the gemini-api CLI and a key in the OS keychain. Without it Via falls back to a fresh-context reviewer.",
               "https://ai.google.dev/gemini-api/docs/api-key"),
    "atlassian": ("Files findings as Jira tickets with inline screenshots. Without it Via writes markdown tickets.",
                  "https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/"),
    "figma": ("Needed only for /via export figma. Authorize the connector with /mcp.",
              "https://help.figma.com/hc/en-us/articles/32132100833559-Guide-to-the-Figma-MCP-server"),
    "obsidian": ("Each run can write a note into your vault. Set obsidian.vault_path in the config.",
                 "https://help.obsidian.md/Getting+started/Create+a+vault"),
    "simulators": ("Via screenshots screens on an emulator or simulator. Without one it says so and skips visual checks.",
                   "https://developer.android.com/studio/run/managing-avds"),
    "browser": ("Web screenshots need a browser Claude can drive.",
                "https://code.claude.com/docs/en/chrome"),
    "pillow": ("Image conversion for /via assets runs in a private Python environment with Pillow. Without it /via assets cannot convert images.",
               "https://pillow.readthedocs.io/en/stable/installation/"),
    "skills": ("Via runs the design skills installed here and reports the ones it skipped.",
               "https://code.claude.com/docs/en/skills"),
}


# Pinned so every machine gets the same converter.
PILLOW_VERSION = "12.3.0"
PILLOW_MIN_PYTHON = (3, 10)  # the oldest Python this Pillow release ships wheels for


def pillow_command():
    """The create command, run with a Python that can build Pillow; None when none is found."""
    script = shlex.quote(os.path.realpath(__file__))
    if sys.version_info >= PILLOW_MIN_PYTHON:
        # The plain name when PATH's python3 is this interpreter; else this interpreter's own path.
        on_path = shutil.which("python3")
        same = bool(on_path) and os.path.realpath(on_path) == os.path.realpath(sys.executable)
        return "%s -I -B %s --create-pillow" % ("python3" if same else shlex.quote(sys.executable), script)
    check = "import sys; sys.exit(0 if sys.version_info >= (%d, %d) else 1)" % PILLOW_MIN_PYTHON
    for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3.10"):
        path = shutil.which(name)
        if not path:
            continue
        try:
            ok = subprocess.run([path, "-I", "-B", "-c", check], capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            return "%s -I -B %s --create-pillow" % (shlex.quote(path), script)
    return None


def pillow_env(home):
    return os.path.join(home, ".local", "share", "nousfx", "via", "venv")


def pillow_python(home):
    # POSIX layout only. Follow-up: Windows keeps the interpreter at Scripts\\python.exe.
    return os.path.join(pillow_env(home), "bin", "python")


def detect_pillow(home):
    """present: imports with the pinned version. missing: no environment. broken: anything else."""
    py = pillow_python(home)
    if not os.path.isdir(pillow_env(home)):
        return "missing"  # an env folder with a dead or absent interpreter is broken, not missing
    try:
        out = subprocess.run([py, "-I", "-B", "-c", "import PIL; print(PIL.__version__)"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return "broken"
    if out.returncode == 0 and out.stdout.strip() == PILLOW_VERSION:
        return "present"
    return "broken"


def create_pillow(home, state):
    """Build the environment. Returns True when Pillow checks out afterwards."""
    env = pillow_env(home)
    if os.name == "nt":
        print("Creating the Pillow environment is not supported on Windows yet.", file=sys.stderr)
        return False
    if sys.version_info < PILLOW_MIN_PYTHON:
        print("Pillow %s needs Python %s or newer; this is %s (%s). Run the create command with a newer python3."
              % (PILLOW_VERSION, ".".join(map(str, PILLOW_MIN_PYTHON)), ".".join(map(str, sys.version_info[:3])),
                 sys.executable), file=sys.stderr)
        return False
    try:
        os.makedirs(os.path.dirname(env), exist_ok=True)
        # In-process venv.create with the CLI's POSIX defaults (symlinked interpreter): sys.executable may be a launcher stand-in in tests.
        venv.create(env, clear=(state == "broken"), with_pip=True, symlinks=(os.name != "nt"))
        # -I and cwd=env: a pip.py or pip/ folder in the caller's project must not run in place of pip.
        # Wheels only: a missing wheel must not fall back to running an sdist's build code.
        r = subprocess.run([pillow_python(home), "-I", "-m", "pip", "install", "--disable-pip-version-check",
                            "--only-binary=:all:", "Pillow==" + PILLOW_VERSION],
                           capture_output=True, text=True, timeout=600, cwd=env)
        if r.returncode != 0:
            print((r.stderr or r.stdout).strip()[-600:], file=sys.stderr)
            return False
    except (OSError, subprocess.SubprocessError, ImportError) as e:
        print(str(e), file=sys.stderr)
        return False
    return detect_pillow(home) == "present"


# Status shown in the summary line. Anything unlisted (needs-auth, no-key, partial) prints as is.
GLYPH = {"present": "✓", "detected": "✓", "missing": "✗"}


def has_secret(value):
    return bool(SECRET_RE.search(str(value)))


def find_gemini(home):
    local = os.path.join(home, ".local", "bin", "gemini-api")
    if os.access(local, os.X_OK):
        return local
    return shutil.which("gemini-api")


def detect_gemini(home):
    cli = find_gemini(home)
    if not cli:
        return "missing"
    try:
        out = subprocess.run([cli, "whoami", "--output-format", "json"], capture_output=True,
                             text=True, timeout=10)
        creds = json.loads(out.stdout).get("credentials")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return "no-key"
    if not isinstance(creds, dict):
        return "no-key"
    # Only the source is read. The masked value in the output is dropped, never stored or printed.
    for name in ("api-key", "access-token"):
        entry = creds.get(name)
        if isinstance(entry, dict) and entry.get("source") not in (None, "unset"):
            return "present"
    return "no-key"


def find_vault(home):
    roots = [os.path.join(home, "Documents"),
             os.path.join(home, "Library", "Mobile Documents", "iCloud~md~obsidian", "Documents")]
    for root in roots:
        try:
            for entry in sorted(os.listdir(root)):
                if os.path.isdir(os.path.join(root, entry, ".obsidian")):
                    return os.path.join(root, entry)
        except OSError:
            continue
    return None


def detect_obsidian(home, configured):
    if isinstance(configured, str) and configured and os.path.isdir(os.path.expanduser(configured)):
        return "present", configured
    found = find_vault(home)
    return ("detected", found) if found else ("missing", None)


def detect_simulators():
    tools = [t for t in ("xcrun", "adb", "emulator") if shutil.which(t)]
    return ("present" if tools else "missing"), tools


def load_config(path):
    """Return (parsed config, raw text). A missing file gives ({}, None)."""
    try:
        with open(path) as f:
            raw = f.read()
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        sys.exit("Stopped: cannot read %s (%s). Nothing was changed." % (path, e))
    try:
        cfg = json.loads(raw)
    except ValueError:
        cfg = None
    if not isinstance(cfg, dict):
        sys.exit("Stopped: %s is not a JSON object. Fix or delete it, nothing was changed." % path)
    return cfg, raw


write_atomic = via_home.write_atomic


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config")
    ap.add_argument("--atlassian", choices=["present", "missing"], default="missing")
    ap.add_argument("--figma", choices=["authorized", "needs-auth", "missing"], default="missing")
    ap.add_argument("--browser", choices=["chrome", "builtin", "none"], default="none")
    ap.add_argument("--skills", nargs="?", const="", default="")
    ap.add_argument("--create-pillow", action="store_true")
    args = ap.parse_args()

    for value in (args.config or "", args.skills):
        if has_secret(value):
            print("Stopped: an argument looks like a secret. Nothing was written.", file=sys.stderr)
            return 2

    home = via_home.home_dir()
    try:
        path = os.path.abspath(args.config) if args.config else via_home.default_config()
        via_home.validate(path, via_home.anchor())
        migration = "none" if args.config else via_home.migrate()
    except via_home.Refused as e:
        print("Stopped: %s. The config must live inside %s, outside any git work tree. Nothing was written."
              % (e, via_home.anchor()), file=sys.stderr)
        return 1

    cfg, current = load_config(path)
    with open(TEMPLATE) as f:
        template = json.load(f)
    for key, default in template.items():
        cfg.setdefault(key, default)
        if isinstance(default, dict) and not isinstance(cfg[key], dict):
            print("Stopped: \"%s\" in %s must be an object. Fix or delete the file, nothing was changed." % (key, path),
                  file=sys.stderr)
            return 1

    pillow = detect_pillow(home)
    create_failed = False
    probed = None  # (command,) once pillow_command() has run, so the probe runs once
    if args.create_pillow and pillow != "present":
        if create_pillow(home, pillow):
            pillow = "present"
        else:
            pillow = detect_pillow(home)
            create_failed = True
            probed = (pillow_command(),)
            cmd = probed[0]
            if cmd:
                print("Pillow environment not ready (see the reason above). Fix it, then run: %s" % cmd, file=sys.stderr)
            else:
                print("Pillow environment not ready (see the reason above). Python %s or newer is needed."
                      % ".".join(map(str, PILLOW_MIN_PYTHON)), file=sys.stderr)

    simulators, sim_tools = detect_simulators()
    obsidian, vault = detect_obsidian(home, cfg["obsidian"].get("vault_path"))
    installed = {s for s in args.skills.split(",") if s}
    skills = "present" if WANTED_SKILLS <= installed else "partial" if installed & WANTED_SKILLS else "missing"
    status = {
        "gemini": detect_gemini(home),
        "atlassian": args.atlassian,
        "figma": "present" if args.figma == "authorized" else args.figma,
        "obsidian": obsidian,
        "simulators": simulators,
        "browser": "missing" if args.browser == "none" else "present",
        "pillow": pillow,
        "skills": skills,
    }
    if args.create_pillow and isinstance(cfg["integrations"], dict):
        # A create-only run gets none of the caller's flags: keep what the full setup run recorded.
        for key in ("atlassian", "figma", "browser", "skills"):
            if key in cfg["integrations"]:
                status[key] = cfg["integrations"][key]

    report = {name: {"status": st, "why": FIX[name][0], "fix_url": FIX[name][1]}
              for name, st in status.items()}
    if vault:
        report["obsidian"]["path"] = vault
    if sim_tools:
        report["simulators"]["tools"] = sim_tools
    if pillow != "present":
        cmd = probed[0] if probed else pillow_command()
        if cmd:
            report["pillow"]["create_command"] = cmd
        else:
            report["pillow"]["needs_python"] = ".".join(map(str, PILLOW_MIN_PYTHON))
    if skills == "partial":
        report["skills"]["missing"] = sorted(WANTED_SKILLS - installed)

    cfg["integrations"] = status
    if not isinstance(cfg["assets"], dict):
        cfg["assets"] = {}
    cfg["assets"]["python"] = pillow_python(home) if pillow == "present" else None
    if not args.create_pillow:
        cfg["figma"]["state"] = args.figma

    text = json.dumps(cfg, indent=2) + "\n"
    if has_secret(text):
        print("Stopped: %s holds a secret-shaped value. Remove it by hand; nothing was written." % path,
              file=sys.stderr)
        return 1
    if current == text:
        outcome = "unchanged"
    else:
        try:
            write_atomic(path, text)
        except OSError as e:
            print("Stopped: cannot write %s (%s). Nothing was changed." % (path, e), file=sys.stderr)
            return 1
        outcome = "written"

    summary = "Via integrations: " + " · ".join(
        "%s %s" % (n, GLYPH.get(st, st)) for n, st in status.items())
    json.dump({"integrations": report, "config": outcome, "config_path": path, "migration": migration, "summary": summary},
              sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 1 if create_failed else 0


if __name__ == "__main__":
    sys.exit(main())
