#!/usr/bin/env python3
"""Tests for failure_modes.py (NFX-201): every "Failure modes" bullet of a ticket maps to a test, or a stated waiver.
Run: python3 -B claude/skills/review-loop/scripts/test_failure_modes.py

The CLI runs on files in a temp dir; it needs no repo and runs nothing.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "failure_modes.py")
sys.dont_write_bytecode = True

TICKET = """## Goal

Something.

## Acceptance criteria

* [ ] It works. Proof: `python3 -B t.py`

## Failure modes

* The check is slow: it reports the timeout, never a pass.
* A pure refactor passes without the change: waivable with a reason.
- The copy leaks ignored files: they are not copied,
  and the copy is removed after the run.

## Environment

* env-none is not a failure mode.
"""

# NFX-190 as filed: five failure modes. Its PR (#149) had a proof table that maps none of them.
NFX_190 = """## Failure modes

* The check is slow and runs twice: it uses the same timeout as `--mark` and reports the timeout, never a pass.
* A pure refactor legitimately passes without the change: the finding is waivable with a stated reason in the PR.
* The copy leaks secrets or ignored files: it lives in system temp, is removed after the run, and gitignored files are not copied.
* The restore step touches the real tree: the criterion above proves it does not.
* Another stack prints test failures differently: the failure pattern is configurable, and with no match the verdict is `build`, which never counts as proof.

## Environment

env-none
"""
PR_149 = """| criterion | proof | result |
|---|---|---|
| fix + test prints `yes` | `test_testfail.py` | pass |
| passing test prints `no`, exit 1 | `test_testfail.py` | pass |
| tree and index unchanged by a run | `test_testfail.py` | pass |
| review-loop names the step and the `no` finding | `test_review_loop_split.py` | pass |
"""


def table(*rows):
    return "| failure mode | test |\n|---|---|\n" + "".join(f"| {a} | {b} |\n" for a, b in rows)


class FailureModes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        path = os.path.join(self._tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def cli(self, *args):
        r = subprocess.run([sys.executable, "-B", SCRIPT, *args], capture_output=True, text=True,
                           cwd=self._tmp.name)
        return r.returncode, r.stdout, r.stderr

    def check(self, ticket, rows_text):
        return self.cli("check", self.write("ticket.md", ticket), self.write("table.md", rows_text))

    def findings(self, out):
        return [l for l in out.splitlines() if l.startswith("FM") and " | med | " in l]

    def test_n_failure_modes_with_n_minus_one_mapped_tests_yield_one_finding(self):
        code, out, _ = self.check(TICKET, table(("FM1 slow check", "`test_x.py::test_timeout`"),
                                                ("FM3 leaks ignored files", "`test_x.py::test_no_ignored`")))
        self.assertEqual(code, 1)
        found = self.findings(out)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("FM2 | med | 80 | "))
        self.assertIn("A pure refactor passes without the change", found[0])
        self.assertIn("Failure-Modes: 3 listed, 2 tested, 0 waived, 1 missing", out)
        self.assertNotIn("env-none", out)

    def test_every_failure_mode_mapped_is_clean(self):
        code, out, _ = self.check(TICKET, table(("FM1", "`test_a`"), ("FM2", "`test_b`"), ("FM3", "`test_c`")))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.findings(out), [])
        self.assertIn("Failure-Modes: 3 listed, 3 tested, 0 waived, 0 missing", out)

    def test_a_judgement_call_with_a_stated_reason_is_waived(self):
        code, out, _ = self.check(TICKET, table(("FM1", "`test_a`"), ("FM2", "waived: wording, judged by review"),
                                                ("FM3", "`test_c`")))
        self.assertEqual(code, 0, out)
        self.assertIn("Failure-Modes: 3 listed, 2 tested, 1 waived, 0 missing", out)

    def test_a_waiver_with_no_reason_is_a_finding(self):
        code, out, _ = self.check(TICKET, table(("FM1", "`test_a`"), ("FM2", "waived:"), ("FM3", "`test_c`")))
        self.assertEqual(code, 1)
        self.assertEqual([f[:3] for f in self.findings(out)], ["FM2"])

    def test_a_prose_or_empty_test_cell_is_a_finding(self):
        code, out, _ = self.check(TICKET, table(("FM1", "covered by the suite"), ("FM2", ""), ("FM3", "``")))
        self.assertEqual(code, 1)
        self.assertEqual([f[:3] for f in self.findings(out)], ["FM1", "FM2", "FM3"])

    def test_the_real_nfx_190_instance_yields_five_findings(self):
        code, out, _ = self.check(NFX_190, PR_149)
        self.assertEqual(code, 1)
        self.assertEqual([f[:3] for f in self.findings(out)], ["FM1", "FM2", "FM3", "FM4", "FM5"])

    def test_a_ticket_with_no_failure_modes_heading_passes_and_says_so(self):
        code, out, _ = self.check("## Goal\n\n* a bullet\n", "")
        self.assertEqual(code, 0)
        self.assertIn('Failure-Modes: 0 listed (no "Failure modes" heading)', out)

    def test_failure_modes_named_without_a_matching_heading_are_refused(self):
        for text in ("**Failure modes**\n\n* one\n", "h2. Failure modes\n* one\n",
                     "## Failure modes and mitigations\n\n* one\n"):
            with self.subTest(text=text):
                code, out, err = self.check("## Goal\n\nx\n\n" + text, "")
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("line 5", err)

    def test_an_item_indented_one_space_is_a_sibling(self):
        code, out, _ = self.check("## Failure modes\n\n* one\n * two\n  - detail of two\n", "")
        self.assertIn("2 listed", out)

    def test_a_fence_closes_only_on_its_own_marker(self):
        ticket = "## Failure modes\n\n````md\n```\n# not a heading\n~~~\n````\n\n* one\n\n## Environment\n"
        code, out, _ = self.check(ticket, "")
        self.assertEqual(code, 1, out)
        self.assertIn("1 listed", out)

    def test_a_numbered_list_counts_as_failure_modes(self):
        code, out, _ = self.check("## Failure modes\n\n1. first\n2) second\n", table(("FM1", "`a`")))
        self.assertEqual(code, 1)
        self.assertEqual([f[:3] for f in self.findings(out)], ["FM2"])

    def test_a_heading_with_a_colon_or_a_parenthesised_note_still_counts(self):
        for heading in ("## Failure modes:", "## Failure modes (production)", "### FAILURE MODES"):
            with self.subTest(heading=heading):
                code, out, _ = self.check(f"{heading}\n\n* one\n", "")
                self.assertEqual(code, 1)
                self.assertIn("1 listed", out)

    def test_a_subheading_or_a_fenced_hash_line_does_not_end_the_section(self):
        ticket = ("## Failure modes\n\n* one\n\n### Details\n\n* two\n\n```sh\n# a shell comment\n```\n\n* three\n\n"
                  "## Environment\n\n* not one\n")
        code, out, _ = self.check(ticket, "")
        self.assertIn("3 listed", out)

    def test_an_indented_item_continues_the_item_above_it(self):
        code, out, _ = self.cli("list", self.write("t.md", "## Failure modes\n\n* one\n  * detail\n* two\n"))
        self.assertIn("| FM1 one * detail |", out)
        self.assertEqual(len([l for l in out.splitlines() if l.startswith("| FM")]), 2)

    def test_prose_holding_a_backticked_word_is_not_a_test(self):
        code, out, _ = self.check("## Failure modes\n\n* one\n* two\n",
                                  table(("FM1", "no test yet, see `prove.py`"), ("FM2", "`test_a`, `test_b`")))
        self.assertEqual(code, 1)
        self.assertEqual([f[:3] for f in self.findings(out)], ["FM1"])

    def test_a_byte_order_mark_is_ignored(self):
        ticket = self.write("bom.md", "﻿## Failure modes\n\n* one\n")
        rows = self.write("rows.md", "﻿| FM1 one | `test_a` |\n")
        code, out, _ = self.cli("check", ticket, rows)
        self.assertEqual(code, 0, out)
        self.assertIn("1 listed, 1 tested", out)

    def test_list_prints_nothing_when_it_refuses(self):
        code, out, _ = self.cli("list", self.write("t.md", "## Failure modes\n\nIt could time out.\n"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")

    def test_list_says_when_there_is_no_heading(self):
        code, out, _ = self.cli("list", self.write("t.md", "## Goal\n\n* a\n"))
        self.assertEqual(code, 0)
        self.assertIn('no "Failure modes" heading', out)

    def test_a_section_with_text_but_no_list_item_is_refused(self):
        code, out, err = self.check("## Failure modes\n\nIt could time out, or leak files.\n\n## Environment\n", "")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("no list item", err)

    def test_a_row_for_an_unknown_or_repeated_failure_mode_is_refused(self):
        for rows in ((("FM1", "`a`"), ("FM4", "`d`")), (("FM1", "`a`"), ("FM1", "`b`"))):
            with self.subTest(rows=rows):
                code, out, err = self.check(TICKET, table(*rows))
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("FM", err)

    def test_an_unreadable_file_is_refused(self):
        code, _, err = self.cli("check", os.path.join(self._tmp.name, "missing.md"), self.write("t.md", ""))
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_list_prints_one_row_per_failure_mode_for_the_plan(self):
        code, out, _ = self.cli("list", self.write("ticket.md", TICKET))
        self.assertEqual(code, 0)
        rows = [l for l in out.splitlines() if l.startswith("| FM")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[2].startswith("| FM3 The copy leaks ignored files: they are not copied, and the copy"))
        self.assertTrue(out.startswith("| failure mode | test |\n|---|---|\n"))

    def test_list_escapes_a_pipe_so_its_table_reads_back(self):
        ticket = "## Failure modes\n\n* a | b breaks the table\n"
        code, out, _ = self.cli("list", self.write("ticket.md", ticket))
        self.assertIn("| FM1 a \\| b breaks the table |", out)
        filled = out.replace("table |  |", "table | `test_pipe` |")
        code, out, _ = self.check(ticket, filled)
        self.assertEqual(code, 0, out)

    def test_the_unfilled_list_reads_back_as_all_missing(self):
        _, skeleton, _ = self.cli("list", self.write("ticket.md", TICKET))
        code, out, _ = self.check(TICKET, skeleton)
        self.assertEqual(code, 1)
        self.assertEqual(len(self.findings(out)), 3)


if __name__ == "__main__":
    unittest.main()
