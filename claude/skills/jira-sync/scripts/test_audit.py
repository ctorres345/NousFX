#!/usr/bin/env python3
"""Tests for audit.py. Run: python3 -I ~/.claude/skills/jira-sync/scripts/test_audit.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit import audit, commit_keys, missing_proof_paths, next_up, repo_evidence

READY = ("## Goal\nx\n## Scope\nx\n## Acceptance criteria\n- [ ] x works. Proof: `true`\n"
         "- [ ] x on device. Proof: manual: open the app\n## Failure modes\nx\n## Environment\nx\n"
         "## Review\nx\n## Human gates\nNone\n## Blocked by\nNone\n")


def issue(key, cat="new", labels=("env-none",), blocked_by=(), text="", kind="Task", summary="work", resolution=None):
    links = [{"type": {"name": "Blocks"}, "inwardIssue": {"key": k}} for k in blocked_by]
    return key, {"key": key, "fields": {
        "summary": summary, "description": text, "labels": list(labels), "issuelinks": links,
        "issuetype": {"name": kind}, "resolution": {"name": resolution} if resolution else None,
        "status": {"name": cat, "statusCategory": {"key": cat}}}}


def evidence(**kw):
    base = {"commits": {}, "branches": {}, "prs": {}, "gaps": [], "mentioned": {}}
    base.update(kw)
    return base


def pr(state, by_branch=True):
    return {"state": state, "url": "u", "by_branch": by_branch, "number": 1}


def ids(issues, ev=None):
    return {f["id"] for f in audit("DEM", dict(issues), ev or evidence())}


class Checks(unittest.TestCase):
    def test_clean_ticket_has_no_findings(self):
        self.assertEqual(ids([issue("DEM-1")]), set())

    def test_env_label_count(self):
        self.assertIn("env-label:DEM-1", ids([issue("DEM-1", labels=())]))
        self.assertIn("env-label:DEM-1", ids([issue("DEM-1", labels=("env-none", "env-device"))]))
        self.assertNotIn("env-label:DEM-1", ids([issue("DEM-1", labels=(), kind="Epic")]))

    def test_blocker_states(self):
        blocker = issue("DEM-1")
        self.assertIn("done-open-blocker:DEM-2", ids([blocker, issue("DEM-2", "done", blocked_by=["DEM-1"])]))
        self.assertIn("started-open-blocker:DEM-2", ids([blocker, issue("DEM-2", "indeterminate", blocked_by=["DEM-1"])]))
        self.assertEqual(ids([issue("DEM-1", "done"), issue("DEM-2", blocked_by=["DEM-1"])]), set())

    def test_dropped_blocker(self):
        self.assertIn("dropped-blocker:DEM-2",
                      ids([issue("DEM-1", "done", resolution="Won't Do"), issue("DEM-2", blocked_by=["DEM-1"])]))

    def test_cycle(self):
        found = ids([issue("DEM-1", blocked_by=["DEM-2"]), issue("DEM-2", blocked_by=["DEM-1"])])
        self.assertTrue(any(i.startswith("cycle:") for i in found))

    def test_repo_evidence(self):
        self.assertIn("merged-open:DEM-1", ids([issue("DEM-1")], evidence(prs={"DEM-1": [pr("MERGED")]})))
        self.assertIn("merged-open:DEM-1", ids([issue("DEM-1")], evidence(commits={"DEM-1": ["abc"]})))
        self.assertIn("open-pr-todo:DEM-1", ids([issue("DEM-1")], evidence(prs={"DEM-1": [pr("OPEN")]})))
        self.assertIn("branch-no-pr:DEM-1", ids([issue("DEM-1")], evidence(branches={"DEM-1": "origin/cesar/DEM-1"})))
        self.assertIn("unknown-key:DEM-9", ids([issue("DEM-1")], evidence(mentioned={"DEM-9": ["commit a"]})))
        self.assertIn("agent-pr-stale:DEM-1",
                      ids([issue("DEM-1", labels=("env-none", "agent-pr"))], evidence(prs={"DEM-1": [pr("MERGED")]})))

    def test_ready_check(self):
        ready = ("env-none", "ready-for-agent")
        self.assertEqual(ids([issue("DEM-1", labels=ready, text=READY)]), set())
        self.assertIn("ready-check:DEM-1", ids([issue("DEM-1", labels=ready, text="## Goal\nx\n")]))
        gated = READY.replace("Human gates\nNone", "Human gates\nCesar signs off")
        self.assertIn("ready-check:DEM-1", ids([issue("DEM-1", labels=ready, text=gated)]))

    def test_ready_check_needs_proofs_and_failure_modes(self):
        ready = ("env-none", "ready-for-agent")
        no_modes = READY.replace("## Failure modes\nx\n", "")
        self.assertIn("ready-check:DEM-1", ids([issue("DEM-1", labels=ready, text=no_modes)]))
        no_proof = READY.replace(". Proof: `true`", "")
        found = audit("DEM", dict([issue("DEM-1", labels=ready, text=no_proof)]), evidence())
        self.assertIn("without Proof:", " ".join(f["evidence"] for f in found if f["id"] == "ready-check:DEM-1"))
        empty = READY.replace("Proof: `true`", "Proof: ")
        self.assertIn("ready-check:DEM-1", ids([issue("DEM-1", labels=ready, text=empty)]))
        via = READY + "## Visual spec (Via)\n### Acceptance criteria\n- [ ] Matches the target mock\n"
        self.assertEqual(ids([issue("DEM-1", labels=ready, text=via)]), set())
        no_ac = READY.replace("- [ ] x works. Proof: `true`\n- [ ] x on device. Proof: manual: open the app\n", "x\n")
        self.assertIn("ready-check:DEM-1", ids([issue("DEM-1", labels=ready, text=no_ac)]))

    def test_text_dependency(self):
        linked = issue("DEM-2", text="Depends on DEM-1", blocked_by=["DEM-1"])
        self.assertEqual(ids([issue("DEM-1"), linked]), set())
        self.assertIn("unlinked-dependency:DEM-2<-DEM-1", ids([issue("DEM-1"), issue("DEM-2", text="Depends on DEM-1")]))
        self.assertEqual(ids([issue("DEM-1"), issue("DEM-2", text="Related to DEM-1")]), set())

    def test_ui_checks(self):
        found = ids([issue("DEM-1", summary="Redesign the screen")])
        self.assertLessEqual({"env-none-ui:DEM-1", "ui-no-evidence:DEM-1"}, found)


class BranchEvidence(unittest.TestCase):
    """NFX-119: any <prefix>/<KEY> branch counts, -shots does not, and the finding names the real branch."""

    def test_any_prefix_counts_and_is_printed(self):
        repo = tempfile.mkdtemp(prefix="audit-branch-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

        def git(*args):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                           capture_output=True, env=env)
        git("init", "-q", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "init")
        for ref in ("main", "ana/DEM-3", "ana/DEM-4-shots", "Bob/DEM-5", "fix-DEM-6"):
            git("update-ref", f"refs/remotes/origin/{ref}", "HEAD")
        git("update-ref", "refs/remotes/upstream/DEM-7", "HEAD")  # another remote: its name is no prefix
        ev = repo_evidence("DEM", repo)
        self.assertEqual(ev["branches"], {"DEM-3": "origin/ana/DEM-3"})
        findings = audit("DEM", dict([issue("DEM-3"), issue("DEM-4")]), ev)
        branch = [f for f in findings if f["id"] == "branch-no-pr:DEM-3"]
        self.assertEqual(len(branch), 1, findings)
        self.assertEqual(branch[0]["evidence"], "origin/ana/DEM-3")
        self.assertNotIn("branch-no-pr:DEM-4", {f["id"] for f in findings})


class CommitKeys(unittest.TestCase):
    def test_prefix_and_closing_keys_count(self):
        self.assertEqual(commit_keys("DEM", "DEM-1: Fix it"), {"DEM-1"})
        self.assertEqual(commit_keys("YUY", "Add sync (YUY-2, level B)"), {"YUY-2"})
        self.assertEqual(commit_keys("YUY", "Add wizard (YUY-2, YUY-3)"), {"YUY-2", "YUY-3"})

    def test_passing_mention_does_not_count(self):
        self.assertEqual(commit_keys("DEM", "Follow up on DEM-1 notes"), set())


class ProofPaths(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        os.makedirs(os.path.join(self.repo, "claude", "hooks"))
        open(os.path.join(self.repo, "claude", "hooks", "test_real.py"), "w").close()

    def found(self, proof, labels=("env-none", "ready-for-agent")):
        text = READY.replace("Proof: `true`", "Proof: " + proof)
        findings = audit("DEM", dict([issue("DEM-1", labels=labels, text=text)]), evidence(repo=self.repo))
        return [f for f in findings if f["id"] == "proof-path:DEM-1"], findings

    def test_missing_path_is_flagged(self):
        flagged, _ = self.found("`python3 -I claude/hooks/test_gone.py`")
        self.assertEqual(len(flagged), 1)
        self.assertIn("claude/hooks/test_gone.py", flagged[0]["evidence"])

    def test_missing_path_with_pytest_suffix_is_flagged(self):
        flagged, _ = self.found("`pytest claude/hooks/test_gone.py::TestX`")
        self.assertEqual(len(flagged), 1)

    def test_existing_path_passes(self):
        # The same command with a missing sibling is flagged, so an empty result shows the check ran.
        gone = "claude/hooks/test_gone.py"
        for command in ("python3 -I {}", "pytest {}::TestX", "ls {}"):
            real = command.format("claude/hooks/test_real.py")
            self.assertEqual(self.found(f"`{real}`")[0], [], real)
            self.assertEqual(len(self.found(f"`{command.format(gone)}`")[0]), 1, command)
        self.assertEqual(self.found("`ls claude/hooks`")[0], [])

    def test_manual_and_pathless_commands_pass(self):
        gone = "claude/hooks/test_gone.py"
        self.assertEqual(len(self.found(f"`python3 {gone}`")[0]), 1)
        for proof in (f"manual: open {gone}", f"manual: run `python3 {gone}`", "`true`", "`python3 -B -m unittest`"):
            self.assertEqual(self.found(proof)[0], [], proof)

    def test_flags_globs_urls_and_absolute_paths_pass(self):
        for proof in ("`x --out=claude/gone/a.txt`", "`x -f`", "`python3 plugins/*/test_*.py`",
                      "`curl http://host/a/b.json`", "`cat /etc/hosts`", "`cat ~/a/b.txt`", "`cat $HOME/a/b.txt`"):
            self.assertEqual(self.found(proof)[0], [], proof)

    def test_refs_modules_redirects_and_api_paths_pass(self):
        for proof in ("`git diff origin/main...HEAD`", "`python3 -m unittest scripts.test_audit`",
                      "`grep -q foo claude/hooks/test_real.py 2>/dev/null`", "`python3 claude/hooks/test_real.py > out/log.txt`",
                      "`python3 claude/hooks/test_real.py >out/log.txt`", "`gh api repos/o/r/pulls`", "`cat ../outside.txt`"):
            self.assertEqual(self.found(proof)[0], [], proof)

    def test_quoted_args_fd_redirects_cd_and_git_paths_pass(self):
        real = "claude/hooks/test_real.py"
        for proof in ('`bash -c "python3 claude/gone.py"`', f"`python3 {real} 2> err.txt`", f"`python3 {real} 2>> log.txt`",
                      f"`python3 {real} &> out.txt`", "`cd claude/hooks && python3 -B test_gone.py`",
                      "`git show HEAD:claude/hooks/x.py`", "`git show origin/main:claude/hooks/x.py`"):
            self.assertEqual(self.found(proof)[0], [], proof)

    def test_unspaced_shell_operators_do_not_hide_a_path(self):
        for proof in ("`python3 claude/hooks/test_gone.py|tee log`", "`python3 claude/hooks/test_gone.py;echo ok`",
                      "`python3 claude/hooks/test_gone.py&&echo ok`"):
            self.assertEqual(len(self.found(proof)[0]), 1, proof)
        for proof in ("test checks `response.json()` is empty", "`el.html()`"):
            self.assertEqual(self.found(proof)[0], [], proof)
        self.assertEqual(self.found("`python3 claude/hooks/test_real.py|tee log`")[0], [])

    def test_a_span_that_is_only_a_path_is_checked(self):
        for proof in ("`scripts/gone.sh`", "`./scripts/gone.sh`", "new test in `claude/hooks/test_gone.py`",
                      "`true` and see `docs/gone.md`"):
            self.assertEqual(len(self.found(proof)[0]), 1, proof)
        self.assertEqual(self.found("new test in `claude/hooks/test_real.py`")[0], [])

    def test_emphasised_labels_and_directory_flags(self):
        gone = "python3 claude/hooks/test_gone.py"
        self.assertEqual(self.found(f"manual: run `{gone}`")[0], [])
        item = f"- [ ] x. **Proof:** manual: run `{gone}`"
        self.assertEqual(missing_proof_paths([item], self.repo), [])
        self.assertEqual(missing_proof_paths([f"- [ ] x. _Proof:_ `{gone}`"], self.repo), ["claude/hooks/test_gone.py"])
        self.assertEqual(self.found("`git -C claude/hooks ls-files test_x.py`")[0], [])
        self.assertEqual(self.found("`npm --prefix web run t.js`")[0], [])

    def test_quoted_proof_label_in_the_prose_does_not_hide_the_command(self):
        item = "- [ ] A ticket with no `Proof:` line is flagged. Proof: `python3 claude/hooks/test_gone.py`"
        self.assertEqual(missing_proof_paths([item], self.repo), ["claude/hooks/test_gone.py"])
        self.assertEqual(missing_proof_paths(["- [ ] x. proof: `python3 claude/hooks/test_gone.py`"], self.repo),
                         ["claude/hooks/test_gone.py"])

    def test_warning_never_blocks_the_ticket(self):
        flagged, findings = self.found("`python3 claude/hooks/test_gone.py`")
        self.assertEqual(len(flagged), 1)
        self.assertNotIn("ready-check:DEM-1", {f["id"] for f in findings})

    def test_warns_on_ready_for_agent_only_and_not_on_done_tickets(self):
        self.assertEqual(len(self.found("`python3 claude/hooks/test_gone.py`")[0]), 1)
        self.assertEqual(self.found("`python3 claude/hooks/test_gone.py`", labels=("env-none",))[0], [])  # a draft may name a file it will create
        text = READY.replace("Proof: `true`", "Proof: `python3 claude/hooks/test_gone.py`")
        ready = ("env-none", "ready-for-agent")
        open_ticket = audit("DEM", dict([issue("DEM-1", labels=ready, text=text)]), evidence(repo=self.repo))
        self.assertIn("proof-path:DEM-1", {f["id"] for f in open_ticket})  # control: only the status differs below
        done = audit("DEM", dict([issue("DEM-1", cat="done", labels=ready, text=text)]), evidence(repo=self.repo))
        self.assertNotIn("proof-path:DEM-1", {f["id"] for f in done})

    def test_no_repo_skips_the_check(self):
        text = READY.replace("Proof: `true`", "Proof: `python3 claude/hooks/test_gone.py`")
        ready = ("env-none", "ready-for-agent")
        self.assertIn("proof-path:DEM-1", ids([issue("DEM-1", labels=ready, text=text)], evidence(repo=self.repo)))  # control
        self.assertNotIn("proof-path:DEM-1", ids([issue("DEM-1", labels=ready, text=text)]))

    def test_directory_flag_with_equals_skips_the_command(self):
        self.assertEqual(self.found("`node --prefix=web src/gone.js`")[0], [])


class NextUp(unittest.TestCase):
    def test_grouping(self):
        issues = dict([
            issue("DEM-1", labels=("env-none", "ready-for-agent")),
            issue("DEM-2", labels=("env-none",)),
            issue("DEM-3", labels=("env-emulator",)),
            issue("DEM-4", labels=("env-device",)),
            issue("DEM-5", blocked_by=["DEM-1"]),
            issue("DEM-6", labels=("env-none", "ready-for-agent", "fable-review")),
            issue("DEM-7"),
        ])
        groups = next_up(issues, evidence(branches={"DEM-7": "origin/cesar/DEM-7"}), [])
        self.assertEqual([e[:5] for e in groups["Queue or cloud now"]], ["DEM-1"])
        self.assertEqual([e[:5] for e in groups["Queue or cloud once ready-for-agent"]], ["DEM-2", "DEM-6"])
        self.assertEqual([e[:5] for e in groups["Mac session"]], ["DEM-3"])
        self.assertEqual([e[:5] for e in groups["Cesar"]], ["DEM-4"])

    def test_failed_ready_check_is_not_queued(self):
        issues = dict([issue("DEM-1", labels=("env-none", "ready-for-agent"))])
        groups = next_up(issues, evidence(), [{"id": "ready-check:DEM-1"}])
        self.assertEqual(groups["Queue or cloud now"], [])
        self.assertEqual(len(groups["Queue or cloud once ready-for-agent"]), 1)


if __name__ == "__main__":
    unittest.main()
