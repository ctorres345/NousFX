#!/usr/bin/env python3
"""The project-setup audit flags a synced project whose agents cannot find its ADRs (NFX-234).

Run: python3 -B scripts/test_project_setup_adr.py

A project with .nousfx/project.toml needs docs/agents/domain.md naming docs/adr/. Without it, the ADRs that comment
cleanups (epic NFX-227) create are never read. The note is advice: docs/adr/ itself is created lazily, and the note never
changes sync's exit code.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import nousfx_project  # noqa: E402 (imported after the sys.path insert above)
import nousfx_sync  # noqa: E402 (imported after the sys.path insert above)

FIX = "/setup-matt-pocock-skills"
DOMAIN_REL = os.path.join("docs", "agents", "domain.md")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class DomainDocNote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = self.tmp.name
        write(os.path.join(self.project, ".nousfx", "project.toml"), '[check]\ncommand = "true"\n')

    def tearDown(self):
        self.tmp.cleanup()

    def notes(self):
        return nousfx_project.plan_project(ROOT, self.project, nousfx_sync.SyncError).notes

    def domain_notes(self):
        return [n for n in self.notes() if "domain.md" in n]

    def test_missing_domain_md_is_named_with_the_fix(self):
        [note] = self.domain_notes()
        self.assertIn(os.path.join(self.project, DOMAIN_REL), note)
        self.assertIn("missing", note)
        self.assertIn(FIX, note)

    def test_domain_md_without_adr_pointer_is_named_with_the_fix(self):
        write(os.path.join(self.project, DOMAIN_REL), "# Domain Docs\n\nRead GLOSSARY.md first.\n")
        [note] = self.domain_notes()
        self.assertIn("docs/adr/", note)
        self.assertIn(FIX, note)

    def test_nousfx_own_domain_md_passes_without_docs_adr(self):
        with open(os.path.join(ROOT, DOMAIN_REL), encoding="utf-8") as f:
            write(os.path.join(self.project, DOMAIN_REL), f.read())
        self.assertFalse(os.path.exists(os.path.join(self.project, "docs", "adr")))
        self.assertEqual(self.domain_notes(), [])

    def test_passes_with_docs_adr_present(self):
        write(os.path.join(self.project, DOMAIN_REL), "Read `docs/adr/` before exploring.\n")
        write(os.path.join(self.project, "docs", "adr", "0001-x.md"), "---\nstatus: accepted\n---\n# X\n")
        self.assertEqual(self.domain_notes(), [])

    def test_multi_context_layout_passes_when_it_names_docs_adr(self):
        write(os.path.join(self.project, "GLOSSARY-MAP.md"), "ordering: src/ordering/GLOSSARY.md\n")
        write(os.path.join(self.project, DOMAIN_REL),
              "Read GLOSSARY-MAP.md, then docs/adr/ and src/<context>/docs/adr/.\n")
        self.assertEqual(self.domain_notes(), [])

    def test_project_without_project_toml_is_not_flagged(self):
        os.remove(os.path.join(self.project, ".nousfx", "project.toml"))
        self.assertEqual(self.domain_notes(), [])

    def test_symlink_leading_outside_the_repo_is_not_read(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        write(os.path.join(outside.name, "domain.md"), "no pointer here\n")
        os.makedirs(os.path.dirname(os.path.join(self.project, DOMAIN_REL)))
        os.symlink(os.path.join(outside.name, "domain.md"), os.path.join(self.project, DOMAIN_REL))
        [note] = self.domain_notes()
        self.assertIn("left alone", note)
        self.assertNotIn(FIX, note)

    def test_symlink_inside_the_repo_is_checked(self):
        shared = os.path.join(self.project, "docs", "shared-domain.md")
        write(shared, "Read GLOSSARY.md.\n")
        os.makedirs(os.path.dirname(os.path.join(self.project, DOMAIN_REL)))
        os.symlink(shared, os.path.join(self.project, DOMAIN_REL))
        [note] = self.domain_notes()
        self.assertIn(FIX, note)
        write(shared, "Read docs/adr/.\n")
        self.assertEqual(self.domain_notes(), [])

    def test_pointer_without_trailing_slash_passes(self):
        for text in ("Read docs/adr for decisions.\n", "See [ADRs](docs/adr).\n", "Decisions live in docs/adr.\n"):
            write(os.path.join(self.project, DOMAIN_REL), text)
            self.assertEqual(self.domain_notes(), [], text)

    def test_other_names_starting_with_docs_adr_do_not_count(self):
        for text in ("See docs/adr.md.\n", "See docs/adr-notes/.\n", "See docs/adrs/.\n",
                     "Read src/ordering/docs/adr/ only.\n", "See mkdocs/adr/.\n", "See ../other/docs/adr/.\n"):
            write(os.path.join(self.project, DOMAIN_REL), text)
            [note] = self.domain_notes()
            self.assertIn(FIX, note, text)

    def test_non_utf8_domain_md_does_not_stop_sync(self):
        path = os.path.join(self.project, DOMAIN_REL)
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(b"caf\xe9 docs/adr/\n")
        [note] = self.domain_notes()
        self.assertIn("left alone", note)

    def test_symlinked_parent_folder_is_not_read(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        write(os.path.join(outside.name, "domain.md"), "no pointer here\n")
        os.makedirs(os.path.join(self.project, "docs"))
        os.symlink(outside.name, os.path.join(self.project, "docs", "agents"))
        [note] = self.domain_notes()
        self.assertIn("left alone", note)
        self.assertNotIn(FIX, note)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")
    def test_fifo_domain_md_is_not_read(self):
        os.makedirs(os.path.dirname(os.path.join(self.project, DOMAIN_REL)))
        os.mkfifo(os.path.join(self.project, DOMAIN_REL))
        [note] = self.domain_notes()
        self.assertIn("left alone", note)

    def test_note_is_advice_not_drift(self):
        """The audit runs sync --check; the domain note prints there but never changes what sync does or its exit."""
        git = subprocess.run(["git", "init", "-q", self.project], capture_output=True)
        self.assertEqual(git.returncode, 0, git.stderr)
        home = os.path.join(self.project, "home")
        os.makedirs(home)

        def check():
            plan = nousfx_project.plan_project(ROOT, self.project, nousfx_sync.SyncError)
            run = subprocess.run([sys.executable, "-B", os.path.join(HERE, "nousfx_sync.py"), "--project",
                                  self.project, "--check"], capture_output=True, text=True, cwd=ROOT,
                                 env={**os.environ, "HOME": home, "USERPROFILE": home})
            return (plan.files, plan.links, plan.executable), run

        flagged, run_flagged = check()
        self.assertIn(FIX, run_flagged.stdout, run_flagged.stdout + run_flagged.stderr)
        write(os.path.join(self.project, DOMAIN_REL), "Read docs/adr/.\n")
        passing, run_passing = check()
        self.assertNotIn(FIX, run_passing.stdout)
        self.assertEqual(flagged, passing)
        self.assertEqual(run_flagged.returncode, run_passing.returncode)


class SkillPointsAtTheNote(unittest.TestCase):
    def test_audit_reports_the_domain_note(self):
        with open(os.path.join(ROOT, "claude", "skills", "project-setup", "SKILL.md"), encoding="utf-8") as f:
            skill = f.read()
        audit = skill.split("## 1. Audit", 1)[1].split("## 2.", 1)[0]
        self.assertIn("domain.md", audit)
        self.assertIn(FIX, audit)


if __name__ == "__main__":
    unittest.main()
