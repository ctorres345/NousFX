#!/usr/bin/env python3
"""Changelog fragments (NFX-292). Each ticket writes changelog.d/<KEY>.md instead of editing CHANGELOG.md,
so parallel PRs stop conflicting. The release folds them in.

  check [--release]                       fail on a malformed fragment; --release also fails while one is left
  fold --version X.Y.Z --date YYYY-MM-DD  fold every fragment into ## Unreleased, date that heading, delete them
  guard [BASE]                            fail when the diff from the merge base edits the ## Unreleased block

A fragment: first line `### <Section>` (a name already in CHANGELOG.md or in ALLOWED_NEW_SECTIONS, which keeps typos failing), then one or more
`- ` bullets; other lines continue the bullet above. A second PR for one ticket writes <KEY>-2.md.
All three take --root (default: this repo). Exit 0 ok, 1 refused, 2 usage.
"""
import argparse
import os
import re
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = "changelog.d"
IGNORED = {"README.md", ".gitkeep"}
NAME = re.compile(r"^[A-Z][A-Z0-9]*-(\d+)(?:-(\d+))?\.md$")
UNRELEASED = "## Unreleased"
DATED = re.compile(r"^## \d+\.\d+\.\d+ \(\d{4}-\d{2}-\d{2}\)$")
ALLOWED_NEW_SECTIONS = ()


class FragmentError(Exception):
    pass


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read().replace("\r\n", "\n")


def changelog_path(root):
    return os.path.join(root, "CHANGELOG.md")


def known_sections(changelog):
    names = [line[4:].strip() for line in changelog.splitlines() if line.startswith("### ")]
    return list(dict.fromkeys(names + list(ALLOWED_NEW_SECTIONS)))


def block_span(lines, heading):
    """Line range [start, end) of the block under this exact `## ` heading, heading included, or None."""
    if heading not in lines:
        return None
    start = lines.index(heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def unreleased_span(lines):
    return block_span(lines, UNRELEASED)


def newest_heading(lines):
    return next((line for line in lines if line.startswith("## ")), None)


def fragment_files(root):
    folder = os.path.join(root, FOLDER)
    if not os.path.isdir(folder):
        return []
    return sorted(n for n in os.listdir(folder) if n not in IGNORED and not n.startswith(".") and not n.endswith("~"))


def parse(root, name, known):
    """(section, bullet lines) of one fragment, or a FragmentError naming the file."""
    label = "%s/%s" % (FOLDER, name)
    if not NAME.match(name):
        raise FragmentError("%s: bad file name, use <KEY>.md or <KEY>-2.md" % label)
    try:
        lines = read(os.path.join(root, FOLDER, name)).split("\n")
    except (OSError, UnicodeDecodeError):
        raise FragmentError("%s: cannot read it as a UTF-8 text file" % label)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[0].startswith("### "):
        raise FragmentError("%s: no section, the first line must be `### <Section>`" % label)
    section = lines[0][4:].strip()
    if section not in known:
        raise FragmentError("%s: unknown section %r, use one of: %s" % (label, section, ", ".join(known)))
    body = lines[1:]
    while body and not body[0].strip():
        body.pop(0)
    if not body or not body[0].startswith("- "):
        raise FragmentError("%s: no bullet, write `- ` bullets after the section line" % label)
    if any(line.startswith("#") for line in body):
        raise FragmentError("%s: one section per fragment, write another section in <KEY>-2.md" % label)
    return section, body


def order_key(name):
    m = NAME.match(name)
    return int(m.group(1)), int(m.group(2) or 1)


def load(root):
    """Parse every fragment; return ([(name, section, body)], [errors])."""
    known = known_sections(read(changelog_path(root)))
    good, errors = [], []
    for name in fragment_files(root):
        try:
            good.append((name,) + parse(root, name, known))
        except FragmentError as e:
            errors.append(str(e))
    return good, errors


def check(root, release=False):
    """Error lines, empty when ok."""
    good, errors = load(root)
    if release:
        errors += ["%s/%s: not folded, run `changelog_fragments.py fold` in the release PR" % (FOLDER, name)
                   for name, _, _ in good]
    return errors


def insert_newest_first(block, fragments):
    """Put each fragment's bullets on top of the first same-named section of block, highest ticket number
    first; a section the block lacks is added at the end."""
    for name, section, body in sorted(fragments, key=lambda g: order_key(g[0])):
        head = "### " + section
        at = next((i for i, line in enumerate(block) if line.rstrip() == head), None)
        if at is not None:
            block[at + 1:at + 1] = body
        else:
            if block:
                block.append("")
            block += [head] + body


def fold(root, version, date):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise FragmentError("use --version X.Y.Z --date YYYY-MM-DD")
    good, errors = load(root)
    if errors:
        raise FragmentError("\n".join(errors))
    path = changelog_path(root)
    lines = read(path).split("\n")
    heading = "## %s (%s)" % (version, date)
    if any(line.startswith("## %s " % version) for line in lines):
        raise FragmentError("CHANGELOG.md already has a %s heading" % version)
    span = unreleased_span(lines)
    odd = [line for line in lines if line != UNRELEASED and line.strip().lower() == UNRELEASED.lower()]
    if odd:
        raise FragmentError("CHANGELOG.md: write the heading %r exactly as `%s`" % (odd[0], UNRELEASED))
    if span is None:
        if not good:
            raise FragmentError("nothing to release: no ## Unreleased block and no fragments")
        at = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        lines[at:at] = [UNRELEASED, ""]
        span = (at, at + 2)
    start, end = span
    block = lines[start + 1:end]
    while block and not block[-1].strip():
        block.pop()
    insert_newest_first(block, good)
    if block and block[0].strip():
        block.insert(0, "")
    lines[start:end] = [heading] + block + ([""] if end < len(lines) else [])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    for name, _, _ in good:
        os.remove(os.path.join(root, FOLDER, name))


def git(root, *args):
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 127, "", str(e))


def resolve_base(root):
    for ref in ("origin/HEAD", "origin/main"):
        r = git(root, "merge-base", ref, "HEAD")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def moved_lines(root, base):
    """Body lines of the fragments that exist at base and are gone now: a hand fold may add them."""
    r = git(root, "ls-tree", "--name-only", base, FOLDER + "/")
    moved = Counter()
    for path in r.stdout.split() if r.returncode == 0 else []:
        name = os.path.basename(path)
        if not NAME.match(name) or os.path.exists(os.path.join(root, FOLDER, name)):
            continue
        shown = git(root, "show", "%s:%s" % (base, path))
        if shown.returncode == 0:
            moved.update(l for l in shown.stdout.lstrip("\ufeff").replace("\r\n", "\n").split("\n") if l.strip())
    return moved


def guard(root, base=None):
    """(ok, message). Fails when the working tree adds or changes a line of the newest `## ` block
    (## Unreleased, or the newest release once a fold dated it), and when a release PR leaves a fragment.
    Lines of a fragment the diff deletes may be added: that is the hand fold of RUNBOOK release step 4."""
    if base is None:
        base = resolve_base(root)
        if base is None:
            return True, "guard skipped: no origin/HEAD or origin/main to diff from"
    old = git(root, "show", "%s:CHANGELOG.md" % base)
    if old.returncode != 0:
        return True, "guard skipped: cannot read CHANGELOG.md at %s (not a git repo?)" % base
    old_lines = old.stdout.replace("\r\n", "\n").split("\n")
    new_lines = read(changelog_path(root)).split("\n")
    dated = [l for l in new_lines if DATED.match(l) and l not in old_lines]
    if dated and not unreleased_span(new_lines):
        left = check(root, release=True)
        if left:
            return False, "release PR (dates %s) leaves fragments:\n%s" % (dated[0], "\n".join(left))
        return True, "guard: release PR (dates %s), skipped" % dated[0]
    heading = newest_heading(new_lines)
    if heading is None:
        return True, "guard: no version block"
    new_span = block_span(new_lines, heading)
    old_span = block_span(old_lines, heading)
    remaining = Counter(old_lines[old_span[0]:old_span[1]] if old_span else [])
    moved = moved_lines(root, base)
    added = []
    for line in new_lines[new_span[0]:new_span[1]]:
        if remaining[line]:
            remaining[line] -= 1
        elif moved[line]:
            moved[line] -= 1
        elif line.strip():
            added.append(line)
    if not added:
        return True, "guard: %s unchanged" % heading
    shown = "\n".join("  " + l[:120] for l in added[:5])
    return False, ("CHANGELOG.md: this diff edits the newest block (%s):\n%s\n" % (heading, shown) +
                   "Write the entry in changelog.d/<KEY>.md instead (first line `### <Section>`, then the bullet); "
                   "the release folds it in.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--release", action="store_true")
    f = sub.add_parser("fold")
    f.add_argument("--version", required=True)
    f.add_argument("--date", required=True)
    g = sub.add_parser("guard")
    g.add_argument("base", nargs="?")
    g.add_argument("--base", dest="base_opt")
    for s in (c, f, g):
        s.add_argument("--root", default=ROOT)
    a = p.parse_args(argv)
    if a.cmd == "check":
        errors = check(a.root, a.release)
        for e in errors:
            print(e)
        print("changelog fragments: %s" % ("%d problem(s)" % len(errors) if errors else "ok"))
        return 1 if errors else 0
    if a.cmd == "fold":
        try:
            fold(a.root, a.version, a.date)
        except FragmentError as e:
            print(e, file=sys.stderr)
            return 1
        print("folded into ## %s (%s)" % (a.version, a.date))
        return 0
    ok, message = guard(a.root, a.base or a.base_opt)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
