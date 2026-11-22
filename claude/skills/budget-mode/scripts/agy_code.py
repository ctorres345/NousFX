#!/usr/bin/env python3
"""Run one headless agy coding pass in the current git repository (budget mode, NFX-281).

Usage: python3 -I -B agy_code.py --prompt-file FILE [--timeout 900] [--model NAME] [--dry-run]

Exit codes:
  0  done (agy returned JSON with status SUCCESS)
  2  bad usage
  3  setup problem (not in a linked git worktree, a nested repository or submodule in it, unreadable or empty
     prompt file, allow-list unreadable)
  4  project not allow-listed for agy in ~/.config/nousfx/via.json, or its gemini_code_paths leave out a file
     (agy reads the whole worktree)
  7  the prompt, or an uncommitted file agy could read, looks like a secret
  9  agy failed (non-zero exit, non-JSON output, or status other than SUCCESS), or UNSAFE: it wrote a gitignored
     file or touched .git (checked before any git call, so a planted core.fsmonitor or hook never runs here)
 10  timed out
 21  subscription limit reached
 22  agy not installed or not signed in

On any non-zero exit this prints one line `Stopped: <why>` to stderr (argparse's own usage errors, exit 2, print
its usage text instead); on 9, 10, 21 and 22 it also prints
the JSON summary line on stdout.
"""
import argparse
import hashlib
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

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.join(HERE, "..", "..", "..", "..")
sys.path.insert(0, os.path.join(REPO, "plugins", "via", "scripts"))
sys.path.insert(0, os.path.join(REPO, "claude", "skills", "gemini-review", "scripts"))
import via_agy_call as vac  # noqa: E402
import gemini_common as gc  # noqa: E402

MODEL = "gemini-4-neon-medium"
DEFAULT_TIMEOUT = 900
MIN_TIMEOUT = 60
MAX_TIMEOUT = 1800
KILL_GRACE_SECONDS = 30

RULES = (
    "Edit files only inside this repository and only for the task below. "
    "There is no shell, so do not run commands or tests. "
    "Never edit an existing test to make it pass; add tests instead. "
    "Finish with a short summary of the files you changed."
)


def _coding_settings():
    """via_agy_call.SETTINGS with file writes and edits allowed: headless agy otherwise runs shell commands and
    reads files outside the workspace, whatever RULES says. Any deny rule added there reaches coding runs too."""
    settings = json.loads(vac.SETTINGS)
    deny = settings["permissions"]["deny"]
    settings["permissions"]["deny"] = [d for d in deny if d not in ("write_file(*)", "edit_file(*)")]
    return json.dumps(settings)


SETTINGS = _coding_settings()


def _stop(code, why):
    sys.stderr.write("Stopped: %s\n" % why)
    sys.stderr.flush()
    sys.exit(code)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompt-file", required=True, help="UTF-8 file holding the ticket and plan")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds (%d..%d)" % (MIN_TIMEOUT, MAX_TIMEOUT))
    ap.add_argument("--model", default=MODEL, help="agy model name (default: %s)" % MODEL)
    ap.add_argument("--dry-run", action="store_true", help="print the command as JSON and exit 0")
    args = ap.parse_args(argv)
    if not (MIN_TIMEOUT <= args.timeout <= MAX_TIMEOUT):
        _stop(2, "--timeout must be between %d and %d seconds" % (MIN_TIMEOUT, MAX_TIMEOUT))
    if not gc.AGY_MODEL_ARG_RE.fullmatch(args.model):
        _stop(2, "bad --model %r" % args.model)
    return args


def build_command(agy, prompt, model, timeout):
    return [
        agy,
        "-p", RULES + "\n\n" + prompt,
        "--model", model,
        "--output-format", "json",
        "--mode", "accept-edits",
        "--print-timeout", "%ds" % timeout,
        "--disable-slash-commands",
        "--settings", SETTINGS,
    ]


def _sha256(path):
    """The file's sha256; for a symlink or other non-regular file a tag of its kind and target, so deleting one
    still counts as a change; None only when the path is missing."""
    try:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            return "link:" + os.readlink(path)
        if not stat.S_ISREG(st.st_mode):
            return "mode:%o" % stat.S_IFMT(st.st_mode)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def snapshot(root):
    """Map each dirty or untracked path relative to root to _sha256 of it."""
    r = gc.git("-C", root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if r.returncode != 0:
        _stop(3, "git status failed")
    out = {}
    entries = iter(r.stdout.split(b"\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        if b"R" in entry[:2] or b"C" in entry[:2]:
            next(entries, None)  # porcelain -z follows a rename/copy destination with its original path
        path = os.fsdecode(entry[3:])
        out[path] = _sha256(os.path.join(root, path))
    return out


def git_dir_state(root):
    """The linked worktree's .git file, read without running git: a gitdir repointed to a folder agy wrote, with
    a core.fsmonitor or hook in it, runs code on the next git call. The real gitdir is outside agy's workspace."""
    return {".git": _sha256(os.path.join(root, ".git"))}


def submodules(root):
    """Gitlinks in the index: git status follows each one's .git file, which agy could repoint."""
    r = gc.git("-C", root, "ls-files", "-s", "-z")
    if r.returncode != 0:
        _stop(3, "git ls-files failed")
    return sorted(os.fsdecode(e.split(b"\t", 1)[1]) for e in r.stdout.split(b"\0") if e.startswith(b"160000 "))


def ignored_state(root):
    """Every gitignored file (size and mtime). A diff read never shows them, and one such file
    (.claude/settings.local.json, say) can run code on Claude's next step."""
    out = {}
    r = gc.git("-C", root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    if r.returncode != 0:
        _stop(3, "git ls-files failed")
    for raw in r.stdout.split(b"\0"):
        if raw:
            path = os.fsdecode(raw)
            try:
                st = os.lstat(os.path.join(root, path))
                out[path] = (st.st_size, st.st_mtime_ns)
            except OSError:
                out[path] = None
    return out


SECRET_NAME = re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]*\.(pem|key|p12|pfx|jks|keystore)|local\.properties|"
                         r"settings\.local\.json|credentials(\.json)?)$", re.I)
SCAN_LIMIT = 1024 * 1024


def uncommitted_secrets(root, paths):
    """Uncommitted files (untracked, changed or gitignored) agy could read and send: the workspace rule lets it
    open them. Those whose name or first MiB looks like a secret stop the run."""
    found = []
    for path in sorted(paths):
        if "__pycache__/" in path or path.endswith(".pyc"):
            continue
        if SECRET_NAME.search(path):
            found.append(path)
            continue
        try:
            with open(os.path.join(root, path), "rb") as f:
                if gc._SECRET_RE.search(f.read(SCAN_LIMIT)):
                    found.append(path)
        except OSError:
            continue
    return found


def changed_files(before, after):
    """Sorted paths added, modified, reverted or deleted between two snapshots."""
    return sorted({p for p in after if p not in before or before[p] != after[p]} | (before.keys() - after.keys()))


def parse_reply(out):
    """Parse agy's JSON output; requires a dict with a string response."""
    if not isinstance(out, str):
        raise ValueError("expected string output")
    reply = json.loads(out)
    if not isinstance(reply, dict) or not isinstance(reply.get("response"), str):
        raise ValueError("agy JSON is missing a string response")
    return reply


def run(cmd, cwd, timeout):
    """Run agy in its own session with temp log files. Returns (returncode_or_None_on_timeout, out, err, seconds)."""
    logs = tempfile.mkdtemp(prefix="agy-code-out-")
    old = {}
    started = time.monotonic()
    try:
        def stop_signal(signum, frame):
            raise SystemExit(128 + signum)

        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None)):
                if sig is not None and signal.getsignal(sig) is not signal.SIG_IGN:
                    old[sig] = signal.signal(sig, stop_signal)
        with open(os.path.join(logs, "out"), "w+", encoding="utf-8", errors="replace") as out_f, \
                open(os.path.join(logs, "err"), "w+", encoding="utf-8", errors="replace") as err_f:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                env=vac.agy_env(),
                start_new_session=True,
            )
            try:
                proc.wait(timeout=timeout + KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                vac.kill_group(proc)
                return None, "", "", time.monotonic() - started
            except BaseException:
                vac.kill_group(proc)
                raise
            vac.kill_group(proc)
            elapsed = time.monotonic() - started
            out_f.seek(0)
            err_f.seek(0)
            return proc.returncode, out_f.read(), err_f.read(), elapsed
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        shutil.rmtree(logs, ignore_errors=True)


def classify(returncode, out, err, timeout, seconds=0.0):
    """Maps one agy run to (status, exit code, why or None, parsed reply or {}). A non-zero exit at or past the
    timeout is agy's own --print-timeout firing, so it is a timeout too."""
    if returncode is None or (returncode != 0 and seconds >= timeout):
        return "TIMEOUT", 10, "agy timed out after %d s" % timeout, {}
    if returncode != 0:
        said = err.strip()[-400:]
        if vac.LIMIT.search(err):
            return "LIMIT", 21, "subscription limit reached: %s" % said, {}
        if vac.SIGNED_OUT.search(err):
            return "UNAVAILABLE", 22, "agy is not signed in: %s" % said, {}
        return "FAILED", 9, "agy failed (exit %d): %s" % (returncode, said or out.strip()[-400:] or "no output"), {}
    try:
        reply = parse_reply(out)
    except ValueError:
        return "FAILED", 9, "agy did not return expected JSON", {}
    status = reply.get("status") if isinstance(reply.get("status"), str) and reply.get("status") else "FAILED"
    if status != "SUCCESS":
        return status, 9, "agy reported status %r" % (reply.get("status"),), reply
    return status, 0, None, reply


def _print_summary(status, model, seconds, tokens, changed, response, denied_actions):
    summary = {
        "status": status,
        "model": model,
        "seconds": round(seconds, 1),
        "tokens": tokens,
        "changed": changed,
        "response": response[:2000],
        "denied_actions": denied_actions,
    }
    sys.stdout.write(json.dumps(summary) + "\n")
    sys.stdout.flush()


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        with open(args.prompt_file, encoding="utf-8") as f:
            prompt = f.read()
    except (OSError, UnicodeDecodeError) as e:
        _stop(3, "cannot read prompt file %s (%s)" % (args.prompt_file, e))
    if not prompt.strip():
        _stop(3, "prompt file %s is empty" % args.prompt_file)

    gc.go_to_root()
    root = os.getcwd()

    route = gc.plan_route()
    if not route["agy"]:
        _stop(4, "this project is not allow-listed for agy in ~/.config/nousfx/via.json")
    outside = gc.outside_paths(route["paths"])
    if outside:
        _stop(4, "agy reads the whole worktree, but gemini_code_paths (%s) leave out %d file(s), e.g. %s. Cesar can "
                 "add \".\" to the entry in ~/.config/nousfx/via.json; else use the implementer"
              % (", ".join(route["paths"]), len(outside), ", ".join(outside[:3])))
    gc.stop_on_secret(prompt.encode("utf-8"))

    agy = vac.find_agy()
    if not agy:
        _print_summary("UNAVAILABLE", args.model, 0.0, {}, [], "", [])
        _stop(22, "agy is not installed or not on PATH")

    cmd = build_command(agy, prompt, args.model, args.timeout)
    if args.dry_run:
        sys.stdout.write(json.dumps({"command": cmd}) + "\n")
        sys.stdout.flush()
        return 0

    if not os.path.isfile(os.path.join(root, ".git")):
        _stop(3, "run from a linked worktree (git worktree add): in a main checkout agy could write .git and "
                 "the other worktrees inside it")
    git_before, before, ignored_before = git_dir_state(root), snapshot(root), ignored_state(root)
    nested = sorted(p for p in list(before) + list(ignored_before) if p.endswith("/")) + submodules(root)
    if nested:
        _stop(3, "the worktree holds nested repositories or submodules agy could edit unseen, or whose .git file "
                 "git would follow (%s)" % ", ".join(nested[:5]))
    secrets = uncommitted_secrets(root, set(before) | set(ignored_before))
    if secrets:
        _stop(7, "uncommitted files that agy could read look like secrets (%s); move them out of the worktree or "
                 "use the implementer" % ", ".join(secrets[:5]))
    try:
        returncode, out, err, seconds = run(cmd, root, args.timeout)
    except OSError as e:
        _print_summary("FAILED", args.model, 0.0, {}, [], "", [])
        _stop(9, "cannot start agy (%s)" % (e.strerror or type(e).__name__))
    git_changed = changed_files(git_before, git_dir_state(root))
    if git_changed:
        _print_summary("UNSAFE", args.model, seconds, {}, git_changed, "", [])
        _stop(9, "agy touched .git (%s). Do not run git in this tree; remove the worktree" % ", ".join(git_changed))
    after = snapshot(root)
    changed = changed_files(before, after)
    hidden = changed_files(ignored_before, ignored_state(root))

    status, code, why, reply = classify(returncode, out, err, args.timeout, seconds)
    if hidden:
        status, code = "UNSAFE", 9
        why = "agy changed gitignored files, which a diff does not show: %s" % ", ".join(hidden[:20])
    tokens = reply["usage"] if isinstance(reply.get("usage"), dict) else {}
    denied_actions = reply["denied_actions"] if isinstance(reply.get("denied_actions"), list) else []
    response = reply.get("response") if isinstance(reply.get("response"), str) else (out.strip() if returncode == 0 else "")
    _print_summary(status, args.model, seconds, tokens, changed + hidden, response, denied_actions)
    if code != 0:
        _stop(code, why)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
