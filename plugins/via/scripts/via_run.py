#!/usr/bin/env python3
"""via-run: the bookkeeping behind Via's check and audit runs.

Claude makes the design calls and writes a run record (findings.json). This tool does the rest:
stable finding IDs, the findings ledger, the run folder and the pass record the gate reads.

Usage: via_run.py init   [--project DIR]
       via_run.py merge  RUN_DIR [--project DIR]
       via_run.py reject ID --reason TEXT [--project DIR]
       via_run.py route  RUN_DIR [--jira PROJECT [--parent KEY] [--config FILE]] [--project DIR]
       via_run.py filed  RUN_DIR --payload FILE --key KEY [--project DIR]
       via_run.py pass   RUN_DIR [--project DIR]

Order for a run: init, write RUN_DIR/findings.json, merge, set the approved routes, route, then pass
once the code is final. `route` rejects, writes markdown tickets to docs/design/tickets/ and lists the
fix-now IDs. With --jira and a repo the outbound allow-list lets send to Jira, it writes Jira payloads into
the run folder instead; the skill files them through the Atlassian MCP and records each key with `filed`.
With VIA_UNATTENDED=1 (an unattended run), `route` changes nothing and prints the
decisions as `pending`, and `reject` and `filed` refuse.
The ledger lives in docs/design/via.md between two marker comments. Everything outside them is left alone.
Stdout is JSON. Exit 0 on success, 1 when the input is invalid or unsafe (nothing is written),
2 on a secret-shaped value.
"""
import argparse
import collections
import hashlib
import html
import itertools
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # -I leaves the script folder off sys.path
import via_outbound  # noqa: E402

RUN_AREA = os.path.join(".via", "runs")
IGNORE_LINE = ".via/"
LEDGER = os.path.join("docs", "design", "via.md")
START, END = "<!-- via:ledger:start -->", "<!-- via:ledger:end -->"
PASS_FILE = "via-check-ok"
TICKETS = os.path.join("docs", "design", "tickets")  # markdown tickets for ticket-routed findings, when Jira is not used

JIRA_PROJECT_RE = re.compile(r"[A-Z][A-Z0-9_]{1,9}")
JIRA_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{1,9}-[1-9][0-9]{0,9}")

SEVERITIES = {"high", "med", "low"}
VERDICTS = {"real", "wrong", "unsure"}
ROUTES = {"fix-now", "ticket", "reject"}
SECOND_OPINION = "second-opinion"  # source prefix: second-opinion:argon, :claude, or in agy :agy or :inline
SHOT_STATES = {"default", "empty", "loading", "error", "offline", "long"}  # a test keeps these equal to via_capture_native.py and to findings.schema.json
FONT_SCALES = {"default", "largest"}
DIRECTIONS = {"ltr", "rtl"}
SOURCES = {"preview", "device"}  # where a native shot came from; web shots leave it out
COVERAGE_KINDS = {"state", "matrix", "preview", "note"}
REVIEWS = {"kept", "dropped"}  # Claude's verdict on a screenshot before it may leave the run folder
MODES = {"check", "audit"}
STATUSES = {"new", "open", "fixed", "rejected"}
# A new finding may take over a known sibling ID (same screen, rule and file) only if it sits this close.
SAME_SPOT_LINES = 25

# Same pattern the gemini-review scripts, via_setup.py and claude/hooks/review-gate.py use to stop on a
# secret. Each script ships alone, so the copies stay: when you add a key pattern, add it to all of them.
SECRET_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{36}"
    r"|github_pat_[0-9A-Za-z_]{20,}|glpat-[0-9A-Za-z_-]{20,}|(^|[^0-9A-Za-z_-])sk-[0-9A-Za-z_-]{20,}"
    r"|[sr]k_live_[0-9A-Za-z]{10,}|xox[baprs]-[0-9A-Za-z-]{10,}"
    r"|eyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)"
)


def die(message, code=1):
    print("Stopped: %s" % message, file=sys.stderr)
    sys.exit(code)


def has_secret(value):
    return bool(SECRET_RE.search(str(value)))


def write_atomic(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".via-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:  # newline="": keep CRLF files as they are
            f.write(text)
        # mkstemp makes the file 0600. The ledger and .gitignore are shared project files, so keep
        # the mode they had, or the usual 0644 for a new one.
        os.chmod(tmp, os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_text(path):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        die("cannot read %s (%s). Nothing was changed." % (path, e))


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def emit(payload):
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    print()


# --- git ---

def git(args, cwd, env=None):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        die("git failed (%s). Nothing was changed." % e)
    if r.returncode != 0:
        die("git failed: %s Nothing was changed." % r.stderr.strip())
    return r.stdout.strip()


def worktree_tree(cwd):
    """Tree hash of the working tree on a copy of the index, the same way the review gate does it."""
    index = os.path.abspath(os.path.join(cwd, git(["rev-parse", "--git-path", "index"], cwd)))
    fd, tmp = tempfile.mkstemp(prefix="via-run-index-")
    os.close(fd)
    try:
        if os.path.exists(index):
            with open(index, "rb") as src, open(tmp, "wb") as dst:
                dst.write(src.read())
        else:
            os.remove(tmp)  # git creates it
        env = {**os.environ, "GIT_INDEX_FILE": tmp}
        git(["add", "-A"], cwd, env)
        return git(["write-tree"], cwd, env)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# --- run record ---

def is_text(value):
    return isinstance(value, str) and value.strip() != ""


def is_second_opinion(finding):
    return finding["source"].strip().casefold().startswith(SECOND_OPINION)


def is_dismissed(finding):
    """A second-opinion finding that checking against code and screenshots showed to be wrong."""
    return is_second_opinion(finding) and finding.get("verdict") == "wrong"


def is_count(value):
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def check_token_cost(cost):
    """Return a message naming the problem with a token_cost value, or None when it is valid."""
    if isinstance(cost, dict):
        so = cost.get("second_opinion")
        if so is not None and not (isinstance(so, dict) and is_text(so.get("who"))
                                   and is_count(so.get("input_tokens")) and is_count(so.get("output_tokens"))):
            return "token_cost.second_opinion needs who (text) and input_tokens and output_tokens (whole numbers or null)."
        return None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        return "token_cost must be a number of zero or more, or an object."
    return None


ANCHOR_RE = re.compile(r"f[1-9][0-9]*")
VARIANT_RE = re.compile(r"f[1-9][0-9]*-v[1-9][0-9]*")


def check_prototype(proto):
    """The top-level `prototype` link (written by via_prototype.py). Returns a message, or None when valid."""
    if not isinstance(proto, dict):
        return "prototype must be an object."
    path = proto.get("path")
    if not (is_text(path) and path.endswith(".html") and posixpath.normpath(path) == path
            and not path.startswith(("/", "..")) and "\\" not in path):
        return "prototype.path must be a relative .html path inside the run folder."
    if not isinstance(proto.get("published"), bool):
        return "prototype.published must be true or false."
    url = proto.get("url")
    if url is not None and not (is_text(url) and url.startswith("https://") and not re.search(r"\s", url)):
        return "prototype.url must be an https link or null."
    if proto.get("published") and url is None:
        return "prototype.published is true but prototype.url is empty."
    if "reason" in proto and not is_text(proto["reason"]):
        return "prototype.reason must be text."
    return None


def check_finding_prototype(link):
    if not (isinstance(link, dict) and isinstance(link.get("anchor"), str) and ANCHOR_RE.fullmatch(link["anchor"])):
        return "prototype.anchor must look like f1."
    variants = link.get("variants")
    if not (isinstance(variants, list) and 2 <= len(variants) <= 3
            and all(isinstance(v, str) and VARIANT_RE.fullmatch(v) and v.startswith(link["anchor"] + "-") for v in variants)):
        return "prototype.variants must list 2 or 3 anchors like f1-v1."
    return None


def check_coverage(coverage):
    """Each entry says what native capture shot or skipped: platform, screen, entry, captured, and why when not."""
    if not isinstance(coverage, list):
        return "coverage must be a list."
    for i, c in enumerate(coverage):
        if not (isinstance(c, dict) and all(is_text(c.get(k)) for k in ("platform", "screen", "entry"))
                and isinstance(c.get("kind"), str) and c["kind"] in COVERAGE_KINDS and isinstance(c.get("captured"), bool)):
            return ("coverage[%d] needs platform, screen, entry, kind (%s) and a true/false captured."
                    % (i, ", ".join(sorted(COVERAGE_KINDS))))
        if "reason" in c and not isinstance(c["reason"], str):
            return "coverage[%d].reason must be text." % i
    return None


def check_record(rec):
    """Return a message naming the first problem, or None when the record is valid."""
    if not isinstance(rec, dict):
        return "findings.json must be a JSON object."
    if rec.get("version") != 1:
        return "version must be 1."
    if rec.get("mode") not in MODES:
        return "mode must be one of %s." % ", ".join(sorted(MODES))
    scope = rec.get("scope")
    if scope != "all" and not (isinstance(scope, list) and scope and all(is_text(s) for s in scope)):
        return 'scope must be "all" or a list of screen names.'
    if not (isinstance(rec.get("platforms"), list) and all(is_text(p) for p in rec["platforms"])):
        return "platforms must be a list of names."
    held = rec.get("held", {})
    if not (isinstance(held, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in held.items())):
        return "held is written by merge: a map of finding IDs to run names. Remove it and merge again."
    findings = rec.get("findings")
    if not isinstance(findings, list):
        return "findings must be a list."
    for i, f in enumerate(findings):
        where = "findings[%d]" % i
        if not isinstance(f, dict):
            return "%s must be an object." % where
        for key in ("screen", "rule", "issue", "fix", "source"):
            if not is_text(f.get(key)):
                return "%s.%s must be non-empty text." % (where, key)
        if f.get("severity") not in SEVERITIES:
            return "%s.severity must be one of %s." % (where, ", ".join(sorted(SEVERITIES)))
        loc = f.get("location")
        if not (isinstance(loc, dict) and is_text(loc.get("file"))):
            return "%s.location.file must be non-empty text." % where
        line = loc.get("line")
        if line is not None and not (isinstance(line, int) and not isinstance(line, bool) and line >= 0):
            return "%s.location.line must be a whole number." % where
        if f.get("verdict") not in VERDICTS | {None}:
            return "%s.verdict must be one of %s or null." % (where, ", ".join(sorted(VERDICTS)))
        if is_second_opinion(f) and f.get("verdict") is None:
            return "%s comes from a second opinion, so it needs a verdict: %s." % (where, ", ".join(sorted(VERDICTS)))
        if f.get("route") not in ROUTES | {None}:
            return "%s.route must be one of %s or null." % (where, ", ".join(sorted(ROUTES)))
        if f.get("route_reason") is not None and not is_text(f["route_reason"]):
            return "%s.route_reason must be text." % where
        if f.get("route") is not None and f.get("route_reason") is None:
            return "%s has a route, so it needs a route_reason." % where
        if f.get("group") is not None and not is_text(f["group"]):
            return "%s.group must be text." % where
        if f.get("ticket") is not None and not is_text(f["ticket"]):
            return "%s.ticket must be text." % where
        if f.get("prototype") is not None:
            problem = check_finding_prototype(f["prototype"])
            if problem:
                return "%s.%s" % (where, problem)
    for i, s in enumerate(rec.get("screenshots", [])):
        if not (isinstance(s, dict) and all(is_text(s.get(k)) for k in ("path", "screen", "theme", "size"))
                and isinstance(s.get("redacted"), bool)):
            return "screenshots[%d] needs path, screen, theme, size and a true/false redacted." % i
        if s.get("review") not in REVIEWS | {None}:
            return "screenshots[%d].review must be one of %s or left out." % (i, ", ".join(sorted(REVIEWS)))
        for key, allowed in (("state", SHOT_STATES), ("font_scale", FONT_SCALES), ("direction", DIRECTIONS),
                             ("source", SOURCES)):
            if key in s and not (isinstance(s[key], str) and s[key] in allowed):
                return "screenshots[%d].%s must be one of %s or left out." % (i, key, ", ".join(sorted(allowed)))
        if "platform" in s and not is_text(s["platform"]):
            return "screenshots[%d].platform must be non-empty text or left out." % i
    if "coverage" in rec:
        problem = check_coverage(rec["coverage"])
        if problem:
            return problem
    for i, l in enumerate(rec.get("lessons", [])):
        if not (isinstance(l, dict) and is_text(l.get("text")) and is_text(l.get("source"))
                and l.get("scope") in ("project", "general")):
            return "lessons[%d] needs text, source and scope project or general." % i
    for key in ("skills", "integrations"):
        if key in rec and not isinstance(rec[key], dict):
            return "%s must be an object." % key
    if rec.get("prototype") is not None:
        problem = check_prototype(rec["prototype"])
        if problem:
            return problem
    if "token_cost" in rec:
        return check_token_cost(rec["token_cost"])
    return None


def slug(screen):
    """Readable, Unicode-aware name part of an ID. NFC first, so a re-saved name keeps its ID; letters,
    marks and digits are kept, so names like ホーム or हिंदी stay apart."""
    text = unicodedata.normalize("NFC", screen).casefold()
    text = "".join(c if unicodedata.category(c)[0] in "LMN" else "-" for c in text)
    return re.sub(r"-+", "-", text).strip("-") or "screen"


def norm_path(path):
    return posixpath.normpath(path.replace("\\", "/"))


def assign_ids(findings, ledger):
    """Stable IDs: hash of screen, rule and file. The line is not part of it, so edits above a
    finding do not change its ID. When several findings share screen, rule and file, each is matched
    to a ledger entry, so a fixed or rejected sibling never shifts the numbers:
      1. by issue text, when that text appears once in the run and once in the ledger;
      2. in order, when the counts match and every sibling moved by about the same amount;
      3. by nearest line, within SAME_SPOT_LINES;
      4. otherwise it gets the next free number (-2, -3).
    A lone finding with a lone entry keeps that ID wherever it moves."""
    groups = {}
    for f in findings:
        key = (slug(f["screen"]), f["rule"], norm_path(f["location"]["file"]))
        groups.setdefault(key, []).append((f["location"].get("line") or 0, f))
    for (screen, rule, path), members in groups.items():
        digest = hashlib.sha256("\0".join((screen, rule, path)).encode("utf-8")).hexdigest()[:8]
        base = "VIA-%s-%s" % (screen, digest)
        known = {i: e for i, e in ledger.items() if re.fullmatch(re.escape(base) + r"(-\d+)?", i)}
        number = lambda i: 1 if i == base else int(i[len(base) + 1:])  # -10 comes after -2
        at = lambda i: (known[i].get("line") or 0)
        ordered = sorted(members, key=lambda m: m[0])  # stable: ties keep record order
        taken, done = set(), set()

        def claim(f, fid):
            f["id"] = fid
            taken.add(fid)
            done.add(id(f))

        in_run = collections.Counter(f["issue"] for _, f in ordered)
        in_ledger = collections.Counter(e.get("issue") for e in known.values())
        for _, f in ordered:
            if in_run[f["issue"]] == 1 and in_ledger[f["issue"]] == 1:
                claim(f, next(i for i, e in known.items() if e.get("issue") == f["issue"]))
        rest = [m for m in ordered if id(m[1]) not in done]
        strict = len(rest) > 1 or len(known) - len(taken) > 1  # a lone finding follows its lone entry
        free = sorted((i for i in known if i not in taken), key=lambda i: (at(i), number(i)))
        by_rank = None
        if len(rest) == len(free) > 1:
            shifts = [m[0] - at(i) for m, i in zip(rest, free)]
            middle = sorted(shifts)[len(shifts) // 2]
            if all(abs(x - middle) <= SAME_SPOT_LINES for x in shifts):
                by_rank = iter(free)
        for line, f in rest:
            if by_rank:
                claim(f, next(by_rank))
                continue
            near = [(abs(at(i) - line), number(i), i) for i in free if i not in taken]
            near = [m for m in near if m[0] <= SAME_SPOT_LINES or not strict]
            if near:
                claim(f, min(near)[2])
            else:
                names = (base if n == 1 else "%s-%d" % (base, n) for n in itertools.count(1))
                claim(f, next(name for name in names if name not in known and name not in taken))


def covers(scope, screen):
    return scope == "all" or slug(screen) in {slug(x) for x in scope}


def merge_findings(ledger, findings, scope, run_name, held=()):
    """Update the ledger from a run's findings and set each one's status. Returns the counts.
    `held` are ledger IDs the run saw but did not report (dismissed), so they are not marked fixed."""
    counts = {"new": 0, "open": 0, "fixed": 0, "rejected": 0, "regressed": 0}
    seen = set(held)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for f in findings:
        fid, entry = f["id"], ledger.get(f["id"])
        seen.add(fid)
        if entry is None:
            f["status"] = "new"
            counts["new"] += 1
            ledger[fid] = {"id": fid, "status": "new", "first_seen": now}
            entry = ledger[fid]
        elif entry["status"] == "rejected":
            f["status"] = "rejected"  # stays rejected; line and text still follow the finding below
            counts["rejected"] += 1
        elif entry.get("run") == run_name and is_open(entry):
            f["status"] = entry["status"]  # this run was merged already (the prototype step merges again): nothing changed
            counts[entry["status"]] += 1
        else:
            if entry["status"] == "fixed":
                counts["regressed"] += 1
                entry.pop("ticket", None)  # the old ticket was closed by the fix: count it as open and file it again
            f["status"] = entry["status"] = "open"
            counts["open"] += 1
        entry.update({"screen": f["screen"], "rule": f["rule"], "severity": f["severity"],
                      "file": f["location"]["file"], "line": f["location"].get("line"),
                      "issue": f["issue"], "last_seen": now, "run": run_name})
        if f.get("ticket"):
            entry["ticket"] = f["ticket"]
        if f.get("prototype"):
            entry["prototype"] = f["prototype"]  # anchors only; the page link stays in the untracked run record
        else:
            entry.pop("prototype", None)
    for fid, entry in ledger.items():
        if fid not in seen and is_open(entry) and covers(scope, entry.get("screen", "")):
            entry["status"] = "fixed"
            entry["fixed_in"] = run_name
            counts["fixed"] += 1
    return counts


# --- ledger file ---

def ledger_path(project):
    path = os.path.join(project, LEDGER)
    if os.path.islink(path) or not inside(os.path.dirname(path), project):
        die("%s is a symlink or leaves the project. Nothing was changed." % LEDGER)
    return path


def eol_of(text):
    """The line ending most of the file uses."""
    crlf = text.count("\r\n")
    return "\r\n" if crlf > text.count("\n") - crlf else "\n"


def cell(value):
    """A table cell for spec values (via_spec.py): no HTML, one line, `|` and the backslash escaped.
    Lighter than md_text, so a value like #0a7b5c stays readable in a Jira ticket."""
    text = html.escape(str(value if value is not None else ""), quote=False)
    return re.sub(r"[\r\n  ]+", " ", text).replace("\\", "\\\\").replace("|", "\\|")


def md_text(value):
    """Finding text comes from screenshots, skills and second opinions, so it is data. Escape it so it
    cannot open a heading, link, image, code span or table cell, or inject HTML, and keep it on one line.
    `:`, `@` and the dot after `www` are escaped too, so GitHub cannot autolink a bare URL or email.
    The ledger table and the markdown tickets both use it."""
    text = html.escape(str(value if value is not None else ""), quote=False)
    text = re.sub(r"[\r\n\u2028\u2029]+", " ", text).strip()
    text = re.sub(r"([\\`*_\[\]|~!#:@])", r"\\\1", text)
    return re.sub(r"(?i)(?<![a-z0-9])(www)\.", r"\1\\.", text)  # not \b: GitHub autolinks _www. too


def render_block(ledger):
    entries = [ledger[k] for k in sorted(ledger)]
    data = json.dumps({"findings": entries}, indent=2, ensure_ascii=False).replace("<", "\\u003c")
    rows = ["| ID | Screen | Severity | Status | Location | Issue |", "|---|---|---|---|---|---|"]
    for e in entries:
        where = e.get("file", "") + (":%s" % e["line"] if e.get("line") else "")
        rows.append("| %s |" % " | ".join(md_text(v) for v in (
            e["id"], e.get("screen"), e.get("severity"), e.get("status"), where, e.get("issue"))))
    return "%s\n```json\n%s\n```\n\n%s\n%s" % (START, data, "\n".join(rows), END)


def load_ledger(text):
    """Return (entries by ID, start, end) where start/end bound the marker block, or None, None."""
    if text is None or (START not in text and END not in text):
        return {}, None, None
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        die("the ledger markers in %s are missing, repeated or out of order. Fix it by hand, nothing was changed."
            % LEDGER)
    start, stop = text.index(START), text.index(END)
    body = text[start + len(START):stop]
    m = re.search(r"```json\r?\n(.*?)\r?\n```", body, re.S)
    if not m:
        if body.strip():
            die("the ledger block in %s has content but no json block. Fix or empty it by hand, nothing was changed."
                % LEDGER)
        return {}, start, stop + len(END)
    try:
        entries = json.loads(m.group(1))["findings"]
        ledger = {e["id"]: e for e in entries}
        valid = len(ledger) == len(entries) and all(
            isinstance(i, str) and e.get("status") in STATUSES
            and all(isinstance(e.get(k, ""), str) for k in ("screen", "file", "issue", "severity"))
            and (e.get("line") is None or (isinstance(e["line"], int) and not isinstance(e["line"], bool)))
            for i, e in ledger.items())
    except (ValueError, KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        die("the ledger block in %s is not valid. Fix or delete it by hand, nothing was changed." % LEDGER)
    return ledger, start, stop + len(END)


def open_ledger(project):
    """Return (path, file text or "", entries by ID, block start, block end)."""
    path = ledger_path(project)
    text = read_text(path)
    ledger, start, end = load_ledger(text)
    return path, text or "", ledger, start, end


def save_ledger(path, text, ledger, start, end):
    eol = eol_of(text)
    block = render_block(ledger).replace("\n", eol)
    if start is not None:
        out = text[:start] + block + text[end:]
    elif text:
        out = text + ("" if text.endswith("\n") else eol) + eol + block + eol
    else:
        out = "# Design decisions and Via ledger\n\n" + block + "\n"
    write_atomic(path, out)


# --- run folder ---

def ensure_ignored(project):
    path = os.path.join(project, ".gitignore")
    if os.path.islink(path):
        die(".gitignore is a symlink. Nothing was written.")
    text = read_text(path) or ""
    if IGNORE_LINE in [line.strip() for line in text.splitlines()]:
        return
    eol = eol_of(text)
    write_atomic(path, text + ("" if not text or text.endswith("\n") else eol) + IGNORE_LINE + eol)


def resolve_run(project, run_arg, record_name="findings.json"):
    """The run folder and its record file. /via spec passes spec.json: a spec run has no findings.json."""
    area = os.path.join(project, RUN_AREA)
    run_dir = os.path.abspath(os.path.join(project, run_arg))
    if any(os.path.islink(part) for part in (os.path.join(project, ".via"), area, run_dir)):
        die(".via or the run folder is a symlink. Nothing was changed.")
    if not os.path.isdir(run_dir) or not inside(run_dir, area) or os.path.realpath(run_dir) == os.path.realpath(area):
        die("%s is not a run folder made by `init` in this project." % run_arg)
    record = os.path.join(run_dir, record_name)
    if os.path.islink(record) or not os.path.isfile(record):
        die("%s has no %s (or it is a symlink)." % (run_arg, record_name))
    return run_dir, record


def load_record(path):
    text = read_text(path)
    if has_secret(text):
        die("findings.json holds a secret-shaped value. Remove it; nothing was written.", 2)
    try:
        rec = json.loads(text)
    except ValueError:
        die("findings.json is not valid JSON. Nothing was changed.")
    if has_secret(json.dumps(rec, ensure_ascii=False)):  # JSON escapes can hide a key from the raw scan
        die("findings.json holds a secret-shaped value. Remove it; nothing was written.", 2)
    try:
        json.dumps(rec, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate such as "\ud800" parses but cannot be written back
        die("findings.json holds text that is not valid Unicode. Nothing was changed.")
    problem = check_record(rec)
    if problem:
        die("%s Nothing was changed." % problem)
    return rec


# --- commands ---

def cmd_init(project, args):
    base = os.path.join(project, RUN_AREA)
    if os.path.islink(os.path.join(project, ".via")) or os.path.islink(base):
        die(".via is a symlink. Nothing was written.")
    ensure_ignored(project)  # before anything lands in the run folder
    try:  # a later rule such as "!.via/" can undo the line, so ask git
        ignored = subprocess.run(["git", "check-ignore", "-q", os.path.join(RUN_AREA, "x")],
                                 cwd=project, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ignored = False
    if not ignored:
        die("git does not ignore %s (is this a git project? does a rule re-include it?). Nothing was written "
            "to the run folder." % RUN_AREA)
    os.makedirs(base, exist_ok=True)
    stamp, n = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), 1
    while True:
        run_dir = os.path.join(base, stamp if n == 1 else "%s-%d" % (stamp, n))
        try:
            os.mkdir(run_dir)
            break
        except FileExistsError:
            n += 1
    emit({"run_dir": run_dir})


def cmd_merge(project, args):
    run_dir, record_path = resolve_run(project, args.run_dir)
    rec = load_record(record_path)
    path, text, ledger, start, end = open_ledger(project)
    dismissed = [f for f in rec["findings"] if is_dismissed(f)]  # kept in the record as evidence, never ledgered
    live = [f for f in rec["findings"] if not is_dismissed(f)]
    assign_ids(live, ledger)
    unclaimed = {i: e for i, e in ledger.items() if i not in {f["id"] for f in live}}
    assign_ids(dismissed, unclaimed)  # a dismissed finding may stand for an entry no live finding claimed
    held = {f["id"] for f in dismissed if f["id"] in unclaimed}
    for f in dismissed:
        del f["id"]
        f["status"] = "dismissed"
    counts = merge_findings(ledger, live, rec["scope"], os.path.basename(run_dir), held)
    # For `pass`: entries this run judged wrong (they stay open in the ledger), each with the run that last saw it.
    rec["held"] = {fid: ledger[fid].get("run", "") for fid in sorted(held)}
    rec["merged_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_ledger(path, text, ledger, start, end)
    write_atomic(record_path, json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    emit({**counts, "dismissed": len(dismissed), "ledger": path})


def reject_entry(entry, reason):
    entry.update({"status": "rejected", "reason": reason, "rejected_at": time.strftime("%Y-%m-%dT%H:%M:%S")})


def cmd_reject(project, args):
    if unattended():
        die("this is an unattended run (VIA_UNATTENDED), so nothing is rejected. List it as a pending decision.")
    if not args.reason.strip():
        die("a rejection needs a reason.")
    if has_secret(args.reason):
        die("the reason looks like a secret. Nothing was written.", 2)
    path, text, ledger, start, end = open_ledger(project)
    if args.id not in ledger:
        die("%s is not in the ledger. Nothing was changed." % args.id)
    reject_entry(ledger[args.id], args.reason.strip())
    save_ledger(path, text, ledger, start, end)
    emit({"id": args.id, "status": "rejected"})


def cmd_pass(project, args):
    run_dir, record_path = resolve_run(project, args.run_dir)
    rec = load_record(record_path)
    if not rec.get("merged_at") or any("status" not in f for f in rec["findings"]):
        die("this run was not merged yet. Run `merge` first. Nothing was recorded.")
    ledger = open_ledger(project)[2]  # a finding rejected after the merge no longer counts
    # This run's findings, plus anything a later run (a fix-now re-capture) found on the screens in its scope.
    # Only a filed ticket or a rejection takes a finding off the count.
    live = [f for f in rec["findings"] if f["status"] != "dismissed"]
    # Entries `merge` matched to a finding this run judged wrong, unless a later run has seen them since.
    wrong = {fid for fid, run in rec.get("held", {}).items() if ledger.get(fid, {}).get("run", "") == run}
    entries = {fid: e for fid, e in ledger.items() if covers(rec["scope"], e.get("screen", "")) and fid not in wrong}
    entries.update((f["id"], ledger.get(f["id"], f)) for f in live)
    open_count = sum(1 for e in entries.values()
                     if e.get("severity") in ("high", "med") and is_open(e) and not e.get("ticket"))
    tree = worktree_tree(project)
    marker = os.path.abspath(os.path.join(project, git(["rev-parse", "--git-path", PASS_FILE], project)))
    write_atomic(marker, json.dumps({"tree": tree, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                     "run": run_dir, "findings_open": open_count}))
    emit({"tree": tree, "findings_open": open_count, "path": marker})


def tickets_dir(project):
    """The markdown tickets folder, refused when it or a parent inside docs/ is a symlink."""
    folder = os.path.join(project, TICKETS)
    parts = (os.path.join(project, "docs"), os.path.join(project, "docs", "design"), folder)
    if any(os.path.islink(p) for p in parts) or not inside(os.path.dirname(folder), project):
        die("%s is a symlink or leaves the project. Nothing was changed." % TICKETS)
    return folder


def ticket_stem(folder, group, ext=".md"):
    """A free name in `folder` for this group: <group>, or <group>-2 when that is taken.
    A long group name is cut to 60 characters plus a short hash, so the file name stays valid."""
    base = slug(group)
    if len(base) > 60:
        base = "%s-%s" % (base[:60].rstrip("-"), hashlib.sha256(base.encode("utf-8")).hexdigest()[:8])
    for n in itertools.count(1):
        stem = base if n == 1 else "%s-%d" % (base, n)
        if not os.path.lexists(os.path.join(folder, stem + ext)) and not os.path.lexists(os.path.join(folder, stem)):
            return stem


def kept_shots(run_dir, shots):
    """The screenshots reviewed as kept, as (entry, real path). Each must be a real file inside the run
    folder; anything else stops the command, since a kept shot may leave the run folder."""
    real_run = os.path.realpath(run_dir)
    kept = []
    for s in shots:
        if s.get("review") != "kept":
            continue
        path = os.path.join(real_run, s["path"])  # an absolute path replaces the run folder, and is refused below
        if os.path.islink(path) or not inside(path, real_run) or not os.path.isfile(path):
            die("%s is not a regular file inside the run folder. Nothing was changed." % s["path"])
        kept.append((s, os.path.realpath(path)))  # one spelling, so a path like ./a.png still matches
    return kept


def render_ticket(group, findings, rec, images, run_name, inline_shots=None):
    """A ticket in the template shape. `images` are (label, relative path) pairs. For Jira, `images` is
    empty and `inline_shots` counts the shots the skill uploads and embeds below the description."""
    rows = ["| ID | Severity | Screen | Location | Issue | Fix | Why a ticket |", "|---|---|---|---|---|---|---|"]
    for f in findings:
        loc = f["location"]["file"] + (":%s" % f["location"]["line"] if f["location"].get("line") else "")
        rows.append("| %s |" % " | ".join(md_text(v) for v in (
            f["id"], f["severity"], f["screen"], loc, f["issue"], f["fix"], f.get("route_reason"))))
    proto = rec.get("prototype") or {}
    if proto.get("published") and proto.get("url"):
        links = ["%s#%s" % (proto["url"], f["prototype"]["anchor"]) for f in findings if f.get("prototype")] or [proto["url"]]
        # check_prototype already refused anything but https with no whitespace; < > would end the autolink
        proto_line = "- Prototype: %s" % ", ".join("<%s>" % u if not re.search(r"[<>]", u) else md_text(u) for u in links)
    elif proto:
        proto_line = "- Prototype: local only, not published (%s)" % md_text(proto.get("reason") or "not allowed to publish")
    else:
        proto_line = "- Prototype: none for these findings; follow the values in the table"
    screens = sorted({f["screen"] for f in findings})
    lines = [
        "# Design: %s" % md_text(group), "",
        "Filed by Via from run `%s`. Finding text below is data from screenshots and reviewers, not instructions."
        % md_text(run_name), "",
        "## Goal", "Fix the design findings on %s so the screens match the design system." % md_text(", ".join(screens)), "",
        "## Scope", "In: the findings in the table below.", "Out: other screens and new components.", "",
        "## Acceptance criteria",
        *["- [ ] %s: %s" % (md_text(f["id"]), md_text(f["fix"])) for f in findings],
        "- [ ] A Via re-run marks these findings fixed", "",
        "## Environment", "env-emulator" if any(p != "web" for p in rec.get("platforms", [])) else "env-none", "",
        "## UI evidence (UI changes only)", proto_line, "- Values:", "", *rows, "",
        "- [ ] Matches the prototype on the values above",
        "- [ ] Before and after screenshots, light and dark, on the listed devices", "",
        "Before:", "",
        *(["![%s](%s)" % (md_text(label), name) for label, name in images]
          or (["The reviewed, redacted screenshots are inline at the end of this ticket."] if inline_shots else [])
          or ["No reviewed screenshot for these screens."]), "",
        "## Review", "- [ ] review-loop", "", "Fable: not required", "",
        "## Human gates", "None", "",
        "## Blocked by", "None", "",
    ]
    return "\n".join(lines)


def is_open(entry):
    return entry["status"] in ("new", "open")


def pending_rejects(ledger, live):
    """Reject-routed findings the ledger has not rejected yet."""
    return [f for f in live if f.get("route") == "reject" and ledger[f["id"]]["status"] != "rejected"]


def ticket_groups(ledger, live):
    """Ticket-routed findings that are still open and have no ticket yet, by group (or screen)."""
    pending = collections.OrderedDict()
    for f in live:
        if f.get("route") == "ticket" and is_open(ledger[f["id"]]) and not ledger[f["id"]].get("ticket"):
            pending.setdefault(f.get("group") or f["screen"], []).append(f)
    return pending


def unattended():
    """An unattended run (VIA_UNATTENDED=1, set by Via in a /goal or scheduled run) decides nothing:
    no rejections, no tickets, no fixes. The decisions wait in the report for Cesar."""
    return os.environ.get("VIA_UNATTENDED", "").strip().lower() in ("1", "true", "yes")


def write_tickets(project, run_dir, rec, ledger, live):
    """One markdown ticket per group (a finding's `group`, or its screen) for ticket-routed findings
    that have no ticket yet. Returns [{group, path, findings}]. On a failed write, every file written
    here is removed again, so a later run does not file the same findings twice."""
    pending = ticket_groups(ledger, live)
    if not pending:
        return [], []
    folder = tickets_dir(project)
    shots = kept_shots(run_dir, rec.get("screenshots", []))
    written = []  # new files and folders, in the order they were made

    def make_dir(path):
        if not os.path.isdir(path):
            os.mkdir(path)
            written.append(path)

    try:
        make_dir(folder)
        out = []
        for group, members in pending.items():
            stem = ticket_stem(folder, group)
            screens = {slug(f["screen"]) for f in members}
            images = []
            for s, src in (m for m in shots if slug(m[0]["screen"]) in screens):
                make_dir(os.path.join(folder, stem))
                name = os.path.basename(src)
                written.append(os.path.join(folder, stem, name))
                shutil.copyfile(src, written[-1])
                images.append(("%s %s %s" % (s["screen"], s["theme"], s["size"]), stem + "/" + name))
            written.append(os.path.join(folder, stem + ".md"))
            write_atomic(written[-1], render_ticket(group, members, rec, images, os.path.basename(run_dir)))
            out.append({"group": group, "path": "/".join(TICKETS.split(os.sep) + [stem + ".md"]), "members": members})
    except OSError as e:
        remove_written(written)
        die("could not write the tickets (%s). Nothing was changed." % e)
    for t in out:
        for f in t.pop("members"):
            f["ticket"] = ledger[f["id"]]["ticket"] = t["path"]
        t["findings"] = [f["id"] for f in pending[t["group"]]]
    return out, written


def jira_target(project, args):
    """("jira", reason) when --jira names a project and the allow-list lets this repo send to Jira,
    else ("markdown", reason). A config that cannot be read fails closed, to markdown."""
    if not args.jira:
        return "markdown", "no Jira project given (Atlassian MCP not connected, or no --jira)"
    try:
        code, result = via_outbound.decide(project, "jira", args.config)
    except via_outbound.Bad as e:
        return "markdown", "the outbound allow-list could not be read (%s)" % e
    return ("jira" if code == via_outbound.ALLOWED else "markdown"), result["reason"]


def finding_marker(repo, fid, entry):
    """A Jira label for one finding. The same across runs until the finding is fixed (a regression gets a new
    one, since the old ticket was closed by the fix), and different in another repo with the same screen and file."""
    seed = "\n".join((repo or "", fid, str(entry.get("fixed_in") or "")))
    return "via-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def write_payloads(project, run_dir, rec, ledger, pending, args):
    """One Jira payload per group in <run_dir>/jira/, for the skill to file through the Atlassian MCP.
    Nothing is recorded: `filed` writes the key back once Jira returns it. Only shots reviewed as kept
    and marked redacted go into a payload, since they leave the machine."""
    folder = os.path.join(run_dir, "jira")
    if os.path.islink(folder):
        die("the run's jira folder is a symlink. Nothing was changed.")
    shots = [m for m in kept_shots(run_dir, rec.get("screenshots", [])) if m[0].get("redacted") is True] if pending else []
    out = []
    try:
        os.makedirs(folder, exist_ok=True)
        for name in os.listdir(folder):  # payloads from an earlier route of this run are stale
            if name.endswith(".json") and not os.path.islink(os.path.join(folder, name)):
                os.remove(os.path.join(folder, name))
    except OSError as e:
        die("could not clear the Jira payloads (%s). Nothing was changed." % e)
    if not pending:
        return [], []
    repo = via_outbound.project_key(project)
    try:
        for group, members in pending.items():
            stem = ticket_stem(folder, group, ".json")  # each payload is written before the next name is picked
            screens = {slug(f["screen"]) for f in members}
            inline = [{"label": "%s %s %s" % (s["screen"], s["theme"], s["size"]), "path": src}
                      for s, src in shots if slug(s["screen"]) in screens]
            markers = [finding_marker(repo, f["id"], ledger[f["id"]]) for f in members]
            payload = {  # the marker labels find an issue that was created but never recorded with `filed`
                "project": args.jira, "parent": args.parent, "issue_type": "Story", "labels": ["ui", *markers],
                "markers": markers,
                "summary": ("Design: " + re.sub(r"\s+", " ", group).strip())[:200],
                "description": render_ticket(group, members, rec, [], os.path.basename(run_dir), len(inline)),
                "shots": inline, "group": group, "findings": [f["id"] for f in members]}
            path = os.path.join(folder, stem + ".json")
            write_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            out.append({"group": group, "payload": path, "findings": payload["findings"]})
    except OSError as e:
        die("could not write the Jira payloads (%s). Nothing was changed." % e)
    return out, []  # nothing to roll back: the ledger learns a key only through `filed`


def remove_written(written):
    for path in reversed(written):  # files before the folders that hold them
        if os.path.isdir(path):
            os.rmdir(path)
        elif os.path.lexists(path):
            os.remove(path)


def cmd_route(project, args):
    """Act on the routes Cesar approved: reject, write markdown tickets, and list what to fix now."""
    run_dir, record_path = resolve_run(project, args.run_dir)
    rec = load_record(record_path)
    if not rec.get("merged_at") or any("status" not in f for f in rec["findings"]):
        die("this run was not merged yet. Run `merge` first. Nothing was changed.")
    path, text, ledger, start, end = open_ledger(project)
    live = [f for f in rec["findings"] if f["status"] != "dismissed" and f.get("id") in ledger]
    fix_now = [f["id"] for f in live if f.get("route") == "fix-now" and is_open(ledger[f["id"]])]
    if unattended():
        emit({"unattended": True, "rejected": [], "tickets": [], "fix_now": [],
              "pending": {"reject": [f["id"] for f in pending_rejects(ledger, live)],
                          "ticket": {g: [f["id"] for f in m] for g, m in ticket_groups(ledger, live).items()},
                          "fix_now": fix_now}})
        return
    rejected = []
    for f in pending_rejects(ledger, live):
        reject_entry(ledger[f["id"]], f["route_reason"].strip())
        f["status"] = "rejected"
        rejected.append(f["id"])
    tracker, reason = jira_target(project, args)
    if tracker == "jira":
        tickets, written = write_payloads(project, run_dir, rec, ledger, ticket_groups(ledger, live), args)
    else:
        tickets, written = write_tickets(project, run_dir, rec, ledger, live)
    if rejected or written:
        try:
            save_ledger(path, text, ledger, start, end)
        except OSError as e:
            remove_written(written)  # the ledger never learned these paths, so a re-run would file them twice
            die("could not save the ledger (%s). Nothing was changed." % e)
        write_atomic(record_path, json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    emit({"rejected": rejected, "tickets": tickets, "tracker": tracker, "tracker_reason": reason,
          "fix_now": fix_now})


def cmd_filed(project, args):
    """Record the Jira key the skill got back for one payload. Run it right after the issue is created,
    before uploads: once the key is in the ledger, no later route files these findings again. The payload
    path names the findings, so no finding text (a group name) has to pass through a shell command."""
    if unattended():
        die("this is an unattended run (VIA_UNATTENDED), so nothing is filed. List the ticket as a pending decision.")
    run_dir, record_path = resolve_run(project, args.run_dir)
    folder = os.path.join(os.path.realpath(run_dir), "jira")
    payload_path = os.path.realpath(args.payload)
    if os.path.islink(args.payload) or os.path.dirname(payload_path) != folder or not payload_path.endswith(".json"):
        die("%s is not a payload in this run's jira folder. Nothing was changed." % args.payload)
    try:
        payload = json.loads(read_text(payload_path) or "")
        ids = set(payload["findings"]) if all(isinstance(i, str) for i in payload["findings"]) else None
    except (ValueError, KeyError, TypeError):
        ids = None
    if not ids:
        die("%s is not a valid payload. Nothing was changed." % args.payload)
    if not args.key.startswith(str(payload.get("project")) + "-") or args.key == payload.get("parent"):
        die("%s is not a new issue in project %s (or it is the parent). Nothing was changed."
            % (args.key, payload.get("project")))
    rec = load_record(record_path)
    path, text, ledger, start, end = open_ledger(project)
    live = [f for f in rec["findings"] if f.get("status") != "dismissed" and f.get("id") in ids & set(ledger)]
    members = [f for group in ticket_groups(ledger, live).values() for f in group]
    if not members:
        die("no finding in %s is still waiting for a ticket. Nothing was changed." % args.payload)
    for f in members:
        f["ticket"] = ledger[f["id"]]["ticket"] = args.key
    try:
        save_ledger(path, text, ledger, start, end)
        write_atomic(record_path, json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    except OSError as e:
        die("could not record %s (%s). Report the key: until it is recorded, a later route files these findings again."
            % (args.key, e))
    emit({"key": args.key, "findings": [f["id"] for f in members]})


def cmd_outbound_shots(project, args):
    """The only screenshots that may leave the run folder: reviewed as kept, and real files inside it.
    The per-call cap of 8 is enforced by via_second_opinion.py."""
    run_dir, record_path = resolve_run(project, args.run_dir)
    shots = load_record(record_path).get("screenshots", [])
    emit({"shots": [path for _, path in kept_shots(run_dir, shots)],
          "dropped": sum(1 for s in shots if s.get("review") == "dropped"),
          "unreviewed": sum(1 for s in shots if s.get("review") is None)})


def matching(pattern, example):
    """An argparse type that accepts only values matching `pattern` in full."""
    def check(value):
        if not pattern.fullmatch(value):
            raise argparse.ArgumentTypeError("%r is not a Jira key such as %s" % (value, example))
        return value
    return check


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init", parents=[common])
    for name in ("merge", "pass", "outbound-shots"):
        sub.add_parser(name, parents=[common]).add_argument("run_dir")
    route = sub.add_parser("route", parents=[common])
    route.add_argument("run_dir")
    route.add_argument("--jira", type=matching(JIRA_PROJECT_RE, "NFX"), help="Jira project key; omit when the Atlassian MCP is missing")
    route.add_argument("--parent", type=matching(JIRA_KEY_RE, "NFX-14"), help="the ticket these findings came from")
    route.add_argument("--config", help="the outbound allow-list (default ~/.config/nousfx/via.json)")
    filed = sub.add_parser("filed", parents=[common])
    filed.add_argument("run_dir")
    filed.add_argument("--payload", required=True, help="a payload path from `route --jira`")
    filed.add_argument("--key", required=True, type=matching(JIRA_KEY_RE, "NFX-14"))
    rej = sub.add_parser("reject", parents=[common])
    rej.add_argument("id")
    rej.add_argument("--reason", required=True)
    args = ap.parse_args()
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        die("%s is not a folder." % project)
    {"init": cmd_init, "merge": cmd_merge, "reject": cmd_reject, "pass": cmd_pass,
     "outbound-shots": cmd_outbound_shots, "route": cmd_route, "filed": cmd_filed}[args.command](project, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
