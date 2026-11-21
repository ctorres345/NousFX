#!/usr/bin/env python3
"""via-assets-review: the review gallery behind `/via assets <KEY>`.

Usage: via_assets_review.py add     --key KEY --name NAME --file RAW [--conversation-id ID] [--prompt-file FILE]
                                    [--project DIR]
       via_assets_review.py import  --key KEY --name NAME --file FILE --origin TEXT [--project DIR]
       via_assets_review.py approve --key KEY --name NAME [--project DIR]
       via_assets_review.py reject  --key KEY --name NAME --note TEXT [--project DIR]
       via_assets_review.py status  --key KEY [--project DIR]
       via_assets_review.py shots   --key KEY --run-dir RUN_DIR [--project DIR]
       via_assets_review.py serve   --key KEY [--project DIR] [--port N] [--minutes N]

Everything for one ticket lives in design/asset-review/<KEY>/ in the project: state.json, gallery.html and one
copy of each candidate (<name>-<attempt>.<ext>). The folder holds its own .gitignore ("*"), so none of it is
ever committed. Every command rebuilds gallery.html, a static page (no network, no script except the copy
buttons) with one card per asset: the candidate on a light and a dark tile, name, kind, platforms, size, usage,
state and note. For a row with a transparent key color, add and import also make <name>-<attempt>.keyed.png, the
cut-out approve would write, and the tiles show that (or the card says why the key-out would fail).

States: pending (a candidate waits for review), approved, rejected (with a note). `add` takes a file that
`via_assets.py generate` wrote (only from that ticket's raw-output folder) for a name in design/assets.toml, and
makes it the pending candidate. `import` does the same for an image made outside Via (the Gemini app, by hand)
from any folder, with the same checks plus a real image header, and records --origin in the state, the gallery
card and the log. Adding or importing to a rejected or pending name is the regenerate step: the attempt count
goes up and only that asset changes. `approve` copies the candidate to the asset's `source` and runs `convert`
for that name only, so it lands in the platform folders. `reject` needs a note. Nothing is committed: the user
commits the assets, design/assets.toml and the log design/asset-log/<KEY>.md, which every command appends to.
With VIA_UNATTENDED=1, `approve` and `reject` refuse: an unattended run stops at the gallery.

`shots` copies each pending raster candidate into a Via run folder (made by `via_run.py init`) and lists it in
the run's findings.json draft (mode check, scope assets) with no `review` yet, so Claude can look at it, mark it
kept and send it to `via_second_opinion.py`. SVG candidates are listed under `capture`: shoot the gallery page with
`via_capture.py` for those.

`serve` serves the review folder on 127.0.0.1 for a browser that cannot load the gallery's images from a file://
page, such as the Claude app's browser pane (it shows a local file outside the open folder as a static snapshot).
It serves gallery.html and the candidate images only (no listing, no state.json, no symlinks, no subfolders),
with the gallery's own CSP as a header. It prints {"url", "minutes", "pid"} as its first line, then serves until it is stopped or
--minutes (default 60) pass, so a forgotten server stops by itself.

Stdout is JSON. Exit 0 on success, 1 when the input is wrong or the step is refused (the reason goes to stderr as
"Stopped: ..."; nothing was changed), 2 on a secret-shaped note, prompt or origin.
"""
import argparse
import html
import http.server
import io
import json
import os
import re
import stat
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)  # -I leaves the script folder off sys.path
import via_agy_call  # noqa: E402
import via_assets  # noqa: E402
import via_run  # noqa: E402

REVIEW = os.path.join("design", "asset-review")
LOG = os.path.join("design", "asset-log")
STATES = ("pending", "approved", "rejected")
RASTER = via_assets.RASTER_EXT
MAX_FILE = 16 * 1024 * 1024
MAX_NOTE = 500
MAX_PROMPT_IN_LOG = 600
MAX_ORIGIN = 200
CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
IMAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'"  # an SVG opened on its own runs no script
SERVED = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
          ".webp": "image/webp", ".svg": "image/svg+xml"}
MAX_PIXELS = 89478485  # Pillow's decompression-bomb limit, so approve never trips it
MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".webp": b"RIFF"}
CANDIDATE_RE = re.compile(r"[a-z0-9_]+-[0-9]+\.(svg|png|jpg|jpeg|webp)")  # <name>-<attempt>.<ext>, as add writes
PREVIEW_RE = re.compile(r"[a-z0-9_]+-[0-9]+\.keyed\.png")  # <name>-<attempt>.keyed.png, the key-out preview
MAX_KEY_NOTE = 300


die = via_assets.die
unattended = via_run.unattended  # VIA_UNATTENDED=1, true or yes, as for the other Via scripts


def folder(project, key):
    path = os.path.join(project, REVIEW, key)
    for part in (os.path.join(project, "design"), os.path.join(project, REVIEW), path):
        if os.path.islink(part):
            die("%s is a symlink. Nothing was changed." % via_assets.rel(project, part))
    return path


def load_state(project, key):
    path = os.path.join(folder(project, key), "state.json")
    if not os.path.exists(path):
        return {"version": 1, "key": key, "assets": {}}
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError) as e:
        die("cannot read %s (%s). Nothing was changed." % (via_assets.rel(project, path), e))
    assets = state.get("assets") if isinstance(state, dict) else None
    if not isinstance(assets, dict) or state.get("version") != 1 or state.get("key") != key or not all(
            entry_ok(a) for a in assets.values()):
        die("%s is not a review state this script wrote. Nothing was changed." % via_assets.rel(project, path))
    return state


def entry_ok(a):
    """One asset entry in the shape cmd_add writes."""
    return (isinstance(a, dict) and a.get("state") in STATES and a.get("kind") in via_assets.KINDS
            and isinstance(a.get("file"), str) and CANDIDATE_RE.fullmatch(a["file"]) is not None
            and type(a.get("attempt")) is int and a["attempt"] > 0 and type(a.get("svg_findings", 0)) is int
            and "note" in a and (a["note"] is None or isinstance(a["note"], str))
            and (a.get("origin") is None or isinstance(a["origin"], str))
            and (a.get("preview") is None or isinstance(a["preview"], str) and PREVIEW_RE.fullmatch(a["preview"]))
            and (a.get("key_note") is None or isinstance(a["key_note"], str))
            and (a.get("preview_key") is None or isinstance(a["preview_key"], str))
            and isinstance(a.get("history", []), list) and all(isinstance(h, dict) for h in a.get("history", [])))


def save(project, key, state, rows=None):
    """Write state.json, the folder's .gitignore and gallery.html."""
    base = folder(project, key)
    os.makedirs(base, exist_ok=True)
    ignore = os.path.join(base, ".gitignore")
    if not os.path.exists(ignore):
        via_assets.write_atomic(ignore, b"# Via review gallery: never committed.\n*\n")
    via_assets.write_atomic(os.path.join(base, "state.json"),
                            (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    via_assets.write_atomic(os.path.join(base, "gallery.html"), gallery(state, spec_rows(project) if rows is None else rows).encode("utf-8"))


def spec_rows(project):
    try:
        return {a["name"]: a for a in via_assets.load_spec(project)["assets"]}
    except via_assets.SpecError:
        return {}


def log_path(project, key):
    """The log path, checked before a command writes anything."""
    path = os.path.join(project, LOG, key + ".md")
    if os.path.islink(path) or os.path.islink(os.path.dirname(path)):
        die("%s is a symlink. Nothing was changed." % via_assets.rel(project, path))
    return path


def log(project, key, line):
    path = log_path(project, key)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = "# Asset log: %s\n\nWritten by `/via assets`. Commit it with the assets.\n\n" % key
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    via_assets.write_atomic(path, (text + "- %s %s\n" % (stamp, line)).encode("utf-8"))


def one_line(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def checked_text(value, what, limit):
    if not value or not value.strip():
        die("%s is empty. Nothing was changed." % what)
    if via_run.has_secret(value):
        die("%s holds a secret-shaped value. Nothing was changed." % what, 2)
    return one_line(value, limit)


def entry(spec, name):
    if name not in spec:
        die("%s is not an asset in %s. Add its row first. Nothing was changed." % (name, via_assets.SPEC))
    return spec[name]


def cmd_add(project, args):
    raw_root = via_assets.raw_dir(project, args.key)
    src = os.path.abspath(args.file)
    if os.path.islink(src) or not via_assets.inside(src, raw_root) or not os.path.isfile(src):
        die("%s is not a file that `generate` wrote for %s (it must be inside %s). Nothing was changed."
            % (args.file, args.key, raw_root))
    prompt = None
    if args.prompt_file:
        try:
            with open(args.prompt_file, encoding="utf-8") as f:
                prompt = checked_text(f.read(), "The prompt", MAX_PROMPT_IN_LOG)
        except OSError as e:
            die("cannot read the prompt file (%s). Nothing was changed." % e.strerror)
    conv = args.conversation_id or ""
    if conv and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", conv):
        die("--conversation-id is not an agy conversation id. Nothing was changed.")
    return admit(project, args, src, conv=conv, prompt=prompt)


def cmd_import(project, args):
    """An image made outside Via (the Gemini app, by hand): the same checks as `add`, from any folder."""
    origin = checked_text(args.origin, "The origin", MAX_ORIGIN)
    src = os.path.abspath(args.file)
    if os.path.islink(src) or not os.path.isfile(src):
        die("%s is a symlink or not a file. Nothing was changed." % args.file)
    return admit(project, args, src, origin=origin)


def keyed_preview(base, name, asset):
    """(preview file, None), or (None, why not) for a row with a transparent key color: the keyed PNG approve
    would write, so the gallery tiles show the real cut-out. (None, None) for other rows."""
    if not asset.get("transparent") or name.endswith(".svg"):
        return None, None
    preview = "%s.keyed.png" % os.path.splitext(name)[0]
    error = via_assets.run_resize({"src": os.path.join(base, name), "dst": os.path.join(base, preview),
                                   "key": asset["transparent"]}, "_keyout")
    if not error:
        return preview, None
    if isinstance(error, via_assets.KeyRefusal):
        return None, "Key-out: %s" % error[:MAX_KEY_NOTE]
    return None, "No cut-out preview (%s). Fix it with /via setup; this is no reason to reject." % error[:MAX_KEY_NOTE]


def admit(project, args, src, conv="", prompt=None, origin=None):
    """Check one file against its assets.toml row and make it the asset's pending candidate."""
    spec = load(project)
    asset = entry(spec["rows"], args.name)
    ext = os.path.splitext(src)[1].lower()
    if (asset["kind"] == "svg") != (ext == ".svg") or (ext != ".svg" and ext not in RASTER):
        die("%s is a %s asset, and %s is not. Nothing was changed." % (args.name, asset["kind"], args.file))
    try:
        if os.path.getsize(src) > MAX_FILE:
            die("%s is over %d bytes. Nothing was changed." % (args.file, MAX_FILE))
        data = via_agy_call.read_file(src)
    except (OSError, via_agy_call.AgyError) as e:
        die("cannot read %s (%s). Nothing was changed." % (args.file, getattr(e, "strerror", None) or e))
    if len(data) > MAX_FILE:
        die("%s is over %d bytes. Nothing was changed." % (args.file, MAX_FILE))
    findings = 0
    if ext == ".svg":
        root, _, problem = via_assets.parse_svg_bytes(data)
        found = [] if root is None else via_assets.svg_findings(root, args.name, spec["palette"], "android" in
                                                                  asset["platforms"])
        if problem or {f["rule"] for f in found} & via_assets.UNSAFE_SVG:
            die("%s breaks the SVG safety rules. Generate it again. Nothing was changed." % args.file)
        findings = len(found)
    else:
        size = via_assets.image_size(io.BytesIO(data)) if data.startswith(MAGIC[ext.replace(".jpeg", ".jpg")]) else None
        if size is None:
            die("%s is not a readable %s image. Nothing was changed." % (args.file, ext[1:].upper()))
        if size[0] * size[1] > MAX_PIXELS:
            die("%s is %dx%d, over %d pixels. Nothing was changed." % (args.file, size[0], size[1], MAX_PIXELS))

    state = load_state(project, args.key)
    old = state["assets"].get(args.name)
    if old and old["state"] == "approved":
        die("%s is already approved. Nothing was changed." % args.name)
    log_path(project, args.key)
    attempt = (old["attempt"] if old else 0) + 1
    name = "%s-%d%s" % (args.name, attempt, ext)
    base = folder(project, args.key)
    via_assets.write_atomic(os.path.join(base, name), data)
    preview, key_note = keyed_preview(base, name, asset)
    history = (old or {}).get("history", [])
    if old:
        history.append({k: old.get(k) for k in ("attempt", "state", "note", "conversation_id", "origin")})
    state["assets"][args.name] = {"kind": asset["kind"], "file": name, "state": "pending", "note": None,
                                  "attempt": attempt, "conversation_id": conv or None, "origin": origin,
                                  "svg_findings": findings, "preview": preview, "key_note": key_note,
                                  "preview_key": asset.get("transparent"),
                                  "history": history}
    how = ", agy conversation %s" % conv if conv else ", imported from %s" % origin if origin else ""
    log(project, args.key, "`%s` attempt %d (%s)%s: pending.%s" % (
        args.name, attempt, asset["kind"], how, " Prompt: %s" % prompt if prompt else ""))
    save(project, args.key, state, spec["rows"])
    return report(project, args.key, state, args.name)


def cmd_approve(project, args):
    if unattended():
        die("an unattended run never approves. Stop at the gallery and report.")
    spec = load(project)
    asset = entry(spec["rows"], args.name)
    state = load_state(project, args.key)
    cur = pending(state, args.name)
    candidate = os.path.join(folder(project, args.key), cur["file"])
    source = os.path.join(project, asset["source"])
    if os.path.islink(source) or not via_assets.inside(os.path.dirname(source), project):
        die("the source %s is a symlink or outside the project. Nothing was changed." % asset["source"])
    log_path(project, args.key)
    saved = snapshot([source])
    try:
        place(candidate, source, asset.get("transparent"))
        plan = via_assets.convert(spec["spec"], project, True, [args.name])  # writes nothing
        failed = [f["issue"] for f in plan["flags"] if f["name"] == args.name
                  and f["issue"].startswith(("not converted", "no VectorDrawable"))]
        if failed:  # convert reports a skipped asset as a flag; approved is final, so stop before writing
            raise via_assets.SpecError("; ".join(failed))
        targets = [os.path.join(project, p) for p in plan["would_write"]]
        if any(os.path.islink(t) for t in targets):
            raise via_assets.SpecError("a platform file is a symlink; replace it with a plain file first")
        saved.update(snapshot(targets))
        result = via_assets.convert(spec["spec"], project, False, [args.name])
    except (via_assets.SpecError, via_agy_call.AgyError, OSError, SystemExit) as e:  # SystemExit: no Pillow env
        restore(saved)
        die("approve failed for %s (%s). The asset stays pending." % (args.name, getattr(e, "code", e)))
    cur["state"] = "approved"
    log(project, args.key, "`%s` attempt %d: approved. Wrote %s and %d platform file(s)."
        % (args.name, cur["attempt"], asset["source"], len(result["written"])))
    save(project, args.key, state, spec["rows"])
    out = report(project, args.key, state, args.name)
    out.update({"source": asset["source"], "written": result["written"], "flags": result["flags"]})
    return out


def cmd_reject(project, args):
    if unattended():
        die("an unattended run never rejects. Stop at the gallery and report.")
    note = checked_text(args.note, "The note", MAX_NOTE)
    state = load_state(project, args.key)
    cur = pending(state, args.name)
    cur["state"], cur["note"] = "rejected", note
    log(project, args.key, "`%s` attempt %d: rejected. Note: %s" % (args.name, cur["attempt"], note))
    save(project, args.key, state)
    return report(project, args.key, state, args.name)


def cmd_status(project, args):
    state = load_state(project, args.key)
    save(project, args.key, state)
    return report(project, args.key, state)


def cmd_shots(project, args):
    state = load_state(project, args.key)
    area = os.path.join(project, via_run.RUN_AREA)
    run_dir = os.path.abspath(os.path.join(project, args.run_dir))
    if any(os.path.islink(p) for p in (os.path.join(project, ".via"), area, run_dir)) or not os.path.isdir(
            run_dir) or not via_assets.inside(run_dir, area) or os.path.realpath(run_dir) == os.path.realpath(area):
        die("%s is not a run folder made by `via_run.py init` in this project." % args.run_dir)
    record_path = os.path.join(run_dir, "findings.json")
    rows = spec_rows(project)
    if os.path.exists(record_path):
        rec = via_run.load_record(record_path)
    else:
        rec = {"version": 1, "mode": "check", "scope": ["assets"], "findings": [], "screenshots": [],
               "platforms": sorted({p for a in rows.values() for p in a["platforms"]})}
    rec.setdefault("screenshots", [])
    if isinstance(rec["scope"], list) and "assets" not in rec["scope"]:
        rec["scope"].append("assets")
    listed = {s["path"] for s in rec["screenshots"]}
    added, capture, copies = [], [], []
    for name, a in sorted(state["assets"].items()):
        if a["state"] != "pending":
            continue
        ext = os.path.splitext(a["file"])[1]
        if ext == ".svg":
            capture.append(name)
            continue
        shot = "asset-%s" % a["file"]
        if shot not in listed:
            copies.append((os.path.join(folder(project, args.key), a["file"]), os.path.join(run_dir, shot)))
            rec["screenshots"].append({"path": shot, "screen": "assets", "theme": "none",
                                       "size": rows.get(name, {}).get("size", "unknown"), "redacted": False})
        added.append(shot)
    problem = via_run.check_record(rec)
    if problem:
        die("the run's findings.json would not be valid (%s). Nothing was changed." % problem)
    for src, dst in copies:
        if os.path.islink(src):
            die("%s is a symlink. Nothing was changed." % via_assets.rel(project, src))
        if not os.path.isfile(src) or os.path.lexists(dst):
            die("%s is missing, or %s is already in the run. Nothing was changed."
                % (via_assets.rel(project, src), os.path.basename(dst)))
    written, what = [], "findings.json"  # only the paths this call created, so a failure undoes them
    try:
        for src, dst in copies:
            what = via_assets.rel(project, src)
            create_new(dst, via_agy_call.read_file(src), written)
        what = "findings.json"
        via_assets.write_atomic(record_path, (json.dumps(rec, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    except BaseException as err:
        left = []
        for path in written:
            try:
                os.unlink(path)
            except OSError:
                left.append(os.path.basename(path))
        if not isinstance(err, (OSError, via_agy_call.AgyError)):
            raise
        die("could not copy %s into the run (%s). %s" % (what, getattr(err, "strerror", None) or err,
            "Remove %s from the run by hand before a rerun." % ", ".join(left) if left else "Nothing was changed."))
    return {"run_dir": run_dir, "shots": added, "capture": capture,
            "gallery": via_assets.rel(project, os.path.join(folder(project, args.key), "gallery.html"))}


def create_new(path, data, written):
    """Write data to path, which must not exist yet (O_EXCL: a file another process made there is never
    overwritten). The path goes into written the moment it exists, so a later failure removes it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_BINARY", 0), 0o666 & ~via_assets.UMASK)
    written.append(path)
    with os.fdopen(fd, "wb") as f:
        f.write(data)

def load(project):
    try:
        spec = via_assets.load_spec(project)
    except via_assets.SpecError as e:
        die(str(e))
    return {"spec": spec, "rows": {a["name"]: a for a in spec["assets"]}, "palette": spec["palette"] or []}


def place(candidate, source, transparent=None):
    """Write the candidate to the asset's source in the source's format: agy draws JPEGs, a spec often names
    a PNG. A different raster format is re-encoded at the same size in the Pillow environment. With a key color
    (the row's transparent), the background is keyed out into the PNG source there, or the approve is refused."""
    have, want = (os.path.splitext(p)[1].lower().replace(".jpeg", ".jpg") for p in (candidate, source))
    if transparent:  # load_spec holds such a row to kind image and a .png source
        error = via_assets.run_resize({"src": candidate, "dst": source, "key": transparent}, "_keyout")
        if error:
            raise via_assets.SpecError(error)
        return
    if have == want:
        via_assets.write_atomic(source, via_agy_call.read_file(candidate))
        return
    if have == ".svg" or want not in (".png", ".webp"):
        raise via_assets.SpecError("the candidate is %s and the source %s is not .png or .webp; set the source "
                                   "to a .png in %s" % (have, os.path.basename(source), via_assets.SPEC))
    size = via_assets.image_size(candidate)
    if size is None:
        raise via_assets.SpecError("the candidate is not a readable image")
    error = via_assets.run_resize([{"src": candidate, "dst": source, "width": size[0], "height": size[1],
                                    "format": want[1:]}])
    if error:
        raise via_assets.SpecError(error)


def snapshot(paths):
    """{path: bytes, or None when absent}, so a failed approve can put every file back."""
    saved = {}
    for path in paths:
        saved[path] = None
        if os.path.isfile(path) and not os.path.islink(path):
            with open(path, "rb") as f:
                saved[path] = f.read()
    return saved


def restore(saved):
    for path, data in saved.items():
        if data is not None:
            via_assets.write_atomic(path, data)
        elif os.path.lexists(path):
            os.unlink(path)


def pending(state, name):
    cur = state["assets"].get(name)
    if not cur:
        die("%s has no candidate yet. Generate it and `add` it first. Nothing was changed." % name)
    if cur["state"] != "pending":
        die("%s is %s, not pending. Nothing was changed." % (name, cur["state"]))
    return cur


def counts(state):
    return {s: sum(1 for a in state["assets"].values() if a["state"] == s) for s in STATES}


def report(project, key, state, name=None):
    out = {"key": key, "gallery": via_assets.rel(project, os.path.join(folder(project, key), "gallery.html")),
           "counts": counts(state), "assets": {n: {k: a[k] for k in ("kind", "state", "note", "attempt")}
                                        for n, a in sorted(state["assets"].items())}}
    if name:
        out["name"] = name
    return out


STYLE = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--text:#1b1d21;--muted:#5b616b;--line:#dfe2e7;
--pending:#8a5a00;--pending-bg:#fff3d6;--approved:#1e6b34;--approved-bg:#dcf3e2;--rejected:#a1262b;
--rejected-bg:#fde2e2;--tile-light:#ffffff;--tile-dark:#16181c;--focus:#2457c5}
@media (prefers-color-scheme:dark){:root{--bg:#111316;--card:#1b1e23;--text:#e9ebef;--muted:#a3a9b3;--line:#2e333a;
--pending:#ffd27a;--pending-bg:#3a2d10;--approved:#8ee0a4;--approved-bg:#15321f;--rejected:#ffaaa8;
--rejected-bg:#3d1a1b;--focus:#8fb0ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1120px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;font-weight:600;margin:0 0 4px}
.summary{color:var(--muted);margin:0 0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(280px,100%),1fr));gap:16px}
.card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;
flex-direction:column}
.tiles{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);border-bottom:1px solid var(--line)}
.tile{height:150px;display:flex;align-items:center;justify-content:center;padding:16px}
.tile.light{background:var(--tile-light)}.tile.dark{background:var(--tile-dark)}
.tile img{display:block;width:100%;height:100%;object-fit:contain}
.body{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px;flex:1}
.name{font:600 14px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-all}
.meta{color:var(--muted);font-size:13px;margin:0}
.badge{align-self:flex-start;font-size:12px;font-weight:600;padding:2px 8px;border-radius:999px}
.badge.pending{color:var(--pending);background:var(--pending-bg)}
.badge.approved{color:var(--approved);background:var(--approved-bg)}
.badge.rejected{color:var(--rejected);background:var(--rejected-bg)}
.note{font-size:13px;margin:0;border-left:3px solid var(--rejected);padding-left:8px}
.actions{display:flex;gap:8px;margin-top:auto;padding-top:8px}
button{font:inherit;font-size:13px;min-height:36px;padding:6px 12px;border-radius:8px;border:1px solid var(--line);
background:transparent;color:var(--text);cursor:pointer}
button:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
button:disabled{opacity:.5;cursor:default}
.bulk{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;margin:0 0 16px}
.pick{display:inline-flex;align-items:center;gap:8px;min-height:36px;font-size:13px;cursor:pointer}
.pick input{width:18px;height:18px;margin:0;accent-color:var(--focus)}
.empty{border:1px dashed var(--line);border-radius:12px;padding:40px 16px;text-align:center;color:var(--muted)}
.hint{color:var(--muted);font-size:13px;margin:16px 0 0}
"""

# Copy buttons only write a phrase to the clipboard: the user pastes it to Claude, who runs approve or reject.
# The bulk bar ticks pending cards and copies one "approve a, b" phrase, in page order.
SCRIPT = """function copy(b,t){if(!b.hasAttribute("data-label"))b.setAttribute("data-label",b.textContent);
function done(){b.textContent="Copied";setTimeout(function(){b.textContent=b.getAttribute("data-label")},1500)}
if(navigator.clipboard){navigator.clipboard.writeText(t).then(done,function(){})}}
function picks(){return Array.prototype.slice.call(document.querySelectorAll("input[data-pick]"))}
function sync(){var p=picks(),on=p.filter(function(i){return i.checked}),all=document.getElementById("pick-all"),
s=document.getElementById("copy-selected");if(!all||!s)return;all.checked=on.length>0&&on.length===p.length;
all.indeterminate=on.length>0&&on.length<p.length;s.disabled=!on.length;
var t=on.length?"Copy approve selected ("+on.length+")":"Copy approve selected";s.textContent=t;s.setAttribute("data-label",t)}
document.addEventListener("change",function(e){if(e.target.id==="pick-all"){var c=e.target.checked;
picks().forEach(function(i){i.checked=c})}if(e.target.id==="pick-all"||e.target.hasAttribute("data-pick"))sync()});
document.addEventListener("click",function(e){var b=e.target.closest("button[data-copy]");if(b){copy(b,b.getAttribute("data-copy"));return}
b=e.target.closest("#copy-selected");if(!b||b.disabled)return;var n=picks().filter(function(i){return i.checked})
.map(function(i){return i.getAttribute("data-pick")});if(n.length)copy(b,"approve "+n.join(", "))});sync();"""


def gallery(state, rows):
    e = html.escape
    cards = []
    for name, a in sorted(state["assets"].items()):
        row = rows.get(name, {})
        key = row.get("transparent")
        # The preview and its note hold only for the key color they were made with. A row that dropped
        # transparent, or changed its color since, shows the plain candidate.
        keyed = bool(key) and a.get("preview_key") == key
        stale = bool(key) and not keyed
        img = '<img src="%s" alt="%s candidate %d">' % (e(keyed and a.get("preview") or a["file"]), e(name),
                                                         a["attempt"])
        meta = "%s · %s · %s" % (a["kind"], ", ".join(row.get("platforms", [])) or "no platforms",
                                 row.get("size", "no size"))
        parts = ['<article class="card">',
                 '<div class="tiles"><div class="tile light">%s</div><div class="tile dark">%s</div></div>' % (img, img),
                 '<div class="body"><span class="name">%s</span>' % e(name),
                 '<p class="meta">%s</p>' % e(meta),
                 '<p class="meta">%s</p>' % e(row.get("usage", "")) if row.get("usage") else "",
                 '<p class="meta">Attempt %d%s</p>' % (a["attempt"], " · %d SVG finding(s)" % a["svg_findings"]
                                                        if a.get("svg_findings") else ""),
                 '<p class="meta origin">From: %s</p>' % e(a["origin"]) if a.get("origin") else "",
                 '<p class="meta">Background %s keyed out: approve writes this</p>' % e(row.get("transparent", ""))
                 if keyed and a.get("preview") else "",
                 '<p class="note">%s</p>' % e(a["key_note"]) if keyed and a.get("key_note") else "",
                 '<p class="note">No cut-out preview for background %s yet: add or import this candidate again '
                 'to see it. Approve keys it out with this color.</p>' % e(key) if stale else "",
                 '<span class="badge %s">%s</span>' % (a["state"], a["state"].capitalize())]
        if a["state"] == "rejected" and a.get("note"):
            parts.append('<p class="note">%s</p>' % e(a["note"]))
        if a["state"] == "pending":
            parts.append('<label class="pick"><input type="checkbox" data-pick="%s"> Select</label>'
                         '<div class="actions"><button type="button" data-copy="%s">Copy approve</button>'
                         '<button type="button" data-copy="%s">Copy reject</button></div>'
                         % (e(name), e("approve %s" % name), e("reject %s: " % name)))
        parts.append("</div></article>")
        cards.append("".join(parts))
    n = counts(state)
    summary = "%d pending, %d approved, %d rejected" % (n["pending"], n["approved"], n["rejected"])
    bulk = ('<div class="bulk"><label class="pick"><input type="checkbox" id="pick-all"> Select all pending</label>'
            '<button type="button" id="copy-selected" disabled>Copy approve selected</button></div>'
            if n["pending"] else "")
    body = (bulk + '<div class="grid">%s</div>' % "".join(cards) if cards else
            '<div class="empty">No assets yet. Run <code>/via assets %s</code> to generate them.</div>' % e(state["key"]))
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"%s\">"
            "<title>Assets %s</title><style>%s</style></head><body><main>"
            "<h1>Assets for %s</h1><p class=\"summary\">%s</p>%s"
            "<p class=\"hint\">Tell Claude what to do: copy a phrase (tick several to approve them in one), or write "
            "your own. Rejecting needs a note.</p>"
            "</main><script>%s</script></body></html>\n"
            % (CSP, e(state["key"]), STYLE, e(state["key"]), e(summary), body, SCRIPT))


def served_file(base, path):
    """The file a request path names in the review folder, or None: one plain file name (no folder, no dot
    file), gallery.html or a candidate image, a regular file and not a symlink."""
    name = path.split("?", 1)[0].split("#", 1)[0].lstrip("/") or "gallery.html"
    ext = os.path.splitext(name)[1].lower()
    if "/" in name or "\\" in name or "%" in name or name.startswith(".") or ext not in SERVED:
        return None
    if name != "gallery.html" and not (CANDIDATE_RE.fullmatch(name) or PREVIEW_RE.fullmatch(name)):
        return None  # only the page and the candidates and previews add/import write
    full = os.path.join(base, name)
    if os.path.islink(full) or not os.path.isfile(full):
        return None
    return full


def cmd_serve(project, args):
    base = folder(project, args.key)
    if not os.path.isfile(os.path.join(base, "gallery.html")):
        die("there is no gallery for %s yet: add or import a candidate first. Nothing was started." % args.key)
    if not 0 < args.minutes <= 24 * 60:
        die("--minutes must be 1 to 1440. Nothing was started.")
    if not (args.port == 0 or 1024 <= args.port <= 65535):
        die("--port must be 1024 to 65535, or 0 to pick a free one. Nothing was started.")

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 10  # an idle socket (a browser's preconnect) is dropped, never held open

        def do_GET(self, head=False):
            # Only a request for 127.0.0.1 itself, so a DNS-rebinding page cannot read the folder (as via_capture).
            ok = self.headers.get("Host") == "127.0.0.1:%d" % self.server.server_address[1]
            full = ok and served_file(base, self.path)
            if not full:
                self.send_error(404)
                return
            try:  # O_NOFOLLOW: a symlink swapped in after the check is still not followed; O_NONBLOCK: nor a FIFO
                fd = os.open(full, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            except OSError:
                self.send_error(404)
                return
            with os.fdopen(fd, "rb") as f:
                if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                    self.send_error(404)
                    return
                data = f.read()
            ext = os.path.splitext(full)[1].lower()
            self.send_response(200)
            self.send_header("Content-Type", SERVED[ext])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", CSP if ext == ".html" else IMAGE_CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def do_HEAD(self):
            self.do_GET(head=True)

        def log_message(self, *a):
            pass  # stdout carries the one JSON line; stderr stays quiet

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as err:
        die("could not listen on 127.0.0.1:%d (%s). Nothing was started." % (args.port, err.strerror or err))
    url = "http://127.0.0.1:%d/gallery.html" % server.server_address[1]
    print(json.dumps({"url": url, "minutes": args.minutes, "pid": os.getpid()}), flush=True)
    server.daemon_threads = True
    timer = threading.Timer(args.minutes * 60, server.shutdown)  # stops even while a request is open
    timer.daemon = True
    timer.start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        timer.cancel()
        server.server_close()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=".")
    common.add_argument("--key", required=True)
    a = sub.add_parser("add", parents=[common])
    a.add_argument("--name", required=True)
    a.add_argument("--file", required=True)
    a.add_argument("--conversation-id")
    a.add_argument("--prompt-file")
    for cmd in ("approve", "reject"):
        p = sub.add_parser(cmd, parents=[common])
        p.add_argument("--name", required=True)
        if cmd == "reject":
            p.add_argument("--note", required=True)
    i = sub.add_parser("import", parents=[common])
    i.add_argument("--name", required=True)
    i.add_argument("--file", required=True)
    i.add_argument("--origin", required=True)
    sub.add_parser("status", parents=[common])
    s = sub.add_parser("shots", parents=[common])
    s.add_argument("--run-dir", required=True)
    v = sub.add_parser("serve", parents=[common])
    v.add_argument("--port", type=int, default=0, help="0 picks a free port")
    v.add_argument("--minutes", type=int, default=60, help="stop after this long")
    args = ap.parse_args()
    if not via_assets.KEY_RE.match(args.key):
        die("--key %r is not a ticket key such as APP-7. Nothing was changed." % args.key)
    project = os.path.realpath(args.project)
    if args.cmd == "serve":
        cmd_serve(project, args)
        return
    via_assets.emit({"add": cmd_add, "import": cmd_import, "approve": cmd_approve, "reject": cmd_reject, "status": cmd_status,
                     "shots": cmd_shots}[args.cmd](project, args))


if __name__ == "__main__":
    main()
