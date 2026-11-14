#!/usr/bin/env python3
"""Tests for the Via check inside the review gate (NFX-20): a PR create whose diff touches UI is blocked until
`/via check` recorded a pass for the current code (`via-check-ok`), or a skip with a reason (`via-check-skip`).
Run: python3 -B claude/hooks/test_review_gate_via.py

Each test copies the gate into a throwaway layout (`hooks/` next to `skills/review-loop/scripts/`). The real tier.py
decides what is UI; a fake one stands in for the error paths. Exit 0 allowed, 2 blocked.
"""
import json
import os
import shutil
import subprocess
import textwrap
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_gate_check import BLOCKED, ALLOWED, GATE, Base  # noqa: E402

HOOKS = os.path.dirname(GATE)
TIER_REAL = os.path.join(os.path.dirname(HOOKS), "skills", "review-loop", "scripts", "tier.py")
WORKFLOW = os.path.join(os.path.dirname(os.path.dirname(HOOKS)), ".github", "workflows", "review-gate.yml")
UI_FILE = "app/src/main/java/ui/HomeScreen.kt"
MCP = "mcp__github__create_pull_request"


class ViaBase(Base):
    def setUp(self):
        super().setUp()
        self.sh("git", "update-ref", "refs/remotes/origin/main", "HEAD")  # tier.py needs a base to diff against
        self.gate_path = self.layout()

    def layout(self, tier_body=None, code=0, sleep=0):
        """Copy the gate (and the real tier.py, or a fake that prints tier_body) into tmp/lay. Returns the gate."""
        lay = os.path.join(self.tmp, "lay")
        shutil.rmtree(lay, ignore_errors=True)
        hooks = os.path.join(lay, "hooks")
        scripts = os.path.join(lay, "skills", "review-loop", "scripts")
        os.makedirs(hooks)
        os.makedirs(scripts)
        for name in ("review-gate.py", "pr_create_parser.py", "git_refs.py"):
            shutil.copy(os.path.join(HOOKS, name), hooks)
        if tier_body is None:
            shutil.copy(TIER_REAL, scripts)
        else:
            with open(os.path.join(scripts, "tier.py"), "w", encoding="utf-8") as f:
                f.write("import sys, time\ntime.sleep(%r)\nprint(%r)\nsys.exit(%d)\n" % (sleep, tier_body, code))
        return os.path.join(hooks, "review-gate.py")

    def git_out(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, path, text="x"):
        self.write(path, text)
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")

    def write(self, path, text):
        full = os.path.join(self.repo, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def wtree(self):
        """The working tree's tree hash, the way the gate and via_run.py compute it (a copy of the index)."""
        idx = os.path.join(self.tmp, "idx")
        shutil.copy(os.path.join(self.repo, ".git", "index"), idx)
        env = {**os.environ, "GIT_INDEX_FILE": idx}
        subprocess.run(["git", "add", "-A"], cwd=self.repo, env=env, check=True, capture_output=True)
        return subprocess.run(["git", "write-tree"], cwd=self.repo, env=env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def record_pass(self, tree=None, **fields):
        rec = {"tree": tree or self.wtree(), "time": "2026-11-13T10:00:00", "run": "r", "findings_open": 0,
               "pending_human": 0, "pending_ids": []}
        rec.update(fields)
        with open(os.path.join(self.repo, ".git", "via-check-ok"), "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def body(self, via=True, tree=None, skip=None):
        """Write tmp/pr-body.md for HEAD: the Review-Gate line and, with via, the Via-Check line."""
        head = tree or self.git_out("rev-parse", "HEAD^{tree}")
        lines = ["Review-Gate: " + head] + (["Via-Check: " + head + (" skipped: " + skip if skip else "")] if via else [])
        path = os.path.join(self.tmp, "pr-body.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("## Summary\n\n" + "\n".join(lines) + "\n")
        return path

    def create(self, via_line=True, tool="Bash", extra=(), command=None, skip=None):
        """Run the gate's check for a PR create from the repo. Returns the CompletedProcess."""
        body = self.body(via=via_line, skip=skip)
        cmd = command or f"gh pr create --body-file {body}"
        args = ["--cwd", self.repo, "--command", cmd, "--tool", tool, *extra]
        if tool != "Bash":
            args += ["--body-file", body]
        return self.check(*args, gate=self.gate_path)

    def gate(self, *args, env=None):
        return subprocess.run([sys.executable, "-B", self.gate_path, *args], cwd=self.repo, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})})

    def reviewed_ui_branch(self):
        """A branch whose committed diff is UI and has a clean review mark."""
        self.commit(UI_FILE, "class HomeScreen")
        r = subprocess.run([sys.executable, "-B", self.gate_path, "--mark", "--findings", "0"], cwd=self.repo,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class Decision(ViaBase):
    def test_ui_diff_blocks_until_a_pass_is_recorded(self):  # AC1
        self.reviewed_ui_branch()
        r = self.create()
        self.assertEqual(r.returncode, BLOCKED, r.stdout)
        self.assertIn("/via check", r.stderr)
        self.record_pass()
        r = self.create()
        self.assertEqual(r.returncode, ALLOWED, r.stderr)

    def test_docs_only_diff_needs_no_pass(self):  # AC1
        self.commit("README.md", "docs")
        self.assertEqual(subprocess.run([sys.executable, "-B", self.gate_path, "--mark", "--findings", "0"],
                                        cwd=self.repo, capture_output=True, text=True).returncode, 0)
        r = self.create(via_line=False)
        self.assertEqual(r.returncode, ALLOWED, r.stderr)

    def test_xcassets_only_diff_counts_as_ui(self):
        self.commit("App/Assets.xcassets/Icon.imageset/Contents.json", "{}")
        self.gate("--mark", "--findings", "0")
        self.assertEqual(self.create(via_line=False).returncode, BLOCKED)

    def test_skip_passes_and_is_recorded(self):  # AC1
        self.reviewed_ui_branch()
        r = self.gate("--via-skip", "no emulator on this machine")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.repo, ".git", "via-check-skip"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["reason"], "no emulator on this machine")
        self.assertEqual(self.create().returncode, BLOCKED)  # a plain line does not stand in for a skip
        self.assertEqual(self.create(skip="no emulator on this machine").returncode, ALLOWED)

    def test_skip_refusals(self):
        self.reviewed_ui_branch()
        for reason in ("", "   ", "--x"):
            with self.subTest(reason=reason):
                self.assertEqual(self.gate("--via-skip", reason).returncode, 2)
        self.assertEqual(self.gate("--via-skip", "key AKIAIOSFODNN7EXAMPLE").returncode, 2)
        self.assertEqual(self.gate("--via-skip", "ok reason", env={"VIA_UNATTENDED": "1"}).returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "via-check-skip")))

    def test_skip_for_another_branch_is_stale(self):
        self.reviewed_ui_branch()
        self.gate("--via-skip", "reason")
        path = os.path.join(self.repo, ".git", "via-check-skip")
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        rec["branch"] = "cesar/OTHER-1"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        self.assertEqual(self.create().returncode, BLOCKED)

    def test_edit_after_a_pass_on_uncommitted_code_blocks_again(self):  # AC2
        """The pass covers the working tree (an uncommitted edit); a further edit makes it stale."""
        self.reviewed_ui_branch()
        self.write(UI_FILE, "class HomeScreen { /* v2 */ }")
        self.record_pass()
        self.write(UI_FILE, "class HomeScreen { /* v3 */ }")
        r = self.create()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("changed after", r.stderr)

    def test_uncommitted_edit_after_a_pass_on_clean_head_does_not_block(self):
        """A PR carries HEAD only (CI compares HEAD^{tree}), so a pass for HEAD still covers it."""
        self.reviewed_ui_branch()
        self.record_pass()
        self.write(UI_FILE, "class HomeScreen { /* not committed */ }")
        self.assertEqual(self.create().returncode, ALLOWED)

    def test_new_commit_after_the_pass_blocks_again(self):  # AC2
        self.reviewed_ui_branch()
        self.record_pass()
        self.commit(UI_FILE, "class HomeScreen { /* edited */ }")
        self.gate("--mark", "--findings", "0")
        r = self.create()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("changed after", r.stderr)

    def test_untracked_file_added_after_a_pass_on_uncommitted_code_blocks(self):  # AC2
        """The pass covers the working tree (an untracked UI file); a second untracked file makes it stale.
        With a clean HEAD the pass covers HEAD, which is all a PR carries (see docs/review-gate.md)."""
        self.reviewed_ui_branch()
        self.write("app/src/main/java/ui/NewScreen.kt", "class NewScreen")
        self.record_pass()
        self.write("app/src/main/java/ui/Other.kt", "class Other")
        r = self.create()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("changed after", r.stderr)

    def test_findings_open_must_be_the_integer_zero(self):
        self.reviewed_ui_branch()
        for value in (1, "0", True, None):
            with self.subTest(value=value):
                self.record_pass(findings_open=value)
                r = self.create()
                self.assertEqual(r.returncode, BLOCKED)
                self.assertIn("findings", r.stderr)

    def test_corrupt_record_blocks(self):
        self.reviewed_ui_branch()
        with open(os.path.join(self.repo, ".git", "via-check-ok"), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self.create().returncode, BLOCKED)

    def test_pending_human_passes_with_a_note(self):
        self.reviewed_ui_branch()
        self.record_pass(pending_human=2, pending_ids=["F1", "F2"])
        r = self.create()
        self.assertEqual(r.returncode, ALLOWED, r.stderr)
        self.assertIn("F1", r.stdout + r.stderr)

    def test_compound_command_is_gated(self):
        self.reviewed_ui_branch()
        body = self.body()
        r = self.check("--cwd", self.tmp, "--allow-non-repo", "--command",
                       f"cd {self.repo} && gh pr create --body-file {body}", gate=self.gate_path)
        self.assertEqual(r.returncode, BLOCKED, r.stderr)
        self.assertIn("/via check", r.stderr)

    def test_a_create_pr_mcp_tool_is_gated_the_same_way(self):  # AC4
        self.reviewed_ui_branch()
        r = self.create(tool=MCP, command="x")
        self.assertEqual(r.returncode, BLOCKED, r.stderr)
        self.assertIn("/via check", r.stderr)
        self.record_pass()
        self.assertEqual(self.create(tool=MCP, command="x").returncode, ALLOWED)

    def test_body_without_a_via_line_blocks_a_ui_diff(self):
        self.reviewed_ui_branch()
        self.record_pass()
        r = self.create(via_line=False)
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("Via-Check", r.stderr)


class Errors(ViaBase):
    """AC3: a diff the gate cannot classify blocks, and the message names the error."""

    def blocked_by(self, tier_body, code=0, sleep=0, extra=()):
        self.reviewed_ui_branch()
        self.record_pass()
        self.gate_path = self.layout(tier_body, code, sleep)
        r = self.create(extra=extra)
        self.assertEqual(r.returncode, BLOCKED, r.stdout)
        return r.stderr

    def test_failing_tier_py_blocks_and_names_the_error(self):
        self.assertIn("exit 1", self.blocked_by("", code=1))

    def test_unreadable_output_blocks(self):
        self.assertIn("tier.py", self.blocked_by("not json"))

    def test_fallback_result_blocks(self):
        body = json.dumps({"tier": 2, "reasons": ["Tier 2: could not read the diff (boom)"], "ui": False,
                           "fallback": True, "security": [], "stats": {}})
        err = self.blocked_by(body)
        self.assertIn("could not read the diff", err)

    def test_ui_not_a_bool_blocks(self):
        self.blocked_by(json.dumps({"tier": 1, "reasons": [], "ui": "no", "fallback": False, "stats": {}}))

    def test_a_result_without_the_fallback_key_blocks(self):
        """A tier.py older than the gate says `ui: false` for a diff it could not read, and has no `fallback` key."""
        err = self.blocked_by(json.dumps({"tier": 2, "reasons": ["Tier 2: could not read the diff (x)"],
                                          "ui": False, "stats": {}}))
        self.assertIn("fallback", err)

    def test_a_slow_tier_py_blocks_within_the_budget(self):
        start = time.monotonic()
        self.blocked_by(json.dumps({"tier": 1, "reasons": [], "ui": False, "stats": {}}), sleep=30,
                        extra=("--budget", "3"))
        self.assertLess(time.monotonic() - start, 20)

    def test_missing_tier_py_blocks(self):
        self.reviewed_ui_branch()
        os.remove(os.path.join(self.tmp, "lay", "skills", "review-loop", "scripts", "tier.py"))
        self.assertEqual(self.create().returncode, BLOCKED)

    def test_the_real_tier_py_marks_a_diff_it_could_not_read_as_fallback(self):
        r = subprocess.run([sys.executable, "-B", TIER_REAL, "--cwd", self.tmp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)  # self.tmp is not a repo: "could not find the repo"
        data = json.loads(r.stdout)
        self.assertIs(data["fallback"], True)
        self.assertIs(data["ui"], False)


class Attest(ViaBase):
    def test_attest_prints_the_via_line_for_a_ui_diff(self):
        self.reviewed_ui_branch()
        self.record_pass()
        r = self.gate("attest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Via-Check: " + self.git_out("rev-parse", "HEAD^{tree}"), r.stdout)

    def test_attest_prints_the_skip_line(self):
        self.reviewed_ui_branch()
        self.gate("--via-skip", "no emulator")
        head = self.git_out("rev-parse", "HEAD^{tree}")
        self.assertIn(f"Via-Check: {head} skipped: no emulator", self.gate("attest").stdout)

    def test_a_pass_on_uncommitted_code_does_not_attest_head(self):
        """The PR carries HEAD only: a pass for a dirty working tree must not allow the create or print a line."""
        self.reviewed_ui_branch()
        self.write("notes.txt", "uncommitted")
        self.record_pass()  # for the working tree, which is not HEAD's tree
        r = self.create()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("uncommitted", r.stderr)
        a = self.gate("attest")
        self.assertEqual(a.returncode, 2)  # the review rule already refuses a dirty tree

    def test_a_plain_via_line_does_not_stand_in_for_a_recorded_skip(self):
        self.reviewed_ui_branch()
        self.gate("--via-skip", "no emulator")
        r = self.create()  # the body helper writes a plain `Via-Check: <tree>` line
        self.assertEqual(r.returncode, BLOCKED, r.stderr)
        head = self.git_out("rev-parse", "HEAD^{tree}")
        path = os.path.join(self.tmp, "pr-body.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Review-Gate: {head}\nVia-Check: {head} skipped: no emulator\n")
        r = self.check("--cwd", self.repo, "--command", f"gh pr create --body-file {path}", "--tool", "Bash",
                       gate=self.gate_path)
        self.assertEqual(r.returncode, ALLOWED, r.stderr)

    def test_attest_names_the_unknown_diff_not_the_review(self):
        self.commit("README.md", "docs")
        self.gate("--mark", "--findings", "0")
        self.sh("git", "update-ref", "-d", "refs/remotes/origin/main")
        r = self.gate("attest")
        self.assertEqual(r.returncode, 2)
        self.assertIn("touches UI", r.stderr)
        self.assertNotIn("review loop", r.stderr)

    def test_a_malformed_pending_human_blocks(self):
        self.reviewed_ui_branch()
        for bad in ("2", True, [1], -1):
            with self.subTest(bad):
                self.record_pass(pending_human=bad)
                self.assertEqual(self.create().returncode, BLOCKED)

    def test_a_pass_after_a_skip_attests_the_pass(self):
        self.reviewed_ui_branch()
        self.gate("--via-skip", "no emulator")
        self.record_pass()
        head = self.git_out("rev-parse", "HEAD^{tree}")
        self.assertIn(f"Via-Check: {head}\n", self.gate("attest").stdout)

    def test_an_error_before_the_via_check_blocks(self):
        """A git error or timeout raised on the Via step (in_repo re-raises both) must block, never exit 1 (open)."""
        self.reviewed_ui_branch()
        self.record_pass()
        with open(self.gate_path, encoding="utf-8") as f:
            src = f.read()
        old = "if not in_repo(cwd) or not via_touches_ui(cwd):\n            return None, \"\""
        self.assertEqual(src.count(old), 1)
        src = src.replace(old, "if not (_boom() or in_repo(cwd)) or not via_touches_ui(cwd):\n            return None, \"\"")
        src = src.replace("def via_blocked(cwd):", "def _boom():\n    raise GateTimeout('boom')\n\n\ndef via_blocked(cwd):", 1)
        with open(self.gate_path, "w", encoding="utf-8") as f:
            f.write(src)
        r = self.create()
        self.assertEqual(r.returncode, BLOCKED, r.stderr)
        self.assertIn("GateTimeout", r.stderr)

    def test_a_skip_works_when_tier_py_cannot_classify_the_diff(self):
        """No origin/main (so tier.py falls back) must not leave the user with no way out."""
        self.reviewed_ui_branch()
        self.sh("git", "update-ref", "-d", "refs/remotes/origin/main")
        self.assertEqual(self.create().returncode, BLOCKED)
        self.assertEqual(self.gate("--via-skip", "no base branch here").returncode, 0)
        r = self.create(skip="no base branch here")
        self.assertEqual(r.returncode, ALLOWED, r.stderr)

    def test_attest_fails_without_a_pass_on_a_ui_diff(self):
        self.reviewed_ui_branch()
        r = self.gate("attest")
        self.assertEqual(r.returncode, 2)
        self.assertIn("/via check", r.stderr)

    def test_attest_has_no_via_line_for_docs(self):
        self.commit("README.md", "docs")
        self.gate("--mark", "--findings", "0")
        r = self.gate("attest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Via-Check", r.stdout)


class VerifyVia(ViaBase):
    """The `verify-via` subcommand CI runs on the PR's tier.py JSON and body."""

    def verify(self, tier, text, tree="a" * 40):
        tj, tf = os.path.join(self.tmp, "tier.json"), os.path.join(self.tmp, "body.txt")
        with open(tj, "w", encoding="utf-8") as f:
            json.dump(tier, f)
        with open(tf, "w", encoding="utf-8") as f:
            f.write(text)
        return self.gate("verify-via", "--tier-json", tj, "--text-file", tf, "--tree", tree)

    UI = {"tier": 1, "reasons": [], "ui": True, "fallback": False, "stats": {}}

    def test_not_ui_needs_no_line(self):
        r = self.verify({**self.UI, "ui": False}, "no line")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Via check not needed.", r.stdout)

    def test_ui_with_the_line_passes(self):
        r = self.verify(self.UI, "Via-Check: " + "a" * 40)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Via check verified.", r.stdout)

    def test_ui_without_the_line_fails(self):
        self.assertEqual(self.verify(self.UI, "nothing").returncode, 2)

    def test_a_stale_line_fails(self):
        self.assertEqual(self.verify(self.UI, "Via-Check: " + "b" * 40).returncode, 2)

    def test_a_skip_is_reported_for_the_label_check(self):
        r = self.verify(self.UI, "Via-Check: " + "a" * 40 + " skipped: no emulator")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "Via check skipped: no emulator")

    def test_a_skip_for_another_tree_is_stale(self):
        self.assertEqual(self.verify(self.UI, "Via-Check: " + "b" * 40 + " skipped: old").returncode, 2)

    def test_a_skip_reason_cannot_pose_as_another_outcome(self):
        """CI compares the first line exactly: a reason that quotes an outcome must still read as a skip."""
        for reason in ("Via check verified.", "Via check not needed."):
            with self.subTest(reason):
                r = self.verify(self.UI, "Via-Check: " + "a" * 40 + " skipped: " + reason)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.splitlines()[0], "Via check skipped: " + reason)

    def test_a_pass_line_wins_over_a_skip_line(self):
        r = self.verify(self.UI, "Via-Check: " + "a" * 40 + " skipped: x\nVia-Check: " + "a" * 40)
        self.assertEqual(r.stdout.splitlines()[0], "Via check verified.")

    def test_a_fallback_result_fails(self):
        self.assertEqual(self.verify({**self.UI, "ui": False, "fallback": True}, "x").returncode, 2)

    def test_unreadable_tier_json_fails(self):
        tf = os.path.join(self.tmp, "b.txt")
        open(tf, "w").close()
        r = self.gate("verify-via", "--tier-json", os.path.join(self.tmp, "none.json"), "--text-file", tf,
                      "--tree", "a" * 40)
        self.assertEqual(r.returncode, 2)


class SecretPattern(unittest.TestCase):
    def test_the_gate_and_via_run_use_the_same_secret_pattern(self):
        """Each script ships alone, so the pattern is copied; a change to one has to reach the other."""
        def block(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            start = text.index("SECRET_RE = re.compile(")
            return text[start:text.index("\n)\n", start)]
        via_run = os.path.join(os.path.dirname(os.path.dirname(HOOKS)), "plugins", "via", "scripts", "via_run.py")
        self.assertEqual(block(GATE), block(via_run))

    def test_the_gate_reads_the_file_name_via_run_writes(self):
        """The gate cannot import the plugin, so the pass file's name is a copy (nousfx_deny.py holds a third)."""
        via_run = os.path.join(os.path.dirname(os.path.dirname(HOOKS)), "plugins", "via", "scripts", "via_run.py")
        with open(via_run, encoding="utf-8") as f:
            self.assertIn('PASS_FILE = "via-check-ok"', f.read())
        with open(GATE, encoding="utf-8") as f:
            self.assertIn('VIA_OK_FILE = "via-check-ok"', f.read())


class Workflow(unittest.TestCase):
    def test_ci_runs_verify_via_and_checks_the_via_skip_label(self):  # AC5
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("verify-via", text)
        self.assertIn("via-skip", text)
        self.assertIn("Via check skipped", text)

    def run_arm(self, via_out, label):
        """Run the workflow's `case "$via_first"` block under bash with a stubbed `gh` (the labeler is the owner)."""
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        start = text.index("          via_first=$(")
        end = text.index("          esac", start) + len("          esac")
        script = ("gh() { if [ \"$2\" = --paginate ]; then printf 'owner\\tUser\\n'; else return 0; fi; }\n"
                  "REPO=o/r; PR_NUMBER=1; REPO_OWNER=owner\n" + textwrap.dedent(text[start:end]))
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                              env={**os.environ, "via_out": via_out, "VIA_SKIP_LABEL": label})

    def test_a_skip_without_the_label_fails_and_with_it_passes(self):  # AC5
        for out in ("Via check skipped: no emulator", "Via check skipped: Via check verified."):
            with self.subTest(out):
                self.assertEqual(self.run_arm(out, "false").returncode, 1)
                self.assertEqual(self.run_arm(out, "true").returncode, 0)
        self.assertEqual(self.run_arm("Via check verified.", "false").returncode, 0)
        self.assertEqual(self.run_arm("something else", "true").returncode, 1)

    def test_the_via_check_runs_before_the_pr_code_does(self):
        """run-check executes the PR's own tests, which could rewrite tier.json or the base gate copy."""
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        self.assertLess(text.index("verify-via --tier-json"), text.index("run-check --ref"))

    def test_the_outcome_is_the_first_line_compared_exactly(self):
        """A skip reason is free text from the PR body: it must not pick the `case` arm that skips the label."""
        with open(WORKFLOW, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('case "$via_first" in', text)
        self.assertNotIn('*"Via check verified."*', text)
        self.assertNotIn('*"Via check not needed."*', text)
        arm = text[text.index('case "$via_first" in'):]
        self.assertLess(arm.index('"Via check verified."|"Via check not needed."'), arm.index('"Via check skipped: "*)'))
        self.assertIn("VIA_SKIP_LABEL", arm[arm.index('"Via check skipped: "*)'):])


if __name__ == "__main__":
    unittest.main()
