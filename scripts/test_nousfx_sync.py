#!/usr/bin/env python3
"""Tests for scripts/nousfx_sync.py. Run: python3 -B scripts/test_nousfx_sync.py

The one seam is the CLI, run as a subprocess against a temp home, a temp state home and a
temp copy of the repo sources. Nothing real is touched.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nousfx_board  # noqa: E402
import nousfx_sync  # noqa: E402  (merge_settings is tested directly)
ROOT = os.path.dirname(HERE)
SYNC = os.path.join(HERE, "nousfx_sync.py")
FIX = os.path.join(HERE, "fixtures", "sync")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="nousfx-sync-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.state = os.path.join(self.tmp, "state")
        self.repo = os.path.join(self.tmp, "repo")
        self.rules = os.path.join(self.home, ".claude", "CLAUDE.md")
        self.settings = os.path.join(self.home, ".claude", "settings.json")
        self.hook = os.path.join(self.home, ".claude", "hooks", "review-gate.py")
        os.makedirs(self.home)
        shutil.copytree(os.path.join(ROOT, "claude", "hooks"), os.path.join(self.repo, "claude", "hooks"),
                        ignore=shutil.ignore_patterns("test_*", "__pycache__"))
        write(os.path.join(self.repo, "harness", "AGENTS.md"), read(os.path.join(FIX, "AGENTS.md")))
        write(os.path.join(self.repo, "harness", "overlays", "claude.overlay.md"),
              read(os.path.join(FIX, "claude.overlay.md")))
        write(os.path.join(self.state, "local.md"), read(os.path.join(FIX, "local.md")))
        write(os.path.join(self.repo, "harness", "mcp.toml"), "# no servers\n")  # the shared list is required

    def sync(self, *args, repo=None, env=None):
        return subprocess.run(
            [sys.executable, "-B", SYNC, "--home", self.home, "--state-home", self.state,
             "--repo", repo or self.repo, *args],
            capture_output=True, text=True, cwd=self.tmp, env={**os.environ, **(env or {})})

    def tree(self):
        out = {}
        for base, _, files in os.walk(self.home):
            for name in files:
                p = os.path.join(base, name)
                out[os.path.relpath(p, self.home)] = read(p)
        return out

    def snapshot(self):
        """Every file under home and state home, so 'changes nothing' covers backups too."""
        out = self.tree()
        for base, _, files in os.walk(self.state):
            for name in files:
                p = os.path.join(base, name)
                out["state:" + os.path.relpath(p, self.state)] = read(p)
        return out


class FirstRun(Base):
    def test_writes_expected_rules_and_registers_hook(self):
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        golden = read(os.path.join(FIX, "expected_CLAUDE.md")).replace("{repo}", self.repo).replace("{state}", self.state)
        self.assertEqual(read(self.rules), golden)
        self.assertTrue(os.path.exists(self.hook))
        for name in ("pr_create_parser.py", "git_refs.py"):  # git_refs.py: --mark needs it next to the gate (NFX-108)
            self.assertTrue(os.path.exists(os.path.join(self.home, ".claude", "hooks", name)), name)
        cfg = json.loads(read(self.settings))
        entries = cfg["hooks"]["PreToolUse"]
        self.assertEqual(len(entries), 2)  # the gate, then the testfail-running warning (NFX-271)
        self.assertIn("review-gate.py", entries[0]["hooks"][0]["command"])
        self.assertIn("testfail-running.py", entries[1]["hooks"][0]["command"])

    def test_second_run_changes_nothing(self):
        self.sync()
        before = self.snapshot()
        r = self.sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_posix_hook_command_is_isolated_and_skips_bytecode(self):
        self.sync()
        hook = json.loads(read(self.settings))["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(hook["command"], f'python3 -I -B "{self.hook}" --budget 55')

    def test_shadowed_stdlib_cannot_open_the_gate(self):
        """The installed command ignores PYTHONPATH: a json.py that exits 0 must not allow a PR create."""
        self.sync()
        shadow = os.path.join(self.tmp, "shadow")
        write(os.path.join(shadow, "json.py"), "import sys\nsys.exit(0)\n")
        repo = os.path.join(self.tmp, "wrongbranch")
        subprocess.run(["git", "init", "-q", "-b", "not-allowed/x", repo], check=True)
        r = subprocess.run([sys.executable, "-I", "-B", self.hook, "check", "--cwd", repo,
                            "--command", "gh pr create"],
                           capture_output=True, text=True, env={**os.environ, "PYTHONPATH": shadow})
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_symlinked_target_is_refused_and_nothing_changes(self):
        real = os.path.join(self.tmp, "dotfiles", "CLAUDE.md")
        write(real, "mine\n")
        os.makedirs(os.path.dirname(self.rules))
        os.symlink(real, self.rules)
        before = self.snapshot()
        r = self.sync()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("symlink", r.stderr)
        self.assertEqual(read(real), "mine\n")
        self.assertTrue(os.path.islink(self.rules))
        self.assertEqual(self.snapshot(), before)

    def test_existing_settings_are_kept_and_hook_not_duplicated(self):
        write(self.settings, json.dumps({"model": "x", "hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "other.sh"}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 old/review-gate.py"}]}]}}))
        self.sync()
        self.sync()
        cfg = json.loads(read(self.settings))
        cmds = [h["command"] for e in cfg["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertEqual(cmds[0], "other.sh")
        self.assertEqual(sum("review-gate.py" in c for c in cmds), 1)
        self.assertEqual(cfg["model"], "x")

    def test_missing_local_file_is_fine(self):
        os.remove(os.path.join(self.state, "local.md"))
        self.assertEqual(self.sync().returncode, 0)
        self.assertNotIn("Fixture local rules", read(self.rules))

    def test_windows_hook_command(self):
        self.sync("--platform", "windows")
        hook = json.loads(read(self.settings))["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertTrue(hook["command"].startswith('python -I -B "'))
        self.assertNotIn("\\", hook["command"])
        self.assertEqual(hook["shell"], "powershell")


class DryRunAndCheck(Base):
    def test_dry_run_diff_prints_diff_and_writes_nothing(self):
        write(self.rules, "old rules\n")
        before = self.snapshot()
        r = self.sync("--dry-run", "--diff")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("-old rules", r.stdout)
        self.assertIn("+# Fixture rules", r.stdout)
        self.assertIn(f"+++ {self.rules}\n", r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_summary_by_default_one_line_per_file_and_writes_nothing(self):
        write(self.rules, "old rules\n")
        before = self.snapshot()
        r = self.sync("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, rf"(?m)^would update {re.escape(self.rules)} \(\+\d+ -1\)$")
        self.assertNotIn("old rules", r.stdout)
        self.assertNotIn("+++ ", r.stdout)
        self.assertRegex(r.stdout, r"(?m)^\d+ file\(s\) would change$")
        self.assertEqual(self.snapshot(), before)

    def test_diff_without_dry_run_is_a_usage_error(self):
        for extra in ((), ("--check",)):
            r = self.sync("--diff", *extra)
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn("--diff only goes with --dry-run", r.stderr)
        self.assertFalse(os.path.exists(self.rules))

    def test_check_clean_after_sync(self):
        self.sync()
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_check_before_first_sync_reports_drift(self):
        self.assertNotEqual(self.sync("--check").returncode, 0)
        self.assertFalse(os.path.exists(self.rules))

    def test_check_fails_on_hand_edited_generated_file_and_writes_nothing(self):
        self.sync()
        write(self.rules, read(self.rules) + "hand edit\n")
        before = self.snapshot()
        r = self.sync("--check")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CLAUDE.md", r.stdout)
        self.assertEqual(self.snapshot(), before)


class DryRunSummary(unittest.TestCase):
    """The formatter: one line per file, the action and the +/- line counts, never the content (NFX-215)."""
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="nousfx-sync-summary-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_dry_run_summary_counts_an_update(self):
        path = os.path.join(self.tmp, "a.md")
        write(path, "one\ntwo\nthree\n")
        self.assertEqual(nousfx_sync.dry_run_summary(path, "one\nTWO\nthree\nfour\n"),
                         f"would update {path} (+2 -1)\n")

    def test_dry_run_summary_counts_a_create(self):
        path = os.path.join(self.tmp, "new", "b.py")
        self.assertEqual(nousfx_sync.dry_run_summary(path, "x = 1\ny = 2\n"), f"would create {path} (+2 -0)\n")

    def test_dry_run_summary_counts_a_line_ending_change(self):
        path = os.path.join(self.tmp, "d.md")
        write(path, "one\ntwo")  # only the missing newline at the end differs: never +0 -0
        self.assertEqual(nousfx_sync.dry_run_summary(path, "one\ntwo\n"), f"would update {path} (+1 -1)\n")

    def test_dry_run_summary_never_prints_the_content(self):
        path = os.path.join(self.tmp, "c.md")
        write(path, "old-secret-ish\n")
        line = nousfx_sync.dry_run_summary(path, "new-content\n" * 500)
        self.assertNotIn("secret", line)
        self.assertNotIn("new-content", line)
        self.assertIn("(+500 -1)", line)
        self.assertEqual(line.count("\n"), 1)


class DriftHeader(Base):
    """The header names a hash of the sources, so a repo commit alone is never drift."""

    def vcs(self, *args):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True, env=env)

    def header(self):
        return read(self.rules).splitlines()[0]

    def test_an_unrelated_commit_is_not_drift(self):
        self.vcs("init", "-q")
        self.vcs("add", "-A")
        self.vcs("commit", "-qm", "one")
        self.assertEqual(self.sync().returncode, 0)
        write(os.path.join(self.repo, "unrelated.txt"), "x")
        self.vcs("add", "-A")
        self.vcs("commit", "-qm", "two")
        r = self.sync("--check")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_header_changes_with_each_source_and_not_with_the_commit(self):
        self.sync()
        first = self.header()
        self.assertNotRegex(first, r"nousfx_sync (unknown|[0-9a-f]{7})\.")
        for path in (os.path.join(self.repo, "harness", "AGENTS.md"),
                     os.path.join(self.repo, "harness", "overlays", "claude.overlay.md"),
                     os.path.join(self.state, "local.md")):
            original = read(path)
            write(path, original + "\nmore\n")
            self.assertEqual(self.sync("--check").returncode, 1, path)
            self.sync()
            self.assertNotEqual(self.header(), first, path)
            write(path, original)
            self.sync()
            self.assertEqual(self.header(), first, path)

    def test_editing_the_generated_file_is_drift(self):
        self.sync()
        write(self.rules, read(self.rules).replace("Fixture rules", "Edited rules", 1))
        self.assertEqual(self.sync("--check").returncode, 1)


class Restore(Base):
    def test_restore_returns_files_to_pre_sync_state(self):
        write(self.rules, "my old rules\n")
        write(self.settings, json.dumps({"model": "x"}))
        self.sync()
        self.assertNotEqual(read(self.rules), "my old rules\n")
        r = self.sync("--restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(read(self.rules), "my old rules\n")
        self.assertEqual(json.loads(read(self.settings)), {"model": "x"})
        self.assertFalse(os.path.exists(self.hook))  # did not exist before the sync

    def test_restore_undoes_only_the_last_sync(self):
        self.sync()
        write(os.path.join(self.state, "local.md"), "# changed local\n")
        self.sync()
        self.assertIn("changed local", read(self.rules))
        self.sync("--restore")
        self.assertIn("Fixture local rules", read(self.rules))
        self.assertTrue(os.path.exists(self.hook))

    def test_restore_without_backup_fails(self):
        self.assertNotEqual(self.sync("--restore").returncode, 0)


class Guards(Base):
    def test_secret_in_input_fails_run_and_writes_nothing(self):
        token = "ghp_" + "a" * 36
        write(os.path.join(self.state, "local.md"), "token: " + token + "\n")
        r = self.sync()
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(token, r.stdout + r.stderr)  # never echo the secret
        self.assertEqual(list(self.snapshot()), ["state:local.md"])  # only the planted input exists

    def test_broken_hook_fails_self_test(self):
        write(os.path.join(self.repo, "claude", "hooks", "review-gate.py"), "this is not python (\n")
        r = self.sync()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("self-test", r.stderr)

    def test_hook_that_always_allows_fails_self_test(self):
        write(os.path.join(self.repo, "claude", "hooks", "review-gate.py"), "import sys\nsys.exit(0)\n")
        self.assertNotEqual(self.sync().returncode, 0)

    def test_missing_neutral_rules_fail(self):
        os.remove(os.path.join(self.repo, "harness", "AGENTS.md"))
        self.assertNotEqual(self.sync().returncode, 0)


class DroppedSectionGuard(Base):
    """The regression helper must fail when a rule from today's file has no home in the new sources."""
    HELPER = os.path.join(HERE, "real_machine_regression.py")

    def regress(self, today_text):
        today = os.path.join(self.tmp, "today.md")
        write(today, today_text)
        r = subprocess.run([sys.executable, "-B", self.HELPER, "--out", os.path.join(self.tmp, "out"),
                            "--today", today, "--repo", self.repo, "--state-home", self.state,
                            "--fail-on-dropped"], capture_output=True, text=True)
        report = os.path.join(self.tmp, "out", "regression.txt")
        return r, read(report) if os.path.exists(report) else ""

    def test_every_section_placed_passes(self):
        r, _ = self.regress(read(os.path.join(FIX, "today_CLAUDE.md")))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_dropped_heading_and_rule_fail(self):
        r, report = self.regress(read(os.path.join(FIX, "today_CLAUDE.md"))
                                 + "\n# Forgotten section\n- A rule nobody carried over.\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("# Forgotten section", report)
        self.assertIn("A rule nobody carried over", report)

    def test_heading_whose_title_lives_on_in_a_longer_heading_is_kept(self):
        agents = os.path.join(self.repo, "harness", "AGENTS.md")
        write(agents, read(agents) + "\n## Tickets and branches\n- Branch `<prefix>/<KEY>` off the default branch.\n")
        r, report = self.regress("# Branches\n- Branch `<prefix>/<KEY>` off the default branch.\n")
        self.assertEqual(r.returncode, 0, report)
        self.assertIn("dropped: 0", report)

    def test_rule_moved_to_a_skill_is_kept(self):
        rule = "Always archive the quarterly widget ledger before closing the books."
        write(os.path.join(self.repo, "claude", "skills", "demo", "SKILL.md"), f"---\nname: demo\n---\n\n{rule}\n")
        r, report = self.regress(f"# Fixture rules\n- {rule}\n")
        self.assertEqual(r.returncode, 0, report)
        self.assertIn("moved: 1", report)

    def test_half_moved_rule_is_dropped(self):
        moved = "Always archive the quarterly widget ledger before closing the books."
        write(os.path.join(self.repo, "claude", "skills", "demo", "SKILL.md"), f"---\nname: demo\n---\n\n{moved}\n")
        r, report = self.regress(f"# Fixture rules\n- {moved} Then shred every printed ledger copy afterwards.\n")
        self.assertEqual(r.returncode, 1, report)
        self.assertIn("dropped: 1", report)

    def test_rule_made_of_words_found_in_different_files_is_dropped(self):
        write(os.path.join(self.repo, "claude", "skills", "a", "SKILL.md"), "---\nname: a\n---\n\nWidgets ledger archive quarterly.\n")
        write(os.path.join(self.repo, "claude", "skills", "b", "SKILL.md"), "---\nname: b\n---\n\nShred printed copies afterwards.\n")
        r, report = self.regress("# Fixture rules\n- Widgets ledger quarterly archive, shred printed copies afterwards.\n")
        self.assertEqual(r.returncode, 1, report)

    def test_reordered_and_reworded_do_not_fail(self):
        today = ("# Fixture local rules\n- Branch prefix: demo, so a ticket branch is `demo/KEY-1`.\n\n"
                 "# Fixture rules\n- Use a fast agent for searching code.\n")
        r, report = self.regress(today)
        self.assertEqual(r.returncode, 0, report)
        self.assertIn("reworded: 1", report)


class DriftHookRegistration(Base):
    """NFX-57: sync registers a SessionStart hook that warns about drift, and --check --summary feeds it."""

    def drift_entries(self):
        cfg = json.loads(read(self.settings))
        return [e for e in cfg["hooks"].get("SessionStart", [])
                if any("nousfx-drift.py" in h["command"] for h in e["hooks"])]

    def test_session_start_hook_is_registered_with_repo_and_tools(self):
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        entries = self.drift_entries()
        self.assertEqual(len(entries), 1)
        hook = entries[0]["hooks"][0]
        script = os.path.join(self.home, ".claude", "hooks", "nousfx-drift.py")
        self.assertTrue(os.path.exists(script))
        self.assertEqual(hook["command"], f'python3 -I -B "{script}" --repo "{self.repo}" --tools claude')
        self.assertEqual(hook["timeout"], 5)
        self.assertEqual(entries[0]["matcher"], "startup")

    def test_tools_list_is_passed_through_in_the_order_given(self):
        cfg = json.loads(nousfx_sync.merge_settings(None, "/g/review-gate.py", False,
                                                    ("/h/nousfx-drift.py", "/r", ("claude", "agy")), prefix="me"))
        cmd = cfg["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, 'python3 -I -B "/h/nousfx-drift.py" --repo "/r" --tools claude,agy')

    def test_without_drift_arguments_only_the_gate_is_registered(self):
        cfg = json.loads(nousfx_sync.merge_settings(None, "/g/review-gate.py", False, prefix="me"))
        self.assertEqual(list(cfg["hooks"]), ["PreToolUse"])

    def test_second_sync_replaces_the_entry_and_keeps_other_session_hooks(self):
        write(self.settings, json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": "mine.sh"}]},
            {"hooks": [{"type": "command", "command": "python3 old/nousfx-drift.py --repo x"}]}]}}))
        self.sync()
        self.sync()
        cmds = [h["command"] for e in json.loads(read(self.settings))["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertEqual(cmds[0], "mine.sh")
        self.assertEqual(sum("nousfx-drift.py" in c for c in cmds), 1)
        self.assertIn(self.repo, cmds[-1])

    def test_a_hook_added_after_ours_keeps_its_place_and_check_stays_clean(self):
        self.sync()
        cfg = json.loads(read(self.settings))
        cfg["hooks"]["SessionStart"].append({"hooks": [{"type": "command", "command": "later.sh"}]})
        write(self.settings, json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        self.assertEqual(self.sync("--check").returncode, 0)  # in place: no false drift
        self.sync()
        cmds = [h["command"] for e in json.loads(read(self.settings))["hooks"]["SessionStart"] for h in e["hooks"]]
        self.assertEqual([("nousfx-drift.py" in c, c == "later.sh") for c in cmds], [(True, False), (False, True)])

    def test_windows_command_uses_python_and_powershell(self):
        self.sync("--platform", "windows")
        hook = self.drift_entries()[0]["hooks"][0]
        self.assertTrue(hook["command"].startswith('python -I -B "'), hook["command"])
        self.assertEqual(hook["shell"], "powershell")

    def test_drift_hook_is_not_copied_for_agy(self):
        self.sync("--tools", "claude,agy")
        for base, _, files in os.walk(os.path.join(self.home, ".gemini")):
            self.assertNotIn("nousfx-drift.py", files, base)

    def test_second_run_changes_nothing_and_check_is_clean(self):
        self.sync()
        before = self.snapshot()
        self.assertEqual(self.sync().returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_summary_is_one_json_line_with_counts(self):
        self.sync()
        write(self.rules, "hand edit\n")
        r = self.sync("--check", "--summary")
        self.assertEqual(r.returncode, 1, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, r.stdout)
        counts = json.loads(lines[0])
        self.assertEqual(counts["files"], 1)
        self.assertEqual(set(counts), {"files", "skills", "outside", "rules"})
        self.assertEqual(set(counts["rules"]), {"bytes", "budget", "headroom", "repeats", "shared"})

    def test_summary_shows_both_caps_and_the_headroom_under_each(self):
        """NFX-133: the sync cap and the shared-rules ratchet side by side, the ratchet read from one constant."""
        self.sync()
        rules = json.loads(self.sync("--check", "--summary").stdout.strip().splitlines()[-1])["rules"]
        self.assertEqual(rules["headroom"], rules["budget"] - rules["bytes"])
        shared = rules["shared"]
        self.assertEqual(shared["budget"], nousfx_sync.CLAUDE_RULES_BUDGET)
        self.assertEqual(shared["headroom"], shared["budget"] - shared["bytes"])
        want = sum(len(read(os.path.join(self.repo, rel)).encode("utf-8"))
                   for rel in ("harness/AGENTS.md", "harness/overlays/claude.overlay.md"))
        self.assertEqual(shared["bytes"], want)

    def test_summary_when_clean_exits_zero_with_zero_counts(self):
        self.sync()
        r = self.sync("--check", "--summary")
        self.assertEqual(r.returncode, 0, r.stderr)
        counts = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual({k: counts[k] for k in ("files", "skills", "outside")}, {"files": 0, "skills": 0, "outside": 0})
        self.assertEqual(counts["rules"]["repeats"], 0)
        self.assertLessEqual(counts["rules"]["bytes"], counts["rules"]["budget"])

    def test_summary_without_check_is_a_usage_error(self):
        self.assertEqual(self.sync("--summary").returncode, 2)


class TestfailRunningRegistration(Base):
    """NFX-271: sync registers the Edit/Write hook that warns while testfail.py run is testing the tree."""

    def entries(self):
        cfg = json.loads(read(self.settings))
        return [e for e in cfg["hooks"]["PreToolUse"] if any("testfail-running.py" in h["command"] for h in e["hooks"])]

    def test_testfail_running_hook_is_registered_and_copied(self):
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        entries = self.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["matcher"], "Edit|Write|NotebookEdit")
        script = os.path.join(self.home, ".claude", "hooks", "testfail-running.py")
        self.assertTrue(os.path.exists(script))
        self.assertEqual(entries[0]["hooks"][0]["command"], f'python3 -I -B "{script}"')
        self.assertEqual(entries[0]["hooks"][0]["timeout"], 5)
        self.assertIn("review-gate.py", json.loads(read(self.settings))["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_testfail_running_second_sync_is_idempotent_and_check_is_clean(self):
        self.sync()
        self.sync()
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_testfail_running_keeps_the_users_own_pretooluse_entries(self):
        mine = [{"matcher": "Bash", "hooks": [{"type": "command", "command": "first.sh"}]},
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "mine-edit.sh"}]}]
        write(self.settings, json.dumps({"hooks": {"PreToolUse": mine}}))
        self.sync()
        self.sync()
        pre = json.loads(read(self.settings))["hooks"]["PreToolUse"]
        self.assertEqual(pre[0], mine[0])
        self.assertIn(mine[1], pre)
        self.assertEqual(len(self.entries()), 1)

    def test_testfail_running_windows_command_uses_powershell(self):
        self.sync("--platform", "windows")
        hook = self.entries()[0]["hooks"][0]
        self.assertTrue(hook["command"].startswith('python -I -B "'), hook["command"])
        self.assertEqual(hook["shell"], "powershell")

    def test_testfail_running_hook_is_not_copied_for_agy(self):
        self.sync("--tools", "claude,agy")
        for base, _, files in os.walk(os.path.join(self.home, ".gemini")):
            self.assertNotIn("testfail-running.py", files, base)

    def test_testfail_running_is_off_without_the_keyword(self):
        cfg = json.loads(nousfx_sync.merge_settings(None, "/g/review-gate.py", False, prefix="me"))
        self.assertEqual(len(cfg["hooks"]["PreToolUse"]), 1)


class RulesBudget(Base):
    """NFX-78: the budget covers the whole generated rules, local layers included, and only warns."""
    RULE = "- Always run the full test suite before you push a branch."

    def run_sync(self, *args, env=None):
        return subprocess.run(
            [sys.executable, "-B", SYNC, "--home", self.home, "--state-home", self.state, "--repo", self.repo, *args],
            capture_output=True, text=True, cwd=self.tmp,
            env={**{k: v for k, v in os.environ.items() if k != "NOUSFX_RULES_BUDGET"}, **(env or {})})

    def budget_lines(self, r):
        return [l for l in r.stderr.splitlines() if l.startswith("warning: rules budget:")]

    def summary(self, env=None):
        r = self.run_sync("--check", "--summary", env=env)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def grow_local(self, size=7000):
        lines = [f"- Personal rule number {i} about my own habits." for i in range(size // 45)]
        write(os.path.join(self.state, "local.claude.md"), "# Mine\n" + "\n".join(lines) + "\n")

    def test_local_layers_over_budget_give_one_warning_and_keep_the_exit_code(self):
        self.grow_local()
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = self.budget_lines(r)
        self.assertEqual(len(lines), 1, r.stderr)
        body = len(read(self.rules).split(" -->\n\n", 1)[1].encode("utf-8"))
        self.assertGreater(body, nousfx_sync.RULES_BUDGET)
        self.assertIn(f"is {body} bytes", lines[0])
        self.assertIn(f"{nousfx_sync.RULES_BUDGET} byte budget", lines[0])
        self.assertEqual(self.run_sync("--check").returncode, 0)  # in step: no drift, warning or not
        rc, counts = self.summary()
        self.assertEqual(rc, 0)
        self.assertEqual({k: counts["rules"][k] for k in ("bytes", "budget", "repeats")},
                         {"bytes": body, "budget": nousfx_sync.RULES_BUDGET, "repeats": 0})
        self.assertEqual(counts["rules"]["headroom"], nousfx_sync.RULES_BUDGET - body)

    def test_within_budget_prints_no_warning(self):
        r = self.run_sync()
        self.assertEqual((r.returncode, self.budget_lines(r)), (0, []), r.stderr)

    def test_the_environment_variable_overrides_the_default(self):
        r = self.run_sync(env={"NOUSFX_RULES_BUDGET": "100"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.budget_lines(r)), 1, r.stderr)
        self.assertIn("100 byte budget", self.budget_lines(r)[0])
        self.assertEqual(self.summary({"NOUSFX_RULES_BUDGET": "100"})[1]["rules"]["budget"], 100)

    def test_a_line_in_local_md_overrides_the_default_and_stays_out_of_the_rules(self):
        local = os.path.join(self.state, "local.md")
        write(local, read(local) + "rules-budget: 120\n")
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("120 byte budget", self.budget_lines(r)[0])
        self.assertNotIn("rules-budget", read(self.rules))
        self.assertEqual(self.summary()[1]["rules"]["budget"], 120)
        # the environment wins over the file
        self.assertEqual(self.budget_lines(self.run_sync(env={"NOUSFX_RULES_BUDGET": "99999"})), [])

    def test_a_sentence_repeated_from_agents_md_is_listed_with_both_paths(self):
        agents = os.path.join(self.repo, "harness", "AGENTS.md")
        local = os.path.join(self.state, "local.md")
        write(agents, read(agents) + self.RULE + "\n")
        write(local, read(local) + self.RULE + "\n")
        r = self.run_sync()
        self.assertEqual(r.returncode, 0, r.stderr)
        repeats = [l for l in r.stderr.splitlines() if l.startswith("warning: repeated rule:")]
        self.assertEqual(len(repeats), 1, r.stderr)
        self.assertIn("always run the full test suite", repeats[0])
        self.assertIn(f"is in {local} and {agents}", repeats[0])
        rc, counts = self.summary()
        self.assertEqual(rc, 0)
        self.assertEqual((counts["files"], counts["skills"], counts["outside"]), (0, 0, 0))
        self.assertEqual((counts["rules"]["budget"], counts["rules"]["repeats"]), (nousfx_sync.RULES_BUDGET, 1))

    def test_a_repeat_in_local_md_counts_once_when_two_tools_load_it(self):
        write(os.path.join(self.repo, "harness", "overlays", "agy.overlay.md"), "## agy overlay\n- agy only line\n")
        write(os.path.join(self.repo, "harness", "agy", "agy_gate.py"),
              read(os.path.join(ROOT, "harness", "agy", "agy_gate.py")))
        agents = os.path.join(self.repo, "harness", "AGENTS.md")
        local = os.path.join(self.state, "local.md")
        write(agents, read(agents) + self.RULE + "\n")
        write(local, read(local) + self.RULE + "\n")
        r = self.run_sync("--dry-run", "--tools", "claude,agy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len([l for l in r.stderr.splitlines() if "repeated rule" in l]), 1, r.stderr)
        r = self.run_sync("--check", "--summary", "--tools", "claude,agy")
        self.assertEqual(json.loads(r.stdout)["rules"]["repeats"], 1)

    def test_a_budget_that_is_not_a_plain_number_is_ignored(self):
        r = self.run_sync(env={"NOUSFX_RULES_BUDGET": "\u00b2"})
        self.assertEqual((r.returncode, self.budget_lines(r)), (0, []), r.stderr)

    def test_the_budget_line_is_a_setting_only_in_local_md(self):
        write(os.path.join(self.state, "local.claude.md"), "rules-budget: 10\n- Mine.\n")
        r = self.run_sync()
        self.assertEqual(self.budget_lines(r), [], r.stderr)  # not read from local.claude.md
        self.assertIn("rules-budget: 10", read(self.rules))  # and so not hidden either

    def test_sources_under_home_are_written_from_tilde(self):
        state = os.path.join(self.home, ".config", "nousfx")
        shutil.copytree(self.state, state)
        r = subprocess.run([sys.executable, "-B", SYNC, "--home", self.home, "--repo", self.repo],
                           capture_output=True, text=True, cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        header = read(self.rules).split(" -->", 1)[0]
        self.assertIn("~/.config/nousfx/local.md", header)
        self.assertNotIn(state, header)

    def test_the_header_names_the_sources_and_its_hash_follows_their_text_only(self):
        self.run_sync()
        header = read(self.rules).split(" -->", 1)[0]
        for path in (os.path.join(self.repo, "harness", "AGENTS.md"), os.path.join(self.state, "local.md")):
            self.assertIn(path, header)
        first = header.splitlines()[0]
        local = os.path.join(self.state, "local.md")
        write(local, read(local) + "- One more personal line.\n")
        self.assertEqual(self.run_sync("--check").returncode, 1)
        self.run_sync()
        self.assertNotEqual(read(self.rules).splitlines()[0], first)
        self.assertEqual(self.run_sync("--check").returncode, 0)


class ProjectBehindOrigin(Base):
    """NFX-286: --project --check and --dry-run warn when the checkout is behind origin/<default>."""

    def setUp(self):
        super().setUp()
        write(os.path.join(self.repo, "claude", "githooks", "pre-push"),
              read(os.path.join(ROOT, "claude", "githooks", "pre-push")))
        self.git_env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, env=self.git_env)

    def make_project(self, branch="main", commits=3, behind=0):
        origin = os.path.join(self.tmp, f"origin-{branch}-{commits}-{behind}.git")
        project = os.path.join(self.tmp, f"project-{branch}-{commits}-{behind}")
        self.git(self.tmp, "init", "-q", "--bare", "-b", branch, origin)
        self.git(self.tmp, "init", "-q", "-b", branch, project)
        self.git(project, "remote", "add", "origin", origin)
        for i in range(commits):
            write(os.path.join(project, "file.txt"), f"commit {i}\n")
            self.git(project, "add", "file.txt")
            self.git(project, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"c{i}")
        self.git(project, "push", "-q", "-u", "origin", branch)
        self.git(project, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{branch}")
        if behind:
            self.git(project, "reset", "-q", "--hard", f"HEAD~{behind}")
        return project

    def test_behind_origin_warns_with_count_on_check_and_dry_run(self):
        project = self.make_project(commits=3, behind=2)
        r_check = self.sync("--project", project, "--check")
        self.assertEqual(r_check.returncode, 1, "an unsynced project still exits 1")
        lines = [l for l in r_check.stderr.splitlines() if "behind origin/main" in l]
        self.assertEqual(len(lines), 1, r_check.stderr)
        self.assertIn("2 commits behind origin/main", lines[0])

        r_dry = self.sync("--project", project, "--dry-run")
        self.assertEqual(r_dry.returncode, 0, r_dry.stderr)
        self.assertIn("2 commits behind origin/main", r_dry.stderr)

    def test_behind_origin_singular_commit_on_synced_project(self):
        project = self.make_project(commits=2, behind=1)
        self.assertEqual(self.sync("--project", project).returncode, 0)
        r = self.sync("--project", project, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 commit behind origin/main", r.stderr)
        self.assertNotIn("1 commits", r.stderr)

    def test_behind_origin_at_or_ahead_of_origin_prints_no_warning(self):
        level = self.make_project(commits=2, behind=0)
        ahead = self.make_project(commits=3, behind=0)
        write(os.path.join(ahead, "file.txt"), "local only\n")
        self.git(ahead, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "local")
        for project in (level, ahead):
            for flag, code in (("--check", 1), ("--dry-run", 0)):
                r = self.sync("--project", project, flag)
                self.assertEqual(r.returncode, code, r.stderr)
                self.assertNotIn("behind", r.stderr)

    def test_behind_origin_detached_head_prints_nothing_and_keeps_exit_code(self):
        project = self.make_project(commits=3, behind=2)
        self.git(project, "checkout", "-q", "--detach", "HEAD")
        r = self.sync("--project", project, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("behind", r.stderr)

    def test_behind_origin_no_origin_remote_prints_nothing_and_keeps_exit_code(self):
        project = self.make_project(commits=3, behind=2)
        self.git(project, "remote", "remove", "origin")
        self.assertEqual(self.sync("--project", project).returncode, 0)
        r = self.sync("--project", project, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("behind", r.stderr)

    def test_behind_origin_uses_origin_head_default_branch(self):
        project = self.make_project(branch="trunk", commits=3, behind=2)
        r = self.sync("--project", project, "--check")
        self.assertIn("2 commits behind origin/trunk", r.stderr)


class NousBoard(Base):
    """NFX-295: sync links the NousBoard board skill and CLI and registers its unread hook."""

    def setUp(self):
        super().setUp()
        self.clone = os.path.join(self.home, "Projects", "NousBoard")
        write(os.path.join(self.clone, "skills", "board", "SKILL.md"), "---\nname: board\n---\nboard skill\n")
        write(os.path.join(self.clone, "bin", "board.mjs"), "#!/usr/bin/env node\n")
        os.chmod(os.path.join(self.clone, "bin", "board.mjs"), 0o755)
        self.cli = os.path.join(self.home, ".local", "bin", "board")
        self.skill = os.path.join(self.home, ".claude", "skills", "board")
        self.cmd = f'"{self.cli}" hook'

    def board_hooks(self, event):
        cfg = json.loads(read(self.settings))
        return [h["command"] for e in cfg.get("hooks", {}).get(event, []) for h in e["hooks"]
                if "board" in h["command"]]

    def broken_links(self):
        return [os.path.join(b, n) for b, dirs, files in os.walk(self.home) for n in dirs + files
                if os.path.islink(os.path.join(b, n)) and not os.path.exists(os.path.join(b, n))]

    def test_sync_links_skill_and_cli_and_registers_the_hook(self):
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(read(os.path.join(self.skill, "SKILL.md")),
                         read(os.path.join(self.clone, "skills", "board", "SKILL.md")))
        self.assertEqual(os.path.realpath(self.cli), os.path.join(self.clone, "bin", "board.mjs"))
        for event in ("SessionStart", "Stop"):
            self.assertEqual(self.board_hooks(event), [self.cmd])
        entry = next(e for e in json.loads(read(self.settings))["hooks"]["Stop"]
                     if self.cmd in [h["command"] for h in e["hooks"]])
        self.assertEqual(entry["hooks"][0]["timeout"], 3)
        self.assertNotIn("matcher", entry, "fires on every session start, as install-local.sh registered it")

    def test_second_sync_changes_nothing_and_check_is_clean(self):
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        r = self.sync("--tools", "claude", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        before = self.snapshot()
        self.assertIn("nothing to change", self.sync("--tools", "claude").stdout)
        self.assertEqual(self.snapshot(), before)

    def test_missing_clone_is_skipped_with_a_note_and_no_broken_link(self):
        shutil.rmtree(self.clone)
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stderr.count("nousboard:"), 1, r.stderr)
        self.assertIn("nousboard: no clone at", r.stderr)
        self.assertFalse(os.path.lexists(self.skill))
        self.assertFalse(os.path.lexists(self.cli))
        self.assertEqual(self.board_hooks("SessionStart") + self.board_hooks("Stop"), [])
        self.assertNotIn("Stop", json.loads(read(self.settings))["hooks"], "no empty list left behind")
        self.assertEqual(self.broken_links(), [])
        self.assertEqual(self.sync("--tools", "claude", "--check").returncode, 0)

    def assert_removal_after_clone_goes(self):
        self.assertTrue(os.path.islink(self.cli) and os.path.islink(self.skill))
        self.assertEqual(self.board_hooks("Stop"), [self.cmd])
        shutil.rmtree(self.clone)
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nousboard: no clone at", r.stderr)
        self.assertFalse(os.path.lexists(self.skill))
        self.assertFalse(os.path.lexists(self.cli))
        self.assertEqual(self.board_hooks("SessionStart") + self.board_hooks("Stop"), [])
        self.assertEqual(self.broken_links(), [])
        self.assertEqual(self.sync("--tools", "claude", "--check").returncode, 0)

    def test_clone_removed_after_a_sync_removes_links_and_hook(self):
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        self.assert_removal_after_clone_goes()

    def test_an_agy_only_run_after_the_clone_goes_does_not_strand_the_hook(self):
        write(os.path.join(self.repo, "harness", "overlays", "agy.overlay.md"), "## agy overlay\n")
        write(os.path.join(self.repo, "harness", "agy", "agy_gate.py"),
              read(os.path.join(ROOT, "harness", "agy", "agy_gate.py")))
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        shutil.rmtree(self.clone)
        r = self.sync("--tools", "agy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.lexists(self.cli), "the agy run unlinks the board links and leaves settings.json")
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        self.assertEqual(self.board_hooks("SessionStart") + self.board_hooks("Stop"), [])
        self.assertEqual(self.broken_links(), [])

    def test_an_agy_run_never_reads_claude_settings(self):
        write(os.path.join(self.repo, "harness", "overlays", "agy.overlay.md"), "## agy overlay\n")
        write(os.path.join(self.repo, "harness", "agy", "agy_gate.py"),
              read(os.path.join(ROOT, "harness", "agy", "agy_gate.py")))
        self.install_local()
        os.chmod(self.settings, 0)
        self.addCleanup(os.chmod, self.settings, 0o600)
        r = self.sync("--tools", "agy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.islink(os.path.join(self.home, ".agents", "skills", "board")))

    def install_local(self):
        """What NousBoard's install-local.sh leaves: links straight to the clone and unquoted hook entries."""
        entry = {"hooks": [{"type": "command", "command": f"{self.cli} hook", "timeout": 3}]}
        write(self.settings, json.dumps({"hooks": {"SessionStart": [entry], "Stop": [entry]}}))
        os.makedirs(os.path.dirname(self.cli))
        os.symlink(os.path.join(self.clone, "bin", "board.mjs"), self.cli)
        os.makedirs(os.path.dirname(self.skill))
        os.symlink(os.path.join(self.clone, "skills", "board"), self.skill)
        return entry

    def test_install_local_links_are_adopted_so_a_removed_clone_leaves_nothing_broken(self):
        self.install_local()
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        self.assert_removal_after_clone_goes()

    def test_hook_entries_sync_never_wrote_stay_when_no_clone_is_found(self):
        entry = self.install_local()
        shutil.move(self.clone, os.path.join(self.tmp, "elsewhere"))
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(read(self.settings))["hooks"]["Stop"], [entry])

    def test_a_file_that_replaced_the_cli_link_loses_the_hook(self):
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        os.unlink(self.cli)
        write(self.cli, "something else\n")
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("hook entries are removed", r.stderr)
        self.assertEqual(self.board_hooks("SessionStart") + self.board_hooks("Stop"), [])
        self.assertEqual(read(self.cli), "something else\n")

    def test_install_local_entries_and_links_are_taken_over_not_duplicated(self):
        old = f"{self.cli} hook"
        mine = {"hooks": [{"type": "command", "command": "echo mine"}]}
        write(self.settings, json.dumps({"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": old, "timeout": 3}]}, mine],
            "Stop": [mine, {"hooks": [{"type": "command", "command": old, "timeout": 3}]}]}}))
        os.makedirs(os.path.dirname(self.cli))
        os.symlink(os.path.join(self.clone, "bin", "board.mjs"), self.cli)
        os.makedirs(os.path.dirname(self.skill))
        os.symlink(os.path.join(self.clone, "skills", "board"), self.skill)
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("collision", r.stdout + r.stderr)
        hooks = json.loads(read(self.settings))["hooks"]
        self.assertEqual([e["hooks"][0]["command"] for e in hooks["Stop"]], ["echo mine", self.cmd], "in place")
        self.assertEqual(self.board_hooks("SessionStart"), [self.cmd])
        self.assertEqual(self.sync("--tools", "claude", "--check").returncode, 0)

    def test_a_real_file_at_the_cli_path_is_a_collision_like_any_skill_folder(self):
        write(self.cli, "my own board\n")
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"pass --yes-replace board to replace): {self.cli}", r.stdout)
        self.assertEqual(read(self.cli), "my own board\n")
        self.assertFalse(os.path.lexists(self.skill), "a collision holds back every link of that name")
        self.assertEqual(self.board_hooks("SessionStart") + self.board_hooks("Stop"), [])
        self.assertIn("hook entries are left as they are", r.stderr)
        self.assertEqual(self.sync("--tools", "claude", "--yes-replace", "board").returncode, 0)
        self.assertEqual(os.path.realpath(self.cli), os.path.join(self.clone, "bin", "board.mjs"))

    def test_a_collision_keeps_existing_hook_entries_as_they_are(self):
        old = {"hooks": [{"type": "command", "command": f"{self.cli} hook", "timeout": 3}]}
        write(self.settings, json.dumps({"hooks": {"Stop": [old]}}))
        write(self.cli, "my own board\n")
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        hooks = json.loads(read(self.settings))["hooks"]
        self.assertEqual(hooks["Stop"], [old])
        self.assertEqual(self.board_hooks("SessionStart"), [])

    def test_a_board_mjs_that_is_not_executable_is_skipped_with_a_note(self):
        os.chmod(os.path.join(self.clone, "bin", "board.mjs"), 0o644)
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("is not executable", r.stderr)
        self.assertFalse(os.path.lexists(self.cli))
        self.assertEqual(self.board_hooks("SessionStart") + self.board_hooks("Stop"), [])

    def test_a_path_with_a_quote_is_refused_before_anything_is_written(self):
        write(os.path.join(self.state, "local.toml"), '[nousboard]\npath = "/tmp/a\\"b"\n')
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("may not hold a quote", r.stderr)
        self.assertFalse(os.path.exists(self.settings))

    def test_a_dollar_in_the_path_is_refused_only_on_windows(self):
        write(os.path.join(self.state, "local.toml"), '[nousboard]\npath = "~/Projects/$Board"\n')
        with self.assertRaises(nousfx_sync.SyncError):
            nousfx_board.locate(self.home, self.state, nousfx_sync.SyncError, win=True)
        clone, note = nousfx_board.locate(self.home, self.state, nousfx_sync.SyncError, win=False)
        self.assertIn("no clone at", note)

    def test_a_windows_home_path_is_expanded(self):
        write(os.path.join(self.state, "local.toml"), '[nousboard]\npath = "~\\\\Projects\\\\NousBoard"\n')
        clone, note = nousfx_board.locate(self.home, self.state, nousfx_sync.SyncError, win=True)
        self.assertIn(f"no clone at {self.home}", note, "expanded under home, not refused as relative")

    def test_local_toml_path_overrides_the_default(self):
        moved = os.path.join(self.tmp, "elsewhere", "NousBoard")
        shutil.move(self.clone, moved)
        write(os.path.join(self.state, "local.toml"), f'[nousboard]\npath = "{moved}"\n')
        r = self.sync("--tools", "claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.path.realpath(self.cli), os.path.join(moved, "bin", "board.mjs"))
        self.assertNotIn("nousboard:", r.stderr)

    def test_restore_undoes_the_board_links_and_hook(self):
        write(self.settings, "{}\n")
        self.assertEqual(self.sync("--tools", "claude").returncode, 0)
        r = self.sync("--restore")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.lexists(self.cli))
        self.assertFalse(os.path.lexists(self.skill))
        self.assertEqual(read(self.settings), "{}\n")

    def test_windows_hook_runs_node_and_makes_no_cli_link(self):
        cfg = json.loads(nousfx_sync.merge_settings(None, "/g/review-gate.py", True, prefix="me",
                                                    board='node "C:/c/NousBoard/bin/board.mjs" hook'))
        self.assertEqual(cfg["hooks"]["Stop"][0]["hooks"][0]["command"], 'node "C:/c/NousBoard/bin/board.mjs" hook')
        self.assertEqual(nousfx_board.hook_command(self.home, self.clone, True, lambda p: p),
                         f'node "{os.path.join(self.clone, "bin", "board.mjs")}" hook')
        self.assertIsNone(nousfx_board.cli_link(self.home, self.clone, True))

    def test_merge_settings_removes_board_entries_and_leaves_others(self):
        current = json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/x/.local/bin/board hook"}]},
                                                 {"hooks": [{"type": "command", "command": "echo mine"}]}]}})
        cfg = json.loads(nousfx_sync.merge_settings(current, "/g/review-gate.py", False, prefix="me", board=False))
        self.assertEqual(cfg["hooks"]["Stop"], [{"hooks": [{"type": "command", "command": "echo mine"}]}])
        untouched = json.loads(nousfx_sync.merge_settings(current, "/g/review-gate.py", False, prefix="me"))
        self.assertEqual(len(untouched["hooks"]["Stop"]), 2, "board=None leaves the entries alone")


if __name__ == "__main__":
    unittest.main()
