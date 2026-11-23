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
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "claude", "skills", "review-loop", "scripts")
sys.path.insert(0, SCRIPTS)
sys.dont_write_bytecode = True

import tier  # noqa: E402

PAP_68 = ("testfail.py", "prove.py", "session_report.py")
COPY_DIR = ".claude/skills/review-loop/scripts/"


def reviewed(rel):
    """A NousFX file as tier.py compares it: from the default branch, not the working tree."""
    ref = tier.default_ref(ROOT)
    if not ref:
        raise unittest.SkipTest("NousFX has no origin/HEAD or origin/main: fetch origin first")
    return tier.git(["show", "%s:%s" % (ref, rel)], ROOT, raw=True)


def script(name):
    return reviewed("claude/skills/review-loop/scripts/" + name)


def one_byte_off(data):
    return data[:-1] + (b"\n" if data[-1:] != b"\n" else b" ")


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
            data = script(name)
            if name == mutate:
                data = one_byte_off(data)
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
        full = len(script("session_report.py").decode("utf-8").splitlines())
        self.assertEqual(out["stats"]["lines"], full)
        self.assertEqual(out["tier"], 3, out["reasons"])

    def test_without_the_flag_every_file_counts_as_before(self):
        self.copy_pap_68()
        out = self.result(synced=False)
        self.assertEqual(out["synced"], [])
        self.assertEqual(out["tier"], 3, out["reasons"])
        self.assertFalse(any("synced:" in r for r in out["reasons"]), out["reasons"])

    def test_a_renamed_copy_outside_the_manifest_is_counted(self):
        self.write("tools/prove.py", script("prove.py"))
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

    def test_a_synced_gate_meets_no_risk_path(self):
        self.write(".claude/hooks/review-gate.py", reviewed("claude/hooks/review-gate.py"))
        self.git("add", "-A")
        self.git("commit", "-qm", "gate")
        out = self.result()
        self.assertEqual(out["synced"], [".claude/hooks/review-gate.py"])
        self.assertLessEqual(out["tier"], 1, out["reasons"])
        self.assertNotIn("Tier 3: touches .claude/hooks/review-gate.py", out["reasons"])

    def test_untracked_byte_equal_copies_are_synced_too(self):
        self.write(COPY_DIR + "prove.py", script("prove.py"))
        self.assertEqual(self.result()["synced"], [COPY_DIR + "prove.py"])

    def mark_against(self, root, merged=b"same\n", working=None, copy=b"same\n"):
        """mark_synced with a fake NousFX checkout at `root`: `merged` is src/prove.py on its origin/main, `working`
        (default: the same) its working-tree bytes, `copy` the project's copy."""
        self.write(COPY_DIR + "prove.py", copy)
        os.makedirs(os.path.join(root, "src"), exist_ok=True)
        source = os.path.join(root, "src", "prove.py")
        with open(source, "wb") as fh:
            fh.write(merged)
        nousfx = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root,
                                           capture_output=True, check=True)
        if not os.path.isdir(os.path.join(root, ".git")):
            nousfx("init", "-q", "-b", "main")
        nousfx("add", os.path.join("src", "prove.py"))
        nousfx("commit", "-qm", "source")
        nousfx("update-ref", "refs/remotes/origin/main", "HEAD")
        if working is not None:
            with open(source, "wb") as fh:
                fh.write(working)
        files = [{"path": COPY_DIR + "prove.py"}]
        with mock.patch.object(tier, "nousfx_copies", return_value=(os.path.realpath(root), {COPY_DIR + "prove.py": "src/prove.py"}, {})):
            tier.mark_synced(self.repo, files)
        return files[0].get("synced")

    def test_a_sibling_nousfx_checkout_marks_the_copy(self):
        self.assertTrue(self.mark_against(os.path.join(self.tmp, "nousfx")))

    def test_an_uncommitted_nousfx_edit_is_not_synced(self):
        self.assertFalse(self.mark_against(os.path.join(self.tmp, "nousfx"), working=b"unreviewed\n",
                                           copy=b"unreviewed\n"))

    def test_a_nousfx_feature_branch_is_not_synced(self):
        root = os.path.join(self.tmp, "nousfx")
        self.assertTrue(self.mark_against(root))
        self.assertFalse(self.mark_on_branch(root))

    def test_a_nousfx_without_a_default_branch_is_not_synced(self):
        root = os.path.join(self.tmp, "nousfx")
        with mock.patch.object(tier, "default_ref", return_value=None), \
                mock.patch.object(tier, "nousfx_source") as source:
            self.assertFalse(self.mark_against(root))
        source.assert_not_called()

    def mark_on_branch(self, root):
        """A commit on a NousFX feature branch, never merged: origin/main still has the old bytes."""
        run = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root,
                                        capture_output=True, check=True)
        run("checkout", "-qb", "feature")
        with open(os.path.join(root, "src", "prove.py"), "wb") as fh:
            fh.write(b"branch\n")
        run("commit", "-qam", "feature")
        self.write(COPY_DIR + "prove.py", b"branch\n")
        files = [{"path": COPY_DIR + "prove.py"}]
        with mock.patch.object(tier, "nousfx_copies", return_value=(os.path.realpath(root), {COPY_DIR + "prove.py": "src/prove.py"}, {})):
            tier.mark_synced(self.repo, files)
        return files[0].get("synced")

    def test_a_repo_inside_the_nousfx_checkout_is_never_synced(self):
        self.assertIsNone(self.mark_against(self.tmp))

    def test_a_nousfx_checkout_inside_the_repo_is_never_synced(self):
        self.assertIsNone(self.mark_against(os.path.join(self.repo, "vendor", "nousfx")))

    def test_cli_flag(self):
        self.copy_pap_68()
        r = subprocess.run([sys.executable, "-B", os.path.join(SCRIPTS, "tier.py"), "--base", self.base, "--synced",
                            "--cwd", self.repo], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("synced: 3 files", r.stdout)



class LoopWording(unittest.TestCase):
    def test_step_1_passes_synced_and_names_the_override(self):
        with open(os.path.join(ROOT, "claude", "skills", "review-loop", "SKILL.md"), encoding="utf-8") as fh:
            skill = fh.read()
        self.assertIn("--base <base hash> --synced --labels", skill)
        self.assertIn('--tier-override "synced: N files byte-equal to NousFX"', skill)


if __name__ == "__main__":
    unittest.main()
