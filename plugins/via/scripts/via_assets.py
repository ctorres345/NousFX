#!/usr/bin/env python3
"""via-assets: export a project's images and SVGs to each platform's sizes, and audit them against design/assets.toml.

Usage: via_assets.py audit    [--project DIR]
       via_assets.py convert  [--project DIR] [--dry-run] [--only NAME ...]
       via_assets.py generate --kind image|svg --name NAME --key KEY (--prompt TEXT | --prompt-file FILE)
                              [--project DIR] [--timeout SECONDS]

The spec is design/assets.toml in the project:

    palette = ["#1A73E8", "#FFFFFF"]       # the colors SVGs may use (none and currentColor are always allowed)
    ignore = ["ic_launcher*"]              # file names in the platform folders that are not Via's (optional)

    [paths]                                # platform folders, relative to the project (these are the defaults)
    android = "app/src/main/res"
    flutter = "assets/images"
    web = "public/images"

    [[asset]]
    name = "home_logo_main"                # <feature>_<what>_<variant>, snake_case
    kind = "svg"                           # svg or image
    source = "design/assets/home_logo_main.svg"
    platforms = ["android", "flutter", "web"]
    size = "24x24"                         # 1x size in dp/px; an SVG may say "vector" (its viewBox size)
    usage = "App bar logo"

Targets. image: Android drawable-mdpi..xxxhdpi PNG (1, 1.5, 2, 3, 4x); Flutter <name>.png, 2.0x/, 3.0x/; web
<name>.webp and <name>@2x.webp. svg: Android drawable/<name>.xml (VectorDrawable); Flutter and web <name>.svg (a copy).
VectorDrawable covers path, rect, circle, ellipse, line and polygon with solid fills and strokes, inside g groups
without transforms. Anything else (gradients, filters, masks, text, use, transforms) is flagged, never guessed.

`audit` is stdlib only and reads image sizes from file headers, so `/via check` can run it with `python3 -I -B`.
It prints {"findings": [...]} in Via's findings shape (screen "assets", source "via-assets"), ready for a
run record. `convert` never changes a source and writes only the targets above. Raster resizing always runs in the
pinned Pillow environment `/via setup` creates (assets.python in the personal Via config); without it `convert`
stops and says so. --dry-run lists what it would write and writes nothing.
Stdout is JSON. Exit 0 on success (findings are data, not failure), 1 when the spec or the setup is wrong
(the reason goes to stderr as "Stopped: ...").
Needs Python 3.11 or newer (tomllib).

`generate` makes one image or SVG through agy (the Antigravity CLI, Google AI Pro subscription, no API key) and
saves the raw output outside the repo, in ~/.local/share/nousfx/via/<project folder name>/<KEY>/<name>-<time>-<conversation id, 8 chars>.<ext>.
It sends the prompt only when the outbound allow-list lets this project use the `agy` destination (exit 20 and
nothing sent otherwise). agy runs in an empty temp folder and the prompt tells it to run no commands. An image is
found only from the path named in agy's reply, inside that conversation's folder, written during the call; an SVG
is the one <svg> document in the reply text, saved as text with its audit findings. An SVG that breaks a safety
rule (parse, DOCTYPE, script, embedded content) stops with exit 1 and nothing is written. It needs no assets.toml and no
Pillow. Prints {"path", "kind", "conversation_id", "findings"}; exit 1 with "Stopped: ..." when agy fails.
"""
import argparse
import fnmatch
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.realpath(__file__))
UMASK = os.umask(0)
os.umask(UMASK)
sys.path.insert(0, HERE)  # -I leaves the script folder off sys.path
import via_agy_call  # noqa: E402
import via_home  # noqa: E402
import via_outbound  # noqa: E402
SPEC = os.path.join("design", "assets.toml")
SCREEN, SOURCE = "assets", "via-assets"
NAME_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+){2,}$")
SIZE_RE = re.compile(r"^([1-9][0-9]{0,4})x([1-9][0-9]{0,4})$")
DEFAULT_PATHS = {"android": os.path.join("app", "src", "main", "res"), "flutter": os.path.join("assets", "images"),
                 "web": os.path.join("public", "images")}
PLATFORMS = tuple(DEFAULT_PATHS)
KINDS = ("svg", "image")
ANDROID_DENSITIES = (("mdpi", 1.0), ("hdpi", 1.5), ("xhdpi", 2.0), ("xxhdpi", 3.0), ("xxxhdpi", 4.0))
FLUTTER_SCALES = (("", 1.0), ("2.0x", 2.0), ("3.0x", 3.0))
WEB_SCALES = (("", 1.0), ("@2x", 2.0))
RASTER_EXT = (".png", ".jpg", ".jpeg", ".webp")
ASSET_EXT = RASTER_EXT + (".svg", ".xml")

SEVERITY = {"missing-source": "high", "missing": "high", "wrong-size": "med", "extra": "low", "upscale": "med",
            "aspect": "med", "stale": "med",
            "naming": "med", "palette-missing": "med", "svg-parse": "high", "svg-doctype": "high",
            "svg-script": "high", "svg-embedded-image": "high", "svg-viewbox": "med", "svg-color": "med",
            "svg-unsupported": "med"}

SVG_NS = "{http://www.w3.org/2000/svg}"
EDITOR_NS = ("{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}", "{http://www.inkscape.org/namespaces/inkscape}",
             "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}", "{http://creativecommons.org/ns#}",
             "{http://purl.org/dc/elements/1.1/}")  # editor bookkeeping a browser never draws
SHAPES = {"path", "rect", "circle", "ellipse", "line", "polygon"}
ASSET_HEADER = re.compile(r"^\s*\[\[\s*asset\s*\]\]\s*(#.*)?$")
PATH_DATA = re.compile(r"^[MmZzLlHhVvCcSsQqTtAa0-9eE+\-.,\s]*$")
CONTAINERS = {"svg", "g"}
IGNORED = {"title", "desc", "metadata"}  # carry no drawing
ANIMATION = {"set", "animate", "animateTransform", "animateMotion", "animateColor", "discard"}
COLOR_PROPS = ("fill", "stroke", "stop-color", "color", "flood-color", "lighting-color")
INHERITED = ("fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity", "fill-rule", "stroke-linecap",
             "stroke-linejoin")


class SpecError(Exception):
    pass


def die(message, code=1):
    print("Stopped: %s" % message, file=sys.stderr)
    sys.exit(code)


def emit(payload):
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def rel(project, path):
    return os.path.relpath(path, project).replace(os.sep, "/")


def finding(rule, file, issue, fix):
    return {"screen": SCREEN, "severity": SEVERITY[rule], "location": {"file": file}, "rule": rule,
            "issue": issue, "fix": fix, "source": SOURCE}


# --- the spec file ---

def asset_line(text, index):
    """1-based line of the index-th [[asset]] header, so an error can name it."""
    seen = 0
    for n, line in enumerate(text.splitlines(), 1):
        if ASSET_HEADER.match(line):
            if seen == index:
                return n
            seen += 1
    return 0


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def load_spec(project):
    try:
        import tomllib
    except ImportError:
        raise SpecError("via_assets.py needs Python 3.11 or newer to read %s (this is %d.%d)."
                        % (SPEC, sys.version_info[0], sys.version_info[1]))
    path = os.path.join(project, SPEC)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise SpecError("%s is missing. Write it first (see the format at the top of via_assets.py)." % SPEC)
    except (OSError, UnicodeDecodeError) as e:
        raise SpecError("cannot read %s (%s)." % (SPEC, e))
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:  # its message names the line and column
        raise SpecError("%s is not valid TOML: %s." % (SPEC, e))

    def bad(i, what):
        line = asset_line(text, i)
        where = "line %d (asset %d)" % (line, i + 1) if line else "asset %d" % (i + 1)
        raise SpecError("%s %s: %s." % (SPEC, where, what))

    palette = data.get("palette")
    if palette is not None and not (isinstance(palette, list) and all(isinstance(c, str) for c in palette)):
        raise SpecError("%s: palette must be a list of color strings." % SPEC)
    ignore = data.get("ignore", [])
    if not (isinstance(ignore, list) and all(isinstance(g, str) for g in ignore)):
        raise SpecError("%s: ignore must be a list of file name patterns." % SPEC)
    paths = dict(DEFAULT_PATHS)
    given = data.get("paths", {})
    if not isinstance(given, dict):
        raise SpecError("%s: [paths] must be a table." % SPEC)
    for key, value in given.items():
        if key not in PLATFORMS or not isinstance(value, str) or not value.strip():
            raise SpecError("%s: [paths] %s must be one of %s, set to a folder." % (SPEC, key, ", ".join(PLATFORMS)))
        if os.path.isabs(value) or not inside(os.path.join(project, value), project):
            raise SpecError("%s: [paths] %s must be a folder inside the project." % (SPEC, key))
        paths[key] = os.path.normpath(value)
    assets = data.get("asset", [])
    if not isinstance(assets, list):
        raise SpecError("%s: assets are written as [[asset]] tables." % SPEC)
    seen = set()
    for i, a in enumerate(assets):
        if not isinstance(a, dict):
            bad(i, "an [[asset]] must be a table")
        for key in ("name", "kind", "source", "size", "usage"):
            if not (isinstance(a.get(key), str) and a[key].strip()):
                bad(i, "%s must be non-empty text" % key)
        if a["kind"] not in KINDS:
            bad(i, "kind must be svg or image")
        plats = a.get("platforms")
        if not (isinstance(plats, list) and plats and all(p in PLATFORMS for p in plats)):
            bad(i, "platforms must list one or more of %s" % ", ".join(PLATFORMS))
        if a["size"] != "vector" and not SIZE_RE.match(a["size"]):
            bad(i, 'size must be WIDTHxHEIGHT, such as 24x24 (an SVG may say "vector")')
        if a["size"] == "vector" and a["kind"] != "svg":
            bad(i, 'size "vector" is for SVGs only; give an image its 1x size')
        if os.path.isabs(a["source"]) or not inside(os.path.join(project, a["source"]), project):
            bad(i, "source must be a file inside the project")
        if a["name"] in seen:
            bad(i, "the name %s is used twice" % a["name"])
        seen.add(a["name"])
    return {"palette": palette, "ignore": ignore, "paths": paths, "assets": assets}


def size_of(asset):
    m = SIZE_RE.match(asset["size"])
    return (int(m.group(1)), int(m.group(2))) if m else None


def scaled(size, scale):
    return int(round(size[0] * scale)), int(round(size[1] * scale))


def targets(spec, asset, project):
    """[(platform, path, kind, (w, h) or None)] for one asset. kind is png, webp, vector or svg-copy."""
    out, name, size, paths = [], asset["name"], size_of(asset), spec["paths"]
    for plat in asset["platforms"]:
        base = os.path.join(project, paths[plat])
        if asset["kind"] == "svg":
            if plat == "android":
                out.append((plat, os.path.join(base, "drawable", name + ".xml"), "vector", size))
            else:
                out.append((plat, os.path.join(base, name + ".svg"), "svg-copy", None))
            continue
        if plat == "android":
            for folder, scale in ANDROID_DENSITIES:
                out.append((plat, os.path.join(base, "drawable-" + folder, name + ".png"), "png", scaled(size, scale)))
        elif plat == "flutter":
            for folder, scale in FLUTTER_SCALES:
                out.append((plat, os.path.join(base, folder, name + ".png"), "png", scaled(size, scale)))
        else:
            for suffix, scale in WEB_SCALES:
                out.append((plat, os.path.join(base, name + suffix + ".webp"), "webp", scaled(size, scale)))
    return out


# --- image sizes from headers (stdlib only) ---

def image_size(path):
    """(width, height) of a PNG, JPEG or WebP file as it displays (a JPEG's EXIF rotation applied), or None when the
    header cannot be read or names a zero size."""
    size = _header_size(path)
    return size if size and size[0] > 0 and size[1] > 0 else None


def _exif_orientation(segment):
    """The Orientation tag (1-8) in a JPEG Exif APP1 body, or None when it has none or cannot be read."""
    try:
        tiff = segment[6:]
        order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
        if not order:
            return None
        ifd = struct.unpack(order + "I", tiff[4:8])[0]
        count = struct.unpack(order + "H", tiff[ifd:ifd + 2])[0]
        for i in range(count):
            entry = tiff[ifd + 2 + 12 * i: ifd + 14 + 12 * i]
            tag, kind = struct.unpack(order + "HH", entry[:4])
            if tag == 0x0112:  # SHORT as usual; LONG read by its type, as Pillow does
                return struct.unpack(order + ("I" if kind == 4 else "H"), entry[8:12] if kind == 4 else entry[8:10])[0]
    except (struct.error, IndexError):
        pass
    return None


def _header_size(path):
    exif = xmp = size = None
    seen_exif = False
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                chunk = head[12:16]
                if chunk == b"VP8 ":
                    w, h = struct.unpack("<HH", head[26:30])
                    return w & 0x3FFF, h & 0x3FFF
                if chunk == b"VP8L":
                    b = head[21:25]
                    w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                    h = 1 + (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                    return w, h
                if chunk == b"VP8X":
                    w = 1 + int.from_bytes(head[24:27], "little")
                    h = 1 + int.from_bytes(head[27:30], "little")
                    return w, h
                return None
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    byte = f.read(1)  # as Pillow: skip junk and 0xFF fill bytes before a marker
                    while byte and byte != b"\xff":
                        byte = f.read(1)
                    while byte == b"\xff":
                        byte = f.read(1)
                    if not byte:
                        return None
                    if byte == b"\x00":  # a stuffed 0xFF00 is not a marker
                        continue
                    marker = b"\xff" + byte
                    if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                        continue
                    length = struct.unpack(">H", f.read(2))[0]
                    if marker[1] == 0xDA:  # start of scan: Pillow has read every header block by now
                        if not size:
                            return None
                        orientation = exif if exif is not None else (xmp or 1)
                        return (size[1], size[0]) if orientation in (5, 6, 7, 8) else size
                    body = f.read(length - 2)
                    if marker[1] == 0xE1:  # as Pillow: the first Exif block wins, the last XMP block wins
                        if body.startswith(b"Exif\0\0") and not seen_exif:
                            seen_exif, exif = True, _exif_orientation(body)
                        elif body.startswith(b"http://ns.adobe.com/xap/1.0/\0"):
                            m = re.search(rb'tiff:Orientation(="|>)([0-9])', body)
                            xmp = int(m.group(2)) if m else None
                    elif marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE,
                                       0xCF):  # as Pillow: the last frame header sets the size
                        h, w = struct.unpack(">xHH", body[:5])
                        size = (w, h)
    except (OSError, struct.error, IndexError):
        return None
    return None


def vector_size(path):
    """(width, height) in dp of a VectorDrawable, or None."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    ns = "{http://schemas.android.com/apk/res/android}"
    try:
        return tuple(int(float(root.get(ns + k, "").replace("dp", ""))) for k in ("width", "height"))
    except ValueError:
        return None


# --- SVG ---

def editor_only(el):
    return el.tag.startswith(EDITOR_NS)


def local(tag):
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def style_decls(value):
    out = {}
    for part in (value or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def props(el):
    """Presentation properties of one element: attributes, then style declarations (style wins, as in CSS)."""
    out = {k: v.strip() for k, v in el.attrib.items() if "}" not in k}
    out.update(style_decls(el.get("style")))
    return out


def norm_color(value):
    v = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", v):
        v = "#" + "".join(c * 2 for c in v[1:])
    return v


def parse_svg(path):
    """(root, raw bytes, problem): problem is (rule, text) when the file cannot be read safely."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return None, None, ("svg-parse", "cannot read the file (%s)" % e.strerror)
    return parse_svg_bytes(raw)


def parse_svg_bytes(raw):
    decl = re.match(rb"<\?xml[^>]*encoding\s*=\s*['\"]([^'\"]+)", raw)
    if (raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\0" in raw
            or (decl and decl.group(1).lower() not in (b"utf-8", b"utf8", b"us-ascii"))):
        return None, raw, ("svg-parse", "is not UTF-8, so its text cannot be checked; save it as UTF-8")
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, raw, ("svg-parse", "is not valid UTF-8; save it as UTF-8")
    if re.search(rb"<!(DOCTYPE|ENTITY)", raw, re.I):
        return None, raw, ("svg-doctype", "has a DOCTYPE or ENTITY declaration, which can pull in outside content")
    if re.search(rb"<\?(?!xml[\s?])", raw, re.I):  # ET drops processing instructions, so refuse them here
        return None, raw, ("svg-script", "has a processing instruction (such as xml-stylesheet), which can pull in "
                                         "outside content or run a transform")
    try:
        return ET.fromstring(raw), raw, None
    except ET.ParseError as e:
        return None, raw, ("svg-parse", "is not well-formed XML (%s)" % e)


def svg_findings(root, file, palette, android):
    """Findings for one parsed SVG: viewBox, scripts, embedded images, palette colors, VectorDrawable subset."""
    out, scripts, embedded, unsupported, colors, bad_path = [], set(), set(), set(), set(), set()
    if local(root.tag) != "svg":
        return [finding("svg-parse", file, "The root element is <%s>, not <svg>." % local(root.tag),
                        "Write the asset as a standalone SVG.")]
    if not root.get("viewBox"):
        out.append(finding("svg-viewbox", file, "The SVG has no viewBox, so it cannot scale.",
                           'Add viewBox="0 0 W H" with the drawing size.'))

    def walk(el, inherited, drawn=True):
        """Safety rules on every element; drawing rules only on drawn ones (not inside title, desc, metadata or
        editor elements such as Inkscape's sodipodi:namedview, which a browser never draws)."""
        name = local(el.tag)
        p = props(el)
        foreign = el.tag.startswith("{") and not el.tag.startswith(SVG_NS)
        if foreign and not editor_only(el):
            embedded.add("a <%s> element from another namespace" % name)
        drawn = drawn and not foreign and name not in IGNORED
        for k, v in el.attrib.items():
            key = local(k).lower()
            if key.startswith("on"):
                scripts.add("an %s handler" % key)
            value = re.sub(r"\s+", "", v).lower()
            if "javascript:" in value:  # any attribute: <set to=...> and <animate values=...> can write an href
                scripts.add("a javascript: link")
            elif key == "href" and value.startswith("data:"):
                embedded.add("a data: link")
            elif key == "href" and not value.startswith("#"):
                embedded.add("an outside link")
            elif key in ("src", "srcset", "data", "poster", "action", "formaction", "background"):
                embedded.add("a %s attribute" % key)
            if "\\" in value or re.search(r"(image-set|src)\(", value):  # CSS escapes can spell url( any way
                embedded.add("a CSS escape or image-set()/src() reference")
            for ref in re.findall(r"url\(['\"]?([^)'\"]*)", value):  # any attribute, style included
                if not ref.startswith("#"):
                    embedded.add("a url(%s) reference" % ("data:" if ref.startswith("data:") else "outside"))
        if name == "script":
            scripts.add("a <script> element")
        elif foreign:
            pass
        elif name in ANIMATION or name == "a":
            scripts.add("an <%s> element (links and animation can change attributes at run time)" % name)
        elif name in ("image", "foreignObject", "style"):
            embedded.add("an <%s> element" % name)
        elif drawn and (name not in SHAPES | CONTAINERS or (name == "svg" and el is not root)):
            unsupported.add("<%s>" % ("svg> inside <svg" if name == "svg" else name))
        state = dict(inherited)
        if drawn:
            if "transform" in p and name != "svg":
                unsupported.add("transform")
            for key in ("fill", "stroke"):
                if p.get(key, "").startswith("url("):
                    unsupported.add("%s paint server (gradient or pattern)" % key)
            for key in INHERITED:
                if key in p:
                    state[key] = p[key]
            for key in COLOR_PROPS:
                value = p.get(key)
                if value and not value.startswith("url(") and value.lower() not in ("none", "currentcolor", "inherit"):
                    colors.add(norm_color(value))
            if name == "path" and not PATH_DATA.match(p.get("d", "")):
                bad_path.add("a path whose d holds characters that are not path data")
            if name in SHAPES and name != "line" and "fill" not in state:
                colors.add("#000000 (the default fill)")
        for child in el:
            walk(child, state, drawn)

    walk(root, {})
    if bad_path:
        out.append(finding("svg-parse", file, "The SVG has %s." % ", ".join(sorted(bad_path)),
                           "Write path data with commands and numbers only."))
    if scripts:
        out.append(finding("svg-script", file, "The SVG holds script: %s." % ", ".join(sorted(scripts)),
                           "Remove all script and event handlers; an asset SVG is drawing only."))
    if embedded:
        out.append(finding("svg-embedded-image", file, "The SVG embeds content: %s." % ", ".join(sorted(embedded)),
                           "Draw with shapes only, or ship the picture as an image asset."))
    if palette:
        allowed = {norm_color(c) for c in palette}
        off = sorted(c for c in colors if c.split(" ")[0] not in allowed)
        if off:
            out.append(finding("svg-color", file, "Colors outside the palette: %s." % ", ".join(off),
                               "Use only palette colors from %s (none and currentColor are allowed)." % SPEC))
    if android and unsupported:
        out.append(finding("svg-unsupported", file,
                           "VectorDrawable conversion does not support: %s." % ", ".join(sorted(unsupported)),
                           "Redraw with path, rect, circle, ellipse, line or polygon and solid colors, or drop "
                           "android from this asset's platforms."))
    return out


# --- SVG to VectorDrawable ---

def num(value, default=0.0):
    if value is None:
        return default
    v = value.strip()
    if v.endswith("px"):
        v = v[:-2]
    try:
        return float(v)
    except ValueError:
        raise ValueError("cannot read the length %r" % value)


def fmt(v):
    text = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def shape_path(name, p):
    if name == "path":
        return " ".join(p.get("d", "").split())
    if name == "rect":
        x, y, w, h = (num(p.get(k)) for k in ("x", "y", "width", "height"))
        rx, ry = p.get("rx"), p.get("ry")
        rx, ry = num(rx if rx is not None else ry), num(ry if ry is not None else rx)
        rx, ry = min(rx, w / 2), min(ry, h / 2)
        if rx <= 0 or ry <= 0:
            return "M%s,%s H%s V%s H%s Z" % (fmt(x), fmt(y), fmt(x + w), fmt(y + h), fmt(x))
        a = "A%s,%s 0 0 1 " % (fmt(rx), fmt(ry))
        return ("M%s,%s H%s %s%s,%s V%s %s%s,%s H%s %s%s,%s V%s %s%s,%s Z" % (
            fmt(x + rx), fmt(y), fmt(x + w - rx), a, fmt(x + w), fmt(y + ry), fmt(y + h - ry), a, fmt(x + w - rx),
            fmt(y + h), fmt(x + rx), a, fmt(x), fmt(y + h - ry), fmt(y + ry), a, fmt(x + rx), fmt(y)))
    if name in ("circle", "ellipse"):
        cx, cy = num(p.get("cx")), num(p.get("cy"))
        rx = num(p.get("r" if name == "circle" else "rx"))
        ry = num(p.get("r" if name == "circle" else "ry"))
        a = "A%s,%s 0 1,0 " % (fmt(rx), fmt(ry))
        return "M%s,%s %s%s,%s %s%s,%s Z" % (fmt(cx - rx), fmt(cy), a, fmt(cx + rx), fmt(cy), a, fmt(cx - rx), fmt(cy))
    if name == "line":
        return "M%s,%s L%s,%s" % tuple(fmt(num(p.get(k))) for k in ("x1", "y1", "x2", "y2"))
    nums = [float(n) for n in re.findall(r"-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?", p.get("points", ""), re.I)]
    pairs = ["%s,%s" % (fmt(nums[i]), fmt(nums[i + 1])) for i in range(0, len(nums) - 1, 2)]
    return "M%s L%s Z" % (pairs[0], " L".join(pairs[1:])) if len(pairs) > 1 else ""


def vd_color(value, opacity):
    v = norm_color(value)
    if v == "currentcolor":
        v = "#000000"
    if not re.fullmatch(r"#[0-9a-f]{6}", v):
        raise ValueError("cannot convert the color %r (use #RRGGBB)" % value)
    if opacity < 1:
        return "#%02X%s" % (int(round(max(0.0, opacity) * 255)), v[1:].upper())
    return v.upper()


def to_vector(root, size):
    """VectorDrawable XML text for a parsed SVG in the supported subset. Raises ValueError otherwise."""
    box = [float(n) for n in (root.get("viewBox") or "").replace(",", " ").split()]
    if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
        raise ValueError("needs a viewBox with a positive width and height")
    width, height = size if size else (box[2], box[3])
    paths = []

    def walk(el, state):
        name = local(el.tag)
        if name in IGNORED or editor_only(el):
            return
        p = props(el)
        if (name not in SHAPES | CONTAINERS or ("transform" in p and name != "svg")
                or (name == "svg" and el is not root)):
            raise ValueError("unsupported element or transform: <%s>" % name)
        state = dict(state)
        for key in INHERITED:
            if key in p:
                state[key] = p[key]
        if "opacity" in p:
            state["opacity"] = state.get("opacity", 1.0) * num(p["opacity"], 1)
        if name in SHAPES:
            paths.append((shape_path(name, p), state))
        for child in el:
            walk(child, state)

    walk(root, {})
    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
             '    android:width="%sdp"' % fmt(width), '    android:height="%sdp"' % fmt(height),
             '    android:viewportWidth="%s"' % fmt(box[2]), '    android:viewportHeight="%s">' % fmt(box[3])]
    indent = "    "
    if box[0] or box[1]:
        lines.append('    <group android:translateX="%s" android:translateY="%s">' % (fmt(-box[0]), fmt(-box[1])))
        indent = "        "
    for data, s in paths:
        if not data:
            continue
        opacity = s.get("opacity", 1.0)
        attrs = []
        fill = s.get("fill", "#000000")
        if fill.lower() != "none":
            attrs.append(("fillColor", vd_color(fill, opacity * num(s.get("fill-opacity"), 1))))
            if s.get("fill-rule") == "evenodd":
                attrs.append(("fillType", "evenOdd"))
        stroke = s.get("stroke", "none")
        if stroke.lower() != "none":
            attrs.append(("strokeColor", vd_color(stroke, opacity * num(s.get("stroke-opacity"), 1))))
            attrs.append(("strokeWidth", fmt(num(s.get("stroke-width"), 1))))
            if s.get("stroke-linecap") in ("round", "square", "butt"):
                attrs.append(("strokeLineCap", s["stroke-linecap"]))
            if s.get("stroke-linejoin") in ("round", "bevel", "miter"):
                attrs.append(("strokeLineJoin", s["stroke-linejoin"]))
        attrs.append(("pathData", data))
        lines.append(indent + "<path")
        lines.extend('%s    android:%s=%s' % (indent, k, quoteattr(str(v))) for k, v in attrs[:-1])
        lines.append('%s    android:%s=%s/>' % (indent, attrs[-1][0], quoteattr(str(attrs[-1][1]))))
    if box[0] or box[1]:
        lines.append("    </group>")
    lines.append("</vector>")
    return "\n".join(lines) + "\n"


# --- audit ---

def managed_files(spec, project, platforms):
    """Asset-like files in the folders Via writes to (targets() names the same folders), for the extra check."""
    found = []
    for plat in platforms:
        base = os.path.join(project, spec["paths"][plat])
        folders = {"android": [os.path.join(base, "drawable")] + [os.path.join(base, "drawable-" + d)
                                                                  for d, _ in ANDROID_DENSITIES],
                   "flutter": [os.path.join(base, d) for d, _ in FLUTTER_SCALES], "web": [base]}[plat]
        for folder in folders:
            if os.path.isdir(folder):
                for entry in sorted(os.listdir(folder)):
                    path = os.path.join(folder, entry)
                    if os.path.isfile(path) and entry.lower().endswith(ASSET_EXT):
                        if not any(fnmatch.fnmatch(entry, g) for g in spec["ignore"]):
                            found.append(path)
    return found


def audit(spec, project):
    out, expected, used_platforms = [], set(), set()
    if not spec["palette"]:
        out.append(finding("palette-missing", SPEC, "%s has no palette, so SVG colors cannot be checked." % SPEC,
                           'Add palette = ["#RRGGBB", ...] with the colors assets may use.'))
    for a in spec["assets"]:
        used_platforms.update(a["platforms"])
        src_rel = os.path.normpath(a["source"]).replace(os.sep, "/")
        if not NAME_RE.match(a["name"]):
            out.append(finding("naming", src_rel, "The asset name %r is not <feature>_<what>_<variant> in snake_case."
                               % a["name"], "Rename it in %s, such as home_logo_main." % SPEC))
            continue
        src = os.path.join(project, a["source"])
        tlist = targets(spec, a, project)
        expected.update(os.path.realpath(t[1]) for t in tlist)
        if not os.path.isfile(src):
            out.append(finding("missing-source", src_rel, "The source for %s is missing." % a["name"],
                               "Add the source file, or fix source in %s." % SPEC))
        elif a["kind"] == "svg":
            root, _, problem = parse_svg(src)
            if problem:
                out.append(finding(problem[0], src_rel, "The SVG %s." % problem[1], "Rewrite it as a plain SVG."))
            else:
                out.extend(svg_findings(root, src_rel, spec["palette"], "android" in a["platforms"]))
        else:
            have = image_size(src)
            if have is None:
                out.append(finding("missing-source", src_rel, "The source for %s is not a PNG, JPEG or WebP image."
                                   % a["name"], "Export the source as PNG."))
            else:
                big = max((t[3] for t in tlist), key=lambda s: s[0] * s[1])
                if big[0] > have[0] or big[1] > have[1]:
                    out.append(finding("upscale", src_rel, "The source is %dx%d but the largest target is %dx%d, so "
                                       "it would be upscaled." % (have + big),
                                       "Supply a source at least %dx%d, or lower size." % big))
                if aspect_off(have, big):
                    out.append(finding("aspect", src_rel, "The source is %dx%d but size is %s, so convert "
                                       "crops the edges." % (have[0], have[1], a["size"]),
                                       "Supply a source with the same aspect ratio, or change size."))
        for plat, path, kind, want in tlist:
            file = rel(project, path)
            if not os.path.isfile(path):
                out.append(finding("missing", file, "%s for %s is missing." % (plat.capitalize(), a["name"]),
                                   "Run via_assets.py convert."))
                continue
            if kind == "svg-copy":
                try:
                    with open(path, "rb") as f, open(src, "rb") as g:
                        same = f.read() == g.read()
                except OSError:
                    same = True  # an unreadable source is already reported as missing-source
                if not same:
                    out.append(finding("stale", file, "%s differs from its source %s." % (os.path.basename(path),
                                                                                         src_rel),
                                       "Run via_assets.py convert; edit the source, never the copy."))
                continue
            got = image_size(path) if kind in ("png", "webp") else vector_size(path) if kind == "vector" else None
            if want and kind != "svg-copy" and got != want:
                out.append(finding("wrong-size", file, "%s is %s, the spec wants %dx%d."
                                   % (os.path.basename(path), "%dx%d" % got if got else "unreadable", want[0], want[1]),
                                   "Run via_assets.py convert."))
    for path in managed_files(spec, project, sorted(used_platforms)):
        if os.path.realpath(path) not in expected:
            out.append(finding("extra", rel(project, path), "%s is not in %s." % (os.path.basename(path), SPEC),
                               "Add it to %s, delete it, or list it under ignore." % SPEC))
    return out


# --- convert ---

def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".via-assets-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o666 & ~UMASK)  # mkstemp makes 0600; project files get the usual mode
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def aspect_off(have, want, tolerance=0.02):
    """True when the source and target aspect ratios differ, so ImageOps.fit would crop."""
    want_ratio = want[0] / want[1]
    return abs(have[0] / have[1] - want_ratio) > tolerance * want_ratio


def resize(jobs):
    """Run raster jobs with Pillow in this process. Each job: src, dst, width, height, format."""
    from PIL import Image, ImageOps
    import io
    by_source = {}
    for job in jobs:
        by_source.setdefault(job["src"], []).append(job)
    for src, group in by_source.items():  # decode each source once
        with Image.open(src) as im:
            if im.format == "JPEG":  # image_size() reads a JPEG's EXIF rotation, so turn it the same way
                im = ImageOps.exif_transpose(im)
            im = im.convert("RGBA")
        for job in group:
            out = ImageOps.fit(im, (job["width"], job["height"]), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            if job["format"] == "webp":
                out.save(buf, "WEBP", quality=90, method=6)
            else:
                out.save(buf, "PNG")
            write_atomic(job["dst"], buf.getvalue())


def pillow_python():
    """The Pillow environment's interpreter from the personal config, or None."""
    try:
        path = via_home.config_path()
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (via_home.Refused, OSError, ValueError):
        return None
    py = cfg.get("assets", {}).get("python") if isinstance(cfg, dict) and isinstance(cfg.get("assets"), dict) else None
    return py if isinstance(py, str) and os.path.isabs(py) and os.path.isfile(py) and os.access(py, os.X_OK) else None


def run_resize(jobs):
    """Resize in the pinned Pillow environment from /via setup (never a Pillow that happens to be installed).
    Returns an error or None."""
    py = pillow_python()
    if not py:
        return ("The Pillow environment is missing, so raster images cannot be converted. Run /via setup and "
                "create it, then run convert again. Nothing was written.")
    try:
        r = subprocess.run([py, "-I", "-B", os.path.realpath(__file__), "_resize"], input=json.dumps(jobs),
                           capture_output=True, text=True, timeout=600)
    except (subprocess.TimeoutExpired, OSError) as e:
        r, tail = None, "timed out" if isinstance(e, subprocess.TimeoutExpired) else str(e)
    if r is not None and r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["no output"])[-1]
    if r is None or r.returncode != 0:
        return ("The Pillow environment failed to convert (%s); some targets may already be written. "
                "Run /via setup to check it, then convert again." % tail)
    return None


def convert(spec, project, dry_run, only):
    written, flags, jobs, copies = [], [], [], []
    sources = {os.path.realpath(os.path.join(project, a["source"])) for a in spec["assets"]}
    unknown = sorted(set(only or []) - {a["name"] for a in spec["assets"]})
    if unknown:
        raise SpecError("--only names assets that are not in %s: %s." % (SPEC, ", ".join(unknown)))
    for a in spec["assets"]:
        if only and a["name"] not in only:
            continue
        if not NAME_RE.match(a["name"]):
            flags.append({"name": a["name"], "issue": "not converted: the name is not <feature>_<what>_<variant> "
                                                      "in snake_case (it is never renamed for you)"})
            continue
        src = os.path.join(project, a["source"])
        if not os.path.isfile(src):
            flags.append({"name": a["name"], "issue": "not converted: the source %s is missing" % a["source"]})
            continue
        tlist = targets(spec, a, project)
        for _, path, _, _ in tlist:
            if not inside(os.path.dirname(path), project) or os.path.realpath(path) in sources:
                raise SpecError("the target %s for %s would leave the project or overwrite a source."
                                % (rel(project, path), a["name"]))
        if a["kind"] == "svg":
            root, raw, problem = parse_svg(src)
            unsafe = [f for f in (svg_findings(root, a["source"], None, False) if root is not None else [])
                      if f["rule"] in ("svg-script", "svg-embedded-image", "svg-parse")]
            if problem or unsafe:
                flags.append({"name": a["name"], "issue": "not converted: the SVG %s" % (
                    problem[1] if problem else unsafe[0]["issue"].rstrip(".").lower())})
                continue
            for plat, path, kind, want in tlist:
                if kind == "vector":
                    try:
                        copies.append((path, to_vector(root, want).encode("utf-8")))
                    except ValueError as e:
                        flags.append({"name": a["name"], "issue": "no VectorDrawable: %s" % e})
                        continue
                else:
                    copies.append((path, raw))
                written.append(rel(project, path))
            continue
        have = image_size(src)
        if have is None:
            flags.append({"name": a["name"], "issue": "not converted: the source is not a PNG, JPEG or WebP image"})
            continue
        big = max((t[3] for t in tlist), key=lambda s: s[0] * s[1])
        if aspect_off(have, big):
            flags.append({"name": a["name"], "issue": "cropped: the %dx%d source is cut to the shape of size %s"
                          % (have[0], have[1], a["size"])})
        for plat, path, kind, want in tlist:
            if want[0] > have[0] or want[1] > have[1]:
                flags.append({"name": a["name"], "issue": "upscaled: %s is %dx%d from a %dx%d source"
                              % (rel(project, path), want[0], want[1], have[0], have[1])})
            jobs.append({"src": src, "dst": path, "width": want[0], "height": want[1], "format": kind})
            written.append(rel(project, path))
    if not dry_run:
        if jobs:
            error = run_resize(jobs)
            if error:
                die(error)
        for path, data in copies:
            write_atomic(path, data)
    return {"dry_run": dry_run, "written" if not dry_run else "would_write": written, "flags": flags}


KEY_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
UNSAFE_SVG = {"svg-parse", "svg-doctype", "svg-script", "svg-embedded-image"}
IMAGE_PROMPT = ("Use generate_image to make exactly one image. Then reply with the absolute path of the image file "
                "and nothing else. The image: %s")
SVG_PROMPT = ("Write exactly one standalone SVG document (with a viewBox, drawn with path, rect, circle, ellipse, "
              "line and polygon only, solid colors, no script, no text, no images, no links). Reply with only the "
              "SVG markup. The drawing: %s")


def raw_dir(project, key):
    group = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(project)) or "project"
    return os.path.join(via_home.home_dir(), ".local", "share", "nousfx", "via", group, key)


def generate(project, kind, name, key, prompt, timeout):
    if not NAME_RE.match(name):
        raise SpecError("--name %r is not <feature>_<what>_<variant> in snake_case" % name)
    if not KEY_RE.match(key):
        raise SpecError("--key %r is not a ticket key such as APP-7" % key)
    if not prompt.strip():
        raise SpecError("the prompt is empty")
    try:
        code, result = via_outbound.decide(project, "agy")
    except via_outbound.Bad as e:
        die("%s. Nothing was sent" % e)
    if code != via_outbound.ALLOWED:
        die("%s. Nothing was sent" % result["reason"], code)
    started = via_agy_call.now()
    try:
        reply = via_agy_call.call((IMAGE_PROMPT if kind == "image" else SVG_PROMPT) % prompt.strip(), timeout)
    except via_agy_call.AgyError as e:
        die("%s. Nothing was written" % e)
    # The conversation id makes the name unique: two runs in the same second never overwrite each other.
    stem = "%s-%s-%s" % (name, time.strftime("%Y%m%d-%H%M%S"), reply["conversation_id"][:8])
    folder = raw_dir(project, key)
    findings = []
    if kind == "image":
        got = via_agy_call.image(reply, started)
        if not got:
            die("agy returned no image file. Raw reply: %s. Nothing was written" % reply["response"][:400])
        data, ext = got
        path = os.path.join(folder, stem + ext)
    else:
        text = via_agy_call.svg(reply)
        try:
            data = text.encode("utf-8") if text else None
        except UnicodeEncodeError:  # JSON lets a lone surrogate through
            text = data = None
        root, _, problem = parse_svg_bytes(data) if data else (None, None, None)
        if text is None or (problem and problem[0] == "svg-parse"):
            die("agy's reply holds no single <svg> document. Raw reply: %s. Nothing was written"
                % reply["response"][:400])
        path = os.path.join(folder, stem + ".svg")
        findings = ([finding(problem[0], path, "The SVG %s." % problem[1], "Ask for a plain SVG again.")]
                    if problem else svg_findings(root, path, [], True))
        unsafe = sorted({f["rule"] for f in findings} & UNSAFE_SVG)
        if unsafe:  # checked in memory: model output that could run or pull in content never lands as an .svg
            die("agy's SVG breaks the safety rules (%s). Nothing was written" % ", ".join(unsafe))
    write_atomic(path, data)
    return {"path": path, "kind": kind, "conversation_id": reply["conversation_id"], "findings": findings}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "_resize":  # the Pillow environment's half of convert
        resize(json.loads(sys.stdin.read()))
        return
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--project", default=".")
    c = sub.add_parser("convert")
    c.add_argument("--project", default=".")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--only", nargs="+")
    g = sub.add_parser("generate")
    g.add_argument("--project", default=".")
    g.add_argument("--kind", choices=["image", "svg"], required=True)
    g.add_argument("--name", required=True)
    g.add_argument("--key", required=True)
    src = g.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt")
    src.add_argument("--prompt-file")
    g.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()
    project = os.path.realpath(args.project)
    try:
        if args.cmd == "generate":
            prompt = args.prompt
            if prompt is None:
                try:
                    with open(args.prompt_file, encoding="utf-8") as f:
                        prompt = f.read()
                except OSError as e:
                    raise SpecError("cannot read the prompt file (%s)" % e.strerror)
            emit(generate(project, args.kind, args.name, args.key, prompt, args.timeout))
            return
        spec = load_spec(project)
        if args.cmd == "audit":
            found = audit(spec, project)
            emit({"spec": SPEC, "assets": len(spec["assets"]), "findings": found})
        else:
            emit(convert(spec, project, args.dry_run, args.only))
    except SpecError as e:
        die(str(e))


if __name__ == "__main__":
    main()
