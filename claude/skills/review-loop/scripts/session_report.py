#!/usr/bin/env python3
"""session-report: log each decision while you work, then render one report for Cesar.

Usage:
  session_report.py decide KEY --decision T --why T --rejected T --evidence POINTER --result T
                    [--supersedes ID] [--lesson T] [--held REASON] [--unverified T] [--term "word: meaning"]
                    [--adr TITLE] [--name N [--new]] [--date YYYY-MM-DD]
  session_report.py render [--name N] [--date YYYY-MM-DD] [--prs FILE] [--proofs FILE]
  session_report.py watch add --slug S --ticket KEY --evidence POINTER --note T [--harness | --project] [--new]
                    [--date YYYY-MM-DD]
  session_report.py watch add --merge OLD --into NEW
  session_report.py watch list [--min N]

`decide` appends one line to <root>/<project>/reports/<date>-<name>/decisions.jsonl. `render` writes report.md
beside it: what shipped, decisions, one-way doors held for Cesar, what could not be verified, what to learn.
<root> is $NOUSFX_REPORT_ROOT or ~/Releases; <name> defaults to the ticket key; `decide` refuses a --name that
matches no report folder unless --new is passed (prevents stray folders). Files never go inside a git
checkout, and a value that looks like a secret is refused before anything is written.

The log is append-only (idea from pstack's show-me-your-work skill, MIT, github.com/cursor/plugins).
`decide` never edits or deletes: each entry gets the next id, and a wrong one is corrected by a new entry with
--supersedes ID, which `render` shows beside the old one. A correction keeps the old entry's --held reason
unless it gives its own: only Cesar releases a held door. --evidence is a pointer, never prose: a commit SHA,
#PR, file or file:line, or an https URL. --result says where it stands (tests green, reverted, open,
INCONCLUSIVE). --adr TITLE flags a decision that meets the ADR bar (the delete-comments skill states it); the
review loop's ADR step turns it into docs/adr/NNNN-<slug>.md. A held entry, or a correction of one, gets no --adr.
A refused `decide` names every bad field and the key at once, with one example line.
`render` warns on stderr when a SHA or path does not resolve; PRs and URLs are not checked.

`watch` keeps the watch-item ledger (NFX-272), so a retro counts a mistake across tickets and projects. Rows go
to <root>/<project>/watch.jsonl (--project) or the literal <root>/NousFX/watch.jsonl (--harness: a NousFX script,
skill, hook or the sync); inside NousFX (scripts/nousfx_sync.py exists) every row is a harness row. A new slug needs
--new and the refusal lists the existing ones; a slug lives in one ledger; a repeat ticket is refused. --merge
appends a merge row, never a rewrite. `list` prints each slug with its distinct ticket@project count, most first;
2 or more is a `class`, harness slugs counting across all projects.

--prs FILE is a JSON list of {number, url, title, state}; without it `render` asks `gh pr list`.
--proofs FILE is pasted into the report as is (for example the proof table).

Exit 0 done, 1 other error, 2 refused input (nothing written).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}-[1-9][0-9]{0,6}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Each script ships alone, so every copy of this pattern stays: add a key pattern to all of them
# (`git grep -lF 'AKIA[0-9A-Z]{16}|AIza' -- '*.py'` lists them).
SECRET_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{36}"
    r"|github_pat_[0-9A-Za-z_]{20,}|glpat-[0-9A-Za-z_-]{20,}|(^|[^0-9A-Za-z_-])sk-[0-9A-Za-z_-]{20,}"
    r"|[sr]k_live_[0-9A-Za-z]{10,}|xox[baprs]-[0-9A-Za-z-]{10,}"
    r"|eyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)"
)

FIELDS = ("decision", "why", "rejected", "evidence", "result", "lesson", "held", "unverified", "term", "adr")
REQUIRED = ("decision", "why", "rejected", "evidence", "result")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
PR_RE = re.compile(r"^#[1-9][0-9]*$")
URL_RE = re.compile(r"^https?://\S+$")
EXAMPLE = ('session_report.py decide NFX-191 --decision "Use JSONL" --why "Appending is crash safe" '
           '--rejected "One JSON file" --evidence scripts/report.py:40 --result "tests green"')


def die(message, code=1):
    print("Stopped: %s" % message, file=sys.stderr)
    sys.exit(code)


def has_secret(value):
    return bool(SECRET_RE.search(str(value)))


def repo_paths():
    """(work tree top, common git dir) of the checkout in the current folder, or ("", "") outside one.
    One git call; the common dir is shared by all worktrees, so its parent names the project."""
    try:
        r = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        die("git did not answer: %s" % e)
    lines = r.stdout.splitlines() if r.returncode == 0 else []
    return (lines[0], lines[1]) if len(lines) == 2 else ("", "")


def branch_key():
    """The ticket key from a `<prefix>/<KEY>` branch, or None."""
    try:
        r = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    last = r.stdout.strip().rsplit("/", 1)[-1] if r.returncode == 0 else ""
    return last if KEY_RE.match(last) else None


def dated_folders(parent, name):
    """The <date>-<name> folders in parent, oldest first."""
    pattern = r"^\d{4}-\d{2}-\d{2}-%s$" % re.escape(name)
    return sorted(d for d in os.listdir(parent) if re.match(pattern, d)) if os.path.isdir(parent) else []


def report_root():
    return os.path.realpath(os.path.expanduser(os.environ.get("NOUSFX_REPORT_ROOT") or "~/Releases"))


def project_name(repo):
    """The project's folder name: the parent of the common git dir, right from any worktree."""
    common = repo[1]
    return os.path.basename(os.path.dirname(common.rstrip("/"))) if common else os.path.basename(os.getcwd())


def refuse_inside_checkout(folder, repo, what="report folder"):
    top, common = repo
    target = os.path.realpath(folder)  # where files go, which a root that holds the checkout would put inside it
    for path in (top, common, os.path.dirname(common.rstrip("/")) if common else ""):  # last: the main checkout
        if path:
            path = os.path.realpath(path)
            if target == path or target.startswith(path + os.sep):
                die("the %s %s is inside a git checkout. Set NOUSFX_REPORT_ROOT outside it." % (what, target), 2)


def evidence_problem(evidence):
    """The refusal line for an --evidence that is prose rather than a pointer, or None."""
    if evidence and (len(evidence) > 300 or re.search(r"[\s`]", evidence)):
        return ("--evidence is a pointer, not prose: a commit SHA, #PR, file:line or URL, "
                "with no spaces or backticks, at most 300 characters.")
    return None


def append_line(path, line):
    """Append one JSON line with one O_APPEND write, so two sessions appending at once never interleave
    (Mac and Linux, a line under 4 KB). A torn last line gets a newline first, so ours starts fresh."""
    data = (line + "\n").encode("utf-8")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if os.fstat(fd).st_size:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    data = b"\n" + data
        os.write(fd, data)
    finally:
        os.close(fd)


def report_dir(args, repo, key=None, guess=None, refuse_unknown=False):
    parent = os.path.join(report_root(), project_name(repo) or "project", "reports")
    refuse_inside_checkout(parent, repo)
    name = args.name or key
    if not args.date:  # a name keeps one folder across days: the newest one made for it
        for candidate in (name, guess):  # guess: render's ticket key from the branch, tried only with no name
            same = dated_folders(parent, candidate) if candidate else []
            if same:
                return os.path.join(parent, same[-1])
            if name:
                break
    date = args.date or datetime.date.today().isoformat()
    if not DATE_RE.match(date):
        die("--date must look like 2026-11-19.", 2)
    if not name:  # render: take the one report of that date
        found = sorted(d for d in os.listdir(parent) if d.startswith(date + "-")) if os.path.isdir(parent) else []
        if len(found) != 1:
            die("%d reports for %s; pass --name." % (len(found), date), 2)
        name = found[0][len(date) + 1:]
    if not NAME_RE.match(name):
        die("--name may hold letters, digits, dots, hyphens and underscores only.", 2)
    if refuse_unknown and not dated_folders(parent, name):
        have = dated_folders(parent, key)
        die("--name %s matches no report folder. %s Drop --name to log under %s, or pass --new to start a "
            "report named %s on purpose (a wave report, for example)."
            % (name, "Report folders for %s: %s." % (key, ", ".join(have)) if have
               else "No report folders exist for %s." % key, key, name), 2)
    return os.path.join(parent, "%s-%s" % (date, name))


def cmd_decide(args):
    entry = {"key": args.key, "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    for field in FIELDS:
        entry[field] = (getattr(args, field) or "").strip()
    problems = [] if KEY_RE.match(args.key) else [
        "%s is not a ticket key like NFX-191." % ("the key" if has_secret(args.key) else repr(args.key))]
    problems += ["--%s is empty." % field for field in REQUIRED if not entry[field]]
    if args.adr is not None and not entry["adr"]:
        problems.append("--adr is empty: give the ADR's title, or leave the flag out.")
    if entry["held"] and entry["adr"]:
        problems.append("--held with --adr: a held door is not decided yet, so it gets no ADR.")
    if evidence_problem(entry["evidence"]):
        problems.append(evidence_problem(entry["evidence"]))
    if any(has_secret(v) for v in list(entry.values()) + [args.name or ""]):
        problems.append("a value looks like a secret. Nothing was written.")
    if problems:
        die("\n".join(problems + ["A decision needs what, why, the alternative you rejected, evidence and a result.",
                                  "Example: " + EXAMPLE]), 2)
    directory = report_dir(args, repo_paths(), args.key,
                           refuse_unknown=bool(args.name and args.name != args.key and not args.new))
    existing, lines, _bad = read_log(directory)
    ids = {e["id"] for e in existing}
    entry["id"] = max(ids | {lines}) + 1  # past every line, so a bad line's place is never reused
    if args.supersedes is not None:
        if args.supersedes not in ids:
            die("--supersedes %d: there is no such entry in the log." % args.supersedes, 2)
        later = [e["id"] for e in existing if e.get("supersedes") == args.supersedes]
        if later:  # one chain per decision, so the newest entry is the only current one
            die("entry %d already corrects entry %d: supersede entry %d (or the newest in its chain)."
                % (later[-1], args.supersedes, later[-1]), 2)
        entry["supersedes"] = args.supersedes
        old = next(e for e in existing if e["id"] == args.supersedes)
        if old.get("held") and not entry["held"]:  # a correction never releases a held door by leaving it out
            entry["held"] = old["held"]
            if has_secret(entry["held"]):
                die("the held reason carried from entry %d looks like a secret. Nothing was written."
                    % args.supersedes, 2)
    if entry["held"] and entry["adr"]:
        die("entry %d is held, so its correction keeps the held reason: a held door gets no ADR. "
            "Nothing was written." % args.supersedes, 2)
    path = os.path.join(directory, "decisions.jsonl")
    append_line(path, json.dumps(entry, ensure_ascii=False))
    print(path)
    return 0


def read_log(directory):
    """(entries, lines, bad): the readable entries, the count of non-blank lines, and of lines that are not an
    entry (a torn write, a hand edit). A bad line is skipped, never fatal, so the log stays appendable."""
    path = os.path.join(directory, "decisions.jsonl")
    if not os.path.isfile(path):
        return [], 0, 0
    entries, lines, bad = [], 0, 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            lines += 1
            try:
                entry = json.loads(line)
            except ValueError:
                entry = None
            if isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("key",) + REQUIRED) \
                    and all(isinstance(entry.get(k, ""), str) for k in FIELDS) \
                    and all(type(entry.get(k, 1)) is int for k in ("id", "supersedes")) and "id" in entry:
                entries.append(entry)
            else:
                bad += 1
    return entries, lines, bad


def current_entries(entries):
    """The entries no later entry supersedes: the newest in each --supersedes chain."""
    replaced = {e["supersedes"] for e in entries if e.get("supersedes")}
    return [e for e in entries if e["id"] not in replaced]


def unresolved(evidence, top):
    """True when a SHA or path named by `evidence` is not there. PRs and URLs are not checked."""
    if PR_RE.match(evidence) or URL_RE.match(evidence):
        return False
    if SHA_RE.match(evidence):
        try:
            return subprocess.run(["git", "cat-file", "-e", evidence + "^{commit}"], cwd=top,
                                  capture_output=True, timeout=60).returncode != 0
        except (OSError, subprocess.TimeoutExpired):
            return True
    path = re.sub(r":\d+(-\d+)?$", "", evidence)
    return not os.path.exists(os.path.join(top, path))


def cell(text):
    """One table cell: one line, the backslash and `|` escaped (as via_run.py's cell)."""
    return re.sub(r"\s+", " ", text or "").replace("\\", "\\\\").replace("|", "\\|")


def pr_list(args, keys):
    if args.prs:
        try:
            with open(args.prs, encoding="utf-8") as f:
                prs = json.load(f)
        except (OSError, ValueError) as e:
            die("cannot read --prs %s: %s" % (args.prs, e))
        if not isinstance(prs, list) or not all(isinstance(p, dict) for p in prs):
            die("--prs must be a JSON list of {number, url, title, state} objects.", 2)
        if has_secret(json.dumps(prs)):
            die("--prs holds a secret-shaped value. Nothing was written.", 2)
        return prs, None
    if not keys:
        return [], None
    prs = {}
    for key in sorted(keys):
        try:
            r = subprocess.run(["gh", "pr", "list", "--state", "all", "--search", key, "--limit", "200",
                                "--json", "number,url,title,state,headRefName"], capture_output=True, text=True,
                               timeout=120)
            found = json.loads(r.stdout or "[]") if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            found = None
        if not isinstance(found, list):
            return [], "PR list unavailable (`gh pr list` failed)."
        for pr in found:
            if any(re.search(r"(?<![A-Za-z0-9-])%s(?![0-9])" % re.escape(key), pr.get(f) or "")
                   for f in ("title", "headRefName")):
                if has_secret(json.dumps(pr)):
                    die("PR #%s from `gh` holds a secret-shaped value. Nothing was written." % pr.get("number"), 2)
                prs[pr["number"]] = pr
    return sorted(prs.values(), key=lambda p: p["number"]), None


def cmd_render(args):
    repo = repo_paths()
    top = repo[0] or os.getcwd()
    directory = report_dir(args, repo, guess=None if args.name else branch_key())
    entries, _lines, bad = read_log(directory)
    if bad:
        print("Warning: %d unreadable log lines skipped in %s" % (bad, directory), file=sys.stderr)
    keys = {e["key"] for e in entries}
    replaced = {e["supersedes"]: e["id"] for e in entries if e.get("supersedes")}
    prs, pr_problem = pr_list(args, keys)
    out = ["# Session report: %s" % os.path.basename(directory), "", "## What shipped"]
    if pr_problem:
        out.append(pr_problem)
    elif not prs:
        out.append("No pull requests found.")
    else:
        out += ["| PR | title | state |", "| --- | --- | --- |"]
        out += ["| [#%s](%s) | %s | %s |" % (p.get("number"), p.get("url"), cell(p.get("title")), cell(p.get("state")))
                for p in prs]
    out += ["", "## Decisions"]
    if bad:
        out += ["%d unreadable log lines were skipped; the report may be missing decisions." % bad, ""]
    if not entries:
        out.append("No decisions logged. The log is missing or empty, so this report is not a full account.")
        print("Warning: no decisions logged in %s" % directory, file=sys.stderr)
    else:
        out += ["| id | ticket | decision | why | rejected alternative | evidence | result | lesson |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for e in entries:
            bad = unresolved(e["evidence"], top)
            if bad:
                print("Warning: entry %s evidence does not resolve: %s" % (e["id"], e["evidence"]), file=sys.stderr)
            ident = str(e["id"]) + (" (replaces %s)" % e["supersedes"] if e.get("supersedes") else "")
            result = cell(e["result"]) + (" (superseded by %s)" % replaced[e["id"]] if e["id"] in replaced else "")
            out.append("| %s | %s | %s | %s | %s | `%s`%s | %s | %s |" % (
                ident, e["key"], cell(e["decision"]), cell(e["why"]), cell(e["rejected"]), cell(e["evidence"]),
                " (unresolved)" if bad else "", result, cell(e.get("lesson"))))
    current = current_entries(entries)  # a replaced entry stays only in the table
    out += ["", "## Held for Cesar (one-way doors)"]
    held = [e for e in current if e.get("held")]
    out += ["- %s: %s. Held because: %s" % (e["key"], cell(e["decision"]), cell(e["held"])) for e in held] or ["None."]
    out += ["", "## Not verified"]
    out += ["- %s: %s" % (e["key"], cell(e["unverified"])) for e in current if e.get("unverified")] or ["None."]
    if args.proofs:
        try:
            with open(args.proofs, encoding="utf-8") as f:
                proofs = f.read().strip()
        except OSError as e:
            die("cannot read --proofs %s: %s" % (args.proofs, e))
        if has_secret(proofs):
            die("--proofs holds a secret-shaped value. Nothing was written.", 2)
        out += ["", "## Proofs", proofs]
    out += ["", "## What to learn"]
    learn = ["- %s: %s" % (e["key"], cell(e["lesson"])) for e in current if e.get("lesson")]
    learn += ["- %s" % cell(e["term"]) for e in current if e.get("term")]
    out += learn or ["None."]
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(path)
    return 0

# --- watch: the watch-item ledger (NFX-272) ---

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HARNESS = "NousFX"  # the harness ledger's folder: a literal, never a checkout's folder name
ROW_FIELDS = ("project", "slug", "ticket", "evidence", "note", "date")
MERGE_FIELDS = ("project", "merge", "into", "date")
WATCH_EXAMPLE = ('session_report.py watch add --slug testfail-voided-by-edit --ticket NFX-214 --evidence #37 '
                 '--note "an edit during the run voided testfail" --harness')
CHOICE = ("pass exactly one of --harness (about a NousFX script, skill, hook or the sync) "
          "or --project (about this project's own code).")


def is_nousfx(top):
    """True in a NousFX checkout. Each script ships alone, so this copies comment_check.is_nousfx."""
    return bool(top) and os.path.isfile(os.path.join(top, "scripts", "nousfx_sync.py"))


def ledger_paths(repo):
    """{kind: path} of the ledgers this checkout reads. Inside NousFX, or when the project ledger is the harness
    file, only the harness ledger: one file is never read twice."""
    root = report_root()
    paths = {"harness": os.path.join(root, HARNESS, "watch.jsonl")}
    project = os.path.join(root, project_name(repo) or "project", "watch.jsonl")
    if not is_nousfx(repo[0]) and os.path.realpath(project) != os.path.realpath(paths["harness"]):
        paths["project"] = project
    for kind, path in paths.items():
        refuse_inside_checkout(os.path.dirname(path), repo, "%s ledger folder" % kind)
    return paths


def read_ledger(path):
    """(rows, merges, bad): a line that is not a row or a merge (a torn write, a hand edit) is counted, never fatal."""
    rows, merges, bad = [], [], 0
    if not os.path.isfile(path):
        return rows, merges, bad
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if isinstance(row, dict) and all(isinstance(row.get(k), str) for k in ROW_FIELDS):
                rows.append(row)
            elif isinstance(row, dict) and all(isinstance(row.get(k), str) for k in MERGE_FIELDS):
                merges.append(row)
            else:
                bad += 1
    return rows, merges, bad


class Ledger:
    def __init__(self, kind, path):
        self.kind, self.path = kind, path
        self.rows, merges, self.bad = read_ledger(path)
        self.into = {}
        for m in merges:  # the first merge of a slug wins; `add` refuses a second one
            self.into.setdefault(m["merge"], m["into"])

    def canonical(self, slug):
        seen = {slug}
        while slug in self.into and self.into[slug] not in seen:
            slug = self.into[slug]
            seen.add(slug)
        return slug

    def slugs(self):
        return {r["slug"] for r in self.rows} | set(self.into) | set(self.into.values())

    def last_notes(self):
        notes = {}
        for r in self.rows:
            notes[r["slug"]] = r["note"]
        return notes

    def groups(self):
        """{canonical slug: (sorted ticket@project list, last date)}, distinct by ticket and project."""
        out = {}
        for r in self.rows:
            tickets, last = out.get(self.canonical(r["slug"]), (set(), ""))
            tickets.add("%s@%s" % (r["ticket"], r["project"]))
            out[self.canonical(r["slug"])] = (tickets, max(last, r["date"]))
        return {slug: (sorted(t), last) for slug, (t, last) in out.items()}


def load_ledgers(repo):
    return [Ledger(kind, path) for kind, path in sorted(ledger_paths(repo).items(), reverse=True)]


def existing_slugs(ledgers):
    lines = []
    for led in ledgers:
        notes = led.last_notes()
        for slug in sorted(led.slugs()):
            lines.append("  %s%s: %s" % ("[harness] " if led.kind == "harness" else "", slug,
                                         notes.get(slug, "merged into %s" % led.canonical(slug))))
    return lines


def cmd_watch_add(args):
    repo = repo_paths()
    inside = is_nousfx(repo[0])
    project = project_name(repo)
    date = args.date or datetime.date.today().isoformat()
    problems = [] if DATE_RE.match(date) else ["--date must look like 2026-11-19."]
    if not NAME_RE.match(project or ""):
        problems.append("this checkout's folder name cannot name a ledger (letters, digits, dots, hyphens, "
                        "underscores only).")
    if args.merge is not None or args.into is not None:
        return watch_merge(args, repo, project, date, problems)
    values = {f: (getattr(args, f) or "").strip() for f in ("slug", "ticket", "evidence", "note")}
    for field in ("slug", "ticket", "evidence", "note"):
        if not values[field]:
            problems.append("--%s is empty." % field)
    if values["slug"] and not (SLUG_RE.match(values["slug"]) and len(values["slug"]) <= 64):
        problems.append("--slug is kebab case: lower-case letters and digits joined by hyphens, at most 64.")
    if values["ticket"] and not KEY_RE.match(values["ticket"]):
        problems.append("--ticket is not a ticket key like NFX-214.")
    if evidence_problem(values["evidence"]):
        problems.append(evidence_problem(values["evidence"]))
    if len(values["note"]) > 300 or re.search(r"[\r\n]", values["note"]):
        problems.append("--note is one line of at most 300 characters.")
    if any(has_secret(v) for v in list(values.values()) + [date]):
        problems.append("a value looks like a secret. Nothing was written.")
    if inside and args.project:
        problems.append("--project: inside NousFX every row is a harness row; drop --project.")
    elif not inside and args.harness == args.project:
        problems.append("--harness or --project: " + CHOICE)
    kind = "harness" if inside or args.harness else "project"
    ledgers = load_ledgers(repo)
    by_kind = {led.kind: led for led in ledgers}
    target = by_kind.get(kind, by_kind["harness"])
    slug = values["slug"]
    if slug and SLUG_RE.match(slug) and not (inside and args.project) and (inside or args.harness != args.project):
        others = [led for led in ledgers if led is not target and slug in led.slugs()]
        if others:
            problems.append("--slug %s is already a %s slug: a slug lives in one ledger. Add the row there, or pick "
                            "another slug." % (slug, others[0].kind))
        elif slug not in target.slugs() and not args.new:
            have = existing_slugs(ledgers)
            problems.append("--slug %s is new. Reuse an existing slug when it is the same mistake:\n%s\nPass --new to "
                            "start %s on purpose." % (slug, "\n".join(have) if have else "  (no slugs yet)", slug))
        ticket = values["ticket"]
        if ticket and any(target.canonical(r["slug"]) == target.canonical(slug) and r["ticket"] == ticket
                          for r in target.rows):
            problems.append("--ticket %s is already counted under %s: a repeat. Nothing was written."
                            % (ticket, target.canonical(slug)))
    if problems:
        die("\n".join(problems + ["A watch row needs a kebab slug, a ticket key, a pointer as evidence and a "
                                  "one-line note.", "Example: " + WATCH_EXAMPLE]), 2)
    row = dict(project=project, slug=slug, ticket=values["ticket"], evidence=values["evidence"],
               note=values["note"], date=date)
    append_line(target.path, json.dumps(row, ensure_ascii=False))
    print(target.path)
    return 0


def watch_merge(args, repo, project, date, problems):
    old, new = (args.merge or "").strip(), (args.into or "").strip()
    for flag, value in (("--merge", old), ("--into", new)):
        if not SLUG_RE.match(value):
            problems.append("%s needs an existing kebab slug." % flag)
    if any(getattr(args, f) for f in ("slug", "ticket", "evidence", "note")):
        problems.append("--merge OLD --into NEW takes no --slug, --ticket, --evidence or --note.")
    if old and old == new:
        problems.append("--merge and --into name the same slug.")
    ledgers = load_ledgers(repo)
    if not problems:
        home = {s: [led for led in ledgers if s in led.slugs()] for s in (old, new)}
        missing = [s for s in (old, new) if not home[s]]
        if missing:
            problems.append("no ledger has %s.\n%s" % (" or ".join(missing), "\n".join(existing_slugs(ledgers))))
        elif home[old][0] is not home[new][0]:
            problems.append("%s is a %s slug and %s a %s slug: a merge across the two ledgers is refused."
                            % (old, home[old][0].kind, new, home[new][0].kind))
        else:
            led = home[old][0]
            if old in led.into:
                problems.append("%s is already merged into %s." % (old, led.into[old]))
            elif led.canonical(new) == old:
                problems.append("%s already folds into %s: that merge would loop." % (new, old))
    if problems:
        die("\n".join(problems), 2)
    led = home[old][0]
    append_line(led.path, json.dumps(dict(project=project, merge=old, into=new, date=date), ensure_ascii=False))
    print(led.path)
    return 0


def cmd_watch_list(args):
    ledgers = load_ledgers(repo_paths())
    if not any(os.path.isfile(led.path) for led in ledgers):
        print("no ledger yet (%s)" % ", ".join(led.path for led in ledgers))
        return 0
    lines = []
    for led in ledgers:
        if led.bad:
            print("Warning: %d unreadable lines skipped in %s" % (led.bad, led.path), file=sys.stderr)
        for slug, (tickets, last) in led.groups().items():
            lines.append((-len(tickets), led.kind, slug, tickets, last))
    shown = [x for x in sorted(lines) if -x[0] >= args.min]
    for count, kind, slug, tickets, last in shown:
        print("%s%s | %d ticket%s%s | %s | last %s" % ("[harness] " if kind == "harness" else "", slug, -count,
                                                      "" if count == -1 else "s", ", class" if -count >= 2 else "",
                                                      " ".join(tickets), last))
    if not shown:
        print("no slug has %d or more tickets" % args.min)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("decide", formatter_class=argparse.RawDescriptionHelpFormatter, description=(
        "Append one decision to the log.\n\n"
        "Required: --decision, --why, --rejected, --evidence, --result (a blank value counts as missing).\n"
        "--evidence is a pointer, never prose: a commit SHA, #PR, file or file:line, or a URL;\n"
        "no spaces or backticks, at most 300 characters. A refusal names every bad field at once.\n\n"
        "Example:\n  " + EXAMPLE))
    d.add_argument("key")
    d.add_argument("--supersedes", type=int)
    d.add_argument("--new", action="store_true",
                   help="start a report under --name when no folder has that name (a wave report, for example)")
    for field in FIELDS:
        d.add_argument("--" + field)
    r = sub.add_parser("render")
    r.add_argument("--prs")
    r.add_argument("--proofs")
    for p in (d, r):
        p.add_argument("--name")
        p.add_argument("--date")
    w = sub.add_parser("watch", description="The watch-item ledger: a retro counts a mistake across tickets.")
    wsub = w.add_subparsers(dest="action", required=True)
    wa = wsub.add_parser("add", formatter_class=argparse.RawDescriptionHelpFormatter, description=(
        "Append one watch row, or a merge row with --merge OLD --into NEW.\n\n"
        "Outside NousFX, " + CHOICE + "\nA new slug needs --new; a refusal lists the existing slugs.\n\n"
        "Example:\n  " + WATCH_EXAMPLE))
    for field in ("slug", "ticket", "evidence", "note", "date", "merge", "into"):
        wa.add_argument("--" + field)
    for flag in ("harness", "project", "new"):
        wa.add_argument("--" + flag, action="store_true")
    wl = wsub.add_parser("list", description="Slugs by distinct tickets; 2 or more is a class.")
    wl.add_argument("--min", type=int, default=1)
    args = ap.parse_args()
    if args.command == "watch":
        return cmd_watch_add(args) if args.action == "add" else cmd_watch_list(args)
    return cmd_decide(args) if args.command == "decide" else cmd_render(args)


if __name__ == "__main__":
    sys.exit(main())
