#!/usr/bin/env python3
"""NFX-191: tests for session_report.py (decision log and report). No network, no real ~/Releases.

Run: python3 -B scripts/test_session_report.py
"""
import datetime
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "claude", "skills", "review-loop", "scripts", "session_report.py")
VIA_RUN = os.path.join(ROOT, "plugins", "via", "scripts", "via_run.py")
GH_SECRET = "ghp_" + "a1B2c3D4e5" * 3 + "a1B2c3"  # 36 chars after the prefix


def checkout_project():
    """This checkout's project name as session_report.py derives it: the folder holding the common git dir."""
    common = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    return os.path.basename(os.path.dirname(common.rstrip("/")))


def git_status():
    return subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout


class SessionReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "releases")
        self.env = dict(os.environ, NOUSFX_REPORT_ROOT=self.root)

    def run_cli(self, *args, env=None):
        return subprocess.run([sys.executable, "-B", SCRIPT, *args], cwd=ROOT, env=env or self.env,
                              capture_output=True, text=True)

    def decide(self, *extra, key="NFX-191", decision="Use JSONL", why="Appending is crash safe",
               rejected="One JSON file", evidence="scripts/test_session_report.py:1", result="tests green"):
        return self.run_cli("decide", key, "--decision", decision, "--why", why, "--rejected", rejected,
                            "--evidence", evidence, "--result", result, "--date", "2026-11-19", "--name", "t", "--new", *extra)

    def render(self, *extra):
        return self.run_cli("render", "--date", "2026-11-19", "--name", "t", *extra)

    def report_path(self):
        for base, _dirs, files in os.walk(self.root):
            if "report.md" in files:
                return os.path.join(base, "report.md")
        return None

    def read_report(self):
        with open(self.report_path(), encoding="utf-8") as f:
            return f.read()

    def prs_file(self):
        path = os.path.join(self.tmp.name, "prs.json")
        with open(path, "w") as f:
            json.dump([{"number": 7, "url": "https://example.test/pull/7", "title": "NFX-191: report", "state": "OPEN"}], f)
        return path

    def test_decide_then_render_lists_each_decision_with_why_and_rejected(self):
        self.assertEqual(self.decide().returncode, 0)
        self.assertEqual(self.decide(key="NFX-193", decision="Keep it small", why="Scope is fixed", rejected="Wave collection").returncode, 0)
        r = self.render("--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.read_report()
        for heading in ("## What shipped", "## Decisions", "## Held for Cesar", "## Not verified", "## What to learn"):
            self.assertIn(heading, text)
        self.assertIn("| 1 | NFX-191 | Use JSONL | Appending is crash safe | One JSON file | `scripts/test_session_report.py:1` | tests green |", text)
        self.assertIn("| 2 | NFX-193 | Keep it small | Scope is fixed | Wave collection | `scripts/test_session_report.py:1` | tests green |", text)
        self.assertIn("https://example.test/pull/7", text)

    def test_empty_why_is_refused_with_exit_2(self):
        self.refused(self.decide(why="   "))
        self.assertFalse(os.path.exists(self.root))

    def refused(self, r, msg=None):
        """Exit 2 from the script's own refusal, not from argparse or a missing script."""
        self.assertEqual(r.returncode, 2, msg or r.stderr)
        self.assertIn("Stopped:", r.stderr, msg)

    def test_bad_ticket_key_is_refused(self):
        self.refused(self.decide(key="nfx-191"))

    def test_files_land_outside_the_repo_and_the_tree_is_unchanged(self):
        before = git_status()
        self.decide()
        self.render("--prs", self.prs_file())
        self.assertEqual(git_status(), before)
        self.assertTrue(self.report_path().startswith(self.root))
        self.assertFalse(os.path.realpath(self.root).startswith(os.path.realpath(ROOT) + os.sep))

    def test_a_root_inside_the_repo_is_refused(self):
        env = dict(self.env, NOUSFX_REPORT_ROOT=os.path.join(ROOT, "scratch-reports"))
        r = self.run_cli("decide", "NFX-191", "--decision", "d", "--why", "w", "--rejected", "r",
                         "--evidence", "abc1234", "--result", "open", env=env)
        self.refused(r)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "scratch-reports")))

    def test_a_root_that_holds_the_checkout_is_refused(self):
        # The project folder under such a root would be the main checkout itself.
        main = os.path.dirname(os.path.realpath(subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT,
            capture_output=True, text=True).stdout.strip()))
        env = dict(self.env, NOUSFX_REPORT_ROOT=os.path.dirname(main))
        r = self.run_cli("decide", "NFX-191", "--decision", "d", "--why", "w", "--rejected", "r",
                         "--evidence", "abc1234", "--result", "open", env=env)
        self.refused(r)
        self.assertFalse(os.path.exists(os.path.join(main, "reports")))

    def test_held_one_way_door_has_its_own_section_with_the_reason(self):
        self.decide("--held", "Publishing cannot be undone", decision="Publish the report",
                    why="Cesar wants it shared", rejected="Keep private")
        self.render("--prs", self.prs_file())
        text = self.read_report()
        section = text.split("## Held for Cesar", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Publish the report", section)
        self.assertIn("Publishing cannot be undone", section)

    def test_secret_shaped_text_is_refused_in_any_field(self):
        for flag in ("--decision", "--why", "--rejected", "--lesson", "--held", "--unverified", "--term", "--result"):
            args = {"--decision": "d", "--why": "w", "--rejected": "r", "--evidence": "abc1234", "--result": "open"}
            args[flag] = "token " + GH_SECRET
            argv = ["decide", "NFX-191"] + [x for k, v in args.items() for x in (k, v)]
            self.refused(self.run_cli(*argv), flag)
        self.assertFalse(os.path.exists(self.root))

    def test_secret_pattern_matches_via_run(self):
        sys.path.insert(0, os.path.dirname(VIA_RUN))
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("via_run_copy", VIA_RUN)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            spec2 = importlib.util.spec_from_file_location("session_report_copy", SCRIPT)
            mine = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(mine)
        finally:
            sys.path.pop(0)
        self.assertEqual(mine.SECRET_RE.pattern, mod.SECRET_RE.pattern)

    def test_render_with_no_decisions_says_so(self):
        r = self.render("--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.read_report()
        self.assertIn("No decisions logged.", text)
        self.assertNotIn("NFX-", text.split("## Decisions", 1)[1].split("\n## ", 1)[0])
        self.assertIn("no decisions", r.stderr.lower())

    def test_not_verified_terms_and_lessons_are_rendered(self):
        self.decide("--lesson", "Log as you go", "--unverified", "Windows path", "--term", "JSONL: one JSON per line")
        self.render("--prs", self.prs_file())
        text = self.read_report()
        self.assertIn("Windows path", text.split("## Not verified", 1)[1].split("\n## ", 1)[0])
        learn = text.split("## What to learn", 1)[1]
        self.assertIn("Log as you go", learn)
        self.assertIn("JSONL: one JSON per line", learn)

    def test_proofs_file_is_included_verbatim(self):
        self.decide()
        proofs = os.path.join(self.tmp.name, "proofs.md")
        with open(proofs, "w") as f:
            f.write("| criterion | result |\n| a | pass |\n")
        self.render("--prs", self.prs_file(), "--proofs", proofs)
        self.assertIn("| a | pass |", self.read_report())

    # --- pstack ideas (show-me-your-work), NFX-191 ---

    def test_evidence_and_result_are_required_and_evidence_is_a_pointer_not_prose(self):
        for flag in ("--evidence", "--result"):
            argv = ["decide", "NFX-191", "--decision", "d", "--why", "w", "--rejected", "r",
                    "--evidence", "abc1234", "--result", "open"]
            i = argv.index(flag)
            self.assertEqual(self.run_cli(*argv[:i], flag, "  ", *argv[i + 2:]).returncode, 2, flag)
            self.assertEqual(self.run_cli(*argv[:i], *argv[i + 2:]).returncode, 2, flag + " missing")
        self.assertEqual(self.decide(evidence="the tests all passed").returncode, 2)
        for ok in ("abc1234", "#12", "scripts/x.py:40", "https://github.com/cursor/plugins"):
            self.assertEqual(self.decide(evidence=ok).returncode, 0, ok)

    # --- one refusal names every bad field, NFX-219 ---

    def test_decide_names_every_missing_field(self):
        r = self.run_cli("decide", "NFX-219", "--decision", "d", "--why", "w", "--date", "2026-11-19", "--name", "t")
        self.refused(r)
        self.assertEqual(r.stderr.count("Stopped:"), 1, r.stderr)
        for flag in ("--rejected", "--evidence", "--result"):
            self.assertIn(flag + " is empty", r.stderr)
        for flag in ("--decision", "--why"):
            self.assertNotIn(flag + " is empty", r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_decide_names_every_bad_field(self):
        r = self.run_cli("decide", "nfx-219", "--decision", "d", "--why", "w", "--rejected", "r",
                         "--evidence", "the tests all passed", "--date", "2026-11-19", "--name", "t")
        self.refused(r)
        self.assertEqual(r.stderr.count("Stopped:"), 1, r.stderr)
        self.assertIn("--result is empty", r.stderr)
        self.assertIn("--evidence is a pointer, not prose", r.stderr)
        self.assertIn("'nfx-219' is not a ticket key", r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_each_required_field_alone_is_still_refused(self):
        full = {"--decision": "d", "--why": "w", "--rejected": "r", "--evidence": "abc1234", "--result": "open"}
        for flag in full:
            for value in (None, "   "):
                argv = ["decide", "NFX-219", "--date", "2026-11-19", "--name", "t"]
                for f, v in full.items():
                    if f != flag:
                        argv += [f, v]
                    elif value is not None:
                        argv += [f, value]
                r = self.run_cli(*argv)
                self.refused(r, "%s %r" % (flag, value))
                self.assertIn(flag + " is empty", r.stderr)
                self.assertEqual(r.stderr.count(" is empty"), 1, r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_a_refusal_never_echoes_a_secret_value(self):
        r = self.decide(evidence="prose and " + GH_SECRET, result="")
        self.refused(r)
        self.assertNotIn(GH_SECRET, r.stderr)
        self.assertIn("--result is empty", r.stderr)
        self.assertIn("a value looks like a secret", r.stderr)
        r = self.decide(key=GH_SECRET)
        self.refused(r)
        self.assertNotIn(GH_SECRET, r.stderr)
        self.assertIn("not a ticket key", r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_the_refusal_example_is_a_valid_decide_call(self):
        r = self.run_cli("decide", "NFX-219", "--date", "2026-11-19", "--name", "t")
        self.refused(r)
        examples = [line for line in r.stderr.splitlines() if line.startswith("Example: ")]
        self.assertEqual(len(examples), 1, r.stderr)
        argv = shlex.split(examples[0][len("Example: "):])
        self.assertEqual(argv[:2], ["session_report.py", "decide"])
        ok = self.run_cli(*argv[1:], "--date", "2026-11-19", "--name", "t", "--new")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        h = self.run_cli("decide", "--help")
        self.assertIn(examples[0][len("Example: "):], " ".join(h.stdout.split()))

    FULL = ("--decision", "d", "--why", "w", "--rejected", "r", "--evidence", "#1", "--result", "ok")

    def folders(self):
        return sorted(d for _b, ds, _f in os.walk(self.root) for d in ds)

    def test_decide_refuses_an_unknown_name_and_lists_the_key_reports(self):
        self.assertEqual(self.run_cli("decide", "NFX-214", *self.FULL, "--date", "2026-11-20").returncode, 0)
        before = self.folders()
        r = self.run_cli("decide", "NFX-214", *self.FULL, "--name", "checkout-rule")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("2026-11-20-NFX-214", r.stderr)
        self.assertIn("--new", r.stderr)
        self.assertEqual(self.folders(), before)
        self.assertFalse([d for d in self.folders() if d.endswith("-checkout-rule")])

    def test_decide_refuses_an_unknown_name_when_no_report_exists_for_the_key(self):
        r = self.run_cli("decide", "NFX-214", *self.FULL, "--name", "checkout-rule")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--new", r.stderr)
        self.assertIn("No report folders exist for NFX-214", r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_new_starts_a_named_report_and_later_calls_reuse_it(self):
        days = {datetime.date.today().isoformat()}
        first = self.run_cli("decide", "NFX-214", *self.FULL, "--name", "wave-1", "--new")
        days.add(datetime.date.today().isoformat())  # a run that crosses midnight may use either day
        self.assertEqual(first.returncode, 0, first.stderr)
        made = os.path.basename(os.path.dirname(first.stdout.strip()))
        self.assertIn(made, {d + "-wave-1" for d in days})
        r = self.run_cli("decide", "NFX-214", *self.FULL, "--name", "wave-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), first.stdout.strip())
        with open(r.stdout.strip(), encoding="utf-8") as f:
            self.assertEqual(len(f.read().splitlines()), 2)

    def test_a_name_found_on_another_date_needs_no_new(self):
        self.assertEqual(self.run_cli("decide", "NFX-214", *self.FULL, "--name", "wave-2", "--new",
                                      "--date", "2026-11-18").returncode, 0)
        r = self.run_cli("decide", "NFX-214", *self.FULL, "--name", "wave-2", "--date", "2026-11-19")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_cli("decide", "NFX-214", *self.FULL, "--name", "wave-2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.path.basename(os.path.dirname(r.stdout.strip())), "2026-11-19-wave-2")

    def test_a_name_on_an_older_date_is_reused_without_new(self):
        self.assertEqual(self.run_cli("decide", "NFX-214", *self.FULL, "--name", "wave-3", "--new",
                                      "--date", "2026-11-18").returncode, 0)
        r = self.run_cli("decide", "NFX-214", *self.FULL, "--name", "wave-3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.path.basename(os.path.dirname(r.stdout.strip())), "2026-11-18-wave-3")

    def test_a_secret_shaped_name_is_refused_and_never_echoed(self):
        r = self.run_cli("decide", "NFX-214", *self.FULL, "--name", GH_SECRET)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn(GH_SECRET, r.stderr + r.stdout)
        self.assertFalse(os.path.exists(self.root))

    def test_a_name_equal_to_the_key_or_no_name_needs_no_new(self):
        self.assertEqual(self.run_cli("decide", "NFX-214", *self.FULL).returncode, 0)
        self.assertEqual(self.run_cli("decide", "NFX-215", *self.FULL, "--name", "NFX-215").returncode, 0)

    def test_decide_help_names_the_required_fields_and_evidence_forms(self):
        h = self.run_cli("decide", "--help")
        self.assertEqual(h.returncode, 0, h.stderr)
        text = " ".join(h.stdout.split())
        self.assertIn("Required: --decision, --why, --rejected, --evidence, --result", text)
        for form in ("commit SHA", "#PR", "file:line", "URL"):
            self.assertIn(form, text)

    def test_render_shows_the_evidence_and_result_columns(self):
        self.decide(evidence="#12", result="INCONCLUSIVE")
        self.render("--prs", self.prs_file())
        text = self.read_report()
        self.assertIn("| evidence | result |", text)
        self.assertIn("| `#12` | INCONCLUSIVE |", text)

    def test_the_log_is_append_only_and_a_correction_names_the_entry_it_replaces(self):
        self.decide()
        log = os.path.join(self.root, os.listdir(self.root)[0], "reports", "2026-11-19-t", "decisions.jsonl")
        with open(log, "rb") as f:
            first = f.read()
        self.assertEqual(self.decide("--supersedes", "1", decision="Use JSONL, one file per day", result="open").returncode, 0)
        with open(log, "rb") as f:
            after = f.read()
        self.assertTrue(after.startswith(first), "an earlier line changed")
        self.assertEqual(len(after.splitlines()), 2)
        self.assertEqual(self.decide("--supersedes", "9").returncode, 2)  # no such entry
        self.assertNotEqual(self.run_cli("edit", "1").returncode, 0)  # no edit or delete command
        self.assertNotEqual(self.run_cli("delete", "1").returncode, 0)
        self.render("--prs", self.prs_file())
        text = self.read_report()
        self.assertIn("superseded by 2", text)
        self.assertIn("replaces 1", text)

    def test_render_without_a_name_uses_the_one_report_of_that_date(self):
        def add(key):
            return self.run_cli("decide", key, "--decision", "d", "--why", "w", "--rejected", "r",
                                "--evidence", "#1", "--result", "open", "--date", "2026-11-19")
        add("NFX-191")
        r = self.run_cli("render", "--date", "2026-11-19", "--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2026-11-19-NFX-191", r.stdout)
        add("NFX-193")
        self.assertEqual(self.run_cli("render", "--date", "2026-11-19", "--prs", self.prs_file()).returncode, 2)

    def test_a_ticket_keeps_one_log_across_days_and_render_finds_it(self):
        def add(*extra):
            return self.run_cli("decide", "NFX-191", "--decision", "d", "--why", "w", "--rejected", "r",
                                "--evidence", "#1", "--result", "open", *extra)
        self.assertEqual(add("--date", "2026-11-18").returncode, 0)
        self.assertEqual(add().returncode, 0)  # a later day, no --date: same folder
        r = self.run_cli("render", "--name", "NFX-191", "--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2026-11-18-NFX-191", r.stdout)
        self.assertIn("| 2 | NFX-191 |", self.read_report())

    def test_a_superseded_entry_leaves_the_held_unverified_and_learn_sections(self):
        self.decide("--held", "Cannot be undone", "--unverified", "old gap", "--lesson", "old lesson")
        self.decide("--supersedes", "1", "--lesson", "new lesson")
        self.render("--prs", self.prs_file())
        text = self.read_report()
        tail = text.split("## Held for Cesar", 1)[1]
        for stale in ("old gap", "old lesson"):  # a held door is carried forward instead (next test)
            self.assertNotIn(stale, tail)
        self.assertEqual(tail.count("Cannot be undone"), 1)
        self.assertIn("new lesson", tail)

    def test_a_correction_carries_a_held_door_forward(self):
        self.decide("--held", "Cannot be undone", decision="Publish")
        self.assertEqual(self.decide("--supersedes", "1", decision="Publish, typo fixed").returncode, 0)
        self.render("--prs", self.prs_file())
        held = self.read_report().split("## Held for Cesar", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Publish, typo fixed", held)
        self.assertIn("Cannot be undone", held)
        self.assertNotIn("None.", held)

    def test_corrections_stay_one_chain(self):
        self.decide("--held", "Cannot be undone", decision="Publish")
        self.decide("--supersedes", "1", decision="fix A")
        r = self.decide("--supersedes", "1", decision="fix B")  # 1 is already corrected by 2
        self.refused(r)
        self.assertIn("entry 2", r.stderr)
        self.assertEqual(self.decide("--supersedes", "2", decision="fix B").returncode, 0)
        self.render("--prs", self.prs_file())
        held = self.read_report().split("## Held for Cesar", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(held.count("Cannot be undone"), 1)

    def test_a_carried_held_value_is_secret_checked(self):
        self.decide()
        log = os.path.join(self.root, os.listdir(self.root)[0], "reports", "2026-11-19-t", "decisions.jsonl")
        bad = {"key": "NFX-191", "id": 2, "decision": "d", "why": "w", "rejected": "r", "evidence": "#1",
               "result": "open", "held": "token " + GH_SECRET}  # a hand-edited line
        with open(log, "a") as f:
            f.write(json.dumps(bad) + "\n")
        with open(log, "rb") as f:
            before = f.read()
        self.refused(self.decide("--supersedes", "2"))
        with open(log, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_render_without_gh_still_writes_the_report(self):
        self.decide()
        bin_dir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bin_dir)
        os.symlink(shutil.which("git"), os.path.join(bin_dir, "git"))
        env = dict(self.env, PATH=bin_dir)
        r = self.run_cli("render", "--date", "2026-11-19", "--name", "t", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PR list unavailable", self.read_report())

    def test_render_without_a_name_uses_the_branch_key_across_days(self):
        repo = os.path.join(self.tmp.name, "proj")
        subprocess.run(["git", "init", "-q", "-b", "cesar/NFX-7", repo], check=True)

        def cli(*args):
            return subprocess.run([sys.executable, "-B", SCRIPT, *args], cwd=repo, env=self.env,
                                  capture_output=True, text=True)
        cli("decide", "NFX-7", "--decision", "d", "--why", "w", "--rejected", "r", "--evidence", "#1",
            "--result", "open", "--date", "2026-11-18")
        cli("decide", "NFX-8", "--decision", "d", "--why", "w", "--rejected", "r", "--evidence", "#1",
            "--result", "open")  # another ticket today
        r = cli("render", "--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2026-11-18-NFX-7", r.stdout)

    def test_bad_optional_fields_and_backtick_evidence(self):
        self.refused(self.decide(evidence="a`b"))
        self.decide()
        log = os.path.join(self.root, os.listdir(self.root)[0], "reports", "2026-11-19-t", "decisions.jsonl")
        bad = {"key": "NFX-191", "id": 5, "decision": "d", "why": "w", "rejected": "r", "evidence": "#1",
               "result": "open", "lesson": ["x"], "supersedes": [1]}
        with open(log, "a") as f:
            f.write(json.dumps(bad) + "\n")
        r = self.render("--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 unreadable log lines", self.read_report())

    def test_a_bad_log_line_is_skipped_and_reported_not_fatal(self):
        self.decide()
        log = os.path.join(self.root, os.listdir(self.root)[0], "reports", "2026-11-19-t", "decisions.jsonl")
        with open(log, "a") as f:
            f.write("{}\n{\"torn")  # a bad entry, then a torn last line with no newline
        r = self.decide(decision="After the tear")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.render("--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("unreadable", r.stderr)
        text = self.read_report()
        self.assertIn("2 unreadable log lines", text)
        self.assertIn("| 4 | NFX-191 | After the tear |", text)  # ids never repeat a bad line's place

    def test_gh_pr_match_respects_the_key_boundary(self):
        bin_dir = os.path.join(self.tmp.name, "bin")
        os.makedirs(bin_dir)
        os.symlink(shutil.which("git"), os.path.join(bin_dir, "git"))
        with open(os.path.join(bin_dir, "gh"), "w") as f:
            f.write("#!/bin/sh\necho '%s'\n" % json.dumps([
                {"number": 1, "url": "u1", "title": "NFX-19: mine", "state": "MERGED", "headRefName": "cesar/NFX-19"},
                {"number": 2, "url": "u2", "title": "NFX-191: other", "state": "OPEN", "headRefName": "cesar/NFX-191"},
                {"number": 3, "url": "u3", "title": "fix", "state": "OPEN", "headRefName": "cesar/NFX-19"}]))
        os.chmod(os.path.join(bin_dir, "gh"), 0o755)
        self.decide(key="NFX-19")
        r = self.run_cli("render", "--date", "2026-11-19", "--name", "t", env=dict(self.env, PATH=bin_dir))
        self.assertEqual(r.returncode, 0, r.stderr)
        shipped = self.read_report().split("## What shipped", 1)[1].split("\n## ", 1)[0]
        self.assertIn("[#1]", shipped)
        self.assertIn("[#3]", shipped)
        self.assertNotIn("[#2]", shipped)

    def test_a_root_in_the_main_checkout_beside_git_is_refused(self):
        main = os.path.join(self.tmp.name, "main")
        subprocess.run(["git", "init", "-q", main], check=True)
        subprocess.run(["git", "-C", main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True)
        wt = os.path.join(self.tmp.name, "wt")
        subprocess.run(["git", "-C", main, "worktree", "add", "-q", wt], check=True)
        env = dict(self.env, NOUSFX_REPORT_ROOT=os.path.join(main, "reports"))
        r = subprocess.run([sys.executable, "-B", SCRIPT, "decide", "NFX-191", "--decision", "d", "--why", "w",
                            "--rejected", "r", "--evidence", "#1", "--result", "open"], cwd=wt, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(main, "reports")))

    def test_a_bad_or_secret_prs_file_is_refused(self):
        self.decide()
        path = os.path.join(self.tmp.name, "bad.json")
        for body in ({"items": []}, ["x"], [{"number": 1, "title": "leak " + GH_SECRET}]):
            with open(path, "w") as f:
                json.dump(body, f)
            self.assertEqual(self.render("--prs", path).returncode, 2, body)
        self.assertIsNone(self.report_path())

    def test_render_warns_when_evidence_does_not_resolve(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        self.decide(evidence=head)
        self.decide(evidence="0" * 40)
        self.decide(evidence="no/such/file.py:3")
        self.decide(evidence="scripts/test_session_report.py:3")
        r = self.render("--prs", self.prs_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0" * 40, r.stderr)
        self.assertIn("no/such/file.py", r.stderr)
        self.assertNotIn(head, r.stderr)
        self.assertNotIn("test_session_report.py:3", r.stderr)
        text = self.read_report()
        self.assertIn("(unresolved)", text)
        self.assertEqual(text.count("(unresolved)"), 2)

    def log_entries(self):
        log = os.path.join(self.root, os.listdir(self.root)[0], "reports", "2026-11-19-t", "decisions.jsonl")
        with open(log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_adr_flag_stores_the_title(self):
        r = self.decide("--adr", "  Decisions live outside the repo  ")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.decide()
        entries = self.log_entries()
        self.assertEqual(entries[0]["adr"], "Decisions live outside the repo")
        self.assertEqual(entries[1].get("adr", ""), "")

    def test_a_blank_adr_title_is_refused(self):
        self.refused(self.decide("--adr", "   "))
        self.assertFalse(os.path.exists(self.root))

    def test_held_with_adr_is_refused_once_with_the_other_problems(self):
        r = self.decide("--held", "Cannot be undone", "--adr", "Publish reports", why=" ")
        self.refused(r)
        self.assertIn("--why is empty", r.stderr)
        self.assertIn("--held with --adr", r.stderr)

    def test_held_with_adr_is_refused(self):
        r = self.decide("--held", "Cannot be undone", "--adr", "Publish reports")
        self.refused(r)
        self.assertIn("--adr", r.stderr)
        self.assertFalse(os.path.exists(self.root))

    def test_adr_on_a_correction_of_a_held_entry_is_refused(self):
        self.decide("--held", "Cannot be undone")
        r = self.decide("--supersedes", "1", "--adr", "Publish reports")
        self.refused(r)
        self.assertIn("held", r.stderr)
        self.assertEqual(len(self.log_entries()), 1)


class WatchLedger(unittest.TestCase):
    """NFX-272: `watch add` / `watch list`, a ledger of watch items so a retro counts a mistake across tickets."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "releases")
        self.env = dict(os.environ, NOUSFX_REPORT_ROOT=self.root)

    def watch(self, *args, cwd=ROOT):
        return subprocess.run([sys.executable, "-B", SCRIPT, "watch", *args], cwd=cwd, env=self.env,
                              capture_output=True, text=True)

    def add(self, slug, ticket, *extra, cwd=ROOT, evidence="#37", note="seen in the retro", date=None):
        dated = ("--date", date) if date else ()
        return self.watch("add", "--slug", slug, "--ticket", ticket, "--evidence", evidence, "--note", note,
                          *dated, *extra, cwd=cwd)

    def project(self, name):
        """A fixture project repo outside NousFX (no scripts/nousfx_sync.py)."""
        path = os.path.join(self.tmp.name, name)
        subprocess.run(["git", "init", "-q", path], check=True)
        return path

    def line_for(self, out, slug):
        found = [l for l in out.splitlines() if l.split(" | ")[0].replace("[harness] ", "") == slug]
        self.assertEqual(len(found), 1, out)
        return found[0]

    def ledger(self, *parts):
        path = os.path.join(self.root, *parts)
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def refused(self, r, msg=None):
        self.assertEqual(r.returncode, 2, msg or (r.stdout + r.stderr))
        self.assertIn("Stopped:", r.stderr, msg)

    def test_watch_counts(self):
        self.assertEqual(self.add("testfail-voided-by-edit", "NFX-214", "--new").returncode, 0)
        r = self.watch("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        line = self.line_for(r.stdout, "testfail-voided-by-edit")
        self.assertIn("1 ticket", line)
        self.assertNotIn("class", line)
        self.assertEqual(self.add("testfail-voided-by-edit", "NFX-218").returncode, 0)
        line = self.line_for(self.watch("list").stdout, "testfail-voided-by-edit")
        self.assertIn("2 tickets", line)
        self.assertIn("class", line)
        self.assertIn("NFX-214@%s" % checkout_project(), line)
        self.assertIn("NFX-218@%s" % checkout_project(), line)
        self.assertIn("last 20", line)

    def test_watch_new_slug_is_refused_and_lists_both_ledgers(self):
        pap = self.project("pawlypaint")
        self.assertEqual(self.add("tier-synced-silent", "PAP-70", "--harness", "--new", cwd=pap,
                                  note="tier --synced returned nothing").returncode, 0)
        self.assertEqual(self.add("flaky-snapshot", "PAP-12", "--project", "--new", cwd=pap,
                                  note="snapshot drifted on CI").returncode, 0)
        r = self.add("tier-sync-silent", "PAP-71", "--harness", cwd=pap)
        self.refused(r)
        self.assertIn("--slug tier-sync-silent is new", r.stderr)
        self.assertIn("[harness] tier-synced-silent: tier --synced returned nothing", r.stderr)
        self.assertIn("flaky-snapshot: snapshot drifted on CI", r.stderr)
        self.assertIn("--new", r.stderr)
        self.assertEqual(self.add("tier-sync-silent", "PAP-71", "--harness", "--new", cwd=pap).returncode, 0)

    def test_watch_refuses_bad_fields_in_one_refusal(self):
        self.assertEqual(self.add("testfail-voided-by-edit", "NFX-214", "--new").returncode, 0)
        r = self.add("testfail-voided-by-edit", "NFX-214", evidence="see the retro", note="token " + GH_SECRET)
        self.refused(r)
        for part in ("--evidence is a pointer", "a repeat", "looks like a secret"):
            self.assertIn(part, r.stderr)
        self.assertNotIn(GH_SECRET, r.stderr + r.stdout)
        r = self.add("testfail-voided-by-edit", "nfx-9", evidence="  ", note="")
        self.refused(r)
        for part in ("--evidence is empty", "--note is empty", "--ticket is not a ticket key"):
            self.assertIn(part, r.stderr)
        self.refused(self.add("Not_Kebab", "NFX-9", "--new"))
        self.refused(self.add("testfail-voided-by-edit", "NFX-9", note="two\nlines"))
        r = self.add("testfail-voided-by-edit", "NFX-9", note="hidden \x1b[2Ktext")
        self.refused(r)
        self.assertIn("no control characters", r.stderr)
        self.assertEqual(len(self.ledger("NousFX", "watch.jsonl")), 1)

    def test_watch_ledger_choice(self):
        pap = self.project("pawlypaint")
        r = self.add("flaky-snapshot", "PAP-12", "--new", cwd=pap)
        self.refused(r)
        self.assertIn("--harness", r.stderr)
        self.assertIn("--project", r.stderr)
        self.refused(self.add("flaky-snapshot", "PAP-12", "--new", "--harness", "--project", cwd=pap))
        self.assertFalse(os.path.exists(self.root))
        self.assertEqual(self.add("flaky-snapshot", "PAP-12", "--new", "--project", cwd=pap).returncode, 0)
        self.assertEqual(self.ledger("pawlypaint", "watch.jsonl")[0]["project"], "pawlypaint")

    def test_watch_ledger_choice_inside_nousfx_is_harness(self):
        self.assertEqual(self.add("decide-refusals", "NFX-219", "--new").returncode, 0)
        rows = self.ledger("NousFX", "watch.jsonl")
        self.assertEqual([(r["slug"], r["ticket"]) for r in rows], [("decide-refusals", "NFX-219")])
        self.refused(self.add("decide-refusals", "NFX-220", "--project"))
        listed = self.watch("list").stdout
        self.assertEqual(listed.count("decide-refusals"), 1, listed)
        self.assertIn("[harness] decide-refusals", listed)

    def test_watch_ledger_choice_marker_not_folder_name(self):
        clone = self.project("harness-clone")
        os.makedirs(os.path.join(clone, "scripts"))
        open(os.path.join(clone, "scripts", "nousfx_sync.py"), "w").close()
        self.assertEqual(self.add("decide-refusals", "NFX-219", "--new", cwd=clone).returncode, 0)
        self.assertEqual(self.ledger("NousFX", "watch.jsonl")[0]["project"], "harness-clone")
        self.assertFalse(os.path.exists(os.path.join(self.root, "harness-clone")))

    def test_watch_cross_ledger_refusals(self):
        pap = self.project("pawlypaint")
        self.assertEqual(self.add("tier-synced-silent", "PAP-70", "--harness", "--new", cwd=pap).returncode, 0)
        r = self.add("tier-synced-silent", "PAP-71", "--project", "--new", cwd=pap)
        self.refused(r)
        self.assertIn("already a harness slug", r.stderr)
        self.assertEqual(self.add("flaky-snapshot", "PAP-12", "--project", "--new", cwd=pap).returncode, 0)
        self.refused(self.add("flaky-snapshot", "PAP-13", "--harness", "--new", cwd=pap))
        r = self.watch("add", "--merge", "flaky-snapshot", "--into", "tier-synced-silent", cwd=pap)
        self.refused(r)
        self.assertIn("across the two ledgers", r.stderr)
        self.assertEqual(len(self.ledger("NousFX", "watch.jsonl")), 1)
        self.assertEqual(len(self.ledger("pawlypaint", "watch.jsonl")), 1)

    def test_watch_merge_folds_tickets_without_rewriting(self):
        self.add("testfail-edit", "NFX-214", "--new")
        self.add("testfail-voided-by-edit", "NFX-218", "--new")
        before = self.ledger("NousFX", "watch.jsonl")
        r = self.watch("add", "--merge", "testfail-edit", "--into", "testfail-voided-by-edit")
        self.assertEqual(r.returncode, 0, r.stderr)
        after = self.ledger("NousFX", "watch.jsonl")
        self.assertEqual(after[:2], before)
        self.assertEqual((after[2]["merge"], after[2]["into"]), ("testfail-edit", "testfail-voided-by-edit"))
        out = self.watch("list").stdout
        line = self.line_for(out, "testfail-voided-by-edit")
        self.assertIn("2 tickets, class", line)
        self.assertIn("NFX-214@%s" % checkout_project(), line)
        self.assertNotIn("testfail-edit |", out)

    def test_watch_merge_keeps_the_new_slugs_meaning_and_a_repeated_merge_is_not_unreadable(self):
        self.add("testfail-edit", "NFX-214", "--new", note="an early name for it")
        self.add("testfail-voided-by-edit", "NFX-218", "--new", note="an edit during the run voided testfail")
        path = os.path.join(self.root, "NousFX", "watch.jsonl")
        merge = {"v": 1, "project": "NousFX", "merge": "testfail-edit", "into": "testfail-voided-by-edit",
                 "date": "2026-11-27"}
        with open(path, "a") as f:
            f.write(json.dumps(merge) + "\n" + json.dumps(merge) + "\n")
        r = self.watch("list")
        self.assertEqual(r.stderr, "")
        line = self.line_for(r.stdout, "testfail-voided-by-edit")
        self.assertTrue(line.endswith("| an edit during the run voided testfail"), line)

    def test_watch_merge_refuses_repeats_loops_self_and_unknown(self):
        self.add("testfail-edit", "NFX-214", "--new")
        self.add("testfail-voided-by-edit", "NFX-218", "--new")
        self.watch("add", "--merge", "testfail-edit", "--into", "testfail-voided-by-edit")
        self.refused(self.add("testfail-edit", "NFX-218"))
        self.refused(self.watch("add", "--merge", "testfail-voided-by-edit", "--into", "testfail-edit"))
        self.refused(self.watch("add", "--merge", "testfail-edit", "--into", "testfail-edit"))
        self.refused(self.watch("add", "--merge", "no-such-slug", "--into", "testfail-voided-by-edit"))
        self.refused(self.watch("add", "--merge", "testfail-edit", "--into", "testfail-voided-by-edit", "--project"))
        self.assertEqual(len(self.ledger("NousFX", "watch.jsonl")), 3)

    def test_watch_replay_testfail(self):
        # NFX-214, 218, 216 and 236: each retro logged "seen once"; the ledger makes it a class.
        self.add("testfail-voided-by-edit", "NFX-214", "--new", date="2026-11-20")
        line = self.line_for(self.watch("list").stdout, "testfail-voided-by-edit")
        self.assertIn("1 ticket |", line)
        self.assertNotIn("class", line)
        for key, day in (("NFX-218", "2026-11-20"), ("NFX-216", "2026-11-21"), ("NFX-236", "2026-11-21")):
            self.assertEqual(self.add("testfail-voided-by-edit", key, date=day).returncode, 0)
        line = self.line_for(self.watch("list", "--min", "2").stdout, "testfail-voided-by-edit")
        self.assertIn("[harness] testfail-voided-by-edit | 4 tickets, class |", line)
        self.assertIn("last 2026-11-21", line)

    def test_watch_harness_cross_project(self):
        pap, hem = self.project("pawlypaint"), self.project("hemisphere")
        slug = "tier-synced-silent-in-project-copy"
        self.assertEqual(self.add(slug, "PAP-70", "--harness", "--new", cwd=pap).returncode, 0)
        for cwd in (pap, hem):
            line = self.line_for(self.watch("list", cwd=cwd).stdout, slug)
            self.assertIn("1 ticket |", line)
            self.assertNotIn("class", line)
        self.assertEqual(self.add(slug, "HEM-87", "--harness", cwd=hem).returncode, 0)
        for cwd in (pap, hem, ROOT):
            line = self.line_for(self.watch("list", "--min", "2", cwd=cwd).stdout, slug)
            self.assertIn("[harness] %s | 2 tickets, class | HEM-87@hemisphere PAP-70@pawlypaint |" % slug, line)

    def test_watch_list_shows_the_first_note_as_the_meaning(self):
        self.add("shared-tmpdir", "NFX-231", "--new", note="sibling sessions clobbered one TMPDIR file")
        self.add("shared-tmpdir", "NFX-232", note="again, in the wave")
        line = self.line_for(self.watch("list").stdout, "shared-tmpdir")
        self.assertTrue(line.endswith("| sibling sessions clobbered one TMPDIR file"), line)

    def test_watch_cross_ledger_refusals_see_other_projects(self):
        pap, hem = self.project("pawlypaint"), self.project("hemisphere")
        self.assertEqual(self.add("glob-matches-nothing", "PAP-60", "--project", "--new", cwd=pap).returncode, 0)
        r = self.add("glob-matches-nothing", "HEM-86", "--harness", "--new", cwd=hem)
        self.refused(r)
        self.assertIn("project slug in pawlypaint", r.stderr)
        self.refused(self.add("glob-matches-nothing", "NFX-300", "--new"))

    def test_watch_cross_ledger_refusals_free_a_project_slug_once_merged(self):
        pap, hem = self.project("pawlypaint"), self.project("hemisphere")
        self.assertEqual(self.add("glob-matches-nothing", "PAP-60", "--project", "--new", cwd=pap).returncode, 0)
        self.add("no-test-glob-match", "PAP-61", "--project", "--new", cwd=pap)
        self.assertEqual(self.watch("add", "--merge", "glob-matches-nothing", "--into", "no-test-glob-match",
                                    cwd=pap).returncode, 0)
        self.assertEqual(self.add("glob-matches-nothing", "HEM-86", "--harness", "--new", cwd=hem).returncode, 0)
        self.assertEqual(self.add("glob-matches-nothing", "PAP-62", "--harness", cwd=pap).returncode, 0)

    def test_watch_cross_ledger_refusals_skip_other_projects_for_a_known_harness_slug(self):
        pap, hem = self.project("pawlypaint"), self.project("hemisphere")
        self.assertEqual(self.add("glob-matches-nothing", "HEM-86", "--harness", "--new", cwd=hem).returncode, 0)
        os.makedirs(os.path.join(self.root, "pawlypaint"))
        with open(os.path.join(self.root, "pawlypaint", "watch.jsonl"), "w") as f:
            f.write(json.dumps({"v": 1, "project": "pawlypaint", "slug": "glob-matches-nothing", "ticket": "PAP-60",
                                "evidence": "#3", "note": "raced in", "date": "2026-11-27"}) + "\n")
        self.assertEqual(self.add("glob-matches-nothing", "ANI-219", "--harness", cwd=self.project("anima")).returncode, 0)

    def test_watch_rows_carry_a_version_and_newer_rows_are_reported(self):
        self.add("decide-refusals", "NFX-219", "--new")
        self.assertEqual(self.ledger("NousFX", "watch.jsonl")[0]["v"], 1)
        with open(os.path.join(self.root, "NousFX", "watch.jsonl"), "a") as f:
            f.write(json.dumps({"v": 2, "project": "x", "move": "decide-refusals", "to": "y", "date": "2027-01-01"})
                    + "\n")
        r = self.watch("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 rows in", r.stderr)
        self.assertIn("need a newer session_report.py", r.stderr)
        self.assertIn("decide-refusals | 1 ticket |", r.stdout)

    def test_watch_merge_rows_that_close_a_loop_are_skipped(self):
        self.add("a-slug", "NFX-1", "--new")
        self.add("b-slug", "NFX-2", "--new")
        path = os.path.join(self.root, "NousFX", "watch.jsonl")
        merges_from_two_sessions_at_once = (("a-slug", "b-slug"), ("b-slug", "a-slug"))
        with open(path, "a") as f:
            for old, new in merges_from_two_sessions_at_once:
                f.write(json.dumps({"v": 1, "project": "NousFX", "merge": old, "into": new, "date": "2026-11-27"})
                        + "\n")
        r = self.watch("list")
        self.assertIn("1 unreadable lines", r.stderr)
        self.assertIn("b-slug | 2 tickets, class", r.stdout)

    def test_watch_list_without_a_ledger_says_so(self):
        r = self.watch("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no ledger yet", r.stdout)

    def test_watch_list_skips_a_bad_line_and_filters_by_min(self):
        self.add("decide-refusals", "NFX-219", "--new")
        with open(os.path.join(self.root, "NousFX", "watch.jsonl"), "a") as f:
            f.write('{"torn": ')
        self.add("decide-refusals", "NFX-223")
        self.add("shared-tmpdir", "NFX-231", "--new")
        r = self.watch("list", "--min", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 unreadable lines", r.stderr)
        self.assertIn("decide-refusals | 2 tickets, class", r.stdout)
        self.assertNotIn("shared-tmpdir", r.stdout)
        out = self.watch("list").stdout.splitlines()
        self.assertTrue(out[0].startswith("[harness] decide-refusals"), out)

    def test_watch_parallel_appends_stay_whole_lines(self):
        self.add("shared-tmpdir", "NFX-100", "--new")
        argv = [sys.executable, "-B", SCRIPT, "watch", "add", "--slug", "shared-tmpdir", "--evidence", "#37",
                "--note", "x" * 290]
        procs = [subprocess.Popen(argv + ["--ticket", "NFX-%d" % n], cwd=ROOT, env=self.env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for n in range(101, 113)]
        self.assertEqual([p.wait() for p in procs], [0] * 12)
        rows = self.ledger("NousFX", "watch.jsonl")
        self.assertEqual(sorted(r["ticket"] for r in rows), sorted("NFX-%d" % n for n in range(100, 113)))
        r = self.watch("list")
        self.assertEqual(r.stderr, "")
        self.assertIn("shared-tmpdir | 13 tickets, class", r.stdout)

    def test_watch_files_land_outside_the_repo(self):
        before = git_status()
        self.add("decide-refusals", "NFX-219", "--new")
        self.watch("list")
        self.assertEqual(git_status(), before)
        env = dict(self.env, NOUSFX_REPORT_ROOT=os.path.join(ROOT, "releases-inside"))
        r = subprocess.run([sys.executable, "-B", SCRIPT, "watch", "list"], cwd=ROOT, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "releases-inside")))


if __name__ == "__main__":
    unittest.main()
