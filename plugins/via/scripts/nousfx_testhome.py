"""Test helper (not production code): runs a script as a subprocess whose system user record points at a temp home.

The scripts read the home from pwd.getpwuid, so a test cannot steer them with $HOME. A sitecustomize shim, found
through PYTHONPATH, makes pwd.getpwuid report FAKE_PW_DIR. The scripts themselves read no such variable.
"""
import atexit
import os
import shutil
import tempfile

_SHIM = tempfile.mkdtemp(prefix="nousfx-shim-")
atexit.register(shutil.rmtree, _SHIM, True)
with open(os.path.join(_SHIM, "sitecustomize.py"), "w") as _f:
    _f.write(
        "import os, pwd\n"
        "_real = pwd.getpwuid\n"
        "def _fake(uid):\n"
        "    r = _real(uid)\n"
        "    d = os.environ.get('FAKE_PW_DIR')\n"
        "    return pwd.struct_passwd((r.pw_name, r.pw_passwd, r.pw_uid, r.pw_gid, r.pw_gecos, d, r.pw_shell)) if d else r\n"
        "pwd.getpwuid = _fake\n")


def env(home, **extra):
    """An environment where the user record says `home`. HOME is set to it too unless overridden in extra."""
    return {"HOME": home, "FAKE_PW_DIR": home, "PYTHONPATH": _SHIM, **extra}
