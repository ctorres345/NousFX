#!/usr/bin/env python3
"""Usage report for Claude Code transcripts in ~/.claude/projects.

Shows, for the last N days: tokens and estimated cost by project and model, which
subagents ran (and on which model), which tools return the most output, and files read
repeatedly. Read-only. Prices are public API list prices used only to weigh models
against each other; on a subscription plan they are relative weights, not a bill.

Usage: python3 -I scripts/usage-report.py [--days 14] [--project SUBSTRING] [--top 8]
"""
import argparse
import collections
import glob
import json
import os
import re
import time

# Claude names a project folder after its path with every non-alphanumeric made "-"; drop the home part.
HOME_PREFIX = re.sub(r"[^A-Za-z0-9]", "-", os.path.expanduser("~")) + "-"

# $/MTok: input, output, cache read. Cache writes are estimated at 1.25x input.
PRICES = {
    "fable": (10.0, 50.0, 0.25),
    "opus": (4.0, 20.0, 0.20),
    "sonnet": (2.0, 10.0, 0.10),
    "haiku": (0.10, 0.50, 0.01),
}


def family(model):
    for name in PRICES:
        if name in (model or ""):
            return name
    return "other"


def cost(fam, u):
    p = PRICES.get(fam)
    if not p:
        return 0.0
    inp = u.get("input_tokens", 0)
    out = u.get("output_tokens", 0)
    cr = u.get("cache_read_input_tokens", 0)
    cw = u.get("cache_creation_input_tokens", 0)
    return (inp * p[0] + out * p[1] + cr * p[2] + cw * p[0] * 1.25) / 1e6


def parts(fam, u):
    p = PRICES.get(fam)
    if not p:
        return (0, 0, 0, 0)
    return (u.get("input_tokens", 0) * p[0] / 1e6, u.get("output_tokens", 0) * p[1] / 1e6,
            u.get("cache_read_input_tokens", 0) * p[2] / 1e6,
            u.get("cache_creation_input_tokens", 0) * p[0] * 1.25 / 1e6)


def text_len(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(x.get("text", "")) if isinstance(x, dict) else len(str(x)) for x in content)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--project", default="")
    ap.add_argument("--top", type=int, default=8)
    a = ap.parse_args()
    cutoff = time.time() - a.days * 86400
    root = os.path.expanduser("~/.claude/projects")

    proj = collections.defaultdict(lambda: collections.Counter())
    model_tot = collections.defaultdict(lambda: collections.Counter())
    agent_tot = collections.defaultdict(lambda: collections.Counter())
    tool_bytes = collections.Counter()
    tool_calls = collections.Counter()
    reads = collections.Counter()
    sessions = collections.Counter()
    seen = set()
    comp = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    sess = collections.defaultdict(lambda: collections.Counter())
    big = []

    for path in glob.glob(root + "/*/**/*.jsonl", recursive=True):
        if os.path.getmtime(path) < cutoff:
            continue
        rel = os.path.relpath(path, root)
        pname = rel.split(os.sep)[0].replace(HOME_PREFIX, "")
        if a.project and a.project.lower() not in pname.lower():
            continue
        is_sub = os.sep + "subagents" + os.sep in path
        sessions[pname] += 0 if is_sub else 1
        tool_names = {}
        tool_cmd = {}
        sub_type = None
        if is_sub:
            try:
                sub_type = json.load(open(path.replace(".jsonl", ".meta.json"))).get("agentType")
            except (OSError, ValueError):
                sub_type = None
        skey = (pname, rel.split(os.sep)[1].replace(".jsonl", ""))
        sid = rel.split(os.sep)[1].replace(".jsonl", "")
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                msg = d.get("message") or {}
                content = msg.get("content")
                if d.get("type") == "assistant":
                    u = msg.get("usage") or {}
                    key = (msg.get("id"), d.get("requestId"))
                    if msg.get("id") and key in seen:
                        pass
                    else:
                        if msg.get("id"):
                            seen.add(key)
                        fam = family(msg.get("model"))
                        c = cost(fam, u)
                        for i, v in enumerate(parts(fam, u)):
                            comp[fam][i] += v
                        if not is_sub:
                            ctx = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                                   + u.get("cache_creation_input_tokens", 0))
                            sc = sess[skey]
                            sc["calls"] += 1
                            sc["ctx_sum"] += ctx
                            sc["ctx_max"] = max(sc["ctx_max"], ctx)
                            sc["cost_milli"] += int(c * 1000)
                        for bucket in (proj[pname], model_tot[fam]):
                            bucket["calls"] += 1
                            bucket["in"] += u.get("input_tokens", 0)
                            bucket["out"] += u.get("output_tokens", 0)
                            bucket["cache_read"] += u.get("cache_read_input_tokens", 0)
                            bucket["cache_write"] += u.get("cache_creation_input_tokens", 0)
                            bucket["cost_milli"] += int(c * 1000)
                        if is_sub:
                            at = sub_type or "subagent"
                            agent_tot[(at, fam)]["calls"] += 1
                            agent_tot[(at, fam)]["tokens"] += (
                                u.get("input_tokens", 0) + u.get("output_tokens", 0)
                                + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0))
                            agent_tot[(at, fam)]["cost_milli"] += int(c * 1000)
                    if isinstance(content, list):
                        for b in content:
                            if b.get("type") == "tool_use":
                                tool_names[b.get("id")] = b.get("name")
                                tool_cmd[b.get("id")] = str((b.get("input") or {}).get("command") or (b.get("input") or {}).get("file_path") or "")[:90]
                                tool_calls[b.get("name")] += 1
                                inp = b.get("input") or {}
                                if b.get("name") == "Read" and inp.get("file_path"):
                                    reads[(sid, inp["file_path"])] += 1
                                if b.get("name") in ("Agent", "Task"):
                                    st = inp.get("subagent_type") or "general-purpose"
                                    agent_tot[("CALLED:" + st, "-")]["calls"] += 1
                elif d.get("type") == "user" and isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            n = text_len(b.get("content"))
                            tn = tool_names.get(b.get("tool_use_id"), "?")
                            tool_bytes[tn] += n
                            if n > 20000:
                                big.append((n, tn, tool_cmd.get(b.get("tool_use_id"), "")))

    def line(name, c):
        tot = c["in"] + c["out"] + c["cache_read"] + c["cache_write"]
        return "%-34s calls=%-6d out=%-9d cache_read=%-11d est=$%-8.2f (%s)" % (
            name[:34], c["calls"], c["out"], c["cache_read"], c["cost_milli"] / 1000, "%.0fM tok" % (tot / 1e6))

    print("== Last %d days%s ==" % (a.days, " (project~%s)" % a.project if a.project else ""))
    print("\n-- By model family (est. list-price cost, relative weight) --")
    for fam, c in sorted(model_tot.items(), key=lambda x: -x[1]["cost_milli"]):
        print(line(fam, c))
    print("\n-- Where the est. cost goes (input / output / cache read / cache write, $) --")
    for fam, v in sorted(comp.items(), key=lambda x: -sum(x[1])):
        t = sum(v) or 1
        print("%-8s in=$%-7.2f out=$%-7.2f cache_read=$%-7.2f (%2.0f%%) cache_write=$%-7.2f" % (
            fam, v[0], v[1], v[2], 100 * v[2] / t, v[3]))
    print("\n-- Longest / costliest main sessions (avg context tokens per call) --")
    for (pn, sid), c in sorted(sess.items(), key=lambda x: -x[1]["cost_milli"])[: a.top]:
        print("%-24s %s  calls=%-5d avg_ctx=%-7d max_ctx=%-8d est=$%.2f" % (
            pn[:24], sid[:8], c["calls"], c["ctx_sum"] // max(c["calls"], 1), c["ctx_max"], c["cost_milli"] / 1000))
    print("\n-- By project --")
    for name, c in sorted(proj.items(), key=lambda x: -x[1]["cost_milli"]):
        print(line("%s [%d sessions]" % (name, sessions[name]), c))
    print("\n-- Subagents: calls made and the work they did --")
    for (at, fam), c in sorted(agent_tot.items(), key=lambda x: -x[1]["calls"])[: a.top * 2]:
        if at.startswith("CALLED:"):
            print("called  %-30s x%d" % (at[7:], c["calls"]))
        else:
            print("ran     %-30s on %-7s calls=%-5d tokens=%-10d est=$%.2f" % (at, fam, c["calls"], c["tokens"], c["cost_milli"] / 1000))
    print("\n-- Tools returning the most text (chars into context) --")
    for t, b in tool_bytes.most_common(a.top):
        print("%-28s %10d chars  over %d calls" % (t, b, tool_calls[t]))
    print("\n-- Single tool results over 20k chars --")
    for n, tn, cmd in sorted(big, reverse=True)[: a.top]:
        print("%8d  %-8s %s" % (n, tn, cmd.replace("\n", " ")))
    if not big:
        print("none")
    print("\n-- Files read 4+ times in one session --")
    rep = [(n, k) for k, n in reads.items() if n >= 4]
    for n, (sid, f) in sorted(rep, reverse=True)[: a.top]:
        print("%3dx  %s" % (n, f))
    if not rep:
        print("none")


if __name__ == "__main__":
    main()
