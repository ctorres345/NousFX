#!/usr/bin/env python3
"""Tests for via_eval.py, the runner of Via's eval set. Run: python3 -B plugins/via/scripts/test_via_eval.py

run_checks.py runs this suite, so the whole eval set runs on every check. The runner must pass on the
current Via scripts and fail when a copy of them breaks the never-commit rule.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "via_eval.py")
CASES = os.path.join(os.path.dirname(HERE), "evals", "cases.json")
KINDS = {"route", "second-opinion", "unattended-route", "unattended-reject", "never-commits"}
ROUTE_EMIT = '    emit({"rejected": rejected, "tickets": tickets, "fix_now": fix_now})\n'


def run(*args):
    env = {k: v for k, v in os.environ.items() if k != "VIA_UNATTENDED"}
    return subprocess.run([sys.executable, "-B", RUNNER, *args], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=300)


def load_cases():
    with open(CASES, encoding="utf-8") as f:
        return json.load(f)["cases"]


class ViaEvalTest(unittest.TestCase):
    def test_the_eval_file_has_one_case_per_behavior_with_a_prompt_and_the_expected_behavior(self):
        cases = load_cases()
        ids = [c["id"] for c in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for c in cases:
            self.assertIn(c["kind"], KINDS, c["id"])
            self.assertTrue(c["prompt"].strip() and c["expected"].strip(), c["id"])
        self.assertEqual({c["kind"] for c in cases}, KINDS)
        self.assertEqual({c["route"] for c in cases if c["kind"] == "route"}, {"fix-now", "ticket", "reject"})

    def test_every_case_passes_on_the_current_via(self):
        r = run("--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        results = json.loads(r.stdout)["results"]
        self.assertEqual([x["id"] for x in results], [c["id"] for c in load_cases()])
        self.assertTrue(all(x["ok"] for x in results), results)

    def test_the_text_report_has_one_line_per_case_and_a_total(self):
        r = run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), len(load_cases()) + 1)
        self.assertTrue(all(line.startswith("ok   ") for line in lines[:-1]), r.stdout)
        self.assertEqual(lines[-1], "%d/%d cases passed" % ((len(load_cases()),) * 2))

    def test_only_runs_the_named_case(self):
        r = run("--json", "--only", "never-commits")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([x["id"] for x in json.loads(r.stdout)["results"]], ["never-commits"])

    def test_an_unknown_case_name_is_an_error(self):
        r = run("--only", "no-such-case")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no-such-case", r.stderr)

    def test_a_via_that_commits_fails_the_never_commit_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = os.path.join(tmp, "scripts")
            shutil.copytree(HERE, scripts, ignore=shutil.ignore_patterns("test_*", "__pycache__"))
            path = os.path.join(scripts, "via_run.py")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(text.count(ROUTE_EMIT), 1, "the fixture patch no longer matches via_run.py")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace(ROUTE_EMIT, '    git(["commit", "-q", "--allow-empty", "-m", "via"], project)\n'
                                     + ROUTE_EMIT))
            r = run("--json", "--scripts", scripts)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        results = {x["id"]: x for x in json.loads(r.stdout)["results"]}
        self.assertFalse(results["never-commits"]["ok"])
        self.assertIn("commit", results["never-commits"]["detail"])


if __name__ == "__main__":
    unittest.main()
