"""via_home: where the personal Via config lives, shared by via_outbound.py and via_setup.py.

The anchor is ~/.config/nousfx. The home directory comes from the system user record, never from $HOME,
and nothing in the environment can move the anchor. A config must be a regular file (not a symlink) inside the
anchor, and the anchor must not sit inside a git work tree, so a repo cannot allow-list itself.
The old ~/.claude/via.json is read as a fallback for one release and copied once by `migrate`.
"""
import os
import pwd
import subprocess
import tempfile

NAME = "via.json"


class Refused(Exception):
    pass


def home_dir():
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


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
    try:
        r = subprocess.run(["git", "-C", directory, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        raise Refused("cannot run git to check that %s is outside a work tree" % directory)
    return r.returncode == 0 and r.stdout.strip() == "true"


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
            validate(old, legacy_root(), git_check=False)
            return old
    return path


def migrate():
    """Copy the old config to the anchor once. Returns "migrated" or "none". The old file stays in place."""
    new, old = default_config(), legacy_config()
    validate(new, anchor())
    if os.path.lexists(new) or not os.path.lexists(old):
        return "none"
    validate(old, legacy_root(), git_check=False)
    with open(old) as f:
        text = f.read()
    folder = os.path.dirname(new)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".via-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, new)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return "migrated"
