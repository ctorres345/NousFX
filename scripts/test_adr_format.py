#!/usr/bin/env python3
"""NousFX's ADRs in docs/adr/ follow the domain-modeling ADR-FORMAT, as this repo pins it (NFX-229).

Run: python3 -B scripts/test_adr_format.py

The rules live here, not in ~/.claude/skills/domain-modeling/ADR-FORMAT.md: CI and cloud sessions have no ~/.claude.
Each ADR is docs/adr/NNNN-slug.md, numbered from 0001 with no gaps, and holds:
    ---
    status: accepted            proposed | accepted | deprecated | superseded by ADR-NNNN
    ---
    # Title
    Body: a short paragraph (optional sections after), naming the NFX ticket it comes from.
Every ADR-NNNN an ADR names must be a file here. Pointers from shipped files (`NousFX ADR-NNNN`) are resolved by
NFX-228's `verify`, not here. Two branches that both add the next number do not conflict in git, and CI checks the PR
head, not the merge result: before merging a PR that adds an ADR, rebase it on main if docs/adr/ changed there, renumber,
and re-run this test. Dotfiles such as Finder's .DS_Store are ignored.
"""
import os
import re
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADR_DIR = os.path.join(ROOT, "docs", "adr")

NAME = re.compile(r"(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
STATUS = re.compile(r"(?:proposed|accepted|deprecated|superseded by ADR-\d{4})")
TICKET = re.compile(r"\bNFX-\d+\b")
REFERENCE = re.compile(r"\bADR-(\d{4})\b")


def check_text(name, text):
    """Problems in one ADR's text, as "name: why" strings."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return [f"{name}: no frontmatter: the file must start with a --- block"]
    end = lines.index("---", 1)
    fields = dict(line.split(":", 1) for line in lines[1:end] if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    problems = []
    if not STATUS.fullmatch(fields.get("status", "")):
        problems.append(f"{name}: status is {fields.get('status')!r}; use proposed, accepted, deprecated or "
                        "superseded by ADR-NNNN")
    rest = [line for line in lines[end + 1:]]
    while rest and not rest[0].strip():
        rest.pop(0)
    if not rest or not re.fullmatch(r"# \S.*", rest[0]):
        problems.append(f"{name}: the first line after the frontmatter must be a '# Title'")
        return problems
    body = "\n".join(rest[1:]).strip()
    if not body:
        problems.append(f"{name}: no body under the title")
    elif not TICKET.search(body):
        problems.append(f"{name}: the body names no NFX ticket the decision comes from")
    return problems


def check_dir(path):
    """Problems in a docs/adr folder: file names, numbering, each ADR's text and its ADR-NNNN references."""
    entries = sorted(e for e in os.listdir(path) if not e.startswith("."))
    problems, numbers, texts = [], {}, {}
    for entry in entries:
        m = NAME.fullmatch(entry)
        if not m:
            problems.append(f"{entry}: not named NNNN-slug.md (four digits, lowercase slug)")
            continue
        if m.group(1) in numbers:
            problems.append(f"{entry}: number {m.group(1)} is also {numbers[m.group(1)]}")
            continue
        numbers[m.group(1)] = entry
        with open(os.path.join(path, entry), encoding="utf-8") as f:
            texts[entry] = f.read()
        problems += check_text(entry, texts[entry])
    expected = [f"{n:04d}" for n in range(1, len(numbers) + 1)]
    if sorted(numbers) != expected:
        problems.append(f"numbers {sorted(numbers)} are not 0001 onward with no gaps")
    for entry, text in texts.items():
        for ref in REFERENCE.findall(text):
            if ref not in numbers:
                problems.append(f"{entry}: names ADR-{ref}, which has no file")
    return problems


GOOD = "---\nstatus: accepted\n---\n\n# Use flock for slots\n\nWe use flock (NFX-217) because it frees on exit.\n"


class Fixtures(unittest.TestCase):
    """The checker itself, on temporary folders."""

    def folder(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, text in files.items():
            with open(os.path.join(tmp.name, name), "w", encoding="utf-8") as f:
                f.write(text)
        return tmp.name

    def assertFlags(self, files, fragment):
        problems = check_dir(self.folder(files))
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} not in {problems}")

    def test_good_folder_passes(self):
        self.assertEqual(check_dir(self.folder({"0001-a.md": GOOD, "0002-b-c.md": GOOD + "See ADR-0001.\n"})), [])

    def test_dotfiles_are_ignored(self):
        self.assertEqual(check_dir(self.folder({"0001-a.md": GOOD, ".DS_Store": ""})), [])

    def test_superseded_status_passes(self):
        superseded = GOOD.replace("accepted", "superseded by ADR-0002")
        self.assertEqual(check_dir(self.folder({"0001-a.md": superseded, "0002-b.md": GOOD})), [])

    def test_bad_name(self):
        self.assertFlags({"1-a.md": GOOD}, "not named NNNN-slug.md")
        self.assertFlags({"0001-A.md": GOOD}, "not named NNNN-slug.md")
        self.assertFlags({"0001-a.txt": GOOD}, "not named NNNN-slug.md")

    def test_duplicate_and_gap(self):
        self.assertFlags({"0001-a.md": GOOD, "0001-b.md": GOOD}, "is also")
        self.assertFlags({"0001-a.md": GOOD, "0003-c.md": GOOD}, "no gaps")
        self.assertFlags({"0002-a.md": GOOD}, "no gaps")

    def test_frontmatter(self):
        self.assertFlags({"0001-a.md": GOOD.replace("---\nstatus: accepted\n---\n", "")}, "no frontmatter")
        self.assertFlags({"0001-a.md": GOOD.replace("status: accepted\n", "")}, "status is None")
        self.assertFlags({"0001-a.md": GOOD.replace("accepted", "done")}, "status is 'done'")

    def test_title_and_body(self):
        self.assertFlags({"0001-a.md": GOOD.replace("# Use", "Use")}, "'# Title'")
        self.assertFlags({"0001-a.md": "---\nstatus: accepted\n---\n# Title\n\n"}, "no body")
        self.assertFlags({"0001-a.md": GOOD.replace(" (NFX-217)", "")}, "names no NFX ticket")

    def test_reference_must_resolve(self):
        self.assertFlags({"0001-a.md": GOOD + "Unlike ADR-0009.\n"}, "names ADR-0009")
        self.assertFlags({"0001-a.md": GOOD.replace("accepted", "superseded by ADR-0002")}, "names ADR-0002")


class RepoAdrs(unittest.TestCase):
    def test_folder_exists_with_adrs(self):
        self.assertTrue(os.path.isdir(ADR_DIR), "docs/adr/ is missing")
        self.assertTrue(os.listdir(ADR_DIR), "docs/adr/ is empty")

    def test_every_adr_follows_the_format(self):
        self.assertEqual(check_dir(ADR_DIR), [])


if __name__ == "__main__":
    unittest.main()
