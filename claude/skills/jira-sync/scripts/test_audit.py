#!/usr/bin/env python3
"""Tests for audit.py. Run: python3 -I ~/.claude/skills/jira-sync/scripts/test_audit.py"""
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit import audit, commit_keys, next_up, repo_evidence

READY = ("## Goal\nx\n## Scope\nx\n## Acceptance criteria\nx\n## Environment\nx\n"
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
        import shutil
        import subprocess
        import tempfile
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
