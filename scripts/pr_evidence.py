#!/usr/bin/env python3
"""pr-evidence: put screenshots and recordings in a pull request, with no human.

Usage: pr_evidence.py publish KEY FOLDER [--project DIR] [--repo OWNER/NAME]
       pr_evidence.py sweep [--repo OWNER/NAME] [--today YYYY-MM-DD]
       pr_evidence.py migrate PR [--sha COMMIT] [--repo OWNER/NAME] [--write]

`gh` cannot attach images to a PR body, so the files go to a prerelease per month, `evidence-YYYY-MM` (UTC, made on
first use), and publish prints the markdown table to paste in the PR body. Each asset is named KEY-<run>-<shot>.<ext>,
so parallel sessions never collide, and a re-run with the same names replaces them (`--clobber`).

What may leave the machine:
- Public (or internal) repo: only shots that Via's run record marks as kept (`via_run.py outbound-shots`) and approved
  for the public (`public: true`), from a run folder made by `via_run.py init` in the project.
- Private repo: a Via run folder's kept shots with no public approval, or every media file under a plain folder in the
  project; only people who can see the repo see them.
- Only .png, .jpg, .jpeg, .webp, .gif and .mp4 files, with a shot name of lowercase letters, digits and hyphens.
- Metadata is removed first (PNG text and EXIF chunks, JPEG EXIF, XMP and comments, WebP EXIF and XMP,
  GIF comments, MP4 user data). A file whose name or remaining bytes still hold this machine's user name,
  home folder, host name or an emulator serial is refused.
Any refusal stops a publish run before the first upload; migrate keeps the old link for a refused file.

sweep deletes each evidence-YYYY-MM release (and its tag) whose month ended more than 90 days ago, unless an open PR
body links to it, and warns when a release passes 800 assets (GitHub stops at 1000). after-merge runs it.
migrate (private repos) rebuilds the evidence a PR body links in a shots branch or commit: it fetches each file at the
commit, strips it, uploads it to this month's release and repoints the body. Without --write it only prints the plan.

Exit 0 when every file uploaded, 1 invalid input (nothing uploaded), 3 `gh` is missing or its token cannot
write releases on the repo, 4 an upload, delete or body edit failed partway, 5 migrate left some links
unmigrated (listed on stderr; keep their screenshots branch).
"""
import argparse
import datetime
import getpass
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import urllib.parse

NOTES = ("Screenshots and recordings for pull requests opened this month, named <KEY>-<run>-<shot>. "
         "`pr_evidence.py sweep` deletes it once the month ended more than 90 days ago and no open PR links here.")
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED = IMAGES | {".mp4"}
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHOT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
VIA_RUN = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "plugins", "via", "scripts", "via_run.py")
SCOPE_HINT = ("gh's token cannot write releases on %s. A classic token needs the `repo` scope (`public_repo` for a "
              "public repo): run `gh auth refresh -s repo`. A fine-grained token needs Contents: read and write.")
AUTH_WORDS = ("HTTP 401", "HTTP 403", "HTTP 404", "Resource not accessible", "must have push access",
              "requires authentication", "gh auth login", "scope")


class Refused(Exception):
    pass


def die(message, code=1):
    sys.stderr.write("pr_evidence: %s\n" % message)
    sys.exit(code)


PNG_SIG = b"\x89PNG\r\n\x1a\n"
# An allow-list: any other chunk (text, EXIF, time, a vendor's private chunk) is dropped.
PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"bKGD",
            b"pHYs", b"acTL", b"fcTL", b"fdAT"}


def strip_png(data):
    if not data.startswith(PNG_SIG):
        raise Refused("is not a PNG file")
    out, i = [PNG_SIG], len(PNG_SIG)
    while i < len(data):
        if i + 12 > len(data):
            raise Refused("is a damaged PNG file")
        length, kind = struct.unpack(">I4s", data[i:i + 8])
        end = i + 12 + length
        if end > len(data):
            raise Refused("is a damaged PNG file")
        if kind in PNG_KEEP:
            out.append(data[i:end])
        i = end
        if kind == b"IEND":
            return b"".join(out)
    raise Refused("is a damaged PNG file (no IEND)")


def strip_jpeg(data):
    """Keep the image segments, APP0 (JFIF, without its thumbnail) and APP14 when it is Adobe's color transform.
    Every other APPn (EXIF, XMP, ICC profiles, MPF, vendor data), comments and bytes after the end marker are dropped."""
    if not data.startswith(b"\xff\xd8"):
        raise Refused("is not a JPEG file")
    out, i = [b"\xff\xd8"], 2
    while True:
        while i + 1 < len(data) and data[i] == 0xFF and data[i + 1] == 0xFF:  # fill bytes before a marker
            i += 1
        if i + 2 > len(data) or data[i] != 0xFF:
            raise Refused("is a damaged JPEG file")
        marker = data[i + 1]
        if marker == 0xD9:  # EOI: anything after it is dropped
            out.append(b"\xff\xd9")
            return b"".join(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:  # standalone markers carry no length
            out.append(data[i:i + 2])
            i += 2
            continue
        if i + 4 > len(data):
            raise Refused("is a damaged JPEG file")
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        end = i + 2 + length
        if length < 2 or end > len(data):
            raise Refused("is a damaged JPEG file")
        if marker == 0xE0:  # JFIF only, with its embedded thumbnail dropped (it may show the uncropped frame)
            if data[i + 4:i + 9] == b"JFIF\x00" and length >= 16:
                out.append(b"\xff\xe0\x00\x10" + data[i + 4:i + 16] + b"\x00\x00")
        elif not (0xE0 <= marker <= 0xEF or marker == 0xFE) \
                or (marker == 0xEE and data[i + 4:i + 9] == b"Adobe"):  # APP2 (ICC, MPF, FPXR) goes: profiles name displays
            out.append(data[i:end])
        i = end
        if marker == 0xDA:  # entropy-coded data runs to the next marker that is not a stuffed 0xFF00 or an RSTn
            j = i
            while True:
                j = data.find(b"\xff", j)
                if j < 0 or j + 1 >= len(data):
                    raise Refused("is a damaged JPEG file")
                if data[j + 1] == 0x00 or 0xD0 <= data[j + 1] <= 0xD7:
                    j += 2
                    continue
                break
            out.append(data[i:j])
            i = j


def strip_webp(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise Refused("is not a WebP file")
    out, i = [], 12
    while i < len(data):
        if i + 8 > len(data):
            raise Refused("is a damaged WebP file")
        kind, length = data[i:i + 4], struct.unpack("<I", data[i + 4:i + 8])[0]
        if i + 8 + length > len(data):
            raise Refused("is a damaged WebP file")
        end = min(i + 8 + length + (length & 1), len(data))  # odd chunks carry one pad byte
        if kind == b"VP8X" and length < 1:
            raise Refused("is a damaged WebP file")
        if kind == b"VP8X":  # clear the EXIF (0x08) and XMP (0x04) flags, since those chunks go
            chunk = bytearray(data[i:end])
            chunk[8] &= 0xD3  # no ICC, EXIF or XMP
            out.append(bytes(chunk))
        elif kind in (b"VP8 ", b"VP8L", b"ALPH", b"ANIM", b"ANMF"):  # all else (EXIF, XMP, unknown) goes
            out.append(data[i:end])
        i = end
    body = b"".join(out)
    return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body


def gif_sub_blocks(data, i):
    """The index just past a run of GIF data sub-blocks starting at i."""
    while True:
        if i >= len(data):
            raise Refused("is a damaged GIF file")
        size = data[i]
        i += 1 + size
        if size == 0:
            return i


def color_table_len(flags):
    return 3 * 2 ** ((flags & 7) + 1) if flags & 0x80 else 0


def strip_gif(data):
    """Keep graphic control, plain text and loop-count (NETSCAPE2.0, ANIMEXTS1.0) extensions; drop every other one."""
    if data[:6] not in (b"GIF87a", b"GIF89a") or len(data) < 13:
        raise Refused("is not a GIF file")
    flags = data[10]
    i = 13 + color_table_len(flags)
    if i > len(data):
        raise Refused("is a damaged GIF file")
    out, images, ended = [data[:i]], 0, False
    while i < len(data):
        b = data[i]
        if b == 0x3B:
            out.append(b"\x3b")
            ended = True
            break
        if b == 0x21:
            if i + 2 > len(data):
                raise Refused("is a damaged GIF file")
            label = data[i + 1]
            end = gif_sub_blocks(data, i + 2)
            if label in (0xF9, 0x01) or (label == 0xFF and data[i + 3:i + 14] in (b"NETSCAPE2.0", b"ANIMEXTS1.0")):
                out.append(data[i:end])
            i = end
        elif b == 0x2C:
            if i + 10 > len(data):
                raise Refused("is a damaged GIF file")
            lflags = data[i + 9]
            j = i + 10 + color_table_len(lflags)
            end = gif_sub_blocks(data, j + 1)  # one byte of LZW minimum code size, then the data
            out.append(data[i:end])
            images += 1
            i = end
        else:
            raise Refused("is a damaged GIF file")
    if not (ended and images):
        raise Refused("is a damaged GIF file")
    return b"".join(out)


# An allow-list per container: any other box (udta, meta, uuid, XMP_, free space that may hold old metadata, a vendor's
# box) becomes a zero-filled `free` box of the same size. Containers are walked; the rest are kept as they are.
MP4_TREE = {
    None: {b"ftyp", b"styp", b"moov", b"mdat", b"moof", b"mfra", b"sidx"},
    b"moov": {b"mvhd", b"trak", b"mvex", b"iods"},
    b"trak": {b"tkhd", b"edts", b"mdia", b"tref"},
    b"edts": {b"elst"},
    b"mdia": {b"mdhd", b"hdlr", b"minf"},
    b"minf": {b"vmhd", b"smhd", b"nmhd", b"dinf", b"stbl"},
    b"dinf": {b"dref"},
    b"stbl": {b"stsd", b"stts", b"stss", b"ctts", b"stsc", b"stsz", b"stz2", b"stco", b"co64", b"sdtp", b"sgpd",
              b"sbgp", b"cslg"},
    b"mvex": {b"mehd", b"trex"},
    b"moof": {b"mfhd", b"traf"},
    b"traf": {b"tfhd", b"tfdt", b"trun", b"sbgp", b"sgpd", b"saiz", b"saio"},
    b"mfra": {b"tfra", b"mfro"},
}
MP4_TIMED = {b"mvhd", b"tkhd", b"mdhd"}  # their creation and modification times are zeroed


def strip_mp4(data):
    """Blank every box outside MP4_TREE, the recording times and the handler names, in place.
    Nothing moves, so the sample offsets in stco and co64 stay right."""
    buf = bytearray(data)

    def walk(start, stop, parent):
        i = start
        while i < stop:
            if i + 8 > stop:
                raise Refused("is a damaged MP4 file")
            size, kind = struct.unpack(">I4s", bytes(buf[i:i + 8]))
            head = 8
            if size == 1:
                if i + 16 > stop:
                    raise Refused("is a damaged MP4 file")
                size, head = struct.unpack(">Q", bytes(buf[i + 8:i + 16]))[0], 16
            elif size == 0:
                size = stop - i
            if size < head or i + size > stop:
                raise Refused("is a damaged MP4 file")
            body = i + head
            if kind not in MP4_TREE[parent]:
                buf[i + 4:i + 8] = b"free"
                buf[body:i + size] = bytes(size - head)
            elif kind in MP4_TREE:
                walk(body, i + size, kind)
            elif kind in MP4_TIMED:
                if body + 12 > i + size:
                    raise Refused("is a damaged MP4 file")
                times = 16 if buf[body] == 1 else 8  # version 1 has 64-bit times
                if body + 4 + times > i + size:
                    raise Refused("is a damaged MP4 file")
                buf[body + 4:body + 4 + times] = bytes(times)
            elif kind == b"hdlr":
                if i + size < body + 24:
                    raise Refused("is a damaged MP4 file")
                if bytes(buf[body + 8:body + 12]) not in (b"vide", b"soun"):
                    raise Refused("has a track that is not video or sound (timed metadata may hold places or dates)")
                buf[body + 24:i + size] = bytes(i + size - body - 24)  # the handler's name
            i += size

    walk(0, len(buf), None)
    if b"ftyp" not in bytes(buf[4:8]):
        raise Refused("is not an MP4 file")
    return bytes(buf)


STRIP = {".png": strip_png, ".jpg": strip_jpeg, ".jpeg": strip_jpeg, ".webp": strip_webp, ".gif": strip_gif,
         ".mp4": strip_mp4}


def identifiers():
    """Strings that would tie a file to this machine or its owner."""
    found = set()
    try:
        found.add(getpass.getuser())
    except Exception:  # no user name in the environment or the password database
        pass
    home = os.path.expanduser("~")
    found.add(home)
    found.add(os.path.basename(home))
    host = socket.gethostname()
    found.update({host, host.split(".")[0]})
    words = sorted(s for s in found if len(s) >= 3 and s not in ("root", "user", "runner", "/"))
    short, long = [w for w in words if len(w) < 5], [w for w in words if len(w) >= 5]
    return (pattern(short, bounded=True), pattern(long)), pattern(long)


def pattern(words, bounded=False):
    """One case-insensitive search for any of the words. Names are checked for every word; a file's bytes only for
    words of 5 or more characters, since a 3-letter word turns up by chance in a few MB of compressed data."""
    if not words:
        return re.compile(rb"(?!)")  # never matches: an empty alternation would match every file
    alternation = b"|".join(re.escape(w.encode("utf-8")) for w in words)
    if bounded:  # a short word counts in a name only as a whole hyphen-separated part: "max" is not in "maximum"
        return re.compile(rb"(?:^|[-.])(?:" + alternation + rb")(?=[-.]|$)", re.I)
    return re.compile(alternation, re.I)


DEVICE_RE = re.compile(rb"emulator-\d{4}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|/Users/|/home/|"
                       rb"[a-z]:\\Users\\", re.I)


def leak(blob, words):
    if any(p.search(blob) for p in (words if isinstance(words, tuple) else (words,))):
        return "this machine's user, home or host name"
    m = DEVICE_RE.search(blob)
    return ("a path or device id (%r)" % m.group(0).decode("latin-1")) if m else None


def prepare(prefix, path, name_words, byte_words, stem=None):
    """The upload name and the stripped bytes of one kept shot. Raises Refused."""
    own_stem, ext = os.path.splitext(os.path.basename(path))
    stem = stem or own_stem
    ext = ext.lower()
    if ext not in ALLOWED:
        raise Refused("is not one of %s" % ", ".join(sorted(ALLOWED)))
    if not SHOT_RE.match(stem):
        raise Refused("has a name other than lowercase letters, digits and hyphens")
    name = "%s-%s%s" % (prefix, stem, ext)
    why = leak(name.split("-", 2)[2].encode("utf-8"), name_words)  # the ticket key is not this machine's
    if why:
        raise Refused("has %s in its name" % why)
    with open(path, "rb") as f:
        data = STRIP[ext](f.read())
    why = leak(data, byte_words)
    if why:
        raise Refused("still holds %s after its metadata was removed" % why)
    return name, data


def gh(args, repo):
    return subprocess.run(["gh"] + args + ["--repo", repo], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def auth_problem(r):
    return any(w in (r.stderr or "") for w in AUTH_WORDS)


def gh_die(r, what, repo):
    die(SCOPE_HINT % repo if auth_problem(r) else "%s: %s" % (what, (r.stderr or "").strip()), 3)


def utc_today():
    return datetime.datetime.now(datetime.timezone.utc).date()


def month_tag(today=None):
    """The release tag of today's month, evidence-YYYY-MM, by the UTC date."""
    return (today or utc_today()).strftime("evidence-%Y-%m")


def ensure_release(repo, tag):
    r = gh(["release", "view", tag, "--json", "tagName"], repo)
    if r.returncode == 0:
        return
    if "release not found" not in (r.stderr or "").lower():
        gh_die(r, "gh release view failed", repo)
    title = "PR evidence %s" % tag.removeprefix("evidence-")
    r = gh(["release", "create", tag, "--prerelease", "--title", title, "--notes", NOTES], repo)
    # Another run may have made it in the meantime; that is fine.
    if r.returncode != 0 and gh(["release", "view", tag, "--json", "tagName"], repo).returncode != 0:
        gh_die(r, "could not create the evidence release %s" % tag, repo)


def kept_shots(project, run_dir):
    r = subprocess.run([sys.executable, "-I", "-B", VIA_RUN, "outbound-shots", run_dir, "--project", project],
                       capture_output=True, text=True)
    if r.returncode != 0:
        die("%s Only a Via run folder's kept shots can be published." % (r.stderr.strip() or "via_run.py failed."))
    return json.loads(r.stdout)["kept"]


def visibility(repo):
    """PUBLIC, PRIVATE or INTERNAL. Only PRIVATE relaxes the rules: INTERNAL is seen by a whole enterprise."""
    r = subprocess.run(["gh", "repo", "view", repo, "--json", "visibility", "-q", ".visibility"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        gh_die(r, "could not read the visibility of %s" % repo, repo)
    return r.stdout.strip().upper()


def shot_name(rel):
    """A shot name from a path inside a plain folder: after/Home Dark.png -> after-home-dark."""
    return re.sub(r"[^a-z0-9-]+", "-", os.path.splitext(rel)[0].lower()).strip("-")


def in_via(project, folder):
    """True when folder is <project>/.via or anything under it. samefile, so a case variant (.VIA) on a
    case-insensitive disk counts too."""
    via = os.path.join(project, ".via")
    if not os.path.isdir(via):
        return False
    path = os.path.realpath(folder)
    while True:
        if os.path.exists(path) and os.path.samefile(path, via):
            return True
        parent = os.path.dirname(path)
        if parent == path:
            return False
        path = parent


def folder_shots(project, folder):
    """Every media file under a plain folder in the project, as {path, stem}, and the files skipped.
    For a private repo only: its files need no Via review, since only people who can see the repo see them."""
    root, top = os.path.realpath(folder), os.path.realpath(project)
    try:
        inside = os.path.isdir(root) and os.path.commonpath([root, top]) == top
    except ValueError:  # different drives on Windows
        inside = False
    if not inside:
        die("%s is not a folder inside the project %s." % (folder, project))
    shots, skipped = [], []
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            path = os.path.join(here, name)
            rel = os.path.relpath(path, root)
            if os.path.islink(path):
                die("%s is a symlink; only real files are published." % rel)
            if name.startswith(".") or os.path.splitext(name)[1].lower() not in ALLOWED:
                skipped.append(rel)
            else:
                shots.append({"path": path, "stem": shot_name(rel)})
    return shots, skipped


# Native shots have no mask (redacted: false). Only these sources are safe to publish: an emulator shot in demo
# mode cropped to the app (NFX-91), and a preview rendered from code with no device or account behind it.
def unsafe(shot):
    # The web mask blurs fields and tagged elements, not page text, so every shot also needs its own public approval.
    if shot.get("public") is not True:
        return "has no public approval (set public: true only after checking it shows nothing personal to anyone)"
    # A device shot is safe only from an emulator in demo mode; a real phone shows a real account.
    if (shot.get("redacted") is True or shot.get("source") == "preview"
            or (shot.get("source") == "device" and shot.get("emulator") is True)):
        return None
    return "is not masked (redacted: false) and is not an emulator demo-mode or preview shot"


def asset_url(repo, tag, name):
    return "https://github.com/%s/releases/download/%s/%s" % (repo, tag, urllib.parse.quote(name))


def table(repo, tag, names):
    rows = ["| Shot | Evidence |", "|---|---|"]
    for name in names:
        url = asset_url(repo, tag, name)
        stem, ext = os.path.splitext(name)
        cell = ("![%s](%s)" if ext in IMAGES else "[%s](%s)") % (stem, url)
        rows.append("| `%s` | %s |" % (stem, cell))
    return "\n".join(rows)


def resolve_repo(repo, project):
    if shutil.which("gh") is None:
        die("gh is not installed or not on PATH.", 3)
    if repo is None:
        r = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                           capture_output=True, text=True, cwd=project)
        repo = r.stdout.strip()
        if r.returncode != 0 or not repo:
            die("could not tell the GitHub repo of %s; pass --repo OWNER/NAME." % project, 3)
    if not REPO_RE.match(repo):
        die("%s is not OWNER/NAME." % repo)
    return repo


def gh_json(args, repo, what):
    r = gh(args, repo)
    if r.returncode != 0:
        gh_die(r, what, repo)
    try:
        return json.loads(r.stdout)
    except ValueError:
        die("%s: gh printed something that is not JSON." % what, 3)


def cmd_publish(args):
    if not KEY_RE.match(args.key):
        die("%s is not a ticket key like NFX-87." % args.key)
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        die("%s is not a folder." % project)
    repo = resolve_repo(args.repo, project)
    private = visibility(repo) == "PRIVATE"
    via_run = (any(os.path.exists(os.path.join(args.run_dir, f)) for f in ("findings.json", "spec.json"))
               or in_via(project, args.run_dir))
    plain = private and not via_run
    if plain:
        shots, skipped = folder_shots(project, args.run_dir)
        if skipped:
            sys.stderr.write("pr_evidence: skipped (not media): %s\n" % ", ".join(skipped))
    else:
        shots = kept_shots(project, args.run_dir)
    if not shots:
        die("%s has no shot to publish (no media file, or none reviewed as kept). Nothing was uploaded."
            % args.run_dir)
    words = identifiers()
    staged, names, problems = tempfile.mkdtemp(prefix="pr-evidence-"), [], []
    try:
        run_id = os.path.basename(os.path.realpath(args.run_dir)).lower()  # before and after runs reuse shot names
        if not SHOT_RE.match(run_id):
            die("the run folder's name %r is not lowercase letters, digits and hyphens." % run_id)
        if plain:  # a Via run folder's name is a timestamp; a plain folder's (shots/NFX-87) repeats across PRs
            run_id += datetime.datetime.now(datetime.timezone.utc).strftime("-%Y%m%dt%H%M%Sz")
        for shot in shots:
            try:
                # A private repo's shots are seen only by people who can see the repo: no public approval needed.
                why = None if private else unsafe(shot)
                if why:
                    raise Refused(why)
                name, data = prepare("%s-%s" % (args.key, run_id), shot["path"], *words, stem=shot.get("stem"))
            except Refused as e:
                problems.append("%s %s" % (os.path.basename(shot["path"]), e))
                continue
            if name in names:
                problems.append("%s is named twice" % name)
                continue
            with open(os.path.join(staged, name), "wb") as f:
                f.write(data)
            names.append(name)
        if problems:
            die("refused, nothing was uploaded:\n  " + "\n  ".join(problems))
        tag = month_tag()
        ensure_release(repo, tag)
        done, failed = [], []
        for name in names:
            r = gh(["release", "upload", tag, os.path.join(staged, name), "--clobber"], repo)
            if r.returncode == 0:
                done.append(name)
            elif auth_problem(r) and not done:
                die(SCOPE_HINT % repo, 3)
            else:
                failed.append("%s: %s" % (name, (r.stderr or "").strip()))
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    if done:
        print(table(repo, tag, done))
    if failed:
        die("%d of %d uploads failed; the table lists only the ones that uploaded. --clobber deletes the old asset "
            "first, so an earlier file with a failed name may be gone: run again to restore it.\n  %s"
            % (len(failed), len(names), "\n  ".join(failed)), 4)
    return 0


MONTH_TAG_RE = re.compile(r"^evidence-(\d{4})-(0[1-9]|1[0-2])$")
KEEP_DAYS = 90
WARN_ASSETS = 800  # GitHub allows 1000 assets per release
LIST_LIMIT = 1000


def month_end(tag):
    m = MONTH_TAG_RE.match(tag)
    year, month = int(m.group(1)), int(m.group(2))
    first_of_next = datetime.date(year + month // 12, month % 12 + 1, 1)
    return first_of_next - datetime.timedelta(days=1)


def cmd_sweep(args):
    """Delete each evidence-YYYY-MM release whose month ended more than 90 days ago, unless an open PR links to it."""
    try:
        today = datetime.date.fromisoformat(args.today) if args.today else utc_today()
    except ValueError:
        die("--today %r is not a date like 2026-11-20." % args.today)
    repo = resolve_repo(args.repo, os.getcwd())
    listed = gh_json(["release", "list", "--limit", str(LIST_LIMIT), "--json", "tagName"],
                     repo, "gh release list failed")
    if len(listed) >= LIST_LIMIT:
        die("gh listed %d releases, so some may be missing; nothing was deleted." % len(listed), 3)
    tags = sorted(r["tagName"] for r in listed if MONTH_TAG_RE.match(r["tagName"]))
    old = [t for t in tags if (today - month_end(t)).days > KEEP_DAYS]
    failed = []
    prs = []
    if old:
        # A PR merged within the last 90 days counts too: after-merge sweeps right after its own merge, and a PR
        # that stayed open long past its evidence month would otherwise lose its images the moment it merges.
        since = (today - datetime.timedelta(days=KEEP_DAYS)).isoformat()
        for state, extra, what in (("open", [], "open PR %s"), ("merged", ["--search", "merged:>=" + since],
                                                              "PR %s (merged since " + since + ")")):
            found = gh_json(["pr", "list", "--state", state, *extra, "--limit", str(LIST_LIMIT),
                             "--json", "number,body"], repo, "could not list the %s PRs, so nothing was deleted" % state)
            if len(found) >= LIST_LIMIT:
                die("gh listed %d %s PRs, so some may be missing; nothing was deleted." % (len(found), state), 3)
            prs += [(what, p) for p in found]
    for tag in old:
        # A file link (releases/download/<tag>/...) or the folder link migrate writes (releases/tag/<tag>).
        link = re.compile(r"github\.com/%s/releases/(?:download/%s/|tag/%s(?![\w.-]))"
                          % (re.escape(repo), re.escape(tag), re.escape(tag)), re.I)
        linked = [what % ("#%d" % p["number"]) for what, p in prs if link.search(p.get("body") or "")]
        if linked:
            print("kept %s: %s links to it" % (tag, ", ".join(linked)))
            continue
        r = gh(["release", "delete", tag, "--cleanup-tag", "--yes"], repo)
        if r.returncode == 0:
            print("deleted %s" % tag)
        elif "release not found" in (r.stderr or "").lower():  # another after-merge swept it first
            print("deleted %s (already gone)" % tag)
        else:
            failed.append("%s: %s" % (tag, (r.stderr or "").strip()))
    current = month_tag(today)
    if current in tags:  # only this month's release still grows
        count = len(gh_json(["release", "view", current, "--json", "assets"], repo, "gh release view failed")["assets"])
        if count > WARN_ASSETS:
            sys.stderr.write("pr_evidence: warning: %s has %d assets; GitHub stops at 1000. The next step is "
                             "a release per half month (NFX-236).\n" % (current, count))
    if failed:
        die("could not delete:\n  " + "\n  ".join(failed), 4)
    return 0


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def legacy_links(repo):
    """Links into a screenshots branch or commit: blob, raw and tree URLs and raw.githubusercontent.com, under shots/<KEY>/.
    A branch name may hold slashes, so the ref ends at the last /shots/<KEY> in the URL."""
    own = r"(?i:%s)" % re.escape(repo)
    return re.compile(r"https://(?:github\.com/%s/(?P<kind>blob|raw|tree)|raw\.githubusercontent\.com/%s)/"
                      r"(?P<ref>[^\s()\[\]<>\"']+?)/(?P<path>shots/(?P<key>[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6})"
                      r"(?:/[^\s()\[\]<>\"'?#]*)?)(?:\?raw=true)?" % (own, own))


def gh_api_bytes(endpoint, accept="application/vnd.github.raw"):
    return subprocess.run(["gh", "api", "-H", "Accept: " + accept, endpoint], capture_output=True)


def cmd_migrate(args):
    """Rebuild the evidence a PR body links in a shots branch or commit: fetch each file at a commit, strip it,
    upload it to this month's release, and point the body at it. Private repos only."""
    repo = resolve_repo(args.repo, os.getcwd())
    if visibility(repo) != "PRIVATE":
        die("migrate works only on a private repo; on a public repo publish the shots from a Via run instead.")
    if args.sha and not SHA_RE.match(args.sha):
        die("--sha %r is not a full 40-character commit hash." % args.sha)
    body = gh_json(["pr", "view", str(args.pr), "--json", "body"], repo, "gh pr view failed").get("body") or ""
    pattern = legacy_links(repo)
    links = list(pattern.finditer(body))
    if not links:
        print("PR #%d links to no shots/ folder in %s; nothing to do." % (args.pr, repo))
        return 0
    if not args.sha and any(not SHA_RE.match(m.group("ref")) for m in links):
        die("PR #%d links to a branch; pass --sha with that branch's last commit (a deleted branch's head is in "
            "`gh api repos/%s/activity?activity_type=branch_deletion`, field `before`)." % (args.pr, repo))
    words, tag = identifiers(), month_tag()
    staged = tempfile.mkdtemp(prefix="pr-evidence-")
    result, staged_names, problems, trees, cache = {}, {}, [], {}, {}

    def rebuild(commit, path, key):
        """Fetch, strip and stage one file once; its asset name, or None after noting why not."""
        if (commit, path) in cache:
            return cache[commit, path]
        cache[commit, path] = None
        ext = os.path.splitext(path)[1].lower()
        if ext not in ALLOWED:
            problems.append("%s: not one of %s" % (path, ", ".join(sorted(ALLOWED))))
            return None
        endpoint = "repos/%s/contents/%s?ref=%s" % (repo, urllib.parse.quote(urllib.parse.unquote(path)), commit)
        r = gh_api_bytes(endpoint)
        if r.returncode != 0:
            problems.append("%s: not in %s (%s)" % (path, commit[:7], r.stderr.decode("utf-8", "replace").strip()))
            return None
        raw = os.path.join(staged, "raw" + ext)
        with open(raw, "wb") as f:
            f.write(r.stdout)
        stem = shot_name(urllib.parse.unquote(path[len("shots/%s/" % key):]))
        try:
            name, data = prepare("%s-%s" % (key, commit[:7]), raw, *words, stem=stem)
        except Refused as e:
            problems.append("%s %s" % (path, e))
            return None
        if staged_names.get(name, path) != path:  # light/home.png and light-home.png both become light-home
            problems.append("%s: gets the same name as %s (%s)" % (path, staged_names[name], name))
            return None
        with open(os.path.join(staged, name), "wb") as f:
            f.write(data)
        staged_names[name] = path
        cache[commit, path] = name
        return name

    try:
        for m in links:
            url = m.group(0)
            if url in result:
                continue
            commit = m.group("ref") if SHA_RE.match(m.group("ref")) else args.sha
            key, path = m.group("key"), m.group("path").rstrip("/")
            if m.group("kind") == "tree":
                if commit not in trees:
                    r = gh_api_bytes("repos/%s/git/trees/%s?recursive=1" % (repo, commit), "application/vnd.github+json")
                    if r.returncode != 0:
                        problems.append("%s: cannot list %s" % (path, commit[:7]))
                        continue
                    listing = json.loads(r.stdout)
                    if listing.get("truncated"):  # GitHub cut the list short: a folder could lose files silently
                        problems.append("%s: the file list of %s is too long for one call" % (path, commit[:7]))
                        continue
                    trees[commit] = [t["path"] for t in listing["tree"] if t.get("type") == "blob"]
                folder = urllib.parse.unquote(path)  # the URL is percent-encoded; tree paths are not
                inside = [p for p in trees[commit] if p.startswith(folder + "/")
                          and os.path.splitext(p)[1].lower() in ALLOWED]
                rebuilt = {p: rebuild(commit, p, key) for p in inside}
                names = [n for n in rebuilt.values() if n]
                if names:
                    result[url] = ("https://github.com/%s/releases/tag/%s" % (repo, tag), names)
                    missing = sorted(p for p, n in rebuilt.items() if not n)
                    if missing:
                        problems.append("%s: repointed without %s" % (url, ", ".join(missing)))
                else:
                    problems.append("%s: no media file could be rebuilt from that folder" % url)
            else:
                name = rebuild(commit, path, key)
                if name:
                    result[url] = (asset_url(repo, tag, name), [name])
        for url, (new, _) in result.items():
            print("%s\n  -> %s" % (url, new))
        failed = []
        if args.write and result:
            ensure_release(repo, tag)
            for name in sorted(staged_names):
                r = gh(["release", "upload", tag, os.path.join(staged, name), "--clobber"], repo)
                if r.returncode != 0:
                    failed.append(name)
                    problems.append("%s: upload failed: %s" % (staged_names[name], (r.stderr or "").strip()))
            plan = {u: new for u, (new, names) in result.items() if not set(names) & set(failed)}
            new_body = pattern.sub(lambda m: plan.get(m.group(0), m.group(0)), body)
            body_file = os.path.join(staged, "body.md")
            with open(body_file, "w", encoding="utf-8") as f:
                f.write(new_body)
            r = gh(["pr", "edit", str(args.pr), "--body-file", body_file], repo)
            if r.returncode != 0:
                failed.append("body")
                problems.append("PR #%d body: gh pr edit failed: %s" % (args.pr, (r.stderr or "").strip()))
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    if problems:
        sys.stderr.write("pr_evidence: not migrated (a file link keeps its old URL; a folder link "
                         "lists what it lost):\n  %s\n" % "\n  ".join(problems))
    if not args.write:
        print("Dry run: nothing uploaded and the body is unchanged. Add --write to do it.")
    if failed:
        return 4
    return 5 if problems else 0  # partly migrated: the old links still need their screenshots branch


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("publish")
    p.add_argument("key")
    p.add_argument("run_dir")
    p.add_argument("--project")
    p.add_argument("--repo")
    p = sub.add_parser("sweep")
    p.add_argument("--repo")
    p.add_argument("--today", help="the date to judge by, YYYY-MM-DD (default: today, UTC)")
    p = sub.add_parser("migrate")
    p.add_argument("pr", type=int)
    p.add_argument("--sha", help="the shots branch's last commit, for links that name the branch")
    p.add_argument("--repo")
    p.add_argument("--write", action="store_true", help="upload and edit the PR body (default: print the plan)")
    args = ap.parse_args()
    return {"publish": cmd_publish, "sweep": cmd_sweep, "migrate": cmd_migrate}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
