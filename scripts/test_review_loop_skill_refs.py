#!/usr/bin/env python3
"""NFX-211: every file the review-loop SKILL.md names ships to project copies.

Cloud and agy runs read the repo copy of the skill, not ~/.claude, so a `references/<x>.md` or `scripts/<x>.py`
that SKILL.md (or a reference it names) points at must be in the sync's copy list, or the copy dangles."""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_DIR = os.path.join(ROOT, "claude", "skills", "review-loop")
COPY_DIR = os.path.join(".claude", "skills", "review-loop")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.dont_write_bytecode = True

import nousfx_project  # noqa: E402

SPAN = re.compile(r"`([^`\n]+)`")
# references/<x>.md or scripts/<x>.py, bare or after "<this skill's folder>/" (not another folder's scripts/).
REF = re.compile(r"(?:(?<=<this skill's folder>/)|(?<![\w./<>-]))(references/[\w.-]+\.md|scripts/[\w.-]+\.py)(?![\w./-])")


def refs_in(text):
    """The paths the text names inside backticks."""
    return {ref for span in SPAN.findall(text) for ref in REF.findall(span)}


def named(rel):
    with open(os.path.join(LOOP_DIR, rel), encoding="utf-8") as f:
        return refs_in(f.read())


def sibling_imports(rel):
    """scripts/<x>.py files the script imports from its own folder, anywhere in it (testfail.py imports tier)."""
    with open(os.path.join(LOOP_DIR, rel), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    return {f"scripts/{m}.py" for m in names if os.path.isfile(os.path.join(LOOP_DIR, "scripts", f"{m}.py"))}


def skill_refs():
    """Paths SKILL.md names, plus the paths the references it names point at (one hop: testfail.md names
    testfail.py), plus the sibling scripts those scripts import."""
    refs = named("SKILL.md")
    for rel in sorted(refs):
        if rel.startswith("references/") and os.path.exists(os.path.join(LOOP_DIR, rel)):
            refs |= named(rel)
    todo = [r for r in refs if r.startswith("scripts/") and os.path.isfile(os.path.join(LOOP_DIR, r))]
    while todo:
        for dep in sibling_imports(todo.pop()) - refs:
            refs.add(dep)
            todo.append(dep)
    return refs


class SkillRefsShip(unittest.TestCase):
    def test_skill_refs_parser_finds_both_forms(self):
        refs = skill_refs()
        self.assertIn("scripts/tier.py", refs)  # with the "<this skill's folder>/" prefix
        self.assertIn("references/fable.md", refs)  # bare

    def test_skill_refs_parser_skips_other_folders_and_globs(self):
        text = ("`gemini-review/scripts/review.py` `references/*.md` `scripts/x.py.bak` "
                "`python3 -B <this skill's folder>/scripts/a.py run` `scripts/b.py decide` and scripts/c.py unquoted")
        self.assertEqual(refs_in(text), {"scripts/a.py", "scripts/b.py"})

    def test_skill_refs_follow_sibling_imports(self):
        self.assertIn("scripts/tier.py", sibling_imports("scripts/testfail.py"))

    def test_skill_refs_are_copied_and_follow_skill(self):
        missing = []
        for rel in sorted(skill_refs()):
            copy = os.path.join(COPY_DIR, *rel.split("/"))
            if copy not in nousfx_project.COPIES or copy not in nousfx_project.FOLLOW_SKILL:
                missing.append(rel)
        self.assertEqual(missing, [], "SKILL.md names these; add them to COPIES and FOLLOW_SKILL in "
                                      "scripts/nousfx_project.py")

    def test_skill_refs_sources_exist(self):
        for rel in sorted(skill_refs()):
            copy = os.path.join(COPY_DIR, *rel.split("/"))
            src = nousfx_project.COPIES.get(copy)
            if src:
                self.assertTrue(os.path.isfile(os.path.join(ROOT, src)), src)


if __name__ == "__main__":
    unittest.main()
