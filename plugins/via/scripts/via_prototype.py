#!/usr/bin/env python3
"""via-prototype: builds the before/after prototype page for a Via run.

Claude picks the findings worth showing (visual ones, highest severity first, at most 5) and writes
RUN_DIR/prototype.json. This tool checks it and renders one self-contained RUN_DIR/prototype.html: for each finding
the real "before" screenshot beside 2 or 3 "after" variants, a variant switcher, a spec panel and a light/dark toggle.
The variants are HTML/CSS mockups, not real renders. The page has no script and loads nothing from the network.

Usage: via_prototype.py build RUN_DIR [--project DIR] [--config FILE]
       via_prototype.py link  RUN_DIR --url URL [--project DIR] [--config FILE]

Order: run `via_run.py merge` first (the findings need their IDs), then `build`. If the project may publish to
artifacts, publish prototype.html as a private Artifact and record its link with `link`. Run `merge` again so the
ledger picks up the anchors (it is safe to repeat).

prototype.json:
  {"version": 1, "findings": [
    {"id": "VIA-home-1a2b3c4d",
     "variants": [{"label": "Larger button", "rationale": "Why this fixes it.",
                   "html": "<div style=\\"padding: var(--space-6)\\">...</div>",
                   "states": {"default": "...", "pressed": "...", "disabled": "...", "empty": "..."}}]}]}
`states` is optional; each state you leave out shows as "not specified".

Variant HTML is rewritten from a strict allow-list (layout and text tags; class, style, role, title, aria-* and
disabled attributes). Scripts, links, images, forms, url(), @rules, backslashes in styles, position fixed or sticky
and custom properties are refused. Use var(--token) for every color, radius, spacing and type value: the spec panel
lists the tokens a variant uses with their light and dark values read from the project (via_tokens.py), and a name
with no token shows "no token". Literal values are not in the panel; build warns about them.

Only screenshots reviewed as `kept` go in the page. Stdout is JSON. Exit 0 on success, 1 when the input is
invalid or unsafe (nothing is written), 2 on a secret-shaped value.
"""
import argparse
import base64
import html
import json
import os
import re
import sys
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import via_outbound  # noqa: E402
import via_run  # noqa: E402
import via_tokens  # noqa: E402

PROTO_FILE = "prototype.json"
PAGE_FILE = "prototype.html"
MAX_FINDINGS = 5
MAX_JSON = 1_000_000
MAX_SHOT = 4_000_000
MAX_PAGE_SHOTS = 12_000_000
MAX_FRAGMENT = 20_000
STATES = ("default", "pressed", "disabled", "empty")

TAGS = {"div", "span", "p", "h3", "h4", "h5", "h6", "button", "ul", "ol", "li", "strong", "em", "small",
        "label", "section", "header", "footer", "nav", "article", "table", "thead", "tbody", "tr", "th", "td",
        "b", "i", "br", "hr"}
VOID = {"br", "hr"}
HEADINGS = {"h3", "h4", "h5", "h6"}
# A browser closes some elements by itself when these nest. The page would then be built differently from the
# tag stack checked here, and a closing tag could end the page's own wrappers. These shapes are refused.
CLOSES_P = {"div", "p", "ul", "ol", "li", "table", "hr", "section", "header", "footer", "nav", "article"} | HEADINGS
TABLE_PARENT = {"thead": ("table",), "tbody": ("table",), "tr": ("thead", "tbody"), "th": ("tr",), "td": ("tr",)}
TABLE_PARTS = {"table", "thead", "tbody", "tr"}
ATTRS = {"class", "style", "role", "title", "aria-label", "aria-hidden", "aria-disabled", "aria-pressed",
         "aria-selected", "aria-current", "disabled"}
CLASS_RE = re.compile(r"[A-Za-z0-9_-]+")
BAD_STYLE = re.compile(r"url\s*\(|image-set|expression|javascript|behavior|binding|@|\\|/\*|<|>", re.I)
BAD_POSITION = re.compile(r"\b(fixed|sticky)\b", re.I)
SAFE_VALUE = re.compile(r"[#\w\s.,%()+/\-]*")
SAFE_NAME = re.compile(r"--[A-Za-z0-9_-]+")
LITERAL_COLOR = re.compile(r"#[0-9a-f]{3,8}\b|\b(rgb|rgba|hsl|hsla|oklch|oklab)\(", re.I)
LITERAL_SIZE = re.compile(r"\d\s*(px|rem|em|pt|%)", re.I)
SIZE_PROPS = {"font-size", "border-radius", "gap", "row-gap", "column-gap"}
SPACING_PREFIXES = ("padding", "margin")
GROUP_TITLES = (("colors", "Color"), ("type_size", "Type size"), ("type_weight", "Type weight"),
                ("radius", "Radius"), ("spacing", "Spacing"))


class Problem(Exception):
    """Invalid input. Nothing is written."""


# --- variant HTML: rewritten from an allow-list, never passed through ---

class Fragment(HTMLParser):
    def __init__(self, where):
        super().__init__(convert_charrefs=True)
        self.where, self.out, self.stack, self.vars, self.warnings = where, [], [], set(), []

    def fail(self, why):
        raise Problem("%s: %s" % (self.where, why))

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self.fail("<%s> is not allowed. Use layout and text tags only." % tag)
        self.check_nesting(tag)
        parts = []
        seen = set()
        for name, value in attrs:
            name = name.lower()
            if name not in ATTRS or name in seen:
                self.fail("the %s attribute is not allowed on <%s>." % (name, tag))
            seen.add(name)
            if name == "disabled":
                if value not in (None, "", "disabled"):
                    self.fail("disabled takes no value.")
                parts.append(" disabled")
                continue
            value = value or ""
            if name == "class":
                for token in value.split():
                    if not CLASS_RE.fullmatch(token) or token.startswith("vp-"):
                        self.fail("class \"%s\" is not allowed (vp- is reserved for the page)." % token)
            if name == "style":
                value = self.style(value)
            parts.append(' %s="%s"' % (name, html.escape(value, quote=True)))
        self.out.append("<%s%s>" % (tag, "".join(parts)))
        if tag not in VOID:
            self.stack.append(tag)

    def check_nesting(self, tag):
        parent = self.stack[-1] if self.stack else None
        if tag in TABLE_PARENT and parent not in TABLE_PARENT[tag]:
            self.fail("<%s> must sit directly inside <%s>." % (tag, "> or <".join(TABLE_PARENT[tag])))
        if parent in TABLE_PARTS and tag not in TABLE_PARENT:
            self.fail("<%s> cannot sit directly inside <%s>. Put it in a <td> or <th>." % (tag, parent))
        if tag == "button" and "button" in self.stack:
            self.fail("a <button> cannot sit inside another <button>.")
        if tag in CLOSES_P and "p" in self.stack:
            self.fail("<%s> cannot sit inside a <p>: browsers end the paragraph first. Use a <div>." % tag)
        if tag in HEADINGS and parent in HEADINGS:
            self.fail("a heading cannot sit directly inside another heading.")
        if tag == "li":
            for open_tag in reversed(self.stack):
                if open_tag in ("ul", "ol"):
                    break
                if open_tag == "li":
                    self.fail("an <li> cannot sit inside another <li> without a <ul> or <ol> between them.")

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.fail("</%s> does not match the open tags." % tag)
        self.stack.pop()
        self.out.append("</%s>" % tag)

    def handle_data(self, data):
        if data.strip() and self.stack and self.stack[-1] in TABLE_PARTS:
            self.fail("text cannot sit directly inside <%s>. Put it in a <td> or <th>." % self.stack[-1])
        self.out.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        self.fail("comments are not allowed.")

    def refuse(self, data):
        self.fail("declarations and processing instructions are not allowed.")

    handle_decl = handle_pi = unknown_decl = refuse

    def style(self, value):
        if not balanced(value):
            self.fail("this style value has unbalanced parentheses.")
        if BAD_STYLE.search(value):
            self.fail("this style value is not allowed (no url(), @rules, backslashes or comments).")
        for decl in re.split(r";(?![^(]*\))", value):
            if not decl.strip():
                continue
            if ":" not in decl:
                self.fail("style \"%s\" has no value." % decl.strip())
            prop, val = (x.strip() for x in decl.split(":", 1))
            prop = prop.lower()
            if not re.fullmatch(r"[a-z-]+", prop) or prop.startswith("--"):
                self.fail("style property \"%s\" is not allowed (use var(--token), do not define custom properties)." % prop)
            if prop == "position" and BAD_POSITION.search(val):
                self.fail("position %s is not allowed." % val)
            self.vars.update(m.group(1) for m in via_tokens.VAR_RE.finditer(val))
            bare = via_tokens.VAR_RE.sub("", val)
            literal = None
            if prop in ("color", "background", "background-color", "border", "border-color", "fill", "outline"):
                literal = LITERAL_COLOR.search(bare)
            elif prop in SIZE_PROPS or prop.startswith(SPACING_PREFIXES):
                literal = LITERAL_SIZE.search(bare)
            elif prop == "font-weight":
                literal = re.search(r"\b[1-9]00\b|\bbold\b", bare)
            if literal:
                self.warnings.append("%s: %s is a literal value, not a token (%s)." % (self.where, prop, val))
        return value


def clean_fragment(source, where):
    if len(source) > MAX_FRAGMENT:
        raise Problem("%s: the HTML is over %d characters." % (where, MAX_FRAGMENT))
    parser = Fragment(where)
    parser.feed(source)
    parser.close()
    if parser.stack:
        raise Problem("%s: <%s> is never closed." % (where, parser.stack[-1]))
    return "".join(parser.out), parser.vars, parser.warnings


# --- prototype.json ---

def text(value, limit):
    return via_run.is_text(value) and len(value) <= limit


def check_prototype_json(data):
    """Returns the findings list, or raises Problem."""
    if not isinstance(data, dict) or data.get("version") != 1 or set(data) - {"version", "findings"}:
        raise Problem("%s must be an object with version 1 and findings." % PROTO_FILE)
    entries = data.get("findings")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FINDINGS:
        raise Problem("findings must list 1 to %d findings." % MAX_FINDINGS)
    ids = set()
    for i, e in enumerate(entries):
        where = "findings[%d]" % i
        if not isinstance(e, dict) or set(e) - {"id", "variants"} or not text(e.get("id"), 200):
            raise Problem("%s needs an id and variants, nothing else." % where)
        if e["id"] in ids:
            raise Problem("%s repeats the id %s." % (where, e["id"]))
        ids.add(e["id"])
        variants = e.get("variants")
        if not isinstance(variants, list) or not 2 <= len(variants) <= 3:
            raise Problem("%s.variants must list 2 or 3 variants." % where)
        for j, v in enumerate(variants):
            vw = "%s.variants[%d]" % (where, j)
            if not isinstance(v, dict) or set(v) - {"label", "rationale", "html", "states"}:
                raise Problem("%s may only have label, rationale, html and states." % vw)
            if not (text(v.get("label"), 80) and text(v.get("rationale"), 400) and text(v.get("html"), MAX_FRAGMENT)):
                raise Problem("%s needs a label (80 characters), a rationale (400) and html." % vw)
            states = v.get("states", {})
            if not isinstance(states, dict) or set(states) - set(STATES) or not all(text(x, 200) for x in states.values()):
                raise Problem("%s.states may only hold %s, each as short text." % (vw, ", ".join(STATES)))
    return entries


# --- screenshots ---

def image_kind(head):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def data_uri(run_dir, shot):
    path = os.path.join(os.path.realpath(run_dir), shot["path"])
    if os.path.islink(path) or not via_run.inside(path, run_dir) or not os.path.isfile(path):
        raise Problem("%s is not a regular file inside the run folder." % shot["path"])
    if os.path.getsize(path) > MAX_SHOT:
        raise Problem("%s is over %d bytes. Capture it smaller." % (shot["path"], MAX_SHOT))
    with open(path, "rb") as f:
        raw = f.read()
    kind = image_kind(raw[:8])
    if not kind:
        raise Problem("%s is not a PNG or JPEG." % shot["path"])
    return "data:%s;base64,%s" % (kind, base64.b64encode(raw).decode("ascii")), len(raw)


def before_images(rec, run_dir, finding, budget):
    """{'light': uri, 'dark': uri} from the kept screenshots of the finding's screen."""
    want = via_run.slug(finding["screen"])
    found = {}
    kept = [s for s in rec.get("screenshots", []) if s.get("review") == "kept" and via_run.slug(s["screen"]) == want]
    def plainness(s):
        """A phone first (the frame is a phone), then default state, default font and left to right."""
        phone = "phone" in s["size"].lower() or s["size"].startswith("390")
        return (not phone, s.get("state", "default") != "default", s.get("font_scale", "default") != "default",
                s.get("direction", "ltr") != "ltr")
    for shot in sorted(kept, key=plainness):
        found.setdefault(shot["theme"].lower(), shot)
    if not found:
        raise Problem("%s has no kept screenshot for its screen \"%s\". Capture it and set review to kept."
                      % (finding["id"], finding["screen"]))
    uris = {}
    for theme, shot in found.items():
        uris[theme], size = data_uri(run_dir, shot)
        budget[0] += size
        if budget[0] > MAX_PAGE_SHOTS:
            raise Problem("the screenshots add up to over %d bytes. Show fewer findings." % MAX_PAGE_SHOTS)
    fallback = next(iter(uris.values()))
    return {"light": uris.get("light", fallback), "dark": uris.get("dark", fallback), "themes": sorted(uris)}


# --- tokens ---

def balanced(value):
    """True when every ( has its ), so a value cannot swallow the CSS rules after it."""
    depth = 0
    for ch in value:
        depth += (ch == "(") - (ch == ")")
        if depth < 0:
            return False
    return depth == 0


def safe_tokens(tokens):
    """Tokens whose name and values can go into a <style> block as they are."""
    keep = {}
    for group in via_tokens.GROUPS:
        keep[group] = {}
        for name, v in tokens[group].items():
            vals = [x for x in (v["light"], v["dark"]) if x is not None]
            if SAFE_NAME.fullmatch(name) and all(SAFE_VALUE.fullmatch(x) and balanced(x) and "url(" not in x.lower() for x in vals):
                keep[group][name] = v
    return keep


def scope_css(tokens, theme):
    rules = []
    for group in via_tokens.GROUPS:
        for name, v in sorted(tokens[group].items()):
            value = v[theme] if v[theme] is not None else v["light"]
            if value is not None:
                rules.append("%s:%s" % (name, value))
    return ";".join(rules)


# --- page ---

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f4f4f1;color:#1d1d1b;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.vp-root{max-width:1280px;margin:0 auto;padding:24px}
.vp-top{display:flex;flex-wrap:wrap;gap:12px 24px;align-items:center;justify-content:space-between;margin-bottom:24px}
h1{font-size:1.25rem;font-weight:600;margin:0}
.vp-meta{margin:4px 0 0;color:#55554f;font-size:.875rem}
.vp-seg{display:inline-flex;border:1px solid #8b8b84;border-radius:8px;overflow:hidden;position:relative}
.vp-seg input{position:absolute;opacity:0;width:1px;height:1px}
.vp-seg label{display:inline-flex;align-items:center;min-height:44px;padding:0 16px;cursor:pointer;font-size:.875rem;background:#fff}
.vp-seg input:checked+label{background:#1d1d1b;color:#fff}
.vp-seg input:focus-visible+label{outline:2px solid #3f51b5;outline-offset:-2px}
.vp-nav{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 24px;padding:0;list-style:none}
.vp-nav a,.vp-tab{display:inline-flex;align-items:center;min-height:44px;padding:0 16px;border:1px solid #8b8b84;border-radius:8px;background:#fff;color:#1d1d1b;text-decoration:none;font-size:.875rem}
.vp-nav a:hover,.vp-tab:hover{border-color:#1d1d1b}
a:focus-visible{outline:2px solid #3f51b5;outline-offset:2px}
.vp-finding{padding:24px 0;border-top:1px solid #d6d6cf;scroll-margin-top:16px}
h2{font-size:1.125rem;font-weight:600;margin:0 0 4px}
.vp-sev{display:inline-block;margin-right:8px;padding:0 8px;border-radius:8px;background:#e6e6df;font-size:.75rem;font-weight:600}
.vp-id{margin:0 0 4px;color:#55554f;font-size:.8125rem}
.vp-issue{margin:0 0 16px;color:#33332f;max-width:70ch}
.vp-cols{display:grid;grid-template-columns:minmax(0,390px) minmax(0,1fr);gap:24px;align-items:start}
.vp-cap{margin:0 0 8px;font-size:.875rem;font-weight:600}
.vp-note{margin:8px 0 0;color:#55554f;font-size:.8125rem}
.vp-frame{width:100%;max-width:390px;aspect-ratio:390/844;border:1px solid #8b8b84;border-radius:24px;overflow:hidden;contain:layout paint;background:var(--surface,#fff);color:var(--on-surface,#111)}
.vp-before{background:#e9e9e4}
.vp-before img{display:block;width:100%;height:100%;object-fit:contain;object-position:top}
.vp-scope{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:var(--font-size-body,16px)}
.vp-fragment{height:100%;overflow:auto}
.vp-tabs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.vp-variant{display:none;grid-template-columns:minmax(0,390px) minmax(240px,1fr);gap:24px;align-items:start;scroll-margin-top:16px}
.vp-variant:first-of-type{display:grid}
.vp-finding:has(.vp-variant:target) .vp-variant:first-of-type:not(:target){display:none}
.vp-variant:target{display:grid}
.vp-why{margin:8px 0 0;font-size:.875rem;max-width:44ch}
.vp-spec{background:#fff;border:1px solid #d6d6cf;border-radius:8px;padding:16px;font-size:.875rem}
.vp-spec h3{margin:0 0 8px;font-size:.875rem;font-weight:600}
.vp-spec table{width:100%;border-collapse:collapse;margin-bottom:16px}
.vp-spec caption{text-align:left;font-weight:600;padding-bottom:4px}
.vp-spec th,.vp-spec td{text-align:left;padding:4px 8px 4px 0;vertical-align:top;font-weight:400}
.vp-spec th{color:#55554f;word-break:break-all}
.vp-sw{display:inline-block;width:12px;height:12px;margin-right:4px;border:1px solid #8b8b84;border-radius:4px;vertical-align:-1px}
.vp-none{color:#8a3b00}
.vp-spec dl{margin:0}
.vp-spec dt{font-weight:600}
.vp-spec dd{margin:0 0 8px;color:#33332f}
.vp-shot-dark{display:none}
.vp-root:has(#vp-dark:checked) .vp-shot-light{display:none}
.vp-root:has(#vp-dark:checked) .vp-shot-dark{display:block}
@media (min-width:1101px){.vp-before{margin-top:64px}}
@media (max-width:1100px){.vp-cols,.vp-variant{grid-template-columns:minmax(0,1fr)}}
@media (max-width:480px){.vp-root{padding:16px}}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def spec_panel(used, tokens, states, label):
    out = ['<aside class="vp-spec" aria-label="Spec for %s"><h3>Spec</h3>' % esc(label)]
    by_group = {g: [] for g, _ in GROUP_TITLES}
    unknown = []
    for name in sorted(used):
        for g, _ in GROUP_TITLES:
            if name in tokens[g]:
                by_group[g].append(name)
                break
        else:
            unknown.append(name)
    for g, title in GROUP_TITLES:
        if not by_group[g]:
            continue
        out.append('<table><caption>%s</caption><tr><th scope="col">Token</th><th scope="col">Light</th><th scope="col">Dark</th></tr>' % title)
        for name in by_group[g]:
            cells = []
            for theme in ("light", "dark"):
                v = tokens[g][name][theme]
                swatch = '<span class="vp-sw" style="background:%s"></span>' % esc(v) if g == "colors" and v else ""
                cells.append("<td>%s%s</td>" % (swatch, esc(v) if v is not None else '<span class="vp-none">no token</span>'))
            out.append("<tr><th scope=\"row\">%s</th>%s</tr>" % (esc(name), "".join(cells)))
        out.append("</table>")
    if unknown:
        out.append('<table><caption>No token</caption>%s</table>' % "".join(
            '<tr><th scope="row">%s</th><td class="vp-none">no token in this project</td></tr>' % esc(n) for n in unknown))
    if not used:
        out.append('<p class="vp-none">This variant uses no theme tokens.</p>')
    out.append("<h3>States</h3><dl>")
    for state in STATES:
        out.append("<dt>%s</dt><dd>%s</dd>" % (state.capitalize(), esc(states[state]) if state in states else "not specified"))
    out.append("</dl></aside>")
    return "".join(out)


def render(run_name, blocks, tokens):
    nav = "".join('<li><a href="#%s">%s</a></li>' % (b["anchor"], esc(b["title"])) for b in blocks)
    tab_css, sections = [], []
    for b in blocks:
        first = b["variants"][0]["anchor"]
        tab_css.append('#%s:not(:has(.vp-variant:target)) a[href="#%s"]' % (b["anchor"], first))
        tabs, variants = [], []
        for v in b["variants"]:
            tab_css.append('#%s:has(#%s:target) a[href="#%s"]' % (b["anchor"], v["anchor"], v["anchor"]))
            tabs.append('<a class="vp-tab" href="#%s">%s</a>' % (v["anchor"], esc(v["label"])))
            variants.append(
                '<article class="vp-variant" id="%s" aria-label="%s"><div><div class="vp-frame vp-scope">'
                '<div class="vp-fragment" inert>%s</div></div><p class="vp-why">%s</p></div>%s</article>'
                % (v["anchor"], esc(v["label"]), v["html"], esc(v["rationale"]), v["spec"]))
        img = b["images"]
        alt = "Screenshot of %s" % b["screen"]
        if img["light"] == img["dark"]:
            before = '<img src="%s" alt="%s">' % (img["light"], esc(alt))
            note = '<p class="vp-note">Captured in the %s theme only.</p>' % esc(img["themes"][0])
        else:
            before = ('<img class="vp-shot-light" src="%s" alt="%s, light theme">'
                      '<img class="vp-shot-dark" src="%s" alt="%s, dark theme">'
                      % (img["light"], esc(alt), img["dark"], esc(alt)))
            note = ""
        sections.append(
            '<section class="vp-finding" id="%s" aria-labelledby="%s-h"><h2 id="%s-h"><span class="vp-sev">%s</span>%s</h2>'
            '<p class="vp-id">%s</p><p class="vp-issue">%s</p><div class="vp-cols"><figure style="margin:0"><figcaption class="vp-cap">Before</figcaption>'
            '<div class="vp-frame vp-before">%s</div>%s</figure><div><p class="vp-cap">After: pick a variant</p>'
            '<div class="vp-tabs">%s</div>%s</div></div></section>'
            % (b["anchor"], b["anchor"], b["anchor"], esc(b["severity"]), esc(b["title"]), esc(b["id"]), esc(b["issue"]),
               before, note, "".join(tabs), "".join(variants)))
    active = "%s{background:#1d1d1b;color:#fff;border-color:#1d1d1b}" % ",".join(tab_css)
    css = (CSS + ".vp-scope{%s}.vp-root:has(#vp-dark:checked) .vp-scope{%s}" % (scope_css(tokens, "light"), scope_css(tokens, "dark"))
           + active)
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data:; style-src \'unsafe-inline\'">'
        '<title>Via prototypes: %s</title><style>%s</style></head><body><div class="vp-root">'
        '<header class="vp-top"><div><h1>Via prototypes</h1><p class="vp-meta">Run %s. %d finding%s. '
        'The variants are mockups drawn from the project\'s theme tokens, not real renders.</p></div>'
        '<div class="vp-seg" role="radiogroup" aria-label="Theme"><input type="radio" name="vp-theme" id="vp-light" checked>'
        '<label for="vp-light">Light</label><input type="radio" name="vp-theme" id="vp-dark"><label for="vp-dark">Dark</label></div>'
        '</header><ul class="vp-nav" aria-label="Findings">%s</ul><main>%s</main></div></body></html>\n'
        % (esc(run_name), css, esc(run_name), len(blocks), "" if len(blocks) == 1 else "s", nav, "".join(sections)))


# --- commands ---

def load_prototype_json(run_dir):
    path = os.path.join(run_dir, PROTO_FILE)
    if os.path.islink(path) or not os.path.isfile(path):
        raise Problem("%s has no %s (or it is a symlink)." % (os.path.basename(run_dir), PROTO_FILE))
    if os.path.getsize(path) > MAX_JSON:
        raise Problem("%s is over %d bytes." % (PROTO_FILE, MAX_JSON))
    raw = via_run.read_text(path)
    if via_run.has_secret(raw):
        via_run.die("%s holds a secret-shaped value. Remove it; nothing was written." % PROTO_FILE, 2)
    try:
        data = json.loads(raw)
    except ValueError:
        raise Problem("%s is not valid JSON." % PROTO_FILE)
    if via_run.has_secret(json.dumps(data, ensure_ascii=False)):
        via_run.die("%s holds a secret-shaped value. Remove it; nothing was written." % PROTO_FILE, 2)
    return data


def publish_decision(project, config):
    """(allowed, reason). A malformed config fails closed: the page stays local."""
    try:
        code, result = via_outbound.decide(project, "artifacts", config)
    except via_outbound.Bad as e:
        return False, "%s; save the prototype as a local HTML file in the run folder" % e
    return code == via_outbound.ALLOWED, result["reason"]


def save_record(record_path, rec):
    """Validate, then write the run record. A caller cannot skip the check."""
    problem = via_run.check_record(rec)
    if problem:
        raise Problem(problem)
    via_run.write_atomic(record_path, json.dumps(rec, indent=2, ensure_ascii=False) + "\n")


def cmd_build(project, args):
    run_dir, record_path = via_run.resolve_run(project, args.run_dir)
    rec = via_run.load_record(record_path)
    if not rec.get("merged_at"):
        raise Problem("run `via_run.py merge` first: the findings need their IDs.")
    entries = check_prototype_json(load_prototype_json(run_dir))
    live = {f["id"]: f for f in rec["findings"] if f.get("id")}
    tokens = safe_tokens(via_tokens.read_tokens(project))
    warnings = []
    if not any(tokens[g] for g in via_tokens.GROUPS):
        warnings.append("No theme tokens found in the project's CSS, so every spec value shows \"no token\".")
    budget, blocks = [0], []
    for n, e in enumerate(entries, 1):
        finding = live.get(e["id"])
        if finding is None:
            raise Problem("%s is not a finding in this run (dismissed findings have no id)." % e["id"])
        anchor = "f%d" % n
        block = {"anchor": anchor, "title": "%s \u00b7 %s" % (finding["screen"], finding["rule"]), "id": finding["id"], "screen": finding["screen"],
                 "severity": finding["severity"], "issue": finding["issue"],
                 "images": before_images(rec, run_dir, finding, budget), "variants": []}
        for m, v in enumerate(e["variants"], 1):
            where = "%s variant %d" % (finding["id"], m)
            body, used, warns = clean_fragment(v["html"], where)
            warnings += warns
            block["variants"].append({"anchor": "%s-v%d" % (anchor, m), "label": v["label"], "rationale": v["rationale"],
                                      "html": body, "spec": spec_panel(used, tokens, v.get("states", {}), v["label"])})
        blocks.append((block, finding))
    page = render(os.path.basename(run_dir), [b for b, _ in blocks], tokens)
    allowed, reason = publish_decision(project, args.config)
    for f in rec["findings"]:
        f.pop("prototype", None)  # a rebuild replaces every link, so a finding no longer shown loses its anchors
    for block, finding in blocks:
        finding["prototype"] = {"anchor": block["anchor"], "variants": [v["anchor"] for v in block["variants"]]}
    rec["prototype"] = {"path": PAGE_FILE, "published": False, "url": None, "reason": reason}
    problem = via_run.check_record(rec)  # before the page is written, so a refused build leaves nothing behind
    if problem:
        raise Problem(problem)
    via_run.write_atomic(os.path.join(run_dir, PAGE_FILE), page)
    save_record(record_path, rec)
    via_run.emit({"path": os.path.join(run_dir, PAGE_FILE), "publish_allowed": allowed, "reason": reason,
                  "findings": [{"id": f["id"], **f["prototype"]} for _, f in blocks], "warnings": warnings})


def cmd_link(project, args):
    run_dir, record_path = via_run.resolve_run(project, args.run_dir)
    rec = via_run.load_record(record_path)
    if not isinstance(rec.get("prototype"), dict):
        raise Problem("this run has no prototype yet. Run `build` first.")
    if not os.path.isfile(os.path.join(run_dir, rec["prototype"]["path"])):
        raise Problem("%s is missing. Run `build` again." % rec["prototype"]["path"])
    allowed, reason = publish_decision(project, args.config)
    if not allowed:
        raise Problem("this project may not publish to artifacts (%s). The page stays local." % reason)
    url = args.url.strip()
    if via_run.has_secret(url):
        raise Problem("the link looks like it holds a secret.")
    rec["prototype"].update({"url": url, "published": True, "reason": reason})
    save_record(record_path, rec)  # check_record refuses anything but an https URL without spaces
    via_run.emit({"url": url, "published": True})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("build", "link"):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.add_argument("--project")
        p.add_argument("--config")
        if name == "link":
            p.add_argument("--url", required=True)
    args = ap.parse_args(argv)
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        via_run.die("%s is not a folder." % project)
    try:
        {"build": cmd_build, "link": cmd_link}[args.command](project, args)
    except Problem as e:
        via_run.die("%s Nothing was written." % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
