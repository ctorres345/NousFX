#!/usr/bin/env python3
"""Tests for via_run.py. Run: python3 plugins/via/scripts/test_via_run.py

Each test runs the script as a subprocess against a throwaway git repo and checks only what a caller
sees: exit codes, stdout JSON and the files left behind.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True  # loading the review gate must not leave a __pycache__ in the repo

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "via_run.py")
LEDGER = os.path.join("docs", "design", "via.md")
FAKE_KEY = "AQ.FAKEKEYFAKEKEYFAKEKEYFAKEKEY99"
ID_RE = re.compile(r"^VIA-[a-z0-9-]+-[0-9a-f]{8}(-\d+)?$")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def finding(screen="home", rule="contrast", file="app/Home.kt", line=10, severity="med", **extra):
    base = {"screen": screen, "severity": severity, "location": {"file": file, "line": line},
            "rule": rule, "issue": "Low contrast on the subtitle.", "fix": "Use onSurfaceVariant.",
            "source": "design:accessibility-review"}
    base.update(extra)
    return base


def record(findings, scope="all", **extra):
    base = {"version": 1, "mode": "audit", "scope": scope, "platforms": ["compose"], "findings": findings}
    base.update(extra)
    return base


class ViaRunTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = os.path.join(tmp.name, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        write(os.path.join(self.repo, "app", "Home.kt"), "fun Home() {}\n")
        self.git("add", "-A")
        self.git("-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-qm", "init")

    def git(self, *args):
        r = subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args, "--project", self.repo],
                              capture_output=True, text=True)

    def ok(self, *args):
        r = self.run_cli(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def new_run(self, rec):
        run_dir = self.ok("init")["run_dir"]
        write(os.path.join(run_dir, "findings.json"), json.dumps(rec))
        return run_dir

    def merge(self, rec):
        run_dir = self.new_run(rec)
        summary = self.ok("merge", run_dir)
        return run_dir, summary, json.loads(read(os.path.join(run_dir, "findings.json")))

    def statuses(self, merged):
        return {f["id"]: f["status"] for f in merged["findings"]}

    def worktree_tree(self):
        """An independent oracle for the tree `pass` records (test_pass_matches_the_review_gate checks
        it against the gate itself)."""
        index = os.path.join(self.repo, ".git", "index")
        with tempfile.TemporaryDirectory() as d:
            tmp = os.path.join(d, "index")
            with open(index, "rb") as src, open(tmp, "wb") as dst:
                dst.write(src.read())
            env = {**os.environ, "GIT_INDEX_FILE": tmp}
            subprocess.run(["git", "add", "-A"], cwd=self.repo, env=env, check=True, capture_output=True)
            return subprocess.run(["git", "write-tree"], cwd=self.repo, env=env, check=True,
                                  capture_output=True, text=True).stdout.strip()

    def pass_record(self):
        return json.loads(read(os.path.join(self.repo, ".git", "via-check-ok")))

    # --- stable IDs ---

    def test_same_finding_gets_the_same_id_across_runs_even_when_its_line_moves(self):
        _, _, first = self.merge(record([finding(line=10)]))
        _, _, second = self.merge(record([finding(line=42)]))
        self.assertRegex(first["findings"][0]["id"], ID_RE)
        self.assertEqual(first["findings"][0]["id"], second["findings"][0]["id"])

    def test_different_rule_or_file_gives_a_different_id(self):
        _, _, merged = self.merge(record([finding(), finding(rule="touch-target"),
                                          finding(file="app/Other.kt")]))
        ids = [f["id"] for f in merged["findings"]]
        self.assertEqual(len(set(ids)), 3)

    def test_two_findings_with_the_same_screen_rule_and_file_get_numbered_suffixes_in_line_order(self):
        _, _, first = self.merge(record([finding(line=30), finding(line=5)]))
        by_line = {f["location"]["line"]: f["id"] for f in first["findings"]}
        self.assertTrue(by_line[30].endswith("-2"))
        self.assertFalse(re.search(r"-\d$", by_line[5]))
        _, _, second = self.merge(record([finding(line=5), finding(line=31)]))
        again = {f["location"]["line"]: f["id"] for f in second["findings"]}
        self.assertEqual(again[5], by_line[5])
        self.assertEqual(again[31], by_line[30])

    # --- ledger merge ---

    def test_first_run_marks_findings_new_and_a_second_run_marks_them_open(self):
        _, summary, first = self.merge(record([finding()]))
        self.assertEqual(summary["new"], 1)
        self.assertEqual(set(self.statuses(first).values()), {"new"})
        _, summary, second = self.merge(record([finding()]))
        self.assertEqual((summary["new"], summary["open"]), (0, 1))
        self.assertEqual(set(self.statuses(second).values()), {"open"})

    def test_a_finding_missing_from_a_full_rerun_is_marked_fixed_and_a_return_is_open_again(self):
        _, _, first = self.merge(record([finding(), finding(rule="touch-target")]))
        gone = first["findings"][0]["id"]
        _, summary, _ = self.merge(record([finding(rule="touch-target")]))
        self.assertEqual(summary["fixed"], 1)
        ledger = read(os.path.join(self.repo, LEDGER))
        self.assertIn('"status": "fixed"', ledger)
        _, summary, back = self.merge(record([finding(), finding(rule="touch-target")]))
        self.assertEqual(self.statuses(back)[gone], "open")
        self.assertEqual(summary["new"], 0)

    def test_a_scoped_rerun_only_fixes_findings_on_screens_inside_its_scope(self):
        self.merge(record([finding(screen="home"), finding(screen="settings", file="app/Settings.kt")]))
        _, summary, _ = self.merge(record([], scope=["home"]))
        self.assertEqual(summary["fixed"], 1)
        _, summary, _ = self.merge(record([finding(screen="settings", file="app/Settings.kt")], scope=["settings"]))
        self.assertEqual((summary["new"], summary["open"]), (0, 1))

    def test_a_rejected_finding_never_comes_back_as_new(self):
        _, _, first = self.merge(record([finding()]))
        fid = first["findings"][0]["id"]
        out = self.ok("reject", fid, "--reason", "Brand color on purpose")
        self.assertEqual(out["status"], "rejected")
        _, summary, again = self.merge(record([finding(line=99)]))
        self.assertEqual(self.statuses(again)[fid], "rejected")
        self.assertEqual((summary["new"], summary["open"]), (0, 0))
        self.assertEqual(summary["rejected"], 1)
        _, summary, _ = self.merge(record([]))
        self.assertEqual(summary["fixed"], 0)
        self.assertIn("Brand color on purpose", read(os.path.join(self.repo, LEDGER)))

    def test_rejecting_an_unknown_id_fails_and_changes_nothing(self):
        r = self.run_cli("reject", "VIA-home-00000000", "--reason", "x")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, LEDGER)))

    def test_text_outside_the_ledger_markers_is_kept_byte_for_byte(self):
        before, after = "# Design\n\nTokens: primary #123456\n\n", "\n## Lessons\n- Keep cards flat\n  \n"
        write(os.path.join(self.repo, LEDGER),
              before + "<!-- via:ledger:start -->\n<!-- via:ledger:end -->\n" + after)
        self.merge(record([finding()]))
        text = read(os.path.join(self.repo, LEDGER))
        self.assertTrue(text.startswith(before + "<!-- via:ledger:start -->"))
        self.assertTrue(text.endswith("<!-- via:ledger:end -->\n" + after))

    def test_a_ledger_without_markers_gets_the_block_appended(self):
        write(os.path.join(self.repo, LEDGER), "# Design\n\nTokens only.\n")
        self.merge(record([finding()]))
        text = read(os.path.join(self.repo, LEDGER))
        self.assertTrue(text.startswith("# Design\n\nTokens only.\n"))
        self.assertEqual(text.count("<!-- via:ledger:start -->"), 1)

    def test_the_ledger_table_escapes_finding_text(self):
        evil = finding(issue="<script>alert(1)</script> | **bold**")
        self.merge(record([evil]))
        text = read(os.path.join(self.repo, LEDGER))
        table = text.split("<!-- via:ledger:end -->")[0].split("```\n")[-1]
        self.assertNotIn("<script>", table)
        self.assertIn("&lt;script&gt;", table)
        self.assertIn("\\|", table)

    # --- run folder and .gitignore ---

    def test_init_ignores_the_run_folder_before_creating_it(self):
        out = self.ok("init")
        self.assertTrue(os.path.isdir(out["run_dir"]))
        self.assertIn(".via/", read(os.path.join(self.repo, ".gitignore")).splitlines())
        ignored = subprocess.run(["git", "check-ignore", "-q", out["run_dir"]], cwd=self.repo)
        self.assertEqual(ignored.returncode, 0)

    def test_init_keeps_existing_gitignore_lines_and_never_duplicates_its_own(self):
        write(os.path.join(self.repo, ".gitignore"), "build/")  # no trailing newline
        self.ok("init")
        self.ok("init")
        lines = read(os.path.join(self.repo, ".gitignore")).splitlines()
        self.assertEqual(lines, ["build/", ".via/"])

    def test_shared_files_are_not_left_private(self):
        self.ok("init")
        self.merge(record([finding()]))
        for rel in (".gitignore", LEDGER):
            mode = os.stat(os.path.join(self.repo, rel)).st_mode & 0o777
            self.assertEqual(mode & 0o044, 0o044, "%s is %o" % (rel, mode))

    def test_init_stops_when_via_is_a_symlink_and_leaves_gitignore_alone(self):
        elsewhere = os.path.join(os.path.dirname(self.repo), "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.repo, ".via"))
        r = self.run_cli("init")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".gitignore")))
        self.assertEqual(os.listdir(elsewhere), [])

    def test_init_stops_when_gitignore_cannot_be_updated(self):
        target = os.path.join(self.repo, "real-ignore")
        write(target, "")
        os.symlink(target, os.path.join(self.repo, ".gitignore"))
        r = self.run_cli("init")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".via")))

    # --- pass record ---

    def test_pass_writes_a_record_for_the_current_code_state(self):
        run_dir, _, _ = self.merge(record([finding(severity="low")]))
        out = self.ok("pass", run_dir)
        rec = self.pass_record()
        self.assertEqual(rec["tree"], self.worktree_tree())
        self.assertEqual(rec["findings_open"], 0)
        self.assertEqual(os.path.abspath(rec["run"]), os.path.abspath(run_dir))
        self.assertEqual(out["tree"], rec["tree"])

    def test_pass_records_the_same_tree_the_review_gate_computes(self):
        gate = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "claude", "hooks",
                            "review-gate.py")
        if not os.path.exists(gate):
            self.skipTest("the review gate is not next to this plugin")
        write(os.path.join(self.repo, "app", "Home.kt"), "fun Home() { /* dirty */ }\n")
        write(os.path.join(self.repo, "app", "New.kt"), "fun New() {}\n")
        spec = importlib.util.spec_from_file_location("review_gate", gate)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        run_dir, _, _ = self.merge(record([]))
        self.ok("pass", run_dir)
        self.assertEqual(self.pass_record()["tree"], module.worktree_tree(self.repo))

    def test_pass_counts_open_high_and_med_findings_that_are_not_ticketed(self):
        run_dir, _, _ = self.merge(record([
            finding(severity="high"), finding(rule="a", severity="med", route="ticket"),
            finding(rule="b", severity="low"), finding(rule="c", severity="med", route="fix-now")]))
        self.ok("pass", run_dir)
        self.assertEqual(self.pass_record()["findings_open"], 2)

    def test_editing_code_after_a_pass_changes_the_tree_the_record_no_longer_matches(self):
        run_dir, _, _ = self.merge(record([]))
        self.ok("pass", run_dir)
        recorded = self.pass_record()["tree"]
        write(os.path.join(self.repo, "app", "Home.kt"), "fun Home() { /* edited */ }\n")
        self.assertNotEqual(recorded, self.worktree_tree())

    def test_run_folder_files_do_not_change_the_tree(self):
        run_dir, _, _ = self.merge(record([]))
        self.ok("pass", run_dir)
        before = self.pass_record()["tree"]
        write(os.path.join(run_dir, "shot.png"), "x")
        self.assertEqual(before, self.worktree_tree())

    def test_pass_refuses_a_run_that_was_never_merged(self):
        run_dir = self.new_run(record([]))
        r = self.run_cli("pass", run_dir)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "via-check-ok")))

    # --- second-opinion verdicts ---

    def test_a_second_opinion_finding_without_a_verdict_is_refused(self):
        for source in ("second-opinion:claude", "second-opinion:argon"):
            with self.subTest(source=source):
                self.assert_refused_without_writing(
                    json.dumps(record([finding(source=source)])), 1)

    def test_a_second_opinion_finding_with_a_null_verdict_is_refused(self):
        self.assert_refused_without_writing(
            json.dumps(record([finding(source="second-opinion:claude", verdict=None)])), 1)

    def test_real_and_unsure_second_opinion_findings_merge_like_any_other(self):
        _, summary, merged = self.merge(record([
            finding(source="second-opinion:claude", verdict="real"),
            finding(rule="touch-target", source="second-opinion:claude", verdict="unsure")]))
        self.assertEqual(summary["new"], 2)
        self.assertEqual(set(self.statuses(merged).values()), {"new"})

    def test_a_wrong_second_opinion_finding_is_dismissed_and_kept_out_of_the_ledger(self):
        _, summary, merged = self.merge(record([
            finding(),
            finding(rule="spacing", source="second-opinion:claude", verdict="wrong")]))
        self.assertEqual(summary["new"], 1)
        dismissed = [f for f in merged["findings"] if f["rule"] == "spacing"][0]
        self.assertEqual(dismissed["status"], "dismissed")
        self.assertNotIn("id", dismissed)
        ledger = read(os.path.join(self.repo, LEDGER))
        self.assertNotIn("spacing", ledger)
        self.assertEqual(len(set(re.findall(r"VIA-home-[0-9a-f]{8}", ledger))), 1)

    def test_a_dismissed_finding_does_not_count_in_pass(self):
        run_dir, _, _ = self.merge(record([
            finding(rule="spacing", severity="high", source="second-opinion:claude", verdict="wrong")]))
        self.ok("pass", run_dir)
        self.assertEqual(self.pass_record()["findings_open"], 0)

    def test_a_dismissed_finding_does_not_stop_an_old_one_from_being_marked_fixed(self):
        self.merge(record([finding()]))
        _, summary, _ = self.merge(record([
            finding(rule="spacing", source="second-opinion:claude", verdict="wrong")]))
        self.assertEqual(summary["fixed"], 1)

    def test_a_finding_already_in_the_ledger_that_is_now_judged_wrong_is_not_marked_fixed(self):
        _, _, first = self.merge(record([finding(source="second-opinion:claude", verdict="unsure")]))
        old_id = first["findings"][0]["id"]
        _, summary, second = self.merge(record([finding(source="second-opinion:claude", verdict="wrong")]))
        self.assertEqual((summary["fixed"], summary["dismissed"]), (0, 1))
        self.assertEqual(second["findings"][0]["status"], "dismissed")
        self.assertNotIn("id", second["findings"][0])
        ledger = read(os.path.join(self.repo, LEDGER))
        self.assertIn(old_id, ledger)
        self.assertNotIn("fixed", json.dumps(re.findall(r'"status": "[a-z]+"', ledger)))

    def test_a_dismissed_sibling_never_takes_the_ledger_id_a_live_finding_should_keep(self):
        _, _, first = self.merge(record([finding(line=10, issue="Low contrast on the subtitle.")]))
        old_id = first["findings"][0]["id"]
        _, summary, second = self.merge(record([
            finding(line=11, issue="Subtitle contrast is too low."),
            finding(line=10, source="second-opinion:claude", verdict="wrong")]))
        live = [f for f in second["findings"] if f["status"] != "dismissed"][0]
        self.assertEqual(live["id"], old_id)
        self.assertEqual((summary["new"], summary["open"], summary["dismissed"]), (0, 1, 1))
        self.assertEqual(len(set(re.findall(r"VIA-home-[0-9a-f]{8}(?:-\d+)?", read(os.path.join(self.repo, LEDGER))))), 1)

    def test_a_wrong_verdict_from_another_source_is_an_ordinary_finding(self):
        _, summary, merged = self.merge(record([finding(verdict="wrong")]))
        self.assertEqual(summary["new"], 1)
        self.assertEqual(merged["findings"][0]["status"], "new")

    # --- refusals: nothing is written ---

    def assert_refused_without_writing(self, rec_text, code):
        run_dir = self.ok("init")["run_dir"]
        path = os.path.join(run_dir, "findings.json")
        write(path, rec_text)
        r = self.run_cli("merge", run_dir)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
        self.assertEqual(read(path), rec_text)
        self.assertFalse(os.path.exists(os.path.join(self.repo, LEDGER)))

    def test_invalid_records_are_refused(self):
        bad_finding = finding()
        del bad_finding["rule"]
        bad_severity = finding(severity="urgent")
        for rec in ("not json", json.dumps([]), json.dumps(record([bad_finding])),
                    json.dumps(record([bad_severity])), json.dumps({**record([]), "mode": "nope"}),
                    json.dumps({**record([]), "version": 2})):
            with self.subTest(rec=rec[:40]):
                self.assert_refused_without_writing(rec, 1)

    def test_a_secret_shaped_value_in_the_record_exits_2(self):
        self.assert_refused_without_writing(json.dumps(record([finding(issue="key " + FAKE_KEY)])), 2)

    def test_a_symlinked_ledger_is_refused(self):
        target = os.path.join(self.repo, "elsewhere.md")
        write(target, "keep\n")
        os.makedirs(os.path.join(self.repo, "docs", "design"))
        os.symlink(target, os.path.join(self.repo, LEDGER))
        run_dir = self.new_run(record([finding()]))
        r = self.run_cli("merge", run_dir)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(read(target), "keep\n")

    def test_a_run_folder_outside_the_project_run_area_is_refused(self):
        outside = os.path.join(os.path.dirname(self.repo), "stray")
        write(os.path.join(outside, "findings.json"), json.dumps(record([])))
        self.assertEqual(self.run_cli("merge", outside).returncode, 1)

    def test_outputs_are_json_on_stdout_and_stderr_names_the_problem(self):
        r = self.run_cli("merge", os.path.join(self.repo, ".via", "runs", "missing"))
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stderr.startswith("Stopped:"))

    # --- fixes from review ---

    def test_a_ledger_block_with_content_but_no_json_fence_is_refused_not_wiped(self):
        text = "# D\n<!-- via:ledger:start -->\nhand edited, no fence\n<!-- via:ledger:end -->\n"
        write(os.path.join(self.repo, LEDGER), text)
        run_dir = self.new_run(record([finding()]))
        self.assertEqual(self.run_cli("merge", run_dir).returncode, 1)
        self.assertEqual(read(os.path.join(self.repo, LEDGER)), text)

    def test_broken_or_repeated_markers_and_bad_entries_are_refused(self):
        start, end = "<!-- via:ledger:start -->", "<!-- via:ledger:end -->"
        bad_entry = '```json\n{"findings": [{"id": "x"}]}\n```'
        for text in (start + "\n" + end + "\n" + start + "\n" + end, end + "\n" + start, start + "\nno end",
                     start + "\n" + bad_entry + "\n" + end):
            with self.subTest(text=text[:30]):
                write(os.path.join(self.repo, LEDGER), text)
                run_dir = self.new_run(record([finding()]))
                r = self.run_cli("merge", run_dir)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(read(os.path.join(self.repo, LEDGER)), text)

    def test_crlf_line_endings_are_kept_outside_the_ledger_block_and_in_gitignore(self):
        write(os.path.join(self.repo, ".gitignore"), "build/\r\n")
        before = "# D\r\n\r\n"
        write(os.path.join(self.repo, LEDGER), before)
        with open(os.path.join(self.repo, ".gitignore"), "wb") as f:
            f.write(b"build/\r\n")
        with open(os.path.join(self.repo, LEDGER), "wb") as f:
            f.write(before.encode())
        self.merge(record([finding()]))
        with open(os.path.join(self.repo, ".gitignore"), "rb") as f:
            self.assertEqual(f.read(), b"build/\r\n.via/\r\n")
        with open(os.path.join(self.repo, LEDGER), "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(before.encode()))
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"), "the ledger block has bare LF lines")

    def test_a_finding_rejected_after_the_merge_does_not_count_as_open_in_the_pass(self):
        run_dir, _, merged = self.merge(record([finding(severity="high")]))
        self.ok("reject", merged["findings"][0]["id"], "--reason", "intended")
        self.ok("pass", run_dir)
        self.assertEqual(self.pass_record()["findings_open"], 0)

    def test_a_rejected_sibling_keeps_its_id_when_the_other_one_is_fixed_or_a_new_one_appears_above(self):
        _, _, first = self.merge(record([finding(line=10), finding(line=50)]))
        second_id = {f["location"]["line"]: f["id"] for f in first["findings"]}[50]
        self.ok("reject", second_id, "--reason", "intended")
        _, _, only_b = self.merge(record([finding(line=50)]))  # the first one was fixed
        self.assertEqual(only_b["findings"][0]["id"], second_id)
        self.assertEqual(only_b["findings"][0]["status"], "rejected")
        _, _, shifted = self.merge(record([finding(line=5), finding(line=52)]))  # a sibling appeared above
        ids = {f["location"]["line"]: f["id"] for f in shifted["findings"]}
        self.assertEqual(ids[52], second_id)
        self.assertNotEqual(ids[5], second_id)

    def test_an_unrelated_finding_far_from_a_rejected_sibling_does_not_take_its_id(self):
        _, _, first = self.merge(record([finding(line=10), finding(line=50)]))
        rejected = {f["location"]["line"]: f["id"] for f in first["findings"]}[50]
        self.ok("reject", rejected, "--reason", "intended")
        _, summary, merged = self.merge(record([finding(line=10), finding(line=400)]))
        far = [f for f in merged["findings"] if f["location"]["line"] == 400][0]
        self.assertNotEqual(far["id"], rejected)
        self.assertEqual(far["status"], "new")

    def test_the_file_path_is_normalized_before_it_goes_into_the_id(self):
        _, _, merged = self.merge(record([finding(file="./app/Home.kt"), finding(file="app\\Home.kt", rule="x")]))
        _, _, again = self.merge(record([finding(file="app/Home.kt"), finding(file="app/Home.kt", rule="x")]))
        self.assertEqual([f["id"] for f in merged["findings"]], [f["id"] for f in again["findings"]])

    def test_scope_matches_screen_names_the_way_ids_do(self):
        self.merge(record([finding(screen="Home Screen")]))
        _, summary, _ = self.merge(record([], scope=["home screen"]))
        self.assertEqual(summary["fixed"], 1)

    def test_a_secret_hidden_by_json_escapes_is_still_caught(self):
        raw = json.dumps(record([finding()])).replace('"Low contrast', '"\\u0041KIAIOSFODNN7EXAMPLE Low contrast')
        self.assertIn("\\u0041KIA", raw)
        self.assert_refused_without_writing(raw, 2)

    def test_merge_refuses_a_symlinked_dot_via(self):
        run_dir = self.new_run(record([finding()]))
        elsewhere = os.path.join(os.path.dirname(self.repo), "elsewhere")
        os.makedirs(elsewhere)
        os.rename(os.path.join(self.repo, ".via"), os.path.join(elsewhere, ".via"))
        os.symlink(os.path.join(elsewhere, ".via"), os.path.join(self.repo, ".via"))
        self.assertEqual(self.run_cli("merge", run_dir).returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, LEDGER)))

    def test_a_relative_run_folder_is_resolved_against_the_project(self):
        run_dir = self.new_run(record([]))
        self.ok("merge", os.path.relpath(run_dir, self.repo))

    def test_backslashes_and_carriage_returns_cannot_break_a_table_row(self):
        self.merge(record([finding(issue="ends with a slash \\\r\nnext | col")]))
        table = read(os.path.join(self.repo, LEDGER)).split("<!-- via:ledger:end -->")[0].split("```\n")[-1]
        rows = [r for r in table.splitlines() if r.startswith("| VIA-")]
        self.assertEqual(len(rows), 1)
        self.assertIn("slash \\\\ next \\| col", rows[0])

    def test_init_stops_when_a_later_gitignore_rule_re_includes_the_run_folder(self):
        write(os.path.join(self.repo, ".gitignore"), ".via/\n!.via/\n")
        r = self.run_cli("init")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".via")))

    def test_screen_names_without_ascii_letters_stay_apart(self):
        _, _, merged = self.merge(record([finding(screen="ホーム"), finding(screen="設定")]))
        self.assertEqual(len({f["id"] for f in merged["findings"]}), 2)
        _, summary, _ = self.merge(record([], scope=["ホーム"]))
        self.assertEqual(summary["fixed"], 1)  # only the scoped screen

    def test_siblings_that_all_move_far_keep_their_ids_and_a_rejection(self):
        _, _, first = self.merge(record([finding(line=10), finding(line=200)]))
        ids = {f["location"]["line"]: f["id"] for f in first["findings"]}
        self.ok("reject", ids[200], "--reason", "intended")
        _, summary, moved = self.merge(record([finding(line=60), finding(line=250)]))
        got = {f["location"]["line"]: (f["id"], f["status"]) for f in moved["findings"]}
        self.assertEqual(got[60], (ids[10], "open"))
        self.assertEqual(got[250], (ids[200], "rejected"))
        self.assertEqual((summary["new"], summary["fixed"]), (0, 0))

    def test_hand_edited_ledger_entries_with_wrong_types_or_repeated_ids_are_refused(self):
        start, end = "<!-- via:ledger:start -->", "<!-- via:ledger:end -->"
        entry = {"id": "VIA-a-1", "status": "open"}
        for bad in ([{**entry, "screen": None}], [{**entry, "line": "x"}], [{**entry, "file": 5}], [entry, entry]):
            with self.subTest(bad=str(bad)[:40]):
                text = start + "\n```json\n" + json.dumps({"findings": bad}) + "\n```\n" + end + "\n"
                write(os.path.join(self.repo, LEDGER), text)
                run_dir = self.new_run(record([finding()]))
                self.assertEqual(self.run_cli("merge", run_dir).returncode, 1)
                self.assertEqual(read(os.path.join(self.repo, LEDGER)), text)

    def test_eleven_same_line_siblings_keep_their_ids_on_a_rerun(self):
        batch = [finding(line=7, issue="Problem number %d" % n) for n in range(11)]
        _, _, first = self.merge(record(batch))
        _, summary, again = self.merge(record(batch))
        self.assertEqual([f["id"] for f in first["findings"]], [f["id"] for f in again["findings"]])
        self.assertEqual(summary["new"], 0)

    def test_same_line_siblings_with_identical_text_keep_their_ids_in_numeric_order(self):
        batch = [finding(line=7) for _ in range(11)]
        _, _, first = self.merge(record(batch))
        _, _, again = self.merge(record(batch))
        self.assertEqual([f["id"] for f in first["findings"]], [f["id"] for f in again["findings"]])

    def test_swapping_the_order_of_same_line_findings_does_not_swap_their_ids(self):
        a, b = finding(line=7, issue="Alpha problem"), finding(line=7, issue="Beta problem")
        _, _, first = self.merge(record([a, b]))
        ids = {f["issue"]: f["id"] for f in first["findings"]}
        self.ok("reject", ids["Alpha problem"], "--reason", "intended")
        _, _, swapped = self.merge(record([b, a]))
        got = {f["issue"]: (f["id"], f["status"]) for f in swapped["findings"]}
        self.assertEqual(got["Alpha problem"], (ids["Alpha problem"], "rejected"))
        self.assertEqual(got["Beta problem"][0], ids["Beta problem"])
        self.assertEqual(got["Beta problem"][1], "open")

    def test_screen_names_with_combining_marks_stay_apart_and_unicode_forms_match(self):
        _, _, merged = self.merge(record([finding(screen="हिंदी"), finding(screen="हुंदा")]))
        self.assertEqual(len({f["id"] for f in merged["findings"]}), 2)
        _, _, nfc = self.merge(record([finding(screen="caf\u00e9")]))
        _, summary, nfd = self.merge(record([finding(screen="cafe\u0301")]))
        self.assertEqual(nfc["findings"][0]["id"], nfd["findings"][0]["id"])

    def test_a_single_crlf_in_an_lf_file_does_not_turn_the_ledger_block_into_crlf(self):
        write(os.path.join(self.repo, LEDGER), "")
        with open(os.path.join(self.repo, LEDGER), "wb") as f:
            f.write(b"# D\nline\r\nmore\nand more\n")
        self.merge(record([finding()]))
        with open(os.path.join(self.repo, LEDGER), "rb") as f:
            self.assertEqual(f.read().count(b"\r\n"), 1)

    def test_a_rejected_finding_that_is_reworded_and_moves_in_steps_stays_rejected(self):
        same = "Low contrast on the subtitle."
        _, _, first = self.merge(record([finding(line=10, issue="Original wording"),
                                         finding(line=100, issue=same), finding(line=200, issue=same)]))
        rid = [f["id"] for f in first["findings"] if f["location"]["line"] == 10][0]
        self.ok("reject", rid, "--reason", "intended")
        for line, wording in ((30, "Second wording"), (50, "Third wording")):
            _, summary, merged = self.merge(record([finding(line=line, issue=wording),
                                                    finding(line=100, issue=same), finding(line=200, issue=same)]))
            got = {f["location"]["line"]: f for f in merged["findings"]}
            self.assertEqual((got[line]["id"], got[line]["status"]), (rid, "rejected"))
            self.assertEqual(summary["new"], 0)

    def test_the_lone_leftover_pair_after_text_matching_follows_its_entry_anywhere(self):
        _, _, first = self.merge(record([finding(line=10, issue="Reject me"), finding(line=12, issue="Keep me")]))
        rid = [f["id"] for f in first["findings"] if f["issue"] == "Reject me"][0]
        self.ok("reject", rid, "--reason", "intended")
        _, summary, merged = self.merge(record([finding(line=12, issue="Keep me"),
                                                finding(line=40, issue="Reject me, reworded")]))
        got = {f["issue"]: f for f in merged["findings"]}
        self.assertEqual((got["Reject me, reworded"]["id"], got["Reject me, reworded"]["status"]), (rid, "rejected"))
        self.assertEqual(summary["new"], 0)


if __name__ == "__main__":
    unittest.main()
