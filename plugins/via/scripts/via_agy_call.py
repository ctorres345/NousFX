"""via-agy-call: one headless call to agy (the Antigravity CLI) on the user's Google AI Pro subscription.

A library for via_assets.py generate (NFX-162), written so other Via scripts can reuse it (NFX-164).
`call(prompt, timeout)` runs `agy -p ... --output-format json` in an empty temp folder and returns the parsed
reply. `image(reply, started)` reads the image agy says it wrote: only a path named in the reply text, inside this
conversation's brain folder, written during this call, with PNG, JPEG or WebP content. The brain folder is never scanned, so a stale file is never
picked up. `svg(reply)` returns the one <svg> document in the reply text.

Every prompt starts with NO_SHELL. agy's headless auto-deny is only the backstop.

`call(..., files={name: bytes})` puts those files in the temp folder, and the prompt lets agy read only them
(Argon reads screenshots and a diff this way, NFX-164).

Usage as a script: via_agy_call.py text --prompt-file FILE [--timeout S] [--model gemini-...] [--file PATH ...]
                                         [--project DIR]
prints {"conversation_id", "response", "usage"}. Each --file is copied into agy's folder under its base name.
When agy fails it prints "Stopped: ..." and exits 21 when the subscription limit is reached, 22 when agy is not
installed or not signed in (callers may fall back to the billed API on these two), and 1 for anything else.
The caller checks the outbound allow-list (via_outbound.py check --dest agy) first.
"""
import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import via_home  # noqa: E402

MODEL = "gemini-3.8-flash-low"
NO_SHELL = ("Do not run shell commands, do not read, write or delete any file, and do not use any tool except "
            "generate_image (or the image-generator subagent that runs it) when an image is asked for. Answer "
            "in plain text.")
READ_ONLY = ("Do not run shell commands, do not write or delete any file, and do not use any tool except reading "
             "these files in the current folder: %s. Read no other file. Answer in plain text.")
DRAW_WITH = READ_ONLY.replace("these files in the current folder: %s.", "these style reference images in the "
                              "current folder: %s, and generate_image (or the image-generator subagent that runs it) "
                              "when an image is asked for.")
FILE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
MAX_FILES = 16 * 1024 * 1024  # all files of one call together
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
# Exit 21 and 22 allow a billed fallback, so only clear wording counts; anything else is exit 1. The limit is
# checked first.
SIGNED_OUT = re.compile(r"not (signed|logged) in|please (sign|log) ?in|(sign|log) ?in (again|required)"
                        r"|login required|not authenticated|unauthenticated|\bunauthori[sz]ed\b", re.I)
LIMIT = re.compile(r"resource_exhausted|quota (exceeded|reached)|exceeded (your |the )?quota"
                   r"|rate.?limit(ed| (reached|exceeded))|(reached|hit|exceeded) your (usage|daily|plan) limit"
                   r"|\b429 too many requests\b|(?:code|status|error|http)\W{0,3}429\b|\b429\W{0,3}resource", re.I)
# Session-only settings (never saved). Probed 2026-11-17: headless agy runs shell commands and reads files
# anywhere unless told otherwise. With these, file tools stay inside its temp folder (a subagent's too), and
# commands, MCP tools and writes are denied, whatever the user's settings.json allows. search_web cannot be
# denied this way; it goes to Google, which gets the prompt anyway.
SETTINGS = json.dumps({"allowNonWorkspaceAccess": False,
                       "permissions": {"allow": [], "deny": ["command(*)", "mcp(*)", "write_file(*)", "edit_file(*)"]}})
SECRET_ENV = re.compile(r"API_?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)
CLOUD_ENV = re.compile(r"(GOOGLE_|GEMINI_|CLOUDSDK_|VERTEX)", re.I)  # could route agy to billed Cloud auth
MAX_IMAGE = 50 * 1024 * 1024


class AgyError(Exception):
    pass


class AgyUnavailable(AgyError):
    """agy is not installed, or not signed in."""


class AgyLimit(AgyError):
    """The subscription limit is reached."""


def find_agy(skip=()):
    """~/.local/bin/agy (agy.exe on Windows; home from the user record), else agy on PATH, else None. Relative
    PATH entries and any folder inside the current folder or a `skip` folder (the project) are skipped, so a
    program committed to a repo is never started."""
    return find_program("agy", skip)


def find_program(name, skip=()):
    """find_agy's lookup for any program name, such as gemini-api."""
    local = os.path.join(via_home.home_dir(), ".local", "bin", name + ".exe" if os.name == "nt" else name)
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    roots = [os.path.normcase(os.path.realpath(d)) for d in (os.getcwd(), *skip) if d]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        real = os.path.normcase(os.path.realpath(folder)) if folder and os.path.isabs(folder) else None
        if real is None or any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
            continue
        found = shutil.which(name, path=folder)
        if found and os.path.isabs(found):
            return found
    return None


def brain_dir(conversation_id):
    return os.path.join(via_home.home_dir(), ".gemini", "antigravity-cli", "brain", conversation_id)


def agy_env():
    """The caller's environment minus API keys and other secrets: agy must sign in with the subscription, never
    with a billed GEMINI_API_KEY or GOOGLE_API_KEY, and the model process has no use for the caller's tokens."""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k) and not CLOUD_ENV.match(k)}


def call(prompt, timeout, model=MODEL, files=None, skip=(), draw=False):
    """One agy call. Returns {"conversation_id", "response", ...} or raises AgyError with what agy said.

    files maps a plain file name to its bytes. They are written into agy's empty folder, and the prompt lets agy
    read only them."""
    if not 1 <= timeout <= 1800:
        raise AgyError("the timeout must be 1 to 1800 seconds")
    files = dict(files or {})
    if not all(FILE_NAME.fullmatch(n) for n in files):
        raise AgyError("a file name for agy must be a plain name (letters, digits, '_', '.', '-')")
    if sum(len(d) for d in files.values()) > MAX_FILES:
        raise AgyError("the files for agy are over %d bytes" % MAX_FILES)
    rules = (DRAW_WITH if draw else READ_ONLY) % ", ".join(sorted(files)) if files else NO_SHELL
    agy = find_agy(skip)
    if not agy:
        raise AgyUnavailable("agy is not installed or not on PATH. Install the Antigravity CLI and sign in, then try again")
    cwd = tempfile.mkdtemp(prefix="via-agy-")  # agy never sees the repo
    logs = tempfile.mkdtemp(prefix="via-agy-out-")  # output goes to files: a helper holding a pipe cannot stall us
    old = {}
    try:
        for name, data in files.items():
            with open(os.path.join(cwd, name), "xb") as f:
                f.write(data)
        def stop(signum, frame):
            raise SystemExit(128 + signum)
        if threading.current_thread() is threading.main_thread():  # a SIGTERM or SIGHUP to us still kills agy
            for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
                if sig is not None and signal.getsignal(sig) is not signal.SIG_IGN:  # keep nohup's choice
                    old[sig] = signal.signal(sig, stop)
        with open(os.path.join(logs, "out"), "w+", encoding="utf-8", errors="replace") as out_f, \
                open(os.path.join(logs, "err"), "w+", encoding="utf-8", errors="replace") as err_f:
            try:
                # Its own session, so the whole group (agy and every helper it started) can be killed.
                proc = subprocess.Popen([agy, "-p", rules + "\n\n" + prompt, "--model", model, "--output-format",
                                         "json", "--print-timeout", "%ds" % timeout, "--disable-slash-commands",
                                         "--settings", SETTINGS], cwd=cwd,
                                        stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f, env=agy_env(),
                                        start_new_session=True)
            except OSError as e:
                raise AgyError("cannot start agy (%s)" % e.strerror)
            try:
                proc.wait(timeout=timeout + min(30, timeout))
            except subprocess.TimeoutExpired:
                kill_group(proc)
                raise AgyError("agy did not answer within %d s (raise --timeout, or try again later)" % timeout)
            except BaseException:  # Ctrl-C, SIGTERM, SIGHUP: agy is in its own session, so stop it ourselves
                kill_group(proc)
                raise
            kill_group(proc)  # helpers left behind by a finished agy go too
            out_f.seek(0)
            err_f.seek(0)
            out, err = out_f.read(), err_f.read()
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        shutil.rmtree(cwd, ignore_errors=True)
        shutil.rmtree(logs, ignore_errors=True)
    said = err.strip()[-400:]  # only agy's own stderr: stdout can echo the prompt or the model's text
    if proc.returncode != 0:
        if LIMIT.search(said):
            raise AgyLimit("the subscription limit is reached; try again later. agy said: %s" % said)
        if SIGNED_OUT.search(said):
            raise AgyUnavailable("agy is not signed in. Run agy once and sign in with your Google account. agy said: %s"
                                 % said)
        raise AgyError("agy failed (exit %d): %s" % (proc.returncode, said or out.strip()[-400:] or "no output"))
    try:
        reply = json.loads(out)
    except ValueError:
        raise AgyError("agy did not return JSON. Raw output: %s" % out.strip()[:400])
    if (not isinstance(reply, dict) or not isinstance(reply.get("response"), str)
            or not isinstance(reply.get("conversation_id"), str)
            or not re.fullmatch(r"[A-Za-z0-9-]{8,64}", reply["conversation_id"])):
        raise AgyError("agy's JSON is not the expected shape. Raw output: %s" % out.strip()[:400])
    if reply.get("status") != "SUCCESS":
        raise AgyError("agy reported status %r: %s" % (reply.get("status"), reply["response"][:400]))
    denied = reply.get("denied_actions") if isinstance(reply.get("denied_actions"), list) else []
    if denied and not reply["response"].strip():
        raise AgyError("agy answered nothing after a denied tool call: %s" % json.dumps(denied)[:300])
    if files and any(isinstance(d, dict) and d.get("action") == "read_file" for d in denied):
        # It may not have read the files it was given, so its answer cannot be trusted as a review of them.
        raise AgyError("agy was denied a file read, so its answer may not cover the files: %s" % json.dumps(denied)[:300])
    return reply


def kill_group(proc):
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except OSError:
        pass  # already gone
    proc.wait()


def image(reply, started):
    """(bytes, extension) of the image image_file finds, or None."""
    found = image_file(reply, started)
    return found[1:] if found else None


def image_file(reply, started):
    """(path, bytes, extension) of the first image named in the reply text that sits in this conversation's brain
    folder, is a regular file written during this call, is under MAX_IMAGE bytes and whose content is really a
    PNG, JPEG or WebP (the extension comes from the content). None when there is none. Never lists the folder."""
    root = os.path.realpath(brain_dir(reply["conversation_id"])) + os.sep
    for raw in re.findall(r"(/[^\s`'\"()<>\[\]]+?\.(?:png|jpe?g|webp))", reply["response"], re.I):
        path = os.path.realpath(raw)
        if not path.startswith(root):
            continue
        try:  # O_NOFOLLOW: a symlink swapped in after realpath is refused, not followed
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                         | getattr(os, "O_NOCTTY", 0))  # O_NONBLOCK: a FIFO cannot hang us; S_ISREG refuses it
        except OSError:
            continue
        with os.fdopen(fd, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_mtime < started - 1 or st.st_size > MAX_IMAGE:
                continue
            data = f.read(MAX_IMAGE + 1)
        ext = image_type(data)
        if ext and len(data) <= MAX_IMAGE:
            return path, data, ext
    return None


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def svg(reply):
    """The single <svg>...</svg> document in the reply: from the one fenced code block that holds one when there
    is such a block (so prose that names the tag is skipped), else from the whole text. None when there is not
    exactly one. The caller parses it before trusting it."""
    text = reply["response"]
    blocks = [b for b in re.findall(r"```[A-Za-z]*\n(.*?)```", text, re.S) if re.search(r"<svg\b", b, re.I)]
    if len(blocks) == 1:
        text = blocks[0]
    docs = re.findall(r"<svg\b.*?</svg>", text, re.S | re.I)
    return docs[0].strip() + "\n" if len(docs) == 1 else None


def now():
    return time.time()


def read_file(path):
    """A regular file's bytes, opened without following a symlink, so what is checked is what is sent."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                 | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise AgyError("%s is not a regular file" % path)
        return f.read(MAX_FILES + 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("text")
    t.add_argument("--prompt-file", required=True)
    t.add_argument("--timeout", type=int, default=300)
    t.add_argument("--model", default=MODEL)
    t.add_argument("--file", action="append", default=[])
    t.add_argument("--project", action="append", default=[], help="never take agy from inside this folder")
    args = ap.parse_args()
    try:
        if not re.fullmatch(r"gemini-[a-z0-9.-]+", args.model):
            raise AgyError("the model must be a gemini- model from `agy models`")
        files = {}
        for path in args.file:
            name = os.path.basename(path)
            if name in files:
                raise AgyError("two files are named %s" % name)
            files[name] = read_file(path)
        with open(args.prompt_file, encoding="utf-8") as f:
            prompt = f.read()
        reply = call(prompt, args.timeout, args.model, files, args.project)
    except (OSError, AgyError) as e:
        code = 21 if isinstance(e, AgyLimit) else 22 if isinstance(e, AgyUnavailable) else 1
        print("Stopped: %s." % e, file=sys.stderr)
        sys.exit(code)
    usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
    print(json.dumps({"conversation_id": reply["conversation_id"], "response": reply["response"], "usage": usage}))


if __name__ == "__main__":
    main()
