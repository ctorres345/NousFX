#!/usr/bin/env python3
"""Read-only audit of a Jira board against its repo. Prints findings and a next-up list.

Input is saved Jira search pages, plus local git and `gh pr list`. Writes nothing.
"""
import argparse
import json
import os
import re
import subprocess
from collections import defaultdict

REQUIRED_SECTIONS = ["goal", "scope", "acceptance criteria", "environment", "review", "human gates", "blocked by"]
NOT_REALLY_DONE = re.compile(r"won.?t|duplicate|declined|cannot|can.?t|abandon|obsolete|invalid", re.I)
UI_WORDS = re.compile(r"\b(ui|ux|screen|screens|button|sheet|dialog|layout|theme|dark mode|icon|empty state|tab bar|"
                      r"navigation bar|animation|visual|redesign|widget)\b", re.I)
DEPENDENCY_WORDS = r"(?:blocked by|depends on|requires|after|needs|waiting on|waits for)"
RANK = {"high": 0, "medium": 1, "low": 2}


def load_issues(paths):
    issues, tokens = {}, []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        block = data.get("issues", data)
        info = block.get("pageInfo", {}) if isinstance(block, dict) else {}
        tokens.append(info.get("endCursor") or data.get("nextPageToken"))
        nodes = block.get("nodes", []) if isinstance(block, dict) else block
        for node in nodes:
            issues[node["key"]] = node
    if tokens[-1]:
        raise SystemExit("A Jira page is missing: the last page still has a next-page token. Fetch it and run again.")
    return issues


def category(issue):
    status = issue["fields"].get("status") or {}
    return (status.get("statusCategory") or {}).get("key", "unknown")


def status_name(issue):
    return (issue["fields"].get("status") or {}).get("name", "unknown")


def is_epic(issue):
    return (issue["fields"].get("issuetype") or {}).get("name") == "Epic"


def description(issue):
    text = issue["fields"].get("description") or ""
    return text if isinstance(text, str) else json.dumps(text)


def sections(text):
    found, current = {}, None
    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s*(.+?)\s*#*$", line)
        if heading:
            current = re.sub(r"\s*\(.*\)$", "", heading.group(1)).strip().lower()
            found[current] = ""
        elif current:
            found[current] += line + "\n"
    return found


def blockers_of(key, issues):
    """Keys that block this ticket, with a status for ones outside the search."""
    result = {}
    for link in issues[key]["fields"].get("issuelinks") or []:
        if (link.get("type") or {}).get("name") == "Blocks" and link.get("inwardIssue"):
            other = link["inwardIssue"]
            result[other["key"]] = issues.get(other["key"], other)
    return result


def blocker_is_open(blocker):
    fields = blocker.get("fields") or {}
    status = fields.get("status") or {}
    return (status.get("statusCategory") or {}).get("key") != "done"


def find_cycles(issues):
    graph = defaultdict(set)
    for key in issues:
        for blocker in blockers_of(key, issues):
            graph[blocker].add(key)
    cycles, state = set(), {}

    def visit(node, path):
        state[node] = 1
        for nxt in graph[node]:
            if state.get(nxt) == 1:
                loop = path[path.index(nxt):]
                start = loop.index(min(loop))
                cycles.add(tuple(loop[start:] + loop[:start]))
            elif nxt not in state:
                visit(nxt, path + [nxt])
        state[node] = 2

    for node in list(graph):
        if node not in state:
            visit(node, [node])
    return sorted(cycles)


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120, check=True).stdout


def commit_keys(project, subject):
    """Keys a commit claims: a `KEY: summary` prefix, or a closing `(KEY, KEY)` as Yuyay writes them."""
    prefix = re.match(rf"^({project}-\d+):", subject)
    closing = re.search(r"\(([^()]*)\)\s*$", subject)
    return set(prefix.groups() if prefix else ()) | set(re.findall(rf"\b{project}-\d+\b", closing.group(1) if closing else ""))


def repo_evidence(project, repo):
    """Commits, branches and PRs per ticket key. A failed source becomes a gap, not a clean result."""
    pattern = re.compile(rf"\b{project}-\d+\b")
    # <prefix>/<KEY> for any prefix (NFX-119); a <prefix>/<KEY>-shots branch holds screenshots, not work.
    head = re.compile(rf"^[a-z0-9-]+/({project}-\d+)$")
    evidence = {"commits": defaultdict(list), "branches": {}, "prs": defaultdict(list), "gaps": [], "mentioned": defaultdict(list)}
    try:
        try:
            base = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo).strip()
        except subprocess.CalledProcessError:
            base = "origin/main"
        for line in run(["git", "log", base, "--format=%h\t%s"], repo).splitlines():
            sha, _, subject = line.partition("\t")
            for key in commit_keys(project, subject):
                evidence["commits"][key].append(sha)
            for key in pattern.findall(subject):
                evidence["mentioned"][key].append(f"commit {sha}")
        for line in run(["git", "branch", "-r", "--list", "origin/*", "--format=%(refname:short)"], repo).splitlines():
            match = head.match(line.removeprefix("origin/"))
            if match:
                evidence["branches"].setdefault(match.group(1), line)
                evidence["mentioned"][match.group(1)].append(f"branch {line}")
    except (OSError, subprocess.SubprocessError) as e:
        evidence["gaps"].append(f"git evidence unavailable, so code checks did not use commits or branches: {type(e).__name__}")
    try:
        output = run(["gh", "pr", "list", "--state", "all", "--limit", "1000",
                      "--json", "number,state,headRefName,title,url"], repo)
        for pr in json.loads(output):
            match = head.match(pr["headRefName"])
            title = re.match(rf"^({project}-\d+)\b", pr["title"])
            key = match.group(1) if match else title.group(1) if title else None
            if key:
                pr["by_branch"] = bool(match)
                evidence["prs"][key].append(pr)
                evidence["mentioned"][key].append(f"PR {pr['number']}")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        evidence["gaps"].append(f"GitHub PRs unavailable, so PR checks did not run: {type(e).__name__}")
    return evidence


def audit(project, issues, evidence):
    findings = []

    def add(check, key, confidence, problem, proof, fix):
        findings.append({"id": f"{check}:{key}", "confidence": confidence, "problem": problem, "evidence": proof, "fix": fix})

    for key, issue in issues.items():
        cat, labels = category(issue), issue["fields"].get("labels") or []
        text = description(issue)
        summary = issue["fields"].get("summary") or ""
        prs = evidence["prs"].get(key, [])
        merged = [p for p in prs if p["state"] == "MERGED"]
        open_prs = [p for p in prs if p["state"] == "OPEN"]
        blockers = blockers_of(key, issues)
        open_blockers = sorted(k for k, b in blockers.items() if blocker_is_open(b))

        if cat == "done" and open_blockers:
            add("done-open-blocker", key, "high", "Done while a blocker is unfinished", ", ".join(open_blockers),
                "Remove the link if it is wrong, or finish the blocker")
        if cat == "indeterminate" and open_blockers:
            add("started-open-blocker", key, "high", "In progress while a blocker is unfinished", ", ".join(open_blockers),
                "Finish the blocker, or remove the link if it is wrong")

        for pr in prs:
            if pr["state"] == "OPEN" and cat == "new":
                add("open-pr-todo", key, "high", "Open PR, ticket still To Do", pr["url"], "Move to In Progress")

        if key in evidence["branches"] and not prs:
            add("branch-no-pr", key, "medium", f"Branch exists with no PR (status {status_name(issue)})",
                evidence["branches"][key], "Open a PR or delete the branch")

        if "agent-pr" in labels and prs and not open_prs:
            add("agent-pr-stale", key, "high", "Has agent-pr, but no PR is open", prs[-1]["url"], "Remove agent-pr")

        if is_epic(issue) or cat == "done":
            continue

        env = [l for l in labels if l.startswith("env-")]
        if len(env) != 1:
            add("env-label", key, "high", f"Needs exactly one env-* label, has {len(env)}", ", ".join(env) or "none",
                "Add or fix the env label")

        by_branch = [p for p in merged if p.get("by_branch")]
        if not open_prs:
            if by_branch:
                add("merged-open", key, "high", f"PR merged, ticket is {status_name(issue)}", by_branch[-1]["url"],
                    "Confirm the acceptance criteria, then close")
            elif merged or evidence["commits"].get(key):
                proof = merged[-1]["url"] if merged else "commits " + ", ".join(evidence["commits"][key][:5])
                add("merged-open", key, "medium", f"Code on the default branch, ticket is {status_name(issue)}", proof,
                    "Confirm the acceptance criteria, then close")

        for blocker_key, blocker in blockers.items():
            resolution = ((blocker.get("fields") or {}).get("resolution") or {}).get("name") or ""
            if not blocker_is_open(blocker) and NOT_REALLY_DONE.search(resolution):
                add("dropped-blocker", key, "medium", f"Blocker closed as {resolution}", blocker_key,
                    "Decide whether this ticket still makes sense")

        parts = sections(text)
        if "ready-for-agent" in labels:
            problems = []
            for name in REQUIRED_SECTIONS:
                if not parts.get(name, "").strip():
                    problems.append(f"section '{name}' missing or empty")
            gates = parts.get("human gates", "").strip().lower()
            if gates and not re.match(r"^(none|n/a)\b", gates):
                problems.append("human gates is not None")
            if open_blockers:
                problems.append("open blockers " + ", ".join(open_blockers))
            if "env-device" in labels:
                problems.append("env-device stays ready-for-human")
            if len(env) != 1:
                problems.append("env label")
            if problems:
                add("ready-check", key, "high", "ready-for-agent fails the ready check", "; ".join(problems),
                    "Fix the ticket, or replace ready-for-agent with needs-info")

        linked = set(blockers)
        named = {k for k in re.findall(rf"\b{project}-\d+\b", parts.get("blocked by", "")) if k != key}
        for other in sorted(named - linked):
            add("unlinked-dependency", f"{key}<-{other}", "medium", "Blocked by section names a ticket with no link", other,
                f"Link {other} blocks {key}")
        phrase = re.compile(rf"{DEPENDENCY_WORDS}\s+(?:\w+\s+){{0,2}}({project}-\d+)", re.I)
        for other in sorted(set(phrase.findall(text)) - linked - named - {key}):
            add("unlinked-dependency", f"{key}<-{other}", "low", "Text suggests a dependency with no link", other,
                f"Read it, then link {other} blocks {key}")

        looks_ui = bool(UI_WORDS.search(summary)) or bool(parts.get("ui evidence", "").strip())
        if "env-none" in labels and looks_ui:
            add("env-none-ui", key, "low", "env-none, but the summary sounds like UI work", summary[:70], "Consider env-emulator")
        if looks_ui and not re.search(r"screenshot|screen recording|recording", text, re.I):
            add("ui-no-evidence", key, "low", "Looks like UI work, with no screenshot criterion", summary[:70],
                "Add the screenshot criterion")

    for key, sources in evidence["mentioned"].items():
        if key not in issues:
            add("unknown-key", key, "medium", "Ticket key in the repo does not exist in Jira", ", ".join(sources[:3]),
                "Fix the reference, or create the ticket")

    for loop in find_cycles(issues):
        add("cycle", "-".join(loop), "high", "Blocks links form a cycle", " -> ".join(loop + (loop[0],)), "Remove the wrong link")

    return sorted(findings, key=lambda f: (RANK[f["confidence"]], f["id"]))


def next_up(issues, evidence, findings):
    not_ready = {f["id"].split(":", 1)[1] for f in findings if f["id"].startswith("ready-check:")}
    groups = {"Queue or cloud now": [], "Queue or cloud once ready-for-agent": [], "Mac session": [], "Cesar": [], "No env label": []}
    for key, issue in issues.items():
        labels = issue["fields"].get("labels") or []
        if is_epic(issue) or category(issue) != "new" or "needs-info" in labels or "agent-pr" in labels:
            continue
        if any(blocker_is_open(b) for b in blockers_of(key, issues).values()):
            continue
        if key in evidence["branches"] or any(p["state"] in ("OPEN", "MERGED") for p in evidence["prs"].get(key, [])):
            continue
        fable = " (fable-review)" if "fable-review" in labels else ""
        entry = f"{key}{fable}: {(issue['fields'].get('summary') or '')[:70]}"
        if "env-none" in labels:
            ready = "ready-for-agent" in labels and not fable and key not in not_ready
            groups["Queue or cloud now" if ready else "Queue or cloud once ready-for-agent"].append(entry)
        elif "env-emulator" in labels:
            groups["Mac session"].append(entry)
        elif "env-device" in labels:
            groups["Cesar"].append(entry)
        else:
            groups["No env label"].append(entry)
    return groups


def ignored_ids(repo):
    try:
        with open(os.path.join(repo, "docs/agents/jira-sync.md"), encoding="utf-8") as f:
            return set(re.findall(r"^- `([^`]+)`", f.read(), re.M))
    except OSError:
        return set()


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issues", nargs="+", required=True)
    args = parser.parse_args()

    issues = load_issues(args.issues)
    evidence = repo_evidence(args.project, args.repo)
    ignored = ignored_ids(args.repo)
    all_findings = audit(args.project, issues, evidence)
    findings = [f for f in all_findings if f["id"] not in ignored]

    print(f"# {args.project}: {len(issues)} tickets, {len(findings)} findings, {len(all_findings) - len(findings)} ignored\n")
    if evidence["gaps"]:
        print("## Evidence gaps")
        for gap in evidence["gaps"]:
            print(f"- {gap}")
        print()
    print("## Findings\n\n| # | Finding ID | Confidence | Problem | Evidence | Proposed fix |\n|---|---|---|---|---|---|")
    for number, f in enumerate(findings, 1):
        print(f"| {number} | `{f['id']}` | {f['confidence']} | {cell(f['problem'])} | {cell(f['evidence'])} | {cell(f['fix'])} |")
    print("\n## Next up\n")
    for name, entries in next_up(issues, evidence, all_findings).items():
        print(f"### {name} ({len(entries)})")
        for entry in entries:
            print(f"- {entry}")
        print()


if __name__ == "__main__":
    main()
