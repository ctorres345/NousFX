#!/usr/bin/env python3
"""via-eval: runs Via's eval set (plugins/via/evals/cases.json) and reports pass or fail per case.

Usage: via_eval.py [--cases FILE] [--scripts DIR] [--only ID ...] [--json]

Each case pairs a sample prompt with the behavior Via should show. The runner checks that behavior at the
script level, on a throwaway git repo per case, with no model calls and nothing sent out:
  route              `via_run.py route` acts on an approved fix-now, ticket or reject route as expected.
  second-opinion     `via_second_opinion.py` exits 10 (fresh-context fallback) when Gemini is missing.
  unattended-route   with VIA_UNATTENDED=1, `route` changes nothing and prints the decisions as pending.
  unattended-reject  with VIA_UNATTENDED=1, `reject` refuses.
  platform-skills    references/platforms.md lists the named skills, each with a "Run when" rule, in both
                     Skills tables (Web, and Compose and Flutter).
  never-commits      a full run leaves HEAD, refs, index and stash alone, and runs only read-only git
                     commands (a `git` shim on PATH logs every call). SKILL.md states the rule.
--scripts points the checks at another copy of the Via scripts (the tests use it to break one on purpose).
Exit 0 when every case passed, 1 when one failed, 2 when the arguments or the cases file are invalid.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))  # realpath: agy reaches the scripts through a link
sys.path.insert(0, HERE)  # -I leaves the script's folder off sys.path
import nousfx_testhome as th  # noqa: E402 - fakes the user record, so a check's home stays a temp folder

PLUGIN = os.path.dirname(HERE)
DEFAULT_CASES = os.path.join(PLUGIN, "evals", "cases.json")
SKILL = os.path.join(PLUGIN, "skills", "via", "SKILL.md")
PLATFORMS = os.path.join(PLUGIN, "skills", "via", "references", "platforms.md")
NEVER_COMMIT_RULE = "Never commit, push or stash."
LEDGER = os.path.join("docs", "design", "via.md")
TIMEOUT = 120
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
ALLOW_KEY = "acme/app"
# The only git subcommands Via may run: it reads git and hashes the tree (`add` and `write-tree` on a copy of
# the index), nothing more. Anything else, a commit or a writer added later, fails the never-commits case.
GIT_ALLOWED = {"rev-parse", "add", "write-tree", "check-ignore"}
GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
GIT_SHIM = '#!/bin/sh\nprintf \'%%s\\037\' "$@" >> "%s"\nprintf \'\\n\' >> "%s"\nexec "%s" "$@"\n'


class Failed(Exception):
    pass


def expect(condition, detail):
    if not condition:
        raise Failed(detail)


# --- a throwaway project ---

def clean_env(**extra):
    """The caller's environment without VIA_UNATTENDED or GIT_* (a git hook would point every git call at
    the outer repo), and with no global or system git config (commit signing or a global hooks folder would
    break the setup commit), plus extra."""
    base = {k: v for k, v in os.environ.items() if k != "VIA_UNATTENDED" and not k.startswith("GIT_")}
    return {**base, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", **extra}


def git(repo, *args):
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=clean_env(),
                       stdin=subprocess.DEVNULL, timeout=TIMEOUT)
    if r.returncode:
        raise Failed("setup: git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def make_repo(tmp):
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "eval@example.com")  # so a Via that commits does not fail on identity
    git(repo, "config", "user.name", "Via eval")
    write(os.path.join(repo, "app", "Home.kt"), "fun Home() {}\n")
    write(os.path.join(repo, "app", "Settings.kt"), "fun Settings() {}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def finding(screen="home", file="app/Home.kt", rule="contrast", line=10, **extra):
    return {"screen": screen, "severity": "med", "location": {"file": file, "line": line}, "rule": rule,
            "issue": "Eval finding: %s on %s." % (rule, screen), "fix": "Eval fix.",
            "source": "design:accessibility-review", **extra}


class Via:
    """Runs via_run.py the way the skill does: python3 -I -B, JSON on stdout."""

    def __init__(self, scripts, repo, path=None):
        self.script, self.repo, self.path = os.path.join(scripts, "via_run.py"), repo, path

    def run(self, *args, unattended=False):
        env = clean_env(**({"VIA_UNATTENDED": "1"} if unattended else {}))
        if self.path:
            env["PATH"] = self.path
        return subprocess.run([sys.executable, "-I", "-B", self.script, *args, "--project", self.repo],
                              capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=TIMEOUT)

    def ok(self, *args, unattended=False):
        r = self.run(*args, unattended=unattended)
        expect(r.returncode == 0, "via_run.py %s exited %d: %s" % (args[0], r.returncode, r.stderr.strip()))
        try:
            return json.loads(r.stdout)
        except ValueError:
            raise Failed("via_run.py %s printed no JSON" % args[0])

    def merged_run(self, findings):
        run_dir = self.ok("init")["run_dir"]
        rec = {"version": 1, "mode": "audit", "scope": "all", "platforms": ["compose"], "findings": findings}
        write(os.path.join(run_dir, "findings.json"), json.dumps(rec))
        self.ok("merge", run_dir)
        return run_dir, json.loads(read(os.path.join(run_dir, "findings.json")))

    def routed(self, findings, routes):
        """A merged run with the approved routes written onto it, the way Claude does after the batch approval."""
        run_dir, merged = self.merged_run(findings)
        for f, route in zip(merged["findings"], routes):
            f["route"], f["route_reason"] = route, "eval: approved %s" % route
        write(os.path.join(run_dir, "findings.json"), json.dumps(merged))
        return run_dir, merged

    def ledger(self):
        text = read(os.path.join(self.repo, LEDGER))
        m = re.search(r"```json\n(.*?)\n```", text, re.S)
        expect(m, "the ledger has no JSON block")
        return {e["id"]: e for e in json.loads(m.group(1))["findings"]}


# --- the checks: each raises Failed with what went wrong ---

def check_route(case, scripts, tmp):
    via = Via(scripts, make_repo(tmp))
    findings = [finding(**f) for f in case.get("findings", [{}])]
    run_dir, merged = via.routed(findings, [case["route"]] * len(findings))
    out = via.ok("route", run_dir)
    want = case["expect"]
    got = {"fix_now": len(out["fix_now"]), "tickets": len(out["tickets"]), "rejected": len(out["rejected"])}
    expect(got == {k: want[k] for k in got}, "route printed %s, expected %s" % (got, {k: want[k] for k in got}))
    ledger = via.ledger()
    ids = [f["id"] for f in merged["findings"]]
    statuses = {ledger[i]["status"] for i in ids}
    expect(statuses == {want["status"]}, "ledger status %s, expected %s" % (sorted(statuses), want["status"]))
    for t in out["tickets"]:
        expect(os.path.isfile(os.path.join(via.repo, t["path"])), "ticket %s was not written" % t["path"])
    ticketed = [i for i in ids if ledger[i].get("ticket")]
    expect(len(ticketed) == want.get("ticketed", 0), "%d findings carry a ticket path in the ledger, expected %d"
           % (len(ticketed), want.get("ticketed", 0)))
    if case["route"] == "fix-now":
        expect(out["fix_now"] == ids, "fix_now lists %s, expected %s" % (out["fix_now"], ids))
    if case["route"] == "reject":
        expect(all(ledger[i].get("reason") for i in ids), "a rejected finding has no reason in the ledger")


def check_second_opinion(case, scripts, tmp):
    home, project, elsewhere = (os.path.join(tmp, n) for n in ("home", "project", "elsewhere"))
    for d in (home, project, elsewhere):
        os.makedirs(d)
    git(project, "init", "-q")
    git(project, "remote", "add", "origin", "git@github.com:%s.git" % ALLOW_KEY)
    config = os.path.join(home, ".config", "nousfx", "via.json")
    write(config, json.dumps({"version": 1, "integrations": {"gemini": case["gemini"]},
                              "outbound_allowlist": {ALLOW_KEY: {"destinations": ["gemini"]}}}))
    marker = os.path.join(home, "gemini-ran")
    if case.get("cli"):
        cli = os.path.join(home, ".local", "bin", "gemini-api")
        write(cli, '#!/bin/sh\ntouch "%s"\necho \'{"text": "NO FINDINGS"}\'\n' % marker)
        os.chmod(cli, 0o755)
    run_dir = os.path.join(project, ".via", "runs", "20261107T000000Z")
    os.makedirs(run_dir)
    shot = os.path.join(run_dir, "home-light-phone.png")
    with open(shot, "wb") as f:
        f.write(PNG)
    write(os.path.join(run_dir, "findings.json"), json.dumps(
        {"version": 1, "mode": "audit", "scope": "all", "platforms": ["web"], "findings": [],
         "screenshots": [{"path": "home-light-phone.png", "screen": "home", "theme": "light", "size": "phone",
                          "redacted": True, "review": "kept"}]}))
    r = subprocess.run([th.python(home), "-I", "-B", os.path.join(scripts, "via_second_opinion.py"),
                        "--run-dir", run_dir, "--project", project, "--config", config, "--shot", shot,
                        "--brief", "Home screen of the eval app."],
                       capture_output=True, text=True, cwd=elsewhere, stdin=subprocess.DEVNULL, timeout=TIMEOUT,
                       env={"PATH": "/usr/bin:/bin", **th.env(home)})
    expect(r.returncode == 10, "exit %d, expected 10 (fall back): %s" % (r.returncode, r.stderr.strip()))
    out = json.loads(r.stdout)
    expect(out.get("status") == "fallback", "status %r, expected fallback" % out.get("status"))
    expect(case["reason"] in out.get("reason", ""), "reason %r does not say %r" % (out.get("reason"), case["reason"]))
    expect(out.get("shots") == [os.path.realpath(shot)], "the fallback does not hand over the kept shot")
    expect(not os.path.exists(marker), "gemini-api ran although Gemini is missing")


def check_unattended_route(case, scripts, tmp):
    via = Via(scripts, make_repo(tmp))
    findings = [finding(), finding(rule="touch-target", line=30), finding(screen="settings", file="app/Settings.kt")]
    run_dir, merged = via.routed(findings, ["reject", "ticket", "fix-now"])
    ids = [f["id"] for f in merged["findings"]]
    ledger_before, record_before = read(os.path.join(via.repo, LEDGER)), read(os.path.join(run_dir, "findings.json"))
    out = via.ok("route", run_dir, unattended=True)
    expect(out.get("unattended") is True, "route did not report an unattended run")
    expect((out["rejected"], out["tickets"], out["fix_now"]) == ([], [], []), "route acted: %s" % out)
    want = {"reject": [ids[0]], "ticket": {"home": [ids[1]]}, "fix_now": [ids[2]]}
    expect(out.get("pending") == want, "pending is %s, expected %s" % (out.get("pending"), want))
    expect(read(os.path.join(via.repo, LEDGER)) == ledger_before, "the ledger changed")
    expect(read(os.path.join(run_dir, "findings.json")) == record_before, "findings.json changed")
    expect(not os.path.exists(os.path.join(via.repo, "docs", "design", "tickets")), "a ticket was written")


def check_unattended_reject(case, scripts, tmp):
    via = Via(scripts, make_repo(tmp))
    _, merged = via.merged_run([finding()])
    before = read(os.path.join(via.repo, LEDGER))
    r = via.run("reject", merged["findings"][0]["id"], "--reason", "eval", unattended=True)
    expect(r.returncode == 1, "reject exited %d, expected 1" % r.returncode)
    expect("unattended" in r.stderr, "the refusal does not say why: %r" % r.stderr.strip())
    expect(read(os.path.join(via.repo, LEDGER)) == before, "the ledger changed")


def git_state(repo):
    return {"HEAD": git(repo, "rev-parse", "HEAD"), "branch": git(repo, "symbolic-ref", "-q", "HEAD"),
            "refs": git(repo, "for-each-ref"), "index": git(repo, "ls-files", "-s"),
            "stash": git(repo, "stash", "list"), "config": git(repo, "config", "--local", "--list")}


def subcommand(args):
    i = 0
    while i < len(args):
        if args[i] in GIT_VALUE_FLAGS:
            i += 2
        elif args[i].startswith("-"):
            i += 1
        else:
            return args[i]
    return None


def check_never_commits(case, scripts, tmp):
    expect(NEVER_COMMIT_RULE in read(SKILL), "SKILL.md does not state %r" % NEVER_COMMIT_RULE)
    repo = make_repo(tmp)
    real = shutil.which("git")
    expect(real, "setup: git is not on PATH")
    shim_dir, log = os.path.join(tmp, "shim"), os.path.join(tmp, "git.log")
    write(os.path.join(shim_dir, "git"), GIT_SHIM % (log, log, real))
    os.chmod(os.path.join(shim_dir, "git"), 0o755)
    before = git_state(repo)
    via = Via(scripts, repo, path=shim_dir + os.pathsep + os.environ.get("PATH", ""))
    findings = [finding(), finding(rule="touch-target", line=30), finding(screen="settings", file="app/Settings.kt"),
                finding(rule="spacing", line=50)]
    run_dir, merged = via.routed(findings, ["reject", "ticket", "fix-now"])
    via.ok("reject", merged["findings"][3]["id"], "--reason", "eval: a single rejection")
    via.ok("route", run_dir, unattended=True)
    via.ok("route", run_dir)
    write(os.path.join(repo, "app", "Settings.kt"), "fun Settings() { /* fixed */ }\n")  # the fix-now edit
    via.ok("pass", run_dir)
    after = git_state(repo)
    changed = [k for k in before if before[k] != after[k]]
    calls = [line.split("\037")[:-1] for line in read(log).splitlines() if line] if os.path.exists(log) else []
    expect(calls, "the git shim saw no calls, so the check proved nothing")
    others = sorted({subcommand(c) or "(none)" for c in calls} - GIT_ALLOWED)
    expect(not others and not changed, "Via ran git %s and changed %s" % (others or "-", changed or "-"))


def skill_tables(text):
    """The rows of every `### Skills` table in platforms.md, as a list of {skill: run-when} dicts."""
    tables = []
    for body in re.findall(r"^### Skills\n(.*?)(?=^#{1,3} |\Z)", text, re.M | re.S):
        rows = {}
        for line in body.splitlines():
            m = re.match(r"\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*$", line)
            if m:
                rows[m.group(1)] = m.group(2)
        tables.append(rows)
    return tables


def check_platform_skills(case, scripts, tmp):
    tables = skill_tables(read(PLATFORMS))
    expect(len(tables) == 2, "platforms.md has %d Skills tables, expected 2 (Web, Compose and Flutter)" % len(tables))
    for name, rows in zip(("Web", "Compose and Flutter"), tables):
        for skill in case["skills"]:
            expect(skill in rows, "the %s Skills table has no row for %s" % (name, skill))
            expect(rows[skill], "the %s row for %s has an empty Run when" % (name, skill))


CHECKS = {"route": check_route, "second-opinion": check_second_opinion, "unattended-route": check_unattended_route,
          "unattended-reject": check_unattended_reject, "never-commits": check_never_commits,
          "platform-skills": check_platform_skills}


def load_cases(path):
    try:
        with open(path, encoding="utf-8") as f:
            cases = json.load(f)["cases"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise SystemExit("Stopped: cannot read the cases in %s (%s)." % (path, e))
    if not isinstance(cases, list) or not cases:
        raise SystemExit("Stopped: %s has no cases." % path)
    ids = set()
    for c in cases:
        if not isinstance(c, dict) or c.get("kind") not in CHECKS or not c.get("id") or c["id"] in ids:
            raise SystemExit("Stopped: invalid case %r in %s (needs a unique id and a known kind)." % (c, path))
        ids.add(c["id"])
    return cases


def run_case(case, scripts):
    with tempfile.TemporaryDirectory(prefix="via-eval-") as tmp:
        detail = ""
        try:
            CHECKS[case["kind"]](case, scripts, os.path.realpath(tmp))
        except Failed as e:
            detail = str(e)
        except Exception as e:  # any other error fails this case only, so the report still covers every case
            detail = "%s: %s" % (type(e).__name__, e)
        return {"id": case["id"], "ok": not detail, "detail": detail}


def main(argv=None):
    p = argparse.ArgumentParser(description="Run Via's eval set.")
    p.add_argument("--cases", default=DEFAULT_CASES)
    p.add_argument("--scripts", default=HERE, help="folder with the Via scripts to check")
    p.add_argument("--only", action="append", default=[], metavar="ID")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    try:
        cases = load_cases(args.cases)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    unknown = sorted(set(args.only) - {c["id"] for c in cases})
    if unknown:
        print("Stopped: no case named %s." % ", ".join(unknown), file=sys.stderr)
        return 2
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    scripts = os.path.abspath(args.scripts)
    results = []
    for case in cases:
        results.append(run_case(case, scripts))
        if not args.json:
            x = results[-1]
            print("ok   %s" % x["id"] if x["ok"] else "FAIL %s: %s" % (x["id"], x["detail"]), flush=True)
    passed = sum(x["ok"] for x in results)
    if args.json:
        print(json.dumps({"results": results, "passed": passed, "total": len(results)}, indent=2))
    else:
        print("%d/%d cases passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
