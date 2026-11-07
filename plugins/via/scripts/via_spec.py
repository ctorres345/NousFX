#!/usr/bin/env python3
"""via-spec: writes a ticket's visual spec into a Via run folder before any code is written (/via spec).

Usage: via_spec.py build RUN_DIR [--project DIR] [--config FILE]
       via_spec.py link  RUN_DIR --url URL [--project DIR] [--config FILE]
       via_spec.py compare RUN_DIR [--theme FILE] [--project DIR]

Claude reads the ticket and the theme tokens and writes RUN_DIR/spec.json. `build` checks it, resolves each token
row to the project's light and dark values (via_tokens.py), and writes:
  direction.md      the direction note
  spec.md           the spec table (screen, element, property, token, light, dark, note) and the states
  target.html       the target mock: one 390x844 phone frame per screen, with the spec panel and a light/dark toggle
  ticket-update.md  the text Via proposes for the ticket: prototype-fidelity criteria plus the concrete values
It also writes the resolved values back into spec.json (`light`, `dark` per row, `built_at`, `built_hash`,
`outputs`), so /via check can compare a built screen against what was specified. A rebuild recomputes them and drops
`target`. `link` records the published mock (`target: {url, published}`) and rewrites ticket-update.md with the link;
it refuses when spec.json changed since the build (`built_hash`).
`compare` (for /via check) reads the theme as it is now (web: the project's CSS; Compose and Flutter: --theme, a JSON
file {"source": "<theme file>", "theme": {...}} shaped like spec.json theme) and prints, as run-record findings with
source "spec", every token row whose light or dark value no longer matches the built spec. Literal rows are counted,
not compared.

spec.json:
  {"version": 1, "mode": "spec",
   "ticket": {"key": "APP-7", "title": "...", "source": "jira" | "markdown", "path": "tickets/APP-7.md"},
   "platform": "web" | "compose" | "flutter",
   "theme": {"--primary": {"light": "#3f51b5", "dark": "#9fa8da"}},   compose and flutter only, see below
   "direction": "The direction note, up to 2000 characters.",
   "screens": [{"screen": "home", "label": "...", "rationale": "...", "html": "<div ...>...</div>",
                "states": {"default": "...", "pressed": "...", "disabled": "...", "empty": "..."},
                "rows": [{"element": "Start button", "property": "background", "token": "--primary"},
                         {"element": "Start button", "property": "min height", "value": "48px", "note": "..."},
                         {"element": "Card", "property": "border", "value": "#dddddd", "value_dark": "#333333"}]}]}
`path` is required for a markdown ticket and refused otherwise. 1 to 5 screens, 1 to 40 rows each; a row has a
token or a literal value, not both; `value_dark` gives a literal row its dark value (default: `value`).
On web the tokens come from the project's CSS. Compose and Flutter have none, so a native spec declares the app's
theme values under `theme`, as --names, and the table and the mock use them like CSS tokens. The mock HTML follows the prototype page's allow-list (via_prototype.py).

Stdout is JSON. Exit 0 on success, 1 when the input is invalid or unsafe (nothing is written), 2 on a
secret-shaped value.
"""
import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import via_prototype as proto  # noqa: E402
import via_run  # noqa: E402
import via_tokens  # noqa: E402

SPEC_FILE = "spec.json"
OUTPUTS = (("direction", "direction.md"), ("spec", "spec.md"), ("target", "target.html"),
           ("ticket_update", "ticket-update.md"))


MAX_SCREENS = 5
MAX_ROWS = 40
PLATFORMS = {"web", "compose", "flutter"}
SOURCES = {"jira", "markdown"}
TOP_KEYS = {"version", "mode", "ticket", "platform", "direction", "screens", "built_at", "built_hash", "outputs", "target", "theme"}
SCREEN_KEYS = {"screen", "label", "rationale", "html", "states", "rows"}
ROW_KEYS = {"element", "property", "token", "value", "value_dark", "note", "light", "dark"}
DERIVED = {"built_at", "built_hash", "outputs", "target"}  # written by build and link, never by Claude
MAX_THEME = 80
NATIVE = {"compose", "flutter"}
# The rows table is wider than the prototype's token panel: keep words whole and scroll it on a narrow screen.
PAGE_CSS = ".vs-scroll{overflow-x:auto}.vs-scroll table{min-width:520px}.vp-spec .vs-scroll th{word-break:normal}"
Problem = proto.Problem  # invalid input; nothing is written


def check_theme(theme):
    if not isinstance(theme, dict) or not 1 <= len(theme) <= MAX_THEME:
        raise Problem("theme must map 1 to %d token names to their light and dark values." % MAX_THEME)
    for name, v in theme.items():
        if not proto.SAFE_NAME.fullmatch(name) or not isinstance(v, dict) or set(v) != {"light", "dark"} or not all(
                proto.text(x, 80) and proto.safe_value(x) for x in v.values()):
            raise Problem("theme.%s needs a --name and plain CSS light and dark values (80 characters)." % name)


def check_spec(spec, project):
    """Raises Problem when spec.json is not the shape below. See the module docstring."""
    if not isinstance(spec, dict) or spec.get("version") != 1 or spec.get("mode") != "spec":
        raise Problem("%s must be an object with version 1 and mode \"spec\"." % SPEC_FILE)
    extra = set(spec) - TOP_KEYS
    if extra:
        raise Problem("%s has unknown keys: %s." % (SPEC_FILE, ", ".join(sorted(extra))))
    t = spec.get("ticket")
    if not isinstance(t, dict) or set(t) - {"key", "title", "source", "path"} or not (
            proto.text(t.get("key"), 40) and proto.text(t.get("title"), 200)):
        raise Problem("ticket needs a key (40 characters) and a title (200), and nothing but source and path.")
    if t.get("source") not in SOURCES:
        raise Problem("ticket.source must be one of %s." % ", ".join(sorted(SOURCES)))
    if t["source"] == "markdown":
        path = t.get("path")
        full = os.path.join(project, path) if proto.text(path, 500) and not os.path.isabs(path) else None
        if not full or not via_run.inside(full, project) or os.path.islink(full) or not os.path.isfile(full):
            raise Problem("a markdown ticket needs path: the ticket file, relative to the project and inside it.")
    elif "path" in t:
        raise Problem("ticket.path is only for a markdown ticket.")
    if spec.get("platform") not in PLATFORMS:
        raise Problem("platform must be one of %s." % ", ".join(sorted(PLATFORMS)))
    theme = spec.get("theme")
    if theme is not None:
        if spec["platform"] not in NATIVE:
            raise Problem("theme is only for %s; on web the tokens come from the project's CSS." % " and ".join(sorted(NATIVE)))
        check_theme(theme)
    if not proto.text(spec.get("direction"), 2000):
        raise Problem("direction needs the direction note (up to 2000 characters).")
    screens = spec.get("screens")
    if not isinstance(screens, list) or not 1 <= len(screens) <= MAX_SCREENS:
        raise Problem("screens must list 1 to %d screens." % MAX_SCREENS)
    names = set()
    for i, s in enumerate(screens):
        where = "screens[%d]" % i
        if not isinstance(s, dict) or set(s) - SCREEN_KEYS:
            raise Problem("%s may only have %s." % (where, ", ".join(sorted(SCREEN_KEYS))))
        if not (proto.text(s.get("screen"), 60) and proto.text(s.get("label"), 80) and proto.text(s.get("rationale"), 400)
                and proto.text(s.get("html"), proto.MAX_FRAGMENT)):
            raise Problem("%s needs a screen name (60 characters), a label (80), a rationale (400) and html." % where)
        if via_run.slug(s["screen"]) in names:
            raise Problem("%s repeats the screen %s." % (where, s["screen"]))
        names.add(via_run.slug(s["screen"]))
        states = s.get("states", {})
        if not isinstance(states, dict) or set(states) - set(proto.STATES) or not all(proto.text(x, 200) for x in states.values()):
            raise Problem("%s.states may only hold %s, each as short text." % (where, ", ".join(proto.STATES)))
        rows = s.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
            raise Problem("%s.rows must list 1 to %d spec rows." % (where, MAX_ROWS))
        for j, r in enumerate(rows):
            rw = "%s.rows[%d]" % (where, j)
            if not isinstance(r, dict) or set(r) - ROW_KEYS or not (proto.text(r.get("element"), 80) and proto.text(r.get("property"), 40)):
                raise Problem("%s needs an element (80 characters) and a property (40)." % rw)
            if ("token" in r) == ("value" in r):
                raise Problem("%s needs a token or a value, not both." % rw)
            if "token" in r and not (isinstance(r["token"], str) and proto.SAFE_NAME.fullmatch(r["token"])):
                raise Problem("%s.token must be a custom property name such as --primary." % rw)
            if "value" in r and not proto.text(r["value"], 80):
                raise Problem("%s.value must be short text (80 characters)." % rw)
            if "value_dark" in r and ("value" not in r or not proto.text(r["value_dark"], 80)):
                raise Problem("%s.value_dark goes only with a literal value, as short text (80 characters)." % rw)
            if "note" in r and not (isinstance(r["note"], str) and len(r["note"]) <= 200):
                raise Problem("%s.note must be text (200 characters)." % rw)


def resolve_run(project, run_arg):
    run_dir, path = via_run.resolve_run(project, run_arg, SPEC_FILE)
    if os.path.getsize(path) > proto.MAX_JSON:
        raise Problem("%s is over %d bytes." % (SPEC_FILE, proto.MAX_JSON))
    return run_dir, path


def load_spec(path, project):
    raw = via_run.read_text(path)
    if via_run.has_secret(raw):
        via_run.die("%s holds a secret-shaped value. Remove it; nothing was written." % SPEC_FILE, 2)
    try:
        spec = json.loads(raw)
    except ValueError:
        raise Problem("%s is not valid JSON." % SPEC_FILE)
    if via_run.has_secret(json.dumps(spec, ensure_ascii=False)):  # JSON escapes can hide a key from the raw scan
        via_run.die("%s holds a secret-shaped value. Remove it; nothing was written." % SPEC_FILE, 2)
    check_spec(spec, project)
    return spec


def lookup(tokens, name):
    for group in via_tokens.GROUPS:
        if name in tokens[group]:
            return tokens[group][name]["light"], tokens[group][name]["dark"]
    return None, None


def input_hash(spec):
    """A hash of the spec as built (input and resolved values), so link can tell that spec.json changed since."""
    data = {k: v for k, v in spec.items() if k not in DERIVED}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def theme_tokens(project, spec):
    """Web: the project's CSS tokens. Compose and Flutter: only the theme the spec declares (from Color.kt or
    ColorScheme), so a stray CSS file in the repo cannot override the app's theme."""
    if spec["platform"] not in NATIVE:
        return proto.safe_tokens(via_tokens.read_tokens(project))
    tokens = {g: {} for g in via_tokens.GROUPS}
    for name, v in spec.get("theme", {}).items():
        tokens[via_tokens.group_of(name, v["light"]) or "colors"][name] = dict(v)
    return proto.safe_tokens(tokens)


def resolve_rows(spec, tokens):
    """Writes each row's light and dark value: the token's values from the project, or the literal value twice."""
    for s in spec["screens"]:
        for r in s["rows"]:
            r["light"], r["dark"] = (lookup(tokens, r["token"]) if "token" in r
                                     else (r["value"], r.get("value_dark", r["value"])))


def state_text(screen, state):
    return screen.get("states", {}).get(state)


def cell(value):
    return via_run.cell(value) if value is not None else "no token"


def spec_table(spec):
    lines = ["| Screen | Element | Property | Token | Light | Dark | Note |", "|---|---|---|---|---|---|---|"]
    for s in spec["screens"]:
        for r in s["rows"]:
            token = "`%s`" % r["token"] if "token" in r else "literal"
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                cell(s["screen"]), cell(r["element"]), cell(r["property"]), token, cell(r["light"]), cell(r["dark"]),
                via_run.cell(r.get("note", ""))))
    return "\n".join(lines) + "\n"


def states_table(spec):
    lines = ["| Screen | State | Spec |", "|---|---|---|"]
    for s in spec["screens"]:
        for state in proto.STATES:
            value = state_text(s, state)
            lines.append("| %s | %s | %s |" % (cell(s["screen"]), state, cell(value) if value else "not specified"))
    return "\n".join(lines) + "\n"


def ticket_line(spec):
    t = spec["ticket"]
    return "Ticket: %s (%s%s). Platform: %s." % (t["key"], t["source"], ", " + t["path"] if "path" in t else "", spec["platform"])


def ticket_update(spec, run_name):
    """The text Via proposes to add to the ticket. It is added only after the user approves."""
    target = spec.get("target")
    mock = ("Target mock: %s" % target["url"] if target else
            "Target mock: `target.html` in the Via run folder `.via/runs/%s` (local, not published)." % run_name)
    criteria = "".join(
        "- [ ] Matches the target mock for %s: colors, sizes, radii, spacing, icons, states "
        "(open/pressed/selected/empty/dark).\n" % s["screen"] for s in spec["screens"])
    return ("## Visual spec (Via)\n\n%s\n\n%s\n\n### Acceptance criteria\n\n%s"
            "- [ ] Uses the values in the table below, light and dark. A row marked \"no token\" names the ticket that adds the token.\n\n"
            "### Values\n\n%s\n### States\n\n%s"
            % (spec["direction"], mock, criteria, spec_table(spec), states_table(spec)))


def value_cell(value):
    if value is None:
        return '<td class="vp-none">no token</td>'
    swatch = ""
    if via_tokens.COLOR_RE.match(value) and proto.SAFE_VALUE.fullmatch(value) and proto.balanced(value):
        swatch = '<span class="vp-sw" style="background:%s"></span>' % proto.esc(value)
    return "<td>%s%s</td>" % (swatch, proto.esc(value))


def rows_panel(s):
    """The screen's spec rows and states beside the mock: the same values as spec.md."""
    esc = proto.esc
    out = ['<aside class="vp-spec" aria-label="Spec for %s"><h3>Spec</h3><div class="vs-scroll"><table><tr><th scope="col">Element</th>'
           '<th scope="col">Property</th><th scope="col">Token</th><th scope="col">Light</th><th scope="col">Dark</th>'
           '<th scope="col">Note</th></tr>' % esc(s["screen"])]
    for r in s["rows"]:
        out.append('<tr><th scope="row">%s</th><td>%s</td><td>%s</td>%s%s<td>%s</td></tr>' % (
            esc(r["element"]), esc(r["property"]), esc(r.get("token", "literal")), value_cell(r["light"]),
            value_cell(r["dark"]), esc(r.get("note", ""))))
    out.append("</table></div><h3>States</h3><dl>")
    for state in proto.STATES:
        value = state_text(s, state)
        out.append("<dt>%s</dt><dd>%s</dd>" % (state.capitalize(), esc(value) if value else "not specified"))
    out.append("</dl></aside>")
    return "".join(out)


def render_target(run_name, spec, tokens, screens):
    esc = proto.esc
    t = spec["ticket"]
    sections = []
    for n, (s, body, _) in enumerate(screens, 1):
        sections.append(
            '<section class="vp-finding" id="s%d" aria-labelledby="s%d-h"><h2 id="s%d-h">%s</h2>'
            '<p class="vp-id">Screen: %s</p><p class="vp-issue">%s</p><div class="vp-cols">'
            '<figure style="margin:0"><figcaption class="vp-cap">Target</figcaption><div class="vp-frame vp-scope">'
            '<div class="vp-fragment" inert>%s</div></div></figure>%s</div></section>'
            % (n, n, n, esc(s["label"]), esc(s["screen"]), esc(s["rationale"]), body,
               rows_panel(s)))
    css = (proto.CSS + PAGE_CSS + ".vp-scope{%s}.vp-root:has(#vp-dark:checked) .vp-scope{%s}"
           % (proto.scope_css(tokens, "light"), proto.scope_css(tokens, "dark")))
    title = "%s %s" % (t["key"], t["title"])
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<title>Via spec: %s</title><style>%s</style></head><body><div class="vp-root">'
        '<header class="vp-top"><div><h1>Via spec: %s</h1><p class="vp-meta">Run %s. Platform %s. '
        'The target is a mockup drawn from the project\'s theme tokens, not a real render.</p></div>'
        '<div class="vp-seg" role="radiogroup" aria-label="Theme"><input type="radio" name="vp-theme" id="vp-light" checked>'
        '<label for="vp-light">Light</label><input type="radio" name="vp-theme" id="vp-dark"><label for="vp-dark">Dark</label></div>'
        '</header><p class="vp-issue"><strong>Direction.</strong> %s</p><main>%s</main></div></body></html>\n'
        % (esc(title), css, esc(title), esc(run_name), esc(spec["platform"]), esc(spec["direction"]), "".join(sections)))


def warnings_for(spec, screens):
    warnings = []
    for s, _, used in screens:
        missing = sorted({r["token"] for r in s["rows"] if "token" in r and (r["light"] is None or r["dark"] is None)})
        if missing:
            warnings.append("%s: %s has no token in this project for light, dark or both, so the table shows \"no token\" "
                            "there. Add it to the theme or name the ticket that does." % (s["screen"], ", ".join(missing)))
        listed = {r["token"] for r in s["rows"] if "token" in r}
        unlisted = sorted(set(used) - listed)
        if unlisted:
            warnings.append("%s: the mock uses %s, which the spec table does not list. Add a row for each value "
                            "the build must match." % (s["screen"], ", ".join(unlisted)))
    return warnings


def save_spec(spec_path, spec):
    via_run.write_atomic(spec_path, json.dumps(spec, indent=2, ensure_ascii=False) + "\n")


def cmd_build(project, args):
    run_dir, spec_path = resolve_run(project, args.run_dir)
    run_name = os.path.basename(run_dir)
    spec = load_spec(spec_path, project)
    tokens = theme_tokens(project, spec)
    screens, warnings = [], []
    for i, s in enumerate(spec["screens"]):
        body, used, warns = proto.clean_fragment(s["html"], "screens[%d] (%s)" % (i, s["screen"]))
        screens.append((s, body, used))
        warnings += warns
    if not any(tokens[g] for g in via_tokens.GROUPS):
        warnings.append("No theme tokens found in the project's CSS or in spec.json theme, so every token shows \"no token\".")
    resolve_rows(spec, tokens)
    warnings += warnings_for(spec, screens)
    spec.pop("target", None)  # a rebuild replaces the page; publish it again and run link again
    spec["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    spec["outputs"] = dict(OUTPUTS)
    spec["built_hash"] = input_hash(spec)
    title = "%s %s" % (spec["ticket"]["key"], spec["ticket"]["title"])
    files = {
        "direction": "# Direction: %s\n\n%s\n\n%s\n" % (title, ticket_line(spec), spec["direction"]),
        "spec": "# Spec: %s\n\n%s\n\n%s\n## States\n\n%s" % (title, ticket_line(spec), spec_table(spec), states_table(spec)),
        "target": render_target(run_name, spec, tokens, screens),
        "ticket_update": ticket_update(spec, run_name),
    }
    check_spec(spec, project)  # before anything is written, so a refused build leaves nothing behind
    allowed, reason = proto.publish_decision(project, args.config)
    for key, name in OUTPUTS:
        via_run.write_atomic(os.path.join(run_dir, name), files[key])
    save_spec(spec_path, spec)
    via_run.emit({"outputs": {k: os.path.join(run_dir, n) for k, n in OUTPUTS}, "publish_allowed": allowed,
                  "reason": reason, "warnings": warnings})


def cmd_link(project, args):
    run_dir, spec_path = resolve_run(project, args.run_dir)
    spec = load_spec(spec_path, project)
    if not spec.get("built_at") or not os.path.isfile(os.path.join(run_dir, "target.html")):
        raise Problem("this run has no target mock yet. Run `build` first.")
    if spec.get("built_hash") != input_hash(spec):
        raise Problem("spec.json changed since the last build. Run `build` again, publish the new target.html, then link.")
    allowed, reason = proto.publish_decision(project, args.config)
    if not allowed:
        raise Problem("this project may not publish to artifacts (%s). The mock stays local." % reason)
    url = args.url.strip()
    if not url.startswith("https://") or any(c.isspace() for c in url) or len(url) > 500 or via_run.has_secret(url):
        raise Problem("the link must be an https URL with no spaces and no secret in it.")
    spec["target"] = {"url": url, "published": True}
    update = ticket_update(spec, os.path.basename(run_dir))
    save_spec(spec_path, spec)
    via_run.write_atomic(os.path.join(run_dir, "ticket-update.md"), update)
    via_run.emit({"url": url, "published": True})


def load_theme_file(project, path):
    """The app's theme as it is now, on Compose or Flutter: {"source": "<theme file>", "theme": {...}}, written by
    Claude from Color.kt or ColorScheme in the same shape as spec.json theme. `source` is where findings point."""
    data = proto.read_json(path, "the --theme file", "--theme must name a JSON file (not a symlink).")
    source = data.get("source") if isinstance(data, dict) and set(data) == {"source", "theme"} else None
    if not (proto.text(source, 500) and not os.path.isabs(source) and via_run.inside(os.path.join(project, source), project)):
        raise Problem("the --theme file needs source (the theme file, relative to the project and inside it) and theme.")
    check_theme(data["theme"])
    return source, data["theme"]


def same(a, b):
    """Equal CSS values, ignoring case and spacing. A side that is unset (a dark-only token's light) matches unset."""
    if a is None or b is None:
        return a is b
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def pair(light, dark):
    return "%s light, %s dark" % (light or "unset", dark or "unset")


def deviation(screen, r, name, now, where):
    want = pair(r["light"], r["dark"])
    if now == (None, None):
        issue = "%s %s: the spec says %s = %s, but the theme no longer defines %s." % (r["element"], r["property"], name, want, name)
    else:
        issue = "%s %s: the spec says %s = %s; the theme now has %s." % (
            r["element"], r["property"], name, want, pair(*now))
    rule = ("spec-%s-%s" % (via_run.slug(r["element"]), via_run.slug(r["property"])))[:80].rstrip("-")
    return {"screen": screen, "rule": rule, "severity": "med", "issue": issue, "source": "spec",
            "fix": "Set %s back to %s, or run /via spec again if the change is intended." % (name, want),
            "location": {"file": where, "line": None}}


def cmd_compare(project, args):
    """Each token row of a built spec against the theme as it is now. Literal rows (sizes the build must hit) are
    counted, not compared: Claude checks them on the screenshots."""
    run_dir, spec_path = resolve_run(project, args.run_dir)
    spec = load_spec(spec_path, project)
    if not spec.get("built_at"):
        raise Problem("this spec was not built yet. Run `build` first.")
    if spec.get("built_hash") != input_hash(spec):
        raise Problem("spec.json changed since the last build. Run `build` again, then compare.")
    owner = {}
    if spec["platform"] in NATIVE:
        if not args.theme:
            raise Problem("a %s spec needs --theme: the app's current theme values as JSON (see load_theme_file)." % spec["platform"])
        source, theme = load_theme_file(project, args.theme)
        tokens = theme_tokens(project, {"platform": spec["platform"], "theme": theme})
    else:
        if args.theme:
            raise Problem("--theme is for Compose and Flutter; on web the tokens come from the project's CSS.")
        tokens = proto.safe_tokens(via_tokens.read_tokens(project, owner))  # the same filter build used
        source = None
    fallback = os.path.relpath(spec_path, project).replace(os.sep, "/")
    findings, compared, literal = [], 0, 0
    for s in spec["screens"]:
        for r in s["rows"]:
            if "token" not in r:
                literal += 1
                continue
            if r.get("light") is None and r.get("dark") is None:
                continue  # the token was missing when the spec was built: build warned, nothing to compare
            compared += 1
            now = lookup(tokens, r["token"])
            if not (same(now[0], r["light"]) and same(now[1], r["dark"])):
                # the CSS file whose declaration wins (the last one read), else the spec itself
                where = source or owner.get(r["token"], [fallback])[-1]
                findings.append(deviation(s["screen"], r, r["token"], now, where))
    via_run.emit({"spec_run": run_dir, "compared": compared, "literal_rows": literal, "findings": findings})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("build", "link", "compare"):
        p = sub.add_parser(name)
        p.add_argument("run_dir")
        p.add_argument("--project")
        p.add_argument("--config")
        if name == "link":
            p.add_argument("--url", required=True)
        if name == "compare":
            p.add_argument("--theme")
    args = ap.parse_args(argv)
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        via_run.die("%s is not a folder." % project)
    try:
        {"build": cmd_build, "link": cmd_link, "compare": cmd_compare}[args.command](project, args)
    except Problem as e:
        via_run.die("%s Nothing was written." % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
