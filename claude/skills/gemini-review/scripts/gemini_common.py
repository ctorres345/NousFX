"""Shared safety stops for review.py and ui-review's argon_review.py. Import it, then call the functions.

Run the scripts as `python3 -I -B` (`python -I -B` on Windows). Every stop prints "Nothing was sent." and exits:
3 setup, 4 not allowed, 5 nothing to send, 6 too large, 7 likely secret, 8 bad screenshots, 9 gemini-api failed.

The scripts never read the Gemini key. gemini-api reads it from the OS keyring (macOS keychain, Windows
Credential Manager). check_key() refuses to run unless gemini-api reports the key's source as the keyring, and
child_env() hides the key variables from every child process.
"""
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

CLI_VERSION = "0.4.5"
MODEL = "gemini-4-argon"
STRIP_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
CLI_TIMEOUT = 600

# Files that often hold secrets never go into a diff.
_EXCLUDE_NAMES = [
    ".env*", "*.env", "*.env.*", "*password*", ".pgpass", ".pypirc", ".dockercfg", "kubeconfig", "*.ks",
    "*.keystore", "*.jks", "*.pem", "*.p12", "*.p8", "*.pfx", "*.key", "*.tfvars", "*.mobileprovision",
    "key.properties", "local.properties", "google-services.json", "GoogleService-Info.plist",
    "id_rsa*", "id_ed25519*", ".npmrc", ".netrc", "*secret*", "*credential*",
]
EXCLUDES = [":(exclude,icase,glob)**/%s" % p for p in _EXCLUDE_NAMES] + [
    ":(exclude,icase,glob)**/.env*/**", ":(exclude,icase,glob)**/*secret*/**", ":(exclude,icase,glob)**/*credential*/**",
]

# Same pattern Via's setup uses (a test keeps them equal). Matched on bytes, so a line with invalid UTF-8 cannot hide a secret.
SECRET_PATTERN = (
    r"(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{36}"
    r"|github_pat_[0-9A-Za-z_]{20,}|glpat-[0-9A-Za-z_-]{20,}|(^|[^0-9A-Za-z_-])sk-[0-9A-Za-z_-]{20,}"
    r"|[sr]k_live_[0-9A-Za-z]{10,}|xox[baprs]-[0-9A-Za-z-]{10,}"
    r"|eyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)"
)
_SECRET_RE = re.compile(SECRET_PATTERN.encode("ascii"), re.MULTILINE)


def stop(code, message):
    sys.stderr.write(message + "\n")
    sys.exit(code)


def take_platform(argv):
    """Pops the test-only `--platform windows|posix`, accepted only as the first two arguments. Returns (platform, rest)."""
    platform = "windows" if os.name == "nt" else "posix"
    rest = list(argv)
    if rest[:1] == ["--platform"]:
        if len(rest) < 2 or rest[1] not in ("windows", "posix"):
            stop(3, "Bad --platform. Nothing was sent.")
        platform = rest[1]
        del rest[:2]
    return platform, rest


def home_dir():
    try:
        import pwd
        return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    except ImportError:  # Windows has no pwd; the profile folder is its user record
        return os.path.realpath(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def child_env():
    return {k: v for k, v in os.environ.items() if k not in STRIP_ENV}


def _inside(path, folder):
    path, folder = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    try:
        return os.path.commonpath([path, folder]) == folder
    except ValueError:  # different drives
        return False


def _repo_top():
    """The folder holding .git above the current folder, found without running git (None outside a repo)."""
    folder = os.getcwd()
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


@functools.lru_cache(maxsize=None)
def which(name):
    """Absolute path of a program found on PATH, or None. Skips relative PATH entries and any folder inside the current folder or the repo.

    Windows CreateProcess looks a bare program name up in the current folder first, and that folder is the repo under
    review, so every program here is started by absolute path. On Windows only .exe and .com files count: a .bat or
    .cmd would run through cmd.exe, which looks its own commands up in the current folder.
    """
    skip = [d for d in (os.getcwd(), _repo_top()) if d]
    exts = [""] if os.name != "nt" or name.lower().endswith((".exe", ".com")) else [".exe", ".com"]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or not os.path.isabs(folder) or any(_inside(folder, d) for d in skip):
            continue
        for ext in exts:
            path = os.path.join(folder, name + ext)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None


def find_cli(platform):
    """~/.local/bin first; else PATH, but never a program inside the repo being reviewed (which() skips those)."""
    name = "gemini-api.exe" if platform == "windows" else "gemini-api"
    local = os.path.join(home_dir(), ".local", "bin", name)
    if os.access(local, os.X_OK) and not any(_inside(local, d) for d in (os.getcwd(), _repo_top()) if d):
        return local  # the home folder can itself be a repo (dotfiles), so this is checked too
    cli = which(name)
    if not cli:
        stop(3, "gemini-api is not installed")
    return cli


def _run(cmd, **kw):
    """Runs a probe in an empty folder, so nothing in the repo can steer it."""
    with tempfile.TemporaryDirectory(prefix="gemini-") as empty:
        return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, env=child_env(), timeout=30,
                              cwd=empty, **kw)


def check_cli(cli):
    try:
        out = _run([cli, "version"], text=True, errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    if not re.search(r"(^|[^0-9.])%s($|[^0-9.])" % re.escape(CLI_VERSION), out):
        stop(3, "Stopped: gemini-api is not the pinned %s. Nothing was sent." % CLI_VERSION)


def check_key(cli):
    """Stops unless gemini-api reports the key's source as the OS keyring. Only the source is read, never the value."""
    creds = None
    try:
        out = _run([cli, "whoami", "--output-format", "json"], text=True, errors="replace").stdout
        creds = json.loads(out).get("credentials")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        pass
    if isinstance(creds, dict) and all(isinstance(e, dict) for e in creds.values()):
        sources = [e.get("source") for e in creds.values() if e.get("source") not in (None, "unset")]
        if sources and all(s == "keyring" for s in sources):  # every credential in use must come from the keyring
            return
    stop(3, "Stopped: the Gemini key must be in the OS keyring (run `gemini-api auth login`). Nothing was sent.")


def check_range(rng):
    if rng.startswith("-"):
        stop(3, "Stopped: bad range %s. Nothing was sent." % rng)


def git(*args):
    exe = which("git")
    if not exe:
        stop(3, "Stopped: git was not found on PATH. Nothing was sent.")
    try:
        return subprocess.run([exe, *args], stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as e:
        stop(3, "Stopped: git could not run (%s). Nothing was sent." % type(e).__name__)


def go_to_root():
    r = git("rev-parse", "--show-toplevel")
    if r.returncode != 0 or not r.stdout.strip():
        stop(3, "Stopped: not in a git repo. Nothing was sent.")
    os.chdir(os.fsdecode(r.stdout.strip()))


def read_visibility():
    """The origin repo's visibility (PUBLIC, PRIVATE, INTERNAL) or UNKNOWN. Reads origin on purpose: a bare `gh repo view` prefers `upstream`, so a private fork would read as public."""
    try:
        url = os.fsdecode(git("remote", "get-url", "origin").stdout.strip())
        gh = which("gh")
        if url and gh:
            out = _run([gh, "repo", "view", url, "--json", "visibility"], text=True, errors="replace")
            return str(json.loads(out.stdout).get("visibility") or "UNKNOWN")
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        pass
    return "UNKNOWN"


def git_diff(rng, paths):
    """The diff as bytes, with secret-looking files excluded. External diff and textconv programs from repo config never run."""
    r = git("diff", "--no-ext-diff", "--no-textconv", rng, "--", *paths, *EXCLUDES)
    if r.returncode != 0:
        stop(3, "Stopped: git diff failed for %s. Nothing was sent." % rng)
    return r.stdout


def stop_on_secret(data):
    """Scans the text as it is and again with each line's diff marker (+, - or space) removed: a secret at the start of a removed line follows a `-`."""
    if _SECRET_RE.search(data) or _SECRET_RE.search(re.sub(rb"(?m)^[-+ ]", b"", data)):
        stop(7, "Stopped: the text looks like it contains a secret. Nothing was sent.")


def run_analyze(cli, args, stdin_data=None, files=None):
    """Runs `gemini-api analyze` in an empty temp folder (repo files stay out of reach) and prints its reply."""
    workdir = tempfile.mkdtemp(prefix="gemini-")
    try:
        for name, data in (files or {}).items():
            with open(os.path.join(workdir, name), "wb") as f:
                f.write(data)
        try:
            r = subprocess.run([cli, "analyze", *args], cwd=workdir, env=child_env(), stdout=subprocess.PIPE,
                               input=stdin_data, stdin=None if stdin_data is not None else subprocess.DEVNULL,
                               timeout=CLI_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            stop(9, "gemini-api failed (%s)." % type(e).__name__)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if r.returncode != 0:
        stop(9, "gemini-api failed (exit %d)." % r.returncode)
    sys.stdout.flush()
    sys.stdout.buffer.write(r.stdout)
    sys.stdout.buffer.flush()
