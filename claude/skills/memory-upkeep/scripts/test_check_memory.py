#!/usr/bin/env python3
"""Tests for check_memory.py (NFX-79). Run: python3 -B claude/skills/memory-upkeep/scripts/test_check_memory.py

Each test builds a temp home (with a memory folder and a transcript beside it) and a temp git repo.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check_memory  # noqa: E402

OPENED = []
RECORD = [False]


def _audit(event, args):
    if RECORD[0] and event == "open" and isinstance(args[0], str):
        OPENED.append(os.path.realpath(args[0]))


sys.addaudithook(_audit)

DUP = "Always run the review loop before opening a pull request on this repo."


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def note(name, text):
    return f"---\nname: {name}\ndescription: x\n---\n\n{text}\n"


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = os.path.join(self.tmp, "home")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        sh(self.repo, "git", "init", "-q", "-b", "main")
        sh(self.repo, "git", "config", "user.email", "t@example.com")
        sh(self.repo, "git", "config", "user.name", "t")
        write(os.path.join(self.repo, "scripts", "kept.py"), "def keep_me():\n    pass\n")
        write(os.path.join(self.repo, ".gitignore"), "out/\n.claude/\n")
        sh(self.repo, "git", "add", ".")
        sh(self.repo, "git", "commit", "-q", "-m", "one")
        self.first = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.repo,
                                    capture_output=True, text=True).stdout.strip()
        sh(self.repo, "git", "commit", "-q", "--allow-empty", "-m", "two")
        sh(self.repo, "git", "checkout", "-q", "-b", "cesar/NFX-1")
        self.project_dir = os.path.join(self.home, ".claude", "projects", check_memory.slug(self.repo))
        self.mem = os.path.join(self.project_dir, "memory")
        # A transcript beside memory/: the script must never open it.
        write(os.path.join(self.project_dir, "abc.jsonl"), '{"never": "read"}\n')

    def memory(self, index, notes):
        write(os.path.join(self.mem, "MEMORY.md"), index)
        for name, text in notes.items():
            write(os.path.join(self.mem, name), note(name[:-3], text))

    def handoff(self, header):
        write(os.path.join(self.repo, ".claude", "handoff.md"), header + "\n\n## Goal\nx\n")

    def run_json(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check_memory.main([self.repo, "--json", "--home", self.home, *extra])
        return code, json.loads(out.getvalue())

    def kinds(self):
        code, data = self.run_json()
        return {(f["kind"], f["file"]) for f in data["projects"][0]["findings"]}, data

    def broken_memory(self):
        self.memory(
            "- [Kept](kept.md) — ok\n- [Gone](gone.md) — dangling\n- [Dup A](dup-a.md) — a\n- [Dup B](dup-b.md) — b\n",
            {"kept.md": "Use `scripts/kept.py` and `keep_me()` on branch `cesar/NFX-1`.",
             "deleted.md": "The check lives in `scripts/removed.py`.",
             "dup-a.md": DUP + " First note.",
             "dup-b.md": "Second note. " + DUP})


class Findings(Fixture):
    def test_all_four_memory_problems_are_listed_with_evidence(self):
        self.broken_memory()
        found, data = self.kinds()
        self.assertIn(("index-dangling", "MEMORY.md"), found)
        self.assertIn(("index-unlisted", "deleted.md"), found)
        self.assertIn(("stale-path", "deleted.md"), found)
        self.assertIn(("duplicate", "dup-a.md"), found)
        for f in data["projects"][0]["findings"]:
            self.assertTrue(f["evidence"])
        evidence = " ".join(f["evidence"] for f in data["projects"][0]["findings"])
        self.assertIn("gone.md", evidence)
        self.assertIn("scripts/removed.py", evidence)
        self.assertIn("dup-b.md", evidence)

    def test_live_paths_symbols_and_branches_are_not_reported(self):
        self.broken_memory()
        found, _ = self.kinds()
        self.assertFalse([k for k in found if k[1] == "kept.md"])

    def test_deleted_branch_and_symbol_are_reported(self):
        self.memory("- [A](a.md) — a\n", {"a.md": "Work on branch `cesar/NFX-9`; call `gone_func()` first."})
        found, _ = self.kinds()
        self.assertEqual(found, {("stale-branch", "a.md"), ("stale-symbol", "a.md")})

    def test_placeholders_slash_commands_and_ignored_paths_are_skipped(self):
        self.memory("- [A](a.md) — a\n", {"a.md": "Run `/goal`, name it `cesar/DEM-n`, see `docs/...`, build to `out/app.apk`."})
        found, _ = self.kinds()
        self.assertEqual(found, set())

    def test_index_over_200_lines(self):
        self.memory("- [A](a.md) — a\n" + "x\n" * 200, {"a.md": "fine"})
        found, _ = self.kinds()
        self.assertIn(("index-too-long", "MEMORY.md"), found)

    def test_stale_handoff_header(self):
        self.memory("- [A](a.md) — a\n", {"a.md": "fine"})
        self.handoff(f"# Handoff: demo (2026-11-01, main, {self.first})")
        found, data = self.kinds()
        self.assertIn(("handoff-branch", ".claude/handoff.md"), found)
        self.assertIn(("handoff-sha", ".claude/handoff.md"), found)
        self.assertEqual(data["projects"][0]["handoff"], ".claude/handoff.md")

    def test_current_handoff_header_is_clean(self):
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.repo, capture_output=True,
                              text=True).stdout.strip()
        self.memory("- [A](a.md) — a\n", {"a.md": "fine"})
        self.handoff(f"# Handoff: demo (2026-11-01, cesar/NFX-1, {head})")
        found, _ = self.kinds()
        self.assertEqual(found, set())

    def test_header_variants(self):
        p = check_memory.parse_header
        self.assertEqual(p("# Handoff — 2026-10-25 · branch `main` (c150137, in sync)"), ("2026-10-25", "main", "c150137"))
        self.assertEqual(p("# Handoff · 2026-10-20 · main @ 02ec238"), ("2026-10-20", "main", "02ec238"))
        self.assertIsNone(p("# Handoff\nno state here"))


class Safety(Fixture):
    def snapshot(self):
        out = {}
        for root in (self.home, self.repo):
            for d, _, files in os.walk(root):
                for f in files:
                    p = os.path.join(d, f)
                    with open(p, "rb") as fh:
                        out[p] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def test_writes_nothing(self):
        self.broken_memory()
        self.handoff(f"# Handoff: demo (2026-11-01, main, {self.first})")
        before = self.snapshot()
        self.run_json()
        self.assertEqual(self.snapshot(), before)

    def test_opens_only_memory_and_handoff(self):
        self.broken_memory()
        self.handoff(f"# Handoff: demo (2026-11-01, main, {self.first})")
        OPENED.clear()
        RECORD[0] = True
        try:
            self.run_json()
        finally:
            RECORD[0] = False
        handoff = os.path.join(self.repo, ".claude", "handoff.md")
        inside = [p for p in OPENED if p.startswith(self.tmp + os.sep)]
        self.assertTrue(inside, "the audit hook saw no opens")
        bad = [p for p in inside if not p.startswith(self.mem + os.sep) and p != handoff]
        self.assertEqual(bad, [])

    def test_cli_leaves_no_bytecode_in_the_repo(self):
        self.broken_memory()
        cache = os.path.join(HERE, "..", "..", "..", "..", "scripts", "__pycache__")
        if os.path.exists(cache):
            self.skipTest("scripts/__pycache__ already exists")
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "check_memory.py"), self.repo, "--home", self.home],
                       capture_output=True)
        self.assertFalse(os.path.exists(cache))

    def test_json_is_stable(self):
        self.broken_memory()
        _, one = self.run_json()
        _, two = self.run_json()
        self.assertEqual(one, two)
        self.assertEqual(one["version"], 1)
        self.assertEqual(set(one["projects"][0]), {"project", "memory", "handoff", "findings"})
        self.assertEqual(set(one["projects"][0]["findings"][0]), {"kind", "file", "evidence"})

    def test_all_finds_the_project_from_its_slug(self):
        self.memory("- [A](a.md) — a\n", {"a.md": "fine"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            check_memory.main(["--all", "--json", "--home", self.home])
        projects = json.loads(out.getvalue())["projects"]
        self.assertEqual([p["project"] for p in projects], [self.repo])

    def test_exit_codes(self):
        self.memory("- [A](a.md) — a\n", {"a.md": "fine"})
        self.assertEqual(self.run_json()[0], 0)
        self.memory("- [A](a.md) — a\n", {"a.md": "fine", "b.md": "unlisted"})
        self.assertEqual(self.run_json()[0], 1)


if __name__ == "__main__":
    unittest.main()
