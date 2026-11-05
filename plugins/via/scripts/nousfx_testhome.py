"""Test helper (not production code): runs Via scripts as subprocesses whose system user record points at a temp home.

The scripts read the home from pwd.getpwuid, so a test cannot steer them with $HOME, and they run under
`python3 -I -B`, so PYTHONPATH, user site and sitecustomize cannot steer them either. `python(home)` returns an
executable stand-in for the interpreter. It starts the real interpreter with `-I -B` on a small wrapper that patches
pwd.getpwuid to report `home` (a value written into the stand-in, never read from the environment), then runs the
script as __main__. The wrapper also points sys.executable at the stand-in, so a script that starts a helper
script gets the same fake home. Nothing here is reachable from the environment in production.
"""
import atexit
import os
import shutil
import stat
import sys
import tempfile

_ROOT = tempfile.mkdtemp(prefix="nousfx-shim-")
atexit.register(shutil.rmtree, _ROOT, True)
_cache = {}

_WRAPPER = '''import pwd, runpy, sys
home, shim = sys.argv[1], sys.argv[2]
rest = sys.argv[3:]
while rest and rest[0].startswith("-"):
    rest = rest[1:]
real = pwd.getpwuid
def fake(uid):
    r = real(uid)
    return pwd.struct_passwd((r.pw_name, r.pw_passwd, r.pw_uid, r.pw_gid, r.pw_gecos, home, r.pw_shell))
pwd.getpwuid = fake
sys.executable = shim
sys.argv = rest
runpy.run_path(rest[0], run_name="__main__")
'''


def python(home):
    """Path of an interpreter stand-in whose user record says `home`. Call it like `python3 -I -B script args`."""
    if home not in _cache:
        folder = tempfile.mkdtemp(dir=_ROOT)
        wrapper = os.path.join(folder, "wrapper.py")
        with open(wrapper, "w") as f:
            f.write(_WRAPPER)
        shim = os.path.join(folder, "python3")
        with open(shim, "w") as f:
            f.write('#!/bin/sh\nexec "%s" -I -B "%s" "%s" "%s" "$@"\n' % (sys.executable, wrapper, home, shim))
        os.chmod(shim, os.stat(shim).st_mode | stat.S_IXUSR)
        _cache[home] = shim
    return _cache[home]


def bin_dir(home):
    """A folder whose `python3` is the stand-in, for scripts that look python3 up on PATH."""
    return os.path.dirname(python(home))


def env(home, **extra):
    """An environment for a test subprocess. HOME is set to home too unless overridden in extra."""
    return {"HOME": home, **extra}
