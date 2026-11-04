#!/usr/bin/env python3
"""via-run: the bookkeeping behind Via's check and audit runs.

Claude makes the design calls and writes a run record (findings.json). This tool does the rest:
stable finding IDs, the findings ledger, the run folder and the pass record the gate reads.

Usage: via_run.py init   [--project DIR]
       via_run.py merge  RUN_DIR [--project DIR]
       via_run.py reject ID --reason TEXT [--project DIR]
       via_run.py pass   RUN_DIR [--project DIR]

Order for a run: init, write RUN_DIR/findings.json, merge, then pass once the code is final.
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
import subprocess
import sys
import tempfile
import time
import unicodedata

RUN_AREA = os.path.join(".via", "runs")
IGNORE_LINE = ".via/"
LEDGER = os.path.join("docs", "design", "via.md")
START, END = "<!-- via:ledger:start -->", "<!-- via:ledger:end -->"
PASS_FILE = "via-check-ok"

SEVERITIES = {"high", "med", "low"}
VERDICTS = {"real", "wrong", "unsure"}
ROUTES = {"fix-now", "ticket"}
SECOND_OPINION = "second-opinion"  # source prefix: second-opinion:claude, later second-opinion:argon
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
        if f.get("ticket") is not None and not is_text(f["ticket"]):
            return "%s.ticket must be text." % where
    for i, s in enumerate(rec.get("screenshots", [])):
        if not (isinstance(s, dict) and all(is_text(s.get(k)) for k in ("path", "screen", "theme", "size"))
                and isinstance(s.get("redacted"), bool)):
            return "screenshots[%d] needs path, screen, theme, size and a true/false redacted." % i
    for i, l in enumerate(rec.get("lessons", [])):
        if not (isinstance(l, dict) and is_text(l.get("text")) and is_text(l.get("source"))
                and l.get("scope") in ("project", "general")):
            return "lessons[%d] needs text, source and scope project or general." % i
    for key in ("skills", "integrations"):
        if key in rec and not isinstance(rec[key], dict):
            return "%s must be an object." % key
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
        else:
            if entry["status"] == "fixed":
                counts["regressed"] += 1
            f["status"] = entry["status"] = "open"
            counts["open"] += 1
        entry.update({"screen": f["screen"], "rule": f["rule"], "severity": f["severity"],
                      "file": f["location"]["file"], "line": f["location"].get("line"),
                      "issue": f["issue"], "last_seen": now, "run": run_name})
        if f.get("ticket"):
            entry["ticket"] = f["ticket"]
    for fid, entry in ledger.items():
        if fid not in seen and entry["status"] in ("new", "open") and covers(scope, entry.get("screen", "")):
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
    text = html.escape(str(value if value is not None else ""), quote=False)
    return re.sub(r"[\r\n\u2028\u2029]+", " ", text).replace("\\", "\\\\").replace("|", "\\|")


def render_block(ledger):
    entries = [ledger[k] for k in sorted(ledger)]
    data = json.dumps({"findings": entries}, indent=2, ensure_ascii=False).replace("<", "\\u003c")
    rows = ["| ID | Screen | Severity | Status | Location | Issue |", "|---|---|---|---|---|---|"]
    for e in entries:
        where = e.get("file", "") + (":%s" % e["line"] if e.get("line") else "")
        rows.append("| %s |" % " | ".join(cell(v) for v in (
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


def resolve_run(project, run_arg):
    area = os.path.join(project, RUN_AREA)
    run_dir = os.path.abspath(os.path.join(project, run_arg))
    if any(os.path.islink(part) for part in (os.path.join(project, ".via"), area, run_dir)):
        die(".via or the run folder is a symlink. Nothing was changed.")
    if not os.path.isdir(run_dir) or not inside(run_dir, area) or os.path.realpath(run_dir) == os.path.realpath(area):
        die("%s is not a run folder made by `init` in this project." % run_arg)
    record = os.path.join(run_dir, "findings.json")
    if os.path.islink(record) or not os.path.isfile(record):
        die("%s has no findings.json (or it is a symlink)." % run_arg)
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
    rec["merged_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_ledger(path, text, ledger, start, end)
    write_atomic(record_path, json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    emit({**counts, "dismissed": len(dismissed), "ledger": path})


def cmd_reject(project, args):
    if not args.reason.strip():
        die("a rejection needs a reason.")
    if has_secret(args.reason):
        die("the reason looks like a secret. Nothing was written.", 2)
    path, text, ledger, start, end = open_ledger(project)
    if args.id not in ledger:
        die("%s is not in the ledger. Nothing was changed." % args.id)
    ledger[args.id].update({"status": "rejected", "reason": args.reason.strip(),
                            "rejected_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    save_ledger(path, text, ledger, start, end)
    emit({"id": args.id, "status": "rejected"})


def cmd_pass(project, args):
    run_dir, record_path = resolve_run(project, args.run_dir)
    rec = load_record(record_path)
    if not rec.get("merged_at") or any("status" not in f for f in rec["findings"]):
        die("this run was not merged yet. Run `merge` first. Nothing was recorded.")
    ledger = open_ledger(project)[2]  # a finding rejected after the merge no longer counts
    open_count = sum(1 for f in rec["findings"] if f["status"] != "dismissed" and f["severity"] in ("high", "med")
                     and ledger.get(f["id"], f).get("status") in ("new", "open") and f.get("route") != "ticket")
    tree = worktree_tree(project)
    marker = os.path.abspath(os.path.join(project, git(["rev-parse", "--git-path", PASS_FILE], project)))
    write_atomic(marker, json.dumps({"tree": tree, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                     "run": run_dir, "findings_open": open_count}))
    emit({"tree": tree, "findings_open": open_count, "path": marker})


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init", parents=[common])
    for name in ("merge", "pass"):
        sub.add_parser(name, parents=[common]).add_argument("run_dir")
    rej = sub.add_parser("reject", parents=[common])
    rej.add_argument("id")
    rej.add_argument("--reason", required=True)
    args = ap.parse_args()
    project = os.path.abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        die("%s is not a folder." % project)
    {"init": cmd_init, "merge": cmd_merge, "reject": cmd_reject, "pass": cmd_pass}[args.command](project, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
