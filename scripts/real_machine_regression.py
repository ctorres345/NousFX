#!/usr/bin/env python3
"""Real-machine regression for nousfx_sync. Not run in CI (the guard test uses fixtures).

Builds the Claude rules from the repo sources and a state home, then compares them with today's
rules file line by line and writes a classified report to --out/regression.txt:

    unchanged   same line in both
    reordered   same line, different relative order
    reworded    no identical line, but a close one (wording differs)
    dropped     today's line has no home in the generated rules

    python3 -B scripts/real_machine_regression.py --out DIR [--state-home DIR] [--repo DIR]
        [--today FILE | --real-home DIR] [--fail-on-dropped]

The real home is only read (CLAUDE.md, and local.md unless --state-home is given). Nothing is written
except the report. Exit 1 with --fail-on-dropped when a heading or a line is dropped.
"""
import argparse
import difflib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nousfx_sync  # noqa: E402

REWORD_RATIO = 0.6


def norm(line):
    return " ".join(line.strip().split())


def classify(today, generated):
    """Return dict of lists of today's non-blank lines per class, plus dropped headings."""
    gen = [norm(l) for l in generated.splitlines() if l.strip() and not l.startswith("<!--")]
    gen_set = set(gen)
    out = {"unchanged": [], "reordered": [], "reworded": [], "dropped": []}
    last = -1
    for raw in today.splitlines():
        line = norm(raw)
        if not line:
            continue
        if line in gen_set:
            pos = gen.index(line)
            if pos >= last:
                out["unchanged"].append(line)
                last = pos
            else:
                out["reordered"].append(line)
        elif difflib.get_close_matches(line, gen, n=1, cutoff=REWORD_RATIO):
            out["reworded"].append(line)
        else:
            out["dropped"].append(line)
    # A heading survives when its title (any level) is part of some generated heading title.
    titles = [l.lstrip("#").strip().lower() for l in gen if l.startswith("#")]
    for raw in today.splitlines():
        line = norm(raw)
        if line.startswith("#"):
            title = line.lstrip("#").strip().lower()
            if not any(title in t for t in titles):
                out["dropped"].append(line)
    return out


def report(res):
    lines = [f"{k}: {len(res[k])}" for k in ("unchanged", "reordered", "reworded", "dropped")]
    for k in ("dropped", "reworded", "reordered"):
        if res[k]:
            lines.append(f"\n== {k} ==")
            lines += [f"- {l[:160]}" for l in res[k]]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="folder for the report (for example a scratchpad)")
    ap.add_argument("--real-home", help="home holding today's .claude/CLAUDE.md (default: user record)")
    ap.add_argument("--today", help="today's rules file (overrides --real-home)")
    ap.add_argument("--state-home", help="folder with local.md (default: <real home>/.config/nousfx)")
    ap.add_argument("--repo", default=os.path.dirname(HERE))
    ap.add_argument("--fail-on-dropped", action="store_true")
    args = ap.parse_args()

    real = os.path.realpath(args.real_home or nousfx_sync.user_home())
    today_path = args.today or os.path.join(real, ".claude", "CLAUDE.md")
    state = args.state_home or os.path.join(real, ".config", "nousfx")
    with open(today_path, encoding="utf-8") as f:
        today = f.read()
    repo = os.path.abspath(args.repo)
    generated = nousfx_sync.build_rules(repo, state, nousfx_sync.commit_of(repo))

    res = classify(today, generated)
    text = report(res)
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, "regression.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text.split("\n\n")[0] + f"\nreport: {out}")
    if args.fail_on_dropped and res["dropped"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
