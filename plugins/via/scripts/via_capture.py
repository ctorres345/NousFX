#!/usr/bin/env python3
"""via-capture: screenshots of web pages for a Via run, using headless Chrome or Chromium.

Usage: via_capture.py --run-dir RUN_DIR --screen NAME=PAGE [--screen NAME=PAGE ...] [--project DIR]
                      [--redact-selector CSS ...]

PAGE is an .html, .htm or .svg file inside the project (not in a hidden folder), or an http(s) URL that
answers with a page (a dev server). Each screen gets four shots in RUN_DIR: NAME-light-phone.png, NAME-dark-phone.png, NAME-light-desktop.png and NAME-dark-desktop.png.
NAME is lowercase letters, digits and hyphens. RUN_DIR is a folder made by `via_run.py init`.
Stdout is JSON: {"browser": ..., "screenshots": [...]}, with entries in the run record's shape.

Masking: the browser never loads the page directly.
A shot is never written over an existing file: reviewed shots stay what they were when they were reviewed. A mask server on 127.0.0.1 serves project files (visible
ones only) or relays your dev server, and adds a style that blurs inputs, textareas, email and phone links,
[data-via-redact] and every --redact-selector. A script placed first in the head of an HTML page copies that style into
every shadow root (open, closed or declarative) and cancels a navigation to another origin. On a relayed page, absolute
URLs to the dev server in HTML, SVG, JavaScript and JSON are rewritten to the mask server, and every HTML, XHTML and
SVG page is sent with a policy that lets it frame and submit forms only to the mask server (so a third-party frame is blocked). The dev
server's own Content-Security-Policy header is replaced by that policy, so the style can apply. Every shot is marked
"redacted": true. Limits that remain: a closed shadow root made by markup (shadowrootmode="closed") is out of reach, SVG
and XHTML pages get the style but not the script, other XML pages get the policy only, a script that empties a root's adoptedStyleSheets in place (splice, length = 0) removes the mask there, a same-origin frame made
from srcdoc, about:blank or a blob: URL is not masked, and a browser without the Navigation API does not cancel navigation. The mask is a first layer: Claude still looks at each shot
and sets its `review` (kept or dropped) before anything leaves the run folder.

Browser: $VIA_CHROME if set (and then only that), else Google Chrome or Chromium on macOS, else
google-chrome, chromium or chromium-browser on PATH.

Exit 0 success, 1 invalid input, 3 no browser found (say that visual checks were skipped),
4 a URL did not answer or the browser failed to write a shot. Shots land in RUN_DIR only when every one
succeeded. A browser that outlives VIA_SHOT_TIMEOUT seconds (default 60) is killed with its helper processes.
The first shot of a run may wait up to two limits longer, at most 120 s more (180 s in all by default), because
a browser's first launch on a new machine is slow. A note on stderr says so. A hung first launch is still killed.
The run can outlast a foreground command, so run it in the background. SIGTERM and SIGHUP end it with exit 4
after the browser and its helpers are killed.
"""
import argparse
import http.client
import http.server
import json
import mimetypes
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

RUN_AREA = os.path.join(".via", "runs")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SIZES = {"phone": (390, 844), "desktop": (1280, 800)}
# Chrome's --blink-settings=preferredColorScheme value: 0 is dark and 1 is light (checked against real Chrome).
THEMES = {"light": 1, "dark": 0}
MAC_BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
PATH_BROWSERS = ("google-chrome", "chromium", "chromium-browser")
SHOT_TIMEOUT = 60
FIRST_LAUNCH_EXTRA = 120  # most seconds the first shot may wait beyond VIA_SHOT_TIMEOUT
UPSTREAM_TIMEOUT = 30
BLUR_PX = 8
# Form fields that carry what people type, minus the controls whose look is the design under review.
MASKED = ("input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit])"
          ":not([type=reset]):not([type=range]):not([type=color]):not([type=file])",
          "textarea", "[type=email]", 'a[href^="mailto:"]', 'a[href^="tel:"]', "[data-via-redact]")
# A selector may not hold anything that could close the style or start a new rule.
# No slash either, so a selector cannot open a CSS comment.
SELECTOR_RE = re.compile(r"^[A-Za-z0-9_\-\s.#\[\]=\"'*>+~,():^$|@%!]+$")
META_REFRESH_URL_RE = re.compile(rb"<meta\b(?=[^>]*http-equiv\s*=\s*[\"']?refresh)(?=[^>]*\burl\s*=)[^>]*>", re.IGNORECASE)
REFRESH_URL_RE = re.compile(r"^(.*?;\s*url\s*=\s*)(.*)$", re.IGNORECASE | re.DOTALL)
META_CSP_RE = re.compile(rb"<meta\b[^>]*http-equiv\s*=\s*[\"']?content-security-policy[^>]*>", re.IGNORECASE)
LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "content-length", "content-encoding", "content-security-policy",
              "content-security-policy-report-only"}
PROBE_TIMEOUT = 10
# What a mask-server page may frame or submit to: only the mask server. It replaces the dev server's own policy.
FRAMING_POLICY = "frame-src 'self'; object-src 'self'; form-action 'self'"
# Documents a browser can frame or navigate, and everything whose absolute dev-server URLs get rewritten.
FRAMED_KINDS = ("text/html", "application/xhtml+xml", "image/svg+xml", "application/xml", "text/xml")
REWRITE_KINDS = FRAMED_KINDS + ("text/javascript", "application/javascript", "application/x-javascript",
                                "application/json")
# Runs first in the page. It puts the mask rules into every shadow root, open or closed (the page's own style does not
# reach inside one), and cancels a navigation to another origin, so a script cannot walk around the mask server.
GUARD_JS = (
    "(function(){var css=%s,sheet=null;"
    "try{sheet=new CSSStyleSheet();sheet.replaceSync(css);}catch(e){}"
    "var d=Object.getOwnPropertyDescriptor(ShadowRoot.prototype,'adoptedStyleSheets');"
    # A library (Lit) assigns adoptedStyleSheets after attachShadow; the setter puts the mask sheet back every time.
    # A root in another document cannot adopt this document's sheet: the page's own assignment then goes through alone.
    "if(sheet&&d&&d.set){Object.defineProperty(ShadowRoot.prototype,'adoptedStyleSheets',{configurable:true,"
    "enumerable:d.enumerable,get:d.get,set:function(v){var a=Array.from(v).filter(function(x){return x!==sheet;});"
    "try{d.set.call(this,a.concat([sheet]));}catch(e){d.set.call(this,a);}}});}"
    # has(): the mask is on this root, as the shared sheet or as a style child (a page's innerHTML wipes the child).
    "function has(r){return (sheet&&Array.prototype.indexOf.call(r.adoptedStyleSheets,sheet)>=0)"
    "||!!r.querySelector('style[data-via-mask]');}"
    "function style(r){var t=document.createElement('style');t.setAttribute('data-via-mask','');"
    "t.textContent=css;(r.nodeType===9?(r.head||r.documentElement):r).appendChild(t);}"
    "var obs=null,roots=new WeakMap();"
    # The page itself keeps its mask too: a sheet adopted into the document survives head.innerHTML and document.write.
    "function adopt(r){try{if(sheet&&r.nodeType===9){r.adoptedStyleSheets=Array.from(r.adoptedStyleSheets).concat([sheet]);}"
    "else if(sheet&&d&&d.set){r.adoptedStyleSheets=r.adoptedStyleSheets;}if(!has(r)){style(r);}}"
    "catch(e){try{style(r);}catch(x){}}"
    # Watch the root itself: a document observer does not see inside shadow trees, and a page's innerHTML wipes the style child.
    "try{if(obs)obs.observe(r,{childList:true,subtree:true});}catch(e){}}"
    "var attach=Element.prototype.attachShadow;"
    "Element.prototype.attachShadow=function(i){var r=attach.call(this,i);try{roots.set(this,r);adopt(r);}catch(e){}return r;};"
    # Walk what is added and put the mask on any root that lost it; roots.get finds a closed root through its host.
    "function walk(n){if(n.nodeType!==1&&n.nodeType!==11)return;var r=n.shadowRoot||roots.get(n);"
    "if(r){if(!has(r)){adopt(r);}walk(r);}"
    "for(var c=n.firstChild;c;c=c.nextSibling)walk(c);}"
    "obs=new MutationObserver(function(ms){ms.forEach(function(m){try{var g=m.target.getRootNode();"
    "if((g.nodeType===9||(g.nodeType===11&&g.host))&&!has(g))adopt(g);m.addedNodes.forEach(walk);}catch(e){}});});"
    "obs.observe(document,{childList:true,subtree:true});adopt(document);"
    "document.addEventListener('DOMContentLoaded',function(){walk(document);});"
    "if(window.navigation){navigation.addEventListener('navigate',function(e){try{"
    "if(new URL(e.destination.url).origin!==location.origin&&e.cancelable)e.preventDefault();}"
    "catch(x){if(e.cancelable)e.preventDefault();}});}})();")
PAGE_SUFFIXES = (".html", ".htm", ".svg")


def die(message, code=1):
    print("Stopped: %s" % message, file=sys.stderr)
    sys.exit(code)


def real(path):
    return os.path.realpath(path)


def inside(path, root):
    path, root = real(path), real(root)
    return path != root and os.path.commonpath([path, root]) == root


def check_run_dir(project, run_arg):
    area = os.path.join(project, RUN_AREA)
    run_dir = os.path.abspath(os.path.join(project, run_arg))
    if any(os.path.islink(p) for p in (os.path.join(project, ".via"), area, run_dir)):
        die(".via or the run folder is a symlink. Nothing was written.")
    if not os.path.isdir(run_dir) or not inside(run_dir, area):
        die("%s is not a run folder made by `via_run.py init` in this project." % run_arg)
    return run_dir


def parse_screen(project, spec):
    name, sep, page = spec.partition("=")
    if not sep or not NAME_RE.match(name):
        die("--screen must look like name=page, with a name of lowercase letters, digits and hyphens: %r." % spec)
    if page.startswith(("http://", "https://")):
        return name, page
    if "://" in page:
        die("%s: only a file in the project or an http(s) URL can be captured." % name)
    path = os.path.join(project, page)
    if not os.path.isfile(path) or not inside(path, project):
        die("%s: %s is not a file inside the project." % (name, page))
    rel = os.path.relpath(real(path), real(project))
    if not rel.lower().endswith(PAGE_SUFFIXES) or any(part.startswith(".") for part in rel.split(os.sep)):
        die("%s: only .html, .htm and .svg pages outside hidden folders can be captured." % name)
    return name, pathlib.Path(real(path)).as_uri()


def problem_with(url):
    """None when the URL gives a page, else why not. Without this, Chrome shoots its own error page."""
    no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Chrome skips proxies for localhost
    try:
        with no_proxy.open(url, timeout=PROBE_TIMEOUT):
            return None
    except urllib.error.HTTPError as e:
        return "answered HTTP %d" % e.code
    except (OSError, ValueError, http.client.HTTPException) as e:
        return "could not be reached (%s). Is the dev server running?" % type(e).__name__


def mask_style(extra):
    """One rule for the built-in selectors and one per extra selector: CSS drops a whole rule when any
    selector in it is invalid, so a typo in one must not switch off the rest."""
    rule = "%%s { filter: blur(%dpx) !important; }" % BLUR_PX
    return "<style data-via-mask=\"\">%s</style>" % "\n".join(rule % s for s in (", ".join(MASKED),) + tuple(extra))


def kind_of(content_type):
    """The media type alone, lowercase. With no type Chrome sniffs HTML, so that is what none means."""
    return content_type.split(";")[0].strip().lower() or "text/html"


def mask_guard(style):
    """The guard script for a mask style: the style's rules go into every shadow root. Bytes, ready to insert."""
    css = re.search(r"<style[^>]*>(.*)</style>", style, re.DOTALL).group(1)
    # json.dumps gives a JS string; \u003c keeps a "<" in a selector from ever closing the script.
    return ("<script data-via-guard=\"\">%s</script>" % (GUARD_JS % json.dumps(css).replace("<", "\\u003c"))).encode()


def dev_server_pattern(upstream):
    """Matches an absolute URL to the dev server itself, under any loopback spelling, with plain, protocol-relative
    or JSON-escaped slashes. A longer port or host name does not match."""
    u = urllib.parse.urlsplit(upstream)
    hosts = LOOPBACK if u.hostname in LOOPBACK else {u.hostname}
    names = "|".join(re.escape(h) if ":" not in h else r"\[%s\]" % re.escape(h) for h in sorted(hosts))
    default = 443 if u.scheme == "https" else 80
    port = r"(?::%d)?" % default if (u.port or default) == default else r":%d" % u.port
    # No scheme letter, "+", ".", "-" or ":" before it, so "//host:port" after ws: or https: is left whole.
    return re.compile((r"(?<![\w+.\-:])(?:%s:)?(\\?/\\?/)(?:%s)\.?%s(?![\w.@:\-])"
                       % (re.escape(u.scheme), names, port)).encode(), re.IGNORECASE)


def through_the_mask(body, content_type, pattern, origin):
    """Absolute URLs to the dev server in a page, script or JSON point at the mask server instead, so a frame,
    a link or a script navigation cannot load the dev server around the mask."""
    if kind_of(content_type) not in REWRITE_KINDS:
        return body
    escaped = origin.replace("/", "\\/")
    return pattern.sub(lambda m: (escaped if m.group(1).startswith(b"\\") else origin).encode(), body)


def put_guard(body, guard):
    """Where the guard goes: first in the page, but never before a byte order mark, a leading comment or the doctype
    (that would switch on quirks mode), and after a <head> tag and any charset <meta> (the browser reads the encoding
    from the first 1024 bytes, and the guard is longer than that). Only the text before the first script, style,
    textarea or comment is searched for those: a "<head>" inside one of them is not a tag."""
    at = re.match(rb"(?:\xef\xbb\xbf)?(?:\s|<!--.*?-->)*", body, re.DOTALL).end()
    doctype = re.compile(rb"<!doctype[^>]*>", re.IGNORECASE).match(body, at)
    at = doctype.end() if doctype else at
    cut = re.search(rb"<(?:script|style|textarea|!--)", body[at:], re.IGNORECASE)
    window = body[at:at + cut.start()] if cut else body[at:]
    head = re.search(rb"<head(?=[\s>])[^>]*>", window, re.IGNORECASE)
    pos = head.end() if head else 0
    for meta in re.finditer(rb"<meta\b[^>]*charset[^>]*>", window, re.IGNORECASE):
        pos = max(pos, meta.end())
    return body[:at + pos] + guard + body[at + pos:]


def same_server(location, upstream):
    """True when an absolute Location points at the dev server itself, under any loopback spelling."""
    a, b = urllib.parse.urlsplit(location), urllib.parse.urlsplit(upstream)
    if a.scheme != b.scheme or a.port != b.port:
        return False
    return a.hostname == b.hostname or (a.hostname in LOOPBACK and b.hostname in LOOPBACK)


class Unmaskable(Exception):
    pass


def with_mask(body, content_type, style, guard=b""):
    """The page bytes with the mask style added. Other files (scripts, images) pass through unchanged.
    Raises Unmaskable for a page the mask cannot be placed in, so a shot of it is never kept."""
    kind = kind_of(content_type)
    block = style.encode("utf-8")
    if kind in ("image/svg+xml", "application/xhtml+xml"):  # XML: the style must stay inside the root element
        end = b"</svg>" if kind == "image/svg+xml" else b"</html>"
        at = body.lower().rfind(end)
        if at < 0:
            raise Unmaskable("a %s page has no closing %s tag, so the mask cannot be placed" % (kind, end.decode()))
        return body[:at] + block + body[at:]
    if kind == "text/html":
        if META_REFRESH_URL_RE.search(body):  # a redirect by script-less markup would lead Chrome around the mask
            raise Unmaskable("the page redirects with a meta refresh, so a shot of it would show another page")
        body = META_CSP_RE.sub(b"", body)  # a meta CSP could forbid the inline style
        # In the head, where a framework that rebuilds the body leaves it alone.
        found = re.search(rb"</head\s*>", body, re.IGNORECASE) or re.search(rb"<body\b", body, re.IGNORECASE)
        body = body + block if not found else body[:found.start()] + block + body[found.start():]
        return put_guard(body, guard) if guard else body
    return body


class MaskHandler(http.server.BaseHTTPRequestHandler):
    """Serves one origin to the browser. server.root is set for project files, server.upstream for a dev server."""

    def log_message(self, *args):
        pass

    def reply(self, status, headers, body, kind=None):
        """kind is the page's media type (None for an error text); a framable document gets the framing policy."""
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        if kind in FRAMED_KINDS:
            self.send_header("Content-Security-Policy", FRAMING_POLICY)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, message):
        """A relay failure. It also marks the capture as failed, so no shot of an error page is kept."""
        self.server.errors.append(message)
        try:
            self.text(502, message)
        except OSError:
            pass  # the browser has gone; the recorded error still stops the capture

    def text(self, status, message):
        self.reply(status, [("Content-Type", "text/plain")], message.encode())

    def do_GET(self):
        if self.headers.get("Host") != "127.0.0.1:%d" % self.server.server_address[1]:
            return self.text(421, "wrong host")  # blocks DNS rebinding
        if self.headers.get("Service-Worker"):  # a worker would answer later loads without the mask
            return self.fail("the page tried to register a service worker, which could bypass the mask")
        try:
            if self.server.upstream:
                self.relay()
            else:
                self.serve_file()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # whatever else goes wrong must stop the capture, not leave Chrome an error page
            self.fail("relay error (%s)" % type(e).__name__)

    def serve_file(self):
        parts = [p for p in urllib.parse.unquote(urllib.parse.urlsplit(self.path).path).split("/") if p]
        root = self.server.root
        path = os.path.join(root, *parts)
        if (not parts or "\0" in path or any(p.startswith(".") or p in (".", "..") for p in parts)
                or os.path.islink(path) or not os.path.isfile(path) or not inside(path, root)):
            return self.text(404, "not found")
        rel = os.path.relpath(real(path), root)
        if any(p.startswith(".") for p in rel.split(os.sep)):
            return self.text(404, "not found")
        kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            body = f.read()
        try:
            body = with_mask(body, kind, self.server.style, self.server.guard)
        except Unmaskable as e:
            return self.fail(str(e))
        self.reply(200, [("Content-Type", kind)], body, kind_of(kind))

    def through_mask(self, value):
        """A redirect target, rewritten to the mask server. A redirect must not lead the browser around the mask.
        Browsers read a backslash as a slash, so the target is resolved first."""
        try:
            target = urllib.parse.urlsplit(urllib.parse.urljoin(self.server.upstream + self.path,
                                                                value.replace("\\", "/")))
            same = same_server(target.geturl(), self.server.upstream)
        except ValueError:
            same = False
        if not same:
            raise Unmaskable("redirect to another host")
        return self.server.origin + (target.path or "/") + ("?" + target.query if target.query else "")

    def relay(self):
        sent = {"Accept-Encoding": "identity"}
        for name in ("Accept", "Accept-Language", "Cookie", "Authorization", "User-Agent"):
            if self.headers.get(name):
                sent[name] = self.headers[name]
        request = urllib.request.Request(self.server.upstream + self.path, headers=sent)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
        try:
            resp = opener.open(request, timeout=UPSTREAM_TIMEOUT)
        except urllib.error.HTTPError as e:  # also how a redirect or a 404 arrives
            resp = e
        except (OSError, http.client.HTTPException):
            return self.fail("the dev server did not answer")
        with resp:
            if resp.headers.get("Content-Encoding", "identity").lower() != "identity":
                return self.fail("the dev server sent a compressed body")
            if len(resp.headers.get_all("Content-Type") or []) > 1:  # the browser may take the other one
                return self.fail("the dev server sent more than one Content-Type header")
            body = resp.read()
            status, kind = resp.status, resp.headers.get("Content-Type", "")
            headers = []
            for name, value in resp.headers.items():
                if name.lower() in HOP_BY_HOP:
                    continue
                try:
                    if name.lower() == "location":
                        value = self.through_mask(value)
                    elif name.lower() == "refresh":  # "0; url=..." redirects like Location does
                        m = REFRESH_URL_RE.match(value)
                        if m:
                            value = m.group(1) + self.through_mask(m.group(2).strip())
                except Unmaskable as e:
                    return self.fail(str(e))
                headers.append((name, value))
        try:
            body = through_the_mask(body, kind, self.server.pattern, self.server.origin)
            body = with_mask(body, kind, self.server.style, self.server.guard)
        except Unmaskable as e:
            return self.fail(str(e))
        self.reply(status, headers, body, kind_of(kind))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # the redirect itself goes to the browser, through the mask


def start_mask(style, root=None, upstream=None):
    """A mask server on 127.0.0.1. Returns it with its origin, for example http://127.0.0.1:50321."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MaskHandler)
    server.daemon_threads = True
    server.style, server.root, server.upstream, server.errors = style, root, upstream, []
    server.guard = mask_guard(style)
    server.pattern = dev_server_pattern(upstream) if upstream else None
    server.origin = "http://127.0.0.1:%d" % server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def masked_urls(screens, project, style):
    """Maps each page URL to the mask server's URL for it. Returns (servers, mapping)."""
    servers, by_origin, mapping = [], {}, {}
    for _, url in screens:
        if url in mapping:
            continue
        if url.startswith("file://"):
            if "files" not in by_origin:
                by_origin["files"] = start_mask(style, root=real(project))
                servers.append(by_origin["files"])
            rel = os.path.relpath(urllib.parse.unquote(urllib.parse.urlsplit(url).path), real(project))
            mapping[url] = by_origin["files"].origin + "/" + urllib.parse.quote(rel.replace(os.sep, "/"))
            continue
        parts = urllib.parse.urlsplit(url)
        upstream = "%s://%s" % (parts.scheme, parts.netloc)
        if upstream not in by_origin:
            by_origin[upstream] = start_mask(style, upstream=upstream)
            servers.append(by_origin[upstream])
        mapping[url] = by_origin[upstream].origin + (parts.path or "/") + ("?" + parts.query if parts.query else "")
    return servers, mapping


def find_browser():
    override = os.environ.get("VIA_CHROME")
    if override:  # an explicit choice is never replaced by a guess
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else None
    for path in MAC_BROWSERS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    for name in PATH_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def stop_group(proc):
    """Kill the browser and every helper it started. They share a process group of their own."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def shot_timeout():
    try:
        return max(1, int(os.environ.get("VIA_SHOT_TIMEOUT", SHOT_TIMEOUT)))
    except ValueError:
        return SHOT_TIMEOUT


def take(browser, profile, stage, shot, first=False):
    """Take one shot into the staging folder. The first shot of a run may wait two limits longer.

    A browser's first launch on a new machine can take minutes. It is the same process still warming up, so the
    wait is extended instead of killing it and starting over. A hung browser still dies within three limits."""
    name, url = shot["path"], shot["url"]
    width, height = SIZES[shot["size"]]
    out = os.path.join(stage, name)
    cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
           "--no-default-browser-check", "--user-data-dir=" + profile,
           "--blink-settings=preferredColorScheme=%d" % THEMES[shot["theme"]],
           "--window-size=%d,%d" % (width, height), "--screenshot=" + out, url]
    proc = None
    limit = shot_timeout()
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            if not first:
                raise
            extra = min(2 * limit, FIRST_LAUNCH_EXTRA)
            print("The first launch is slow (%d s so far). Waiting up to %d s more." % (limit, extra),
                  file=sys.stderr, flush=True)
            proc.wait(timeout=extra)
    except (OSError, subprocess.SubprocessError) as e:
        slow = " The first launch was slow and still did not finish in %d s." % (limit + min(2 * limit, FIRST_LAUNCH_EXTRA)) \
            if first and isinstance(e, subprocess.TimeoutExpired) else ""
        die("the browser failed on %s (%s).%s No screenshots were kept." % (name, type(e).__name__, slow), 4)
    finally:
        if proc is not None:
            stop_group(proc)  # helpers can outlive the browser, so the whole group goes either way
            proc.wait()
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        die("the browser wrote nothing for %s. No screenshots were kept." % name, 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--screen", action="append", default=[])
    ap.add_argument("--project")
    ap.add_argument("--redact-selector", action="append", default=[], metavar="CSS")
    args = ap.parse_args()
    def stop_run(number, _frame):
        for other in stoppers:  # a second signal must not break into the cleanup
            signal.signal(other, signal.SIG_IGN)
        sys.exit(4)

    stoppers = [getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)]
    for sig in stoppers:  # a tool that stops a long run must still reach the cleanup below
        if signal.getsignal(sig) is signal.SIG_DFL:  # a run started under nohup keeps ignoring a hangup
            signal.signal(sig, stop_run)
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        die("%s is not a folder." % project)
    run_dir = check_run_dir(project, args.run_dir)
    if not args.screen:
        die("pass at least one --screen name=page.")
    for selector in args.redact_selector:
        if not SELECTOR_RE.match(selector) or not selector.strip():
            die("--redact-selector %r is not a plain CSS selector (no braces, semicolons, angle brackets or backslashes)."
                % selector)
    screens = [parse_screen(project, s) for s in args.screen]
    if len({name for name, _ in screens}) != len(screens):
        die("each --screen name may be used once.")
    shots = [{"path": "%s-%s-%s.png" % (name, theme, size), "screen": name, "theme": theme, "size": size,
              "url": url} for name, url in screens for size in SIZES for theme in THEMES]
    for shot in shots:
        dest = os.path.join(run_dir, shot["path"])
        if os.path.lexists(dest):  # a shot Claude already reviewed must never be swapped for a new one
            die("%s already exists in the run folder. Start a new run folder with `via_run.py init`. "
                "Nothing was written." % shot["path"])
    browser = find_browser()
    if browser is None:
        die("no Chrome or Chromium found (set VIA_CHROME to its path). Visual checks were skipped.", 3)
    for url in sorted({u for _, u in screens if u.startswith(("http://", "https://"))}):
        why = problem_with(url)
        if why:
            die("%s %s. No screenshots were taken." % (url, why), 4)
    work = tempfile.mkdtemp(prefix="via-capture-")  # holds the browser profile, so a running Chrome keeps out
    stage = tempfile.mkdtemp(prefix=".stage-", dir=run_dir)  # same filesystem, so os.replace is atomic
    servers, urls = masked_urls(screens, project, mask_style(args.redact_selector))
    try:
        for number, shot in enumerate(shots):
            take(browser, os.path.join(work, "profile-" + shot["path"]), stage, {**shot, "url": urls[shot["url"]]},
                 first=number == 0)
            for server in servers:
                if server.errors:
                    die("%s (while shooting %s). No screenshots were kept." % (server.errors[0], shot["path"]), 4)
        for shot in shots:
            os.replace(os.path.join(stage, shot["path"]), os.path.join(run_dir, shot["path"]))
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        shutil.rmtree(work, ignore_errors=True)  # a helper process may still hold files after a timeout
        shutil.rmtree(stage, ignore_errors=True)
    print(json.dumps({"browser": browser, "screenshots": [
        {**{k: v for k, v in shot.items() if k != "url"}, "redacted": True} for shot in shots]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
