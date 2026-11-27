#!/usr/bin/env python3
"""Tests for comment_check.py against real comments from Cesar's six repos (NFX-276).
Run: python3 -B claude/skills/review-loop/scripts/test_comment_corpus.py [-k shape|verdicts|replay_known_misses|
strip_compare|stale_known|secrets]

The corpus is comment_corpus/<language>.json: a list of entries, each
  source    `repo@sha:path:line` (the excerpt's first line) for NousFX, Hemisphere, PawlyPaint and Demonica, or
            `reduced from animus` / `reduced from yuyay` for the private repos: renamed identifiers, no product
            strings, no sha or path
  path      the original relative path (a stand-in such as `src/x.test.ts` for a reduced entry): verdicts read it
  text      the excerpt, with the lines comment_check needs (the doc target, the next code line)
  expected  every comment as `line form verdict`, line 1 being the excerpt's first line
  known     optional: the NFX ticket that fixes a wrong verdict, with `got`, today's list
A disputed verdict goes in as `known` with `got`, never as `expected`. The sources are provenance only: the test
never reads the six repos. A cleanup that finds a classifier miss adds an entry here (the delete-comments skill).
"""
import ast
import functools
import io
import json
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, "comment_corpus")
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import comment_check  # noqa: E402 (sys.path must name this folder first)
from session_report import SECRET_RE  # noqa: E402 (the one pattern, NFX-274: never a copy here)

MINIMUM = {"python": 8, "kotlin": 8, "dart": 8, "ts": 8, "shell": 3, "js": 3}
LANGUAGE = {"python": "python", "kotlin": "kotlin", "dart": "dart", "ts": "js", "shell": "shell", "js": "js"}
EXTENSIONS = {"ts": (".ts", ".tsx", ".mts", ".cts"), "js": (".js", ".jsx", ".mjs", ".cjs")}
FIELDS = ("source", "path", "text", "expected")
PUBLIC_SOURCE = re.compile(r"^(?:nousfx|hemisphere|pawlypaint|demonica)@[0-9a-f]{7,40}:[^:\s]+:\d+$")
REDUCED_SOURCE = re.compile(r"^reduced from (?:animus|yuyay)$")
PRIVATE = re.compile(r"(?i)animus|yuyay")
STAND_IN = re.compile(r"^(?:(?:src|lib|test|tests)/)?(?:x|test_x|x_test|x\.test|x\.spec)\.\w+$")
ITEM = re.compile(r"^(\d+) (docstring|inline|line|shell) ((?:keep|flag):[\w-]+)$")
KNOWN = re.compile(r"^NFX-\d+$")


@functools.lru_cache(maxsize=None)
def load():
    """{language: [entry]} for every corpus file, read once per run (callers never change it)."""
    out = {}
    for name in MINIMUM:
        with open(os.path.join(CORPUS, name + ".json"), encoding="utf-8") as f:
            out[name] = json.load(f)
    return out


def entries():
    for name, found in load().items():
        for k, entry in enumerate(found, 1):
            yield f"{name}.json #{k} ({entry.get('source', '?')})", name, entry


def shape_problems(name, entry):
    """What is wrong with one entry's fields, as a list of messages; [] when none."""
    out = [f"missing {f}" for f in FIELDS if f not in entry]
    if out:
        return out
    extra = set(entry) - set(FIELDS) - {"known", "got"}
    if extra:
        out.append(f"unknown fields {sorted(extra)}")
    if ("known" in entry) != ("got" in entry):
        out.append("known and got go together")
    if "known" in entry and not KNOWN.match(str(entry["known"])):
        out.append(f"known {entry['known']!r} is not an NFX-<n> key")
    for field in ("expected", "got"):
        items = entry.get(field, [])
        bad = [i for i in items if not ITEM.match(str(i))] if isinstance(items, list) else [items]
        if bad:
            out.append(f"{field} items must read `line form verdict`: {bad}")
    source, path = entry["source"], entry["path"]
    if PRIVATE.search(source):
        if not REDUCED_SOURCE.match(source):
            out.append("a private repo's source must read `reduced from <repo>`, with no sha or path")
        elif not STAND_IN.match(path):
            out.append(f"a reduced entry's path keeps only the extension and test-ness (src/x.test.ts): {path}")
    elif not PUBLIC_SOURCE.match(source):
        out.append(f"source must read repo@sha:path:line: {source}")
    rest = json.dumps({k: v for k, v in entry.items() if k != "source"})
    if PRIVATE.search(rest):
        out.append("names a private repo outside source")
    lang = comment_check.language(path, entry["text"])
    if lang != LANGUAGE[name] or name in EXTENSIONS and not path.endswith(EXTENSIONS[name]):
        out.append(f"{path} is {lang}, not {name}")
    if name == "python":
        try:
            ast.parse(entry["text"])
        except SyntaxError as e:
            out.append(f"does not parse: {e}")
    if LANGUAGE[name] in ("kotlin", "dart", "js"):
        out += doc_without_code(path, entry["text"])
    if name == "shell":
        err = comment_check.syntax_error("bash", entry["text"])
        if err is not None:
            out.append(f"fails bash -n: {err}")
    return out


def doc_without_code(path, text):
    """A doc comment needs its declaration in the excerpt: cut off from it, the doc's target and verdict change."""
    lines = comment_check.text_lines(text)
    return [f"line {c.line}: a doc comment with no code after it" for c, f, _, _ in comment_check.judged(path, text)
            if f == "docstring" and not any(l.strip() and not l.strip().startswith(("//", "/*", "*"))
                                            for l in lines[c.end:])]


def listed(entry):
    return [f"{line} {form} {verdict}" for line, form, verdict in comment_check.verdicts(entry["path"], entry["text"])]


def verdict_problem(label, entry):
    """None when the entry gives its pinned list: `expected`, or `got` while its `known` ticket is open."""
    now = listed(entry)
    if "known" not in entry:
        return None if now == entry["expected"] else f"{label}: expected {entry['expected']}, got {now}"
    if now == entry["got"]:
        return None
    if now == entry["expected"]:
        return f"{label}: {entry['known']} gives expected now: remove `known` from {label}"
    return f"{label}: known {entry['known']} pins got {entry['got']}, now {now}"


def strip(path, text):
    """The text with every flagged comment cut. A whole-line comment drops its lines; an inline one is cut from
    its line; a docstring that was its body's only statement leaves `pass`; a JSX `{/* note */}` child goes with its
    braces. Annotations are code and stay."""
    lines = comment_check.text_lines(text)
    cuts = [c for c, _, kept, _ in comment_check.judged(path, text) if not kept and c.style != "annotation"]
    for c in sorted(cuts, key=lambda c: c.line, reverse=True):
        rows = c.text.split("\n")
        first, last = lines[c.line - 1], lines[c.end - 1]
        head = first[:first.find(rows[0])] if rows[0] in first else ""
        tail = last[last.rfind(rows[-1]) + len(rows[-1]):] if rows[-1] in last else ""
        if comment_check.is_jsx(path) and head.rstrip().endswith("{") and tail.lstrip().startswith("}"):
            head, tail = head.rstrip()[:-1], tail.lstrip()[1:]
        keep = (head + tail).rstrip()
        if keep.strip():
            lines[c.line - 1:c.end] = [keep]
        elif c.style == "docstring" and c.target != "module" and not body_left(lines, c):
            lines[c.line - 1:c.end] = [first[:len(first) - len(first.lstrip())] + "pass"]
        else:
            del lines[c.line - 1:c.end]
    return "\n".join(lines)


def body_left(lines, c):
    """True when a statement follows the docstring at the docstring's indent."""
    indent = len(lines[c.line - 1]) - len(lines[c.line - 1].lstrip())
    for raw in lines[c.end:]:
        if raw.strip():
            return len(raw) - len(raw.lstrip()) >= indent
    return False


def code_differences(name, path, old, new):
    if name == "python":
        found = comment_check.python_differences(old, new)
        return ["the excerpt does not parse"] if found is None else found
    if name == "shell":
        return comment_check.shell_differences(path, old, new)
    return [f"line {row}: {d}" for row, d in comment_check.c_differences(path, old, new)]


class Shape(unittest.TestCase):
    def test_shape_every_entry_has_its_fields_and_parses(self):
        for label, name, entry in entries():
            with self.subTest(label):
                self.assertEqual(shape_problems(name, entry), [])

    def test_shape_each_language_has_its_minimum(self):
        found = load()
        for name, least in MINIMUM.items():
            self.assertGreaterEqual(len(found[name]), least, name)
        self.assertGreaterEqual(sum(e["path"].endswith(".tsx") for e in found["ts"]), 2, "tsx in ts.json")

    def test_shape_refuses_a_pointer_into_a_private_repo(self):
        entry = {"source": "yuyay@abc1234:src/app.ts:3", "path": "src/app.ts", "text": "// hi\nf()\n",
                 "expected": ["1 line flag:comment"]}
        self.assertIn("a private repo's source must read `reduced from <repo>`, with no sha or path",
                      shape_problems("ts", entry))
        entry.update(source="reduced from yuyay")
        self.assertIn("a reduced entry's path keeps only the extension and test-ness (src/x.test.ts): src/app.ts",
                      shape_problems("ts", entry))
        entry.update(path="src/x.ts")
        self.assertEqual(shape_problems("ts", entry), [])

    def test_shape_refuses_a_python_excerpt_that_does_not_parse(self):
        entry = {"source": "nousfx@abc1234:x.py:1", "path": "x.py", "text": '    """Doc."""\n',
                 "expected": ["1 docstring keep:public-doc"]}
        self.assertTrue(any(p.startswith("does not parse") for p in shape_problems("python", entry)))

    def test_shape_refuses_a_kotlin_doc_cut_off_from_its_declaration(self):
        entry = {"source": "hemisphere@abc1234:x.kt:1", "path": "x.kt", "text": "/** Opens the folder. */\n",
                 "expected": ["1 docstring keep:public-doc"]}
        self.assertIn("line 1: a doc comment with no code after it", shape_problems("kotlin", entry))
        entry["text"] += "fun open() = Unit\n"
        self.assertNotIn("line 1: a doc comment with no code after it", shape_problems("kotlin", entry))


class Verdicts(unittest.TestCase):
    def test_verdicts_every_entry_without_known_gets_its_list(self):
        for label, _, entry in entries():
            if "known" not in entry:
                with self.subTest(label):
                    self.assertIsNone(verdict_problem(label, entry))


class KnownMisses(unittest.TestCase):
    def test_replay_known_misses_still_give_got_and_would_fail_without_known(self):
        for label, _, entry in entries():
            if "known" in entry:
                with self.subTest(label):
                    self.assertIsNone(verdict_problem(label, entry))
                    bare = {k: v for k, v in entry.items() if k not in ("known", "got")}
                    self.assertIsNotNone(verdict_problem(label, bare))

    def test_replay_known_misses_holds_the_past_instances(self):
        by_source = {e["source"]: e for _, _, e in entries()}
        banner = by_source.get("pawlypaint@83dbdc5:lib/state/achievements.dart:168")
        contract = by_source.get("demonica@6f98839:lib/design_system/components.dart:433")
        self.assertIsNotNone(banner, "PAP-69 banner")
        self.assertIsNotNone(contract, "DEM-72 contract")
        self.assertIn("1 line flag:banner", banner["expected"], "NFX-267 is Done, so the banner is a plain entry")
        self.assertIn("1 docstring keep:contract", contract["expected"])
        if "known" in contract:
            self.assertEqual(contract["known"], "NFX-269", "the DEM-72 contract waits on NFX-269 until it lands")
            self.assertIn("1 docstring flag:docstring", contract["got"])

    def test_stale_known_entry_that_gives_expected_fails_naming_it(self):
        entry = {"source": "nousfx@abc1234:x.py:1", "path": "x.py", "text": "x = 1  # one\n",
                 "expected": ["1 inline flag:comment"], "known": "NFX-1", "got": ["1 inline keep:license"]}
        self.assertEqual(verdict_problem("python.json #9", entry),
                         "python.json #9: NFX-1 gives expected now: remove `known` from python.json #9")

    def test_stale_known_entry_whose_verdict_moved_elsewhere_fails_too(self):
        entry = {"source": "nousfx@abc1234:x.py:1", "path": "x.py", "text": "x = 1  # one\n",
                 "expected": ["1 inline keep:license"], "known": "NFX-1", "got": ["1 inline flag:ticket"]}
        self.assertIn("known NFX-1 pins got", verdict_problem("python.json #9", entry))


class StripCompare(unittest.TestCase):
    def test_strip_compare_cutting_flagged_comments_leaves_the_code(self):
        for label, name, entry in entries():
            with self.subTest(label):
                stripped = strip(entry["path"], entry["text"])
                self.assertEqual(code_differences(name, entry["path"], entry["text"], stripped), [], stripped)

    def test_strip_compare_turns_a_lone_docstring_into_pass(self):
        text = 'def _f():\n    """Doc."""\n\n\nx = 1  # one\n'
        self.assertEqual(strip("x.py", text), "def _f():\n    pass\n\n\nx = 1\n")

    def test_strip_compare_takes_a_jsx_comment_child_with_its_braces(self):
        text = "const a = (\n  <div>\n    {/* note */}\n    <p />\n  </div>\n)\n"
        self.assertEqual(strip("x.tsx", text), "const a = (\n  <div>\n    <p />\n  </div>\n)\n")

    def test_strip_compare_catches_a_stripper_that_eats_code(self):
        old = "val a = 1 // one\nval b = 2\n"
        self.assertNotEqual(code_differences("kotlin", "x.kt", old, "val a = 1\n"), [])


class Provenance(unittest.TestCase):
    def test_shape_reads_only_the_corpus_never_the_source_repos(self):
        opened, real = [], io.open

        def spy(path, *a, **k):
            if isinstance(path, (str, bytes, os.PathLike)):
                opened.append(os.path.abspath(path))
            return real(path, *a, **k)

        load.cache_clear()
        with mock.patch("builtins.open", spy), mock.patch("io.open", spy), \
                mock.patch("subprocess.run", wraps=subprocess.run) as run:
            for label, name, entry in entries():
                shape_problems(name, entry)
                listed(entry)
                code_differences(name, entry["path"], entry["text"], strip(entry["path"], entry["text"]))
        self.assertTrue(opened)
        self.assertEqual([p for p in opened if not p.startswith(CORPUS + os.sep)], [])
        self.assertEqual({tuple(c.args[0][1:]) for c in run.call_args_list}, {("-n",)}, "only `<shell> -n` runs")


class Secrets(unittest.TestCase):
    def test_secrets_none_in_the_corpus(self):
        for name in MINIMUM:
            with open(os.path.join(CORPUS, name + ".json"), encoding="utf-8") as f:
                self.assertIsNone(SECRET_RE.search(f.read()), name)
        for label, _, entry in entries():
            for field in ("source", "path", "text"):
                self.assertIsNone(SECRET_RE.search(entry[field]), f"{label} {field}")


if __name__ == "__main__":
    unittest.main()
