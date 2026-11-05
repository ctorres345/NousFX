"""via_home: where the personal Via config lives, shared by via_outbound.py and via_setup.py.

The anchor is ~/.config/nousfx. The home directory comes from the system user record (USERPROFILE on Windows, which has no pwd), never from $HOME,
and nothing in the environment can move the anchor. A config must be a regular file (not a symlink) inside the
anchor, and the anchor must not sit inside a git work tree, so a repo cannot allow-list itself. The work-tree check fails closed:
only a clean "true"/"false" or a clean "not a git repository" answer passes; any other git outcome is refused.
The old ~/.claude/via.json is read as a fallback for one release and copied once by `migrate`, and it gets the
same work-tree check (a dotfiles repo pulled from a remote must not be able to allow-list a project).

Callers must run the scripts as `python3 -I -B`, so PYTHONPATH, user site and sitecustomize (which a repo's
committed .claude/settings.json `env` block could set) cannot patch pwd and move the anchor. Out of scope: PATH.
A hostile PATH can swap `python3` or `git` itself, and that is outside what this module can defend.
"""
import os
import subprocess
import tempfile

NAME = "via.json"


class Refused(Exception):
    pass


def home_dir():
    try:
        import pwd
        return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    except ImportError:  # Windows has no pwd; the profile folder is its user record
        return os.path.realpath(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def anchor():
    return os.path.realpath(os.path.join(home_dir(), ".config", "nousfx"))


def default_config():
    return os.path.join(anchor(), NAME)


def legacy_root():
    return os.path.realpath(os.path.join(home_dir(), ".claude"))  # may itself be a symlink (dotfiles)


def legacy_config():
    return os.path.join(legacy_root(), NAME)


def _inside_git_worktree(directory):
    """True when directory (or its nearest existing parent) is in a git work tree. Fails closed on doubt."""
    while not os.path.isdir(directory):
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"  # the "not a git repository" message is matched below
    try:
        r = subprocess.run(["git", "-C", directory, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        raise Refused("cannot run git to check that %s is outside a work tree" % directory)
    answer = r.stdout.strip()
    if r.returncode == 0 and answer in ("true", "false"):
        return answer == "true"
    if r.returncode == 128 and "not a git repository" in r.stderr:
        return False
    # Anything else (git missing, dubious ownership, a corrupt repo, odd output) is doubt: refuse.
    raise Refused("git could not say whether %s is inside a work tree" % directory)


def validate(path, root, git_check=True):
    """Raise Refused unless path is a regular, non-symlink file location inside root (and outside any work tree)."""
    if os.path.islink(path):
        raise Refused("the config %s is a symlink" % path)
    if os.path.lexists(path) and not os.path.isfile(path):
        raise Refused("the config %s is not a regular file" % path)
    folder = os.path.realpath(os.path.dirname(path))
    if os.path.commonpath([folder, root]) != root:
        raise Refused("the config %s is not inside %s" % (path, root))
    if git_check and _inside_git_worktree(folder):
        raise Refused("the config folder %s is inside a git work tree" % folder)


def config_path(explicit=None):
    """The config to use. An explicit path must still sit in the anchor. With none, fall back to the old path."""
    root = anchor()
    path = os.path.abspath(explicit) if explicit else default_config()
    validate(path, root)
    if not explicit and not os.path.lexists(path):
        old = legacy_config()
        if os.path.lexists(old):
            validate(old, legacy_root())
            return old
    return path


def migrate():
    """Copy the old config to the anchor once. Returns "migrated" or "none". The old file stays in place."""
    new, old = default_config(), legacy_config()
    validate(new, anchor())
    if os.path.lexists(new) or not os.path.lexists(old):
        return "none"
    validate(old, legacy_root())
    with open(old) as f:
        text = f.read()
    os.makedirs(os.path.dirname(new), mode=0o700, exist_ok=True)
    write_atomic(new, text)
    return "migrated"


def write_atomic(path, text):
    """Write text to path through a private temp file in the same folder, then rename it into place."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".via-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
