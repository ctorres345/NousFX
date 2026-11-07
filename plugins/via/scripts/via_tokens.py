#!/usr/bin/env python3
"""via-tokens: reads a web project's design tokens (CSS custom properties) for the prototype spec panel.

Usage: via_tokens.py read [--project DIR]

Prints JSON: {"colors": {...}, "type_size": {...}, "type_weight": {...}, "radius": {...}, "spacing": {...},
"sources": [relative css files that gave a token]}. Every group maps a property name to
{"light": value, "dark": value}; dark falls back to light when a theme sets no dark value, and light is null
when only a dark value exists. A name with no token is simply absent: callers show "no token" and never invent a default.

Light values come from :root, html or body. Dark values come from a `prefers-color-scheme: dark` media query or a
dark selector (.dark, [data-theme=dark], .theme-dark, .dark-theme). Other media queries are ignored.
`var(--x)` references are resolved inside one theme, falling back to the light value.
Names pick the group: --radius*, --space*/--spacing*, --font-size*/--text-size*/--fs-*, --font-weight*/--fw-*;
anything else is a color only when its value is a color. Hidden folders, node_modules, build output and symlinks are skipped.
Exit 0 on success, 1 when --project is not a folder.
"""
import argparse
import json
import os
import re
import sys

SKIP_DIRS = {"node_modules", "build", "dist", "out", "venv", "__pycache__", "coverage", "vendor"}
MAX_FILES = 500
MAX_BYTES = 1_000_000
MAX_VALUE = 1000  # a token value longer than this is skipped, and var() expansion stops past it
GROUPS = ("colors", "type_size", "type_weight", "radius", "spacing")
PREFIXES = (
    ("radius", ("radius", "rounded", "corner")),
    ("spacing", ("space", "spacing", "gap")),
    ("type_size", ("font-size", "text-size", "fs-")),
    ("type_weight", ("font-weight", "fw-")),
)
COLOR_RE = re.compile(r"^(#[0-9a-f]{3,8}|(rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\(.*\))$", re.I)
ROOT_RE = re.compile(r"(^|[\s,>+~])(:root|html|body)\b", re.I)
DARK_RE = re.compile(r"\.dark\b|\.dark-theme\b|\.theme-dark\b|\[data-(theme|mode|color-scheme)\s*=\s*[\"']?dark[\"']?\]", re.I)
DARK_MEDIA_RE = re.compile(r"prefers-color-scheme\s*:\s*dark", re.I)
NOT_RE = re.compile(r":not\([^)]*\)", re.I)
TRANSPARENT_AT = ("@layer", "@supports")  # wrappers that do not change which theme a rule belongs to
VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)")


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def declarations(css):
    """Yield (mode, name, value) for each custom property that sits in a light or dark scope."""
    stack, out, pos = [], [], 0
    css = strip_comments(css)
    for m in re.finditer(r"[{};]", css):
        chunk, pos = css[pos:m.start()], m.end()
        if m.group() == "{":
            stack.append(chunk.strip())
            continue
        raw = chunk.strip()
        if raw.startswith("--") and ":" in raw:
            name, value = raw.split(":", 1)
            value = re.sub(r"\s*!important\s*$", "", value.strip(), flags=re.I)
            mode = scope_mode(stack)
            if mode and value and len(value) <= MAX_VALUE:
                out.append((mode, name.strip(), value))
        if m.group() == "}" and stack:
            stack.pop()
    return out


def scope_mode(stack):
    """'light', 'dark' or None for the rule the declaration sits in."""
    if not stack:
        return None
    ats = [s for s in stack if s.startswith("@") and not s.lower().startswith(TRANSPARENT_AT)]
    selector = NOT_RE.sub("", stack[-1])  # `:root:not(.dark)` is the light scope
    if ats:
        if len(ats) == 1 and ats[0].lower().startswith("@media") and DARK_MEDIA_RE.search(ats[0]) \
                and not selector.startswith("@") and (ROOT_RE.search(selector) or DARK_RE.search(selector)):
            return "dark"
        return None
    if DARK_RE.search(selector):
        return "dark"
    if ROOT_RE.search(selector):
        return "light"
    return None


def resolve(value, theme, light):
    """Replace var(--x) with the theme's value (light as the fallback). Unresolved ones stay as they are."""
    def sub(m):
        found = theme.get(m.group(1), light.get(m.group(1)))
        if found is None:
            return m.group(2).strip() if m.group(2) else m.group(0)
        return found

    start = value
    for _ in range(8):
        new = VAR_RE.sub(sub, value)
        if new == value:
            break
        if len(new) > MAX_VALUE:  # self-referencing values grow exponentially
            return start
        value = new
    return value


def group_of(name, value):
    bare = name[2:].lower()
    for group, prefixes in PREFIXES:
        if bare.startswith(prefixes):
            return group
    return "colors" if COLOR_RE.match(value) else None


def css_files(project):
    found = []
    for base, dirs, files in os.walk(project, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(files):
            path = os.path.join(base, name)
            if name.lower().endswith(".css") and not os.path.islink(path):
                found.append(path)
    return found[:MAX_FILES]


def read_tokens(project):
    raw = {"light": {}, "dark": {}}
    owner = {}
    for path in css_files(project):
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                css = f.read()
        except OSError:
            continue
        rel = os.path.relpath(path, project).replace(os.sep, "/")
        for mode, name, value in declarations(css):
            raw[mode][name] = value
            owner.setdefault(name, set()).add(rel)
    result = {g: {} for g in GROUPS}
    sources = set()
    # CSS inherits a light-only declaration into dark, so its var() refs resolve against the dark values.
    in_dark = {**raw["light"], **raw["dark"]}
    for name in sorted(set(raw["light"]) | set(raw["dark"])):
        light = resolve(raw["light"][name], raw["light"], raw["light"]) if name in raw["light"] else None
        dark = resolve(in_dark[name], in_dark, raw["light"])
        group = group_of(name, light if light is not None else dark)
        if group is None:
            continue
        result[group][name] = {"light": light, "dark": dark}
        sources |= owner[name]
    result["sources"] = sorted(sources)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["read"])
    ap.add_argument("--project", default=".")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.project):
        print("Stopped: %s is not a folder." % args.project, file=sys.stderr)
        return 1
    print(json.dumps(read_tokens(args.project), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
