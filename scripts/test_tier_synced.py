#!/usr/bin/env python3
"""NFX-216: tier.py leaves out project files that are byte-equal to their NousFX sync source.
Run: python3 -B scripts/test_tier_synced.py

Each test builds a throwaway project repo under a temp dir; NousFX is this checkout (tier.py finds it from its own
real path). The PAP-68 shape: a branch that adds testfail.py, prove.py and session_report.py copied verbatim.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "claude", "skills", "review-loop", "scripts")
sys.path.insert(0, SCRIPTS)
sys.dont_write_bytecode = True

import tier  # noqa: E402

PAP_68 = ("testfail.py", "prove.py", "session_report.py")
COPY_DIR = ".claude/skills/review-loop/scripts/"


class SyncedFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "project")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.write("README.md", "project\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "cesar/PAP-68")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def write(self, rel, data):
        full = os.path.join(self.repo, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)

    def copy_pap_68(self, mutate=None):
        for name in PAP_68:
            with open(os.path.join(SCRIPTS, name), "rb") as fh:
                data = fh.read()
            if name == mutate:
                data = data[:-1] + (b"\n" if data[-1:] != b"\n" else b" ")  # exactly one byte differs
            self.write(COPY_DIR + name, data)
        self.git("add", "-A")
        self.git("commit", "-qm", "sync")

    def result(self, synced=True):
        return tier.run(self.repo, self.base, [], synced=synced)

    def test_byte_equal_copies_score_tier_0_or_1_and_say_synced(self):
        self.copy_pap_68()
        out = self.result()
        self.assertLessEqual(out["tier"], 1, out["reasons"])
        self.assertTrue(any("synced: 3 files" in r for r in out["reasons"]), out["reasons"])
        self.assertEqual(out["stats"]["lines"], 0)
        self.assertEqual(sorted(out["synced"]), sorted(COPY_DIR + n for n in PAP_68))
        self.assertNotIn("sharp-edges", out["security"])

    def test_one_changed_byte_counts_the_file_in_full(self):
        self.copy_pap_68(mutate="session_report.py")
        out = self.result()
        self.assertTrue(any("synced: 2 files" in r for r in out["reasons"]), out["reasons"])
        self.assertNotIn(COPY_DIR + "session_report.py", out["synced"])
        with open(os.path.join(SCRIPTS, "session_report.py"), "rb") as fh:
            full = len(fh.read().decode("utf-8").splitlines())
        self.assertEqual(out["stats"]["lines"], full)
        self.assertEqual(out["tier"], 3, out["reasons"])

    def test_without_the_flag_every_file_counts_as_before(self):
        self.copy_pap_68()
        out = self.result(synced=False)
        self.assertEqual(out["synced"], [])
        self.assertEqual(out["tier"], 3, out["reasons"])
        self.assertFalse(any("synced:" in r for r in out["reasons"]), out["reasons"])

    def test_a_path_outside_the_manifest_is_counted(self):
        with open(os.path.join(SCRIPTS, "prove.py"), "rb") as fh:
            self.write("tools/prove.py", fh.read())  # a renamed copy: the manifest misses it, so it counts
        self.git("add", "-A")
        self.git("commit", "-qm", "renamed copy")
        out = self.result()
        self.assertEqual(out["synced"], [])
        self.assertGreater(out["stats"]["lines"], 0)

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_a_symlinked_copy_is_counted(self):
        os.makedirs(os.path.join(self.repo, *COPY_DIR.split("/")[:-1]))
        os.symlink(os.path.join(SCRIPTS, "prove.py"), os.path.join(self.repo, *(COPY_DIR + "prove.py").split("/")))
        self.git("add", "-A")
        self.git("commit", "-qm", "link")
        self.assertEqual(self.result()["synced"], [])

    def test_a_deleted_copy_is_counted(self):
        self.copy_pap_68()
        self.base = self.git("rev-parse", "HEAD")
        self.git("rm", "-q", COPY_DIR + "prove.py")
        self.git("commit", "-qm", "drop")
        out = self.result()
        self.assertEqual(out["synced"], [])
        self.assertGreater(out["stats"]["lines"], 0)

    def test_risk_paths_still_count_for_a_synced_file(self):
        with open(os.path.join(ROOT, "claude", "hooks", "review-gate.py"), "rb") as fh:
            self.write(".claude/hooks/review-gate.py", fh.read())
        self.git("add", "-A")
        self.git("commit", "-qm", "gate")
        out = self.result()
        self.assertEqual(out["synced"], [".claude/hooks/review-gate.py"])
        self.assertEqual(out["tier"], 3)
        self.assertIn("Tier 3: touches .claude/hooks/review-gate.py", out["reasons"])

    def test_untracked_byte_equal_copies_are_synced_too(self):
        with open(os.path.join(SCRIPTS, "prove.py"), "rb") as fh:
            self.write(COPY_DIR + "prove.py", fh.read())
        self.assertEqual(self.result()["synced"], [COPY_DIR + "prove.py"])

    def test_the_nousfx_checkout_itself_is_never_synced(self):
        out = tier.run(ROOT, "HEAD", [], synced=True)
        self.assertEqual(out["synced"], [])

    def test_cli_flag(self):
        self.copy_pap_68()
        r = subprocess.run([sys.executable, "-B", os.path.join(SCRIPTS, "tier.py"), "--base", self.base, "--synced",
                            "--cwd", self.repo], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("synced: 3 files", r.stdout)


if __name__ == "__main__":
    unittest.main()
