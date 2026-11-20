#!/usr/bin/env python3
"""Tests for prove.py (NFX-105), the script that runs a ticket's proofs and stamps them.
Run: python3 -B claude/skills/review-loop/scripts/test_prove.py

Each test runs the CLI in a throwaway git repo under a temp dir; the real repo and HOME are never touched. The CLI
runs with an empty temp home (gate_lookup_cases), so it never loads the real ~/.claude gate (NFX-214).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

from gate_lookup_cases import GateLookupCases, run_script

HERE = os.path.dirname(os.path.abspath(__file__))
PROVE = os.path.join(HERE, "prove.py")
sys.dont_write_bytecode = True

LINE = re.compile(r"^AC-Proof: (\d+)/(\d+) manual:(\d+) ([0-9a-f]{40,64}) ([0-9a-f]{64})$", re.M)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "repo")
        self.outside = os.path.join(self._tmp.name, "lists")
        os.makedirs(self.repo)
        os.makedirs(self.outside)
        self.git("init", "-q", "-b", "cesar/NFX-1")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("a\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "NFX-1: start")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write_list(self, text, name="proofs.md", inside=False):
        path = os.path.join(self.repo if inside else self.outside, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def prove(self, *args):
        return run_script([PROVE, *args], cwd=self.repo)

    def stamp_path(self):
        return os.path.join(self.repo, ".git", "claude-proof-pass")


def ac(*proofs):
    return "".join(f"- [ ] Criterion {i}. Proof: {p}\n" for i, p in enumerate(proofs))


class Run(Base):
    def test_one_failing_proof_exits_non_zero_and_writes_no_stamp(self):
        path = self.write_list(ac("`true`", "`false`"))
        r = self.prove("run", path)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(self.stamp_path()))

    def test_failure_removes_an_older_stamp(self):
        path = self.write_list(ac("`true`"))
        self.assertEqual(self.prove("run", path).returncode, 0)
        self.assertTrue(os.path.exists(self.stamp_path()))
        bad = self.write_list(ac("`false`"), name="bad.md")
        self.assertEqual(self.prove("run", bad).returncode, 2)
        self.assertFalse(os.path.exists(self.stamp_path()))

    def test_stops_at_the_first_failure(self):
        path = self.write_list(ac("`false`", "`touch ran-second`"))
        self.assertEqual(self.prove("run", path).returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "ran-second")))

    def test_a_proof_that_edits_files_is_refused(self):
        path = self.write_list(ac("`true`", "`echo x > new-file.txt`"))
        r = self.prove("run", path)
        self.assertEqual(r.returncode, 2)
        self.assertIn("changed files", r.stderr)
        self.assertFalse(os.path.exists(self.stamp_path()))

    def test_a_timeout_counts_as_a_fail(self):
        path = self.write_list(ac("`sleep 30`"))
        r = self.prove("run", path, "--timeout", "1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("did not finish", r.stderr)
        self.assertFalse(os.path.exists(self.stamp_path()))

    def test_a_proof_cannot_leave_a_stamp_behind(self):
        """The command is branch code: a stamp it writes itself must not survive a failing run."""
        path = self.write_list(ac(f"`echo '{{}}' > .git/claude-proof-pass; false`"))
        self.assertEqual(self.prove("run", path).returncode, 2)
        self.assertFalse(os.path.exists(self.stamp_path()))

    def test_a_malformed_proof_line_is_refused(self):
        for bad in ("pytest x", "new test.", "``", "`  `", "manual:", "manual:   "):
            with self.subTest(bad=bad):
                path = self.write_list(ac("`true`") + f"- [ ] Other. Proof: {bad}\n")
                r = self.prove("run", path)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertFalse(os.path.exists(self.stamp_path()))

    def test_a_list_without_a_command_proof_is_refused(self):
        for text in ("", "no proofs here\n", ac("manual: look at it")):
            with self.subTest(text=text):
                r = self.prove("run", self.write_list(text))
                self.assertEqual(r.returncode, 2)
                self.assertFalse(os.path.exists(self.stamp_path()))

    def test_proof_word_in_backticks_is_prose_not_a_proof(self):
        path = self.write_list("Each criterion ends in a `Proof:` line.\n" + ac("`true`"))
        self.assertEqual(self.prove("run", path).returncode, 0)

    def test_any_refused_run_removes_an_older_stamp(self):
        self.assertEqual(self.prove("run", self.write_list(ac("`true`"))).returncode, 0)
        malformed = self.write_list(ac("`true`") + "- [ ] x. Proof: pytest\n", name="m.md")
        self.assertEqual(self.prove("run", malformed).returncode, 2)
        self.assertFalse(os.path.exists(self.stamp_path()))
        self.assertEqual(self.prove("run", self.write_list(ac("`true`"))).returncode, 0)
        self.assertEqual(self.prove("run", malformed, "--timeout", "0").returncode, 2)
        self.assertFalse(os.path.exists(self.stamp_path()))

    def test_ac_proof_in_prose_is_not_a_proof_line(self):
        text = "Scope: attest prints `AC-Proof: 2/2 <tree> <sha>`.\nAC-Proof: 1/1 manual:0 x y\n" + ac("`true`")
        self.assertEqual(self.prove("run", self.write_list(text)).returncode, 0)

    def test_detached_head_is_refused(self):
        self.git("checkout", "-q", "--detach")
        r = self.prove("run", self.write_list(ac("`true`")))
        self.assertEqual(r.returncode, 2)
        self.assertIn("detached", r.stderr)

    def test_outside_a_repo_is_refused(self):
        r = subprocess.run([sys.executable, "-B", PROVE, "run", self.write_list(ac("`true`"))], cwd=self.outside,
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 2)

    def test_missing_list_is_refused(self):
        r = self.prove("run", os.path.join(self.outside, "nope.md"))
        self.assertEqual(r.returncode, 2)


class Lint(Base):
    """NFX-208: `lint` checks a ticket's Proof: lines with run's parser, before any code exists."""

    def lint(self, text):
        return self.prove("lint", self.write_list(text))

    def test_lines_run_cannot_read_are_named_with_their_number(self):
        for bad in ("same", "manual, look at it", "the gate's test suite passes", "manual: x Backup:"):
            with self.subTest(bad=bad):
                r = self.lint(ac("`true`") + f"- [ ] Other. Proof: {bad}\n")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("line 2:", r.stdout)

    def test_every_bad_line_is_listed_not_only_the_first(self):
        r = self.lint(ac("same", "`true`", "manual, x"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("line 1:", r.stdout)
        self.assertIn("line 3:", r.stdout)
        self.assertNotIn("line 2:", r.stdout)

    def test_runnable_proofs_pass(self):
        for good in ("`true`", "`true`.", "manual: open the app", "manual: call the API Backup: `true`"):
            with self.subTest(good=good):
                r = self.lint(ac(good))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_list_of_only_manual_proofs_passes(self):
        """run refuses a list with nothing to run; a ticket whose proofs are all manual is still valid."""
        self.assertEqual(self.lint(ac("manual: look at it", "manual: tap it")).returncode, 0)

    def test_a_file_with_no_proof_lines_passes(self):
        """ticket-check flags a criterion with no Proof: on its own; lint judges only the lines that exist."""
        for text in ("", "no proofs here\n", "Each criterion ends in a `Proof:` line.\n"):
            with self.subTest(text=text):
                self.assertEqual(self.lint(text).returncode, 0)

    def test_a_quoted_example_in_prose_is_refused_like_run_does(self):
        """Shared parser: a quoted "Proof: same" is a proof line to run too, so ticket-check says to backtick it."""
        r = self.lint('Seen: "Proof: same" in NFX-198.\n')
        self.assertEqual(r.returncode, 2)
        self.assertIn("line 1:", r.stdout)

    def test_lint_and_run_agree_on_each_line(self):
        for proof in ("`true`", "same", "manual, x", "manual:", "``", "", "pytest x", "manual: a Backup: b Backup: c"):
            with self.subTest(proof=proof):
                text = ac("`true`", proof)
                self.assertEqual(self.lint(text).returncode == 0, self.prove("run", self.write_list(text)).returncode == 0)

    def test_lint_never_runs_a_proof_or_writes_a_stamp(self):
        r = self.lint(ac("`touch ran.txt`"))
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "ran.txt")))
        self.assertFalse(os.path.exists(self.stamp_path()))

    def test_lint_works_outside_a_repo(self):
        path = self.write_list(ac("same"))
        r = subprocess.run([sys.executable, "-B", PROVE, "lint", path], cwd=self.outside, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("line 1:", r.stdout)

    def test_a_missing_file_is_refused(self):
        self.assertEqual(self.prove("lint", os.path.join(self.outside, "nope.md")).returncode, 2)

    def test_non_ascii_ticket_text_on_a_narrow_pipe_still_lists_the_line(self):
        """Windows pipes default to the locale code page; Jira text has arrows and emoji."""
        path = self.write_list(ac("same → 🚀"))
        r = subprocess.run([sys.executable, "-B", PROVE, "lint", path], cwd=self.outside, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=60, env={**os.environ, "PYTHONIOENCODING": "cp1252"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(b"line 1:", r.stdout)


class Attest(Base):
    def run_ok(self, text, **kw):
        path = self.write_list(text, **kw)
        r = self.prove("run", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        return path

    def test_two_passing_proofs_print_the_line(self):
        path = self.run_ok(ac("`true`", "`test -f a.txt`"))
        r = self.prove("attest", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = LINE.search(r.stdout)
        self.assertIsNotNone(m, r.stdout)
        self.assertEqual(m.group(1, 2, 3), ("2", "2", "0"))
        self.assertEqual(m.group(4), self.git("rev-parse", "HEAD^{tree}"))

    def test_manual_proofs_are_listed_and_never_counted(self):
        path = self.run_ok(ac("`true`", "manual: check on a device", "manual: open the app"))
        r = self.prove("attest", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        m = LINE.search(r.stdout)
        self.assertEqual(m.group(1, 2, 3), ("1", "1", "2"))
        self.assertIn("manual: check on a device", r.stdout)
        self.assertIn("manual: open the app", r.stdout)

    def test_a_manual_proof_with_a_backup_prints_both(self):
        path = self.run_ok(ac("`true`", "manual: make one image with agy Backup: the recorded run in shots/x.png",
                              "manual: check on a device"))
        r = self.prove("attest", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(LINE.search(r.stdout).group(1, 2, 3), ("1", "1", "2"))
        self.assertIn("  manual: make one image with agy (not run)\n    backup: the recorded run in shots/x.png\n",
                      r.stdout)
        self.assertIn("  manual: check on a device (not run)\n", r.stdout)  # a line without a backup is as before

    def test_a_backup_that_names_nothing_is_refused(self):
        for bad in ("manual: make it Backup:", "manual: make it Backup:   ", "manual: Backup: the recording",
                    "manual: a Backup: b Backup: c"):
            with self.subTest(proof=bad):
                r = self.prove("run", self.write_list(ac("`true`", bad)))
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertIn("line 2", r.stderr)

    def test_attest_prints_the_commands(self):
        path = self.run_ok(ac("`test -f a.txt`"))
        self.assertIn("test -f a.txt", self.prove("attest", path).stdout)

    def test_editing_the_list_after_the_stamp_invalidates_it(self):
        path = self.run_ok(ac("`true`", "`test -f a.txt`"))
        with open(path, "w") as f:
            f.write(ac("`true`", "`true`"))
        r = self.prove("attest", path)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("AC-Proof", r.stdout)

    def test_prose_edits_that_keep_the_proofs_keep_the_stamp(self):
        """The sha covers the proofs, not the wording around them."""
        path = self.run_ok(ac("`true`"))
        with open(path, "w") as f:
            f.write("## Acceptance criteria\n\n" + ac("`true`").replace("Criterion 0", "Reworded"))
        self.assertEqual(self.prove("attest", path).returncode, 0)

    def test_changing_a_manual_proof_invalidates_the_stamp(self):
        path = self.run_ok(ac("`true`", "manual: look"))
        with open(path, "w") as f:
            f.write(ac("`true`", "manual: skip it"))
        self.assertEqual(self.prove("attest", path).returncode, 2)

    def test_changing_the_tree_invalidates_the_stamp(self):
        path = self.run_ok(ac("`true`"))
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("changed\n")
        self.assertEqual(self.prove("attest", path).returncode, 2)

    def test_another_branch_invalidates_the_stamp(self):
        path = self.run_ok(ac("`true`"))
        self.git("checkout", "-q", "-b", "cesar/NFX-2")
        self.assertEqual(self.prove("attest", path).returncode, 2)

    def test_no_stamp_means_no_line(self):
        r = self.prove("attest", self.write_list(ac("`true`")))
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("AC-Proof", r.stdout)

    def test_a_forged_stamp_is_rejected(self):
        path = self.run_ok(ac("`true`"))
        with open(self.stamp_path(), encoding="utf-8") as f:
            stamp = json.load(f)
        stamp["mac"] = "0" * 64
        with open(self.stamp_path(), "w", encoding="utf-8") as f:
            json.dump(stamp, f)
        self.assertEqual(self.prove("attest", path).returncode, 2)

    def test_a_hand_written_stamp_without_the_key_is_rejected(self):
        path = self.write_list(ac("`true`"))
        stamp = {"kind": "proof", "exit": 0, "passed": 1, "total": 1, "manual": 0, "mac": "f" * 64}
        with open(self.stamp_path(), "w", encoding="utf-8") as f:
            json.dump(stamp, f)
        self.assertEqual(self.prove("attest", path).returncode, 2)

    def test_the_check_stamp_is_not_a_proof_stamp(self):
        """The two stamps share a key, so the proof stamp must carry its own kind."""
        path = self.run_ok(ac("`true`"))
        os.replace(self.stamp_path(), os.path.join(self.repo, ".git", "claude-review-check-pass"))
        self.assertEqual(self.prove("attest", path).returncode, 2)

    def test_a_stamp_with_zero_passed_is_rejected(self):
        """attest asserts passed == total > 0 even for a validly signed stamp."""
        path = self.run_ok(ac("`true`"))
        gate = load_gate()
        stamp = gate.valid_stamp(self.repo, {}, name=gate.PROOF_STAMP_FILE)
        stamp.pop("mac", None)
        stamp.update(passed=0, total=0)
        gate.write_stamp(self.repo, stamp, gate.stamp_key(self.repo), name=gate.PROOF_STAMP_FILE)
        self.assertEqual(self.prove("attest", path).returncode, 2)

    def test_a_list_inside_the_repo_works_and_editing_it_invalidates_the_stamp(self):
        path = self.run_ok(ac("`true`"), inside=True)
        self.assertEqual(self.prove("attest", path).returncode, 0)
        with open(path, "w") as f:
            f.write(ac("`true`", "`true`"))
        self.assertEqual(self.prove("attest", path).returncode, 2)


class GateFiles(unittest.TestCase):
    def test_the_gate_core_is_unchanged_for_the_check_stamp(self):
        gate = load_gate()
        self.assertEqual(gate.STAMP_FILE, "claude-review-check-pass")
        self.assertEqual(gate.PROOF_STAMP_FILE, "claude-proof-pass")


class GateLookup(GateLookupCases, unittest.TestCase):
    """NFX-214: a project copy never loads the repo's own .claude/hooks/review-gate.py."""
    SCRIPT = PROVE
    GATE_NAMES = "PROOF_STAMP_FILE = 'claude-proof-pass'"

    def test_lint_needs_no_gate_and_runs_as_review_loop_calls_it(self):
        """Plain `python3 -B` (no stand-in interpreter), outside any repo, with no gate anywhere near the copy."""
        script = self.copy_script(os.path.join(self.tmp, "plain", ".claude", "skills", "review-loop", "scripts"))
        path = os.path.join(self.tmp, "proofs.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(ac("`true`"))
        r = subprocess.run([sys.executable, "-B", script, "lint", path], cwd=self.tmp, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)


def load_gate():
    import importlib.util
    path = os.path.join(HERE, "..", "..", "..", "hooks", "review-gate.py")
    spec = importlib.util.spec_from_file_location("review_gate_prove_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
