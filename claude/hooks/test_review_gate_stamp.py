#!/usr/bin/env python3
"""Tests for the check pass stamp (NFX-97): `run-check --stamp` runs the check once, and `--mark` on the same
tree, branch and command does not run it again.
Run: python3 -B claude/hooks/test_review_gate_stamp.py

The check command appends a line to a counter file outside the repo, so the tests can count runs without
changing the tree. A `fail` file next to it makes the command fail.
"""
import hashlib
import hmac
import json
import os
import stat
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_gate import ALLOWED, BLOCKED, GATE, Base  # noqa: E402

STAMP, KEY = "claude-review-check-pass", "claude-review-stamp-key"


class Stamp(Base):
    def setUp(self):
        super().setUp()
        self.runs = os.path.join(self.tmp, "runs").replace("\\", "/")
        self.fail = os.path.join(self.tmp, "fail").replace("\\", "/")
        self.write_check(self.repo, f'echo run >> "{self.runs}"; test ! -f "{self.fail}"')
        self.commit(self.repo)
        self.set_base(self.repo)  # the trusted command comes from the default branch, as in real use

    def stamp(self, cwd=None):
        return subprocess.run([sys.executable, GATE, "run-check", "--stamp"], cwd=cwd or self.repo,
                              capture_output=True, text=True)

    def count(self):
        if not os.path.exists(self.runs):
            return 0
        with open(self.runs, encoding="utf-8") as f:
            return len(f.read().split())

    def git_file(self, name):
        return os.path.join(self.repo, ".git", name)

    def failing(self, on=True):
        if on:
            open(self.fail, "w").close()
        elif os.path.exists(self.fail):
            os.remove(self.fail)

    def edit(self):
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")

    # The point of the stamp.

    def test_mark_after_a_stamp_does_not_run_the_check_again(self):
        r = self.stamp()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Check passed.", r.stdout)
        self.assertEqual(self.count(), 1)
        m = self.mark("--findings", "0")
        self.assertEqual(m.returncode, 0, m.stderr)
        self.assertEqual(self.count(), 1)
        self.assertIn("not run again", m.stdout)
        self.assertEqual(self.run_gate("gh pr create"), ALLOWED)

    def test_the_mark_records_a_passing_check_from_the_stamp(self):
        self.stamp()
        self.mark("--findings", "0")
        with open(self.git_file("claude-review-ok"), encoding="utf-8") as f:
            check = json.load(f)["check"]
        self.assertEqual(check["exit"], 0)
        self.assertIn("echo run", check["command"])
        self.assertEqual(check["source"], "refs/remotes/origin/main")

    def test_a_stamp_is_used_once(self):
        self.stamp()
        self.mark("--findings", "0")
        self.assertFalse(os.path.exists(self.git_file(STAMP)))
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 2)

    def test_a_mark_refused_before_the_check_keeps_the_stamp(self):
        self.stamp()
        self.assertEqual(self.mark("--findings", "0", "--tier", "9").returncode, BLOCKED)
        self.assertTrue(os.path.exists(self.git_file(STAMP)))
        self.assertEqual(self.mark("--findings", "0").returncode, 0)
        self.assertEqual(self.count(), 1)

    def test_stamp_and_ref_together_are_refused(self):
        r = subprocess.run([sys.executable, GATE, "run-check", "--stamp", "--ref", "refs/remotes/origin/main"],
                           cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, BLOCKED)
        self.assertEqual(self.count(), 0)

    def test_run_check_without_ref_or_stamp_is_a_usage_error(self):
        r = subprocess.run([sys.executable, GATE, "run-check"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("usage", r.stderr)

    # Anything that changed since the stamp forces a run.

    def test_a_changed_file_forces_a_run(self):
        self.stamp()
        self.edit()
        self.assertEqual(self.mark("--findings", "0").returncode, 0)
        self.assertEqual(self.count(), 2)

    def test_a_new_untracked_file_forces_a_run(self):
        self.stamp()
        with open(os.path.join(self.repo, "new.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 2)

    def test_another_branch_forces_a_run(self):
        self.stamp()
        self.sh("git", "checkout", "-q", "-b", "cesar/DEM-2", cwd=self.repo)  # same tree
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 2)

    def test_a_changed_default_branch_command_forces_a_run(self):
        self.stamp()
        self.write_check(self.repo, f'echo run >> "{self.runs}"; echo again >> "{self.runs}"')
        self.commit(self.repo)
        self.set_base(self.repo)
        self.sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=self.repo)  # back to the stamped tree
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 3)  # one from the stamp, two lines from the new command

    def test_a_changed_default_branch_timeout_forces_a_run(self):
        self.stamp()
        with open(os.path.join(self.repo, ".nousfx", "project.toml"), "a", encoding="utf-8") as f:
            f.write("timeout = 600\n")
        self.commit(self.repo)
        self.set_base(self.repo)
        self.sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=self.repo)  # back to the stamped tree
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 2)

    def test_a_failing_run_after_the_stamp_still_refuses(self):
        self.stamp()
        self.edit()
        self.failing()
        self.assertEqual(self.mark("--findings", "0").returncode, BLOCKED)
        self.assertEqual(self.run_gate("gh pr create"), BLOCKED)

    # Forgery.

    def stamp_fields(self):
        tree = subprocess.run(["git", "write-tree"], cwd=self.repo, capture_output=True, text=True).stdout.strip()
        with open(os.path.join(self.repo, ".nousfx", "project.toml"), encoding="utf-8") as f:
            command = f.read().split("command = ", 1)[1].splitlines()[0]
        return {"tree": tree, "branch": "cesar/DEM-1", "command": json.loads(command),
                "source": "refs/remotes/origin/main", "timeout": 1800, "exit": 0, "time": "2026-11-07T00:00:00"}

    def test_a_written_stamp_without_the_key_is_ignored(self):
        fields = self.stamp_fields()
        fields["mac"] = "0" * 64
        with open(self.git_file(STAMP), "w", encoding="utf-8") as f:
            json.dump(fields, f)
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 1)

    def test_a_stamp_with_an_edited_tree_is_ignored(self):
        self.stamp()
        self.edit()
        with open(self.git_file(STAMP), encoding="utf-8") as f:
            stamp = json.load(f)
        self.sh("git", "add", "-A", cwd=self.repo)  # so write-tree sees the edit
        edited = self.stamp_fields()["tree"]
        self.assertNotEqual(edited, stamp["tree"])
        stamp["tree"] = edited  # point the signed stamp at the edited tree
        with open(self.git_file(STAMP), "w", encoding="utf-8") as f:
            json.dump(stamp, f)
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 2)

    def test_a_broken_stamp_file_forces_a_run(self):
        with open(self.git_file(STAMP), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(self.mark("--findings", "0").returncode, 0)
        self.assertEqual(self.count(), 1)

    def test_the_mac_covers_every_field(self):
        """Control: a stamp signed with the real key is accepted, so the tests above fail for the right reason.
        The MAC is rebuilt here on purpose, so the test pins the canonical form instead of trusting the gate's."""
        self.stamp()
        with open(self.git_file(KEY), "rb") as f:
            key = f.read()
        fields = self.stamp_fields()
        body = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
        fields["mac"] = hmac.new(key, body, hashlib.sha256).hexdigest()
        with open(self.git_file(STAMP), "w", encoding="utf-8") as f:
            json.dump(fields, f)
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 1)

    @unittest.skipUnless(os.name == "posix", "file modes are POSIX")
    def test_the_key_is_private(self):
        self.stamp()
        self.assertEqual(stat.S_IMODE(os.stat(self.git_file(KEY)).st_mode), 0o600)

    # A stamp run that does not pass leaves no stamp.

    def test_a_failing_stamp_run_leaves_no_stamp(self):
        self.failing()
        r = self.stamp()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertFalse(os.path.exists(self.git_file(STAMP)))

    def test_a_failing_stamp_run_removes_an_older_stamp(self):
        self.stamp()
        self.failing()
        self.assertEqual(self.stamp().returncode, BLOCKED)
        self.assertFalse(os.path.exists(self.git_file(STAMP)))

    def test_a_timed_out_stamp_run_leaves_no_stamp(self):
        self.write_check(self.repo, "%s -c 'import time; time.sleep(30)'" % sys.executable.replace("\\", "/"),
                         timeout=1)
        self.commit(self.repo)
        self.set_base(self.repo)
        r = self.stamp()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("did not finish", r.stderr)
        self.assertFalse(os.path.exists(self.git_file(STAMP)))

    def test_a_file_editing_stamp_run_leaves_no_stamp(self):
        self.write_check(self.repo, "echo more >> f.txt")
        self.commit(self.repo)
        self.set_base(self.repo)
        r = self.stamp()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("changed files", r.stderr)
        self.assertFalse(os.path.exists(self.git_file(STAMP)))

    def test_a_check_that_writes_a_stamp_and_fails_leaves_none(self):
        self.write_check(self.repo, f'echo forged > .git/{STAMP}; exit 1')
        self.commit(self.repo)
        self.set_base(self.repo)
        self.assertEqual(self.stamp().returncode, BLOCKED)
        self.assertFalse(os.path.exists(self.git_file(STAMP)))

    def test_a_failing_check_cannot_plant_a_stamp_for_the_next_mark(self):
        """The check is branch code: it can write its own key and a matching stamp, then fail (architect, NFX-97)."""
        py = sys.executable.replace("\\", "/")
        plant = ("import hashlib,hmac,json,subprocess,time; "
                 "t=subprocess.check_output(['git','write-tree']).decode().strip(); "
                 "k=b'k'*64; open('.git/%s','wb').write(k); "
                 "f={'tree':t,'branch':'cesar/DEM-1','command':open('.git/cmd').read(),"
                 "'source':'refs/remotes/origin/main','timeout':1800,'exit':0,'time':'x'}; "
                 "f['mac']=hmac.new(k,json.dumps(f,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest(); "
                 "json.dump(f,open('.git/%s','w'))" % (KEY, STAMP))
        command = f'echo run >> "{self.runs}"; {py} -c "{plant}"; exit 1'
        self.write_check(self.repo, command)
        self.commit(self.repo)
        self.set_base(self.repo)
        with open(self.git_file("cmd"), "w", encoding="utf-8") as f:
            f.write(command)
        self.assertEqual(self.mark("--findings", "0").returncode, BLOCKED)
        self.assertFalse(os.path.exists(self.git_file(STAMP)))
        self.assertEqual(self.mark("--findings", "0").returncode, BLOCKED)
        self.assertEqual(self.count(), 2)

    def test_a_key_replaced_by_the_check_does_not_sign_the_stamp(self):
        self.write_check(self.repo, f'echo run >> "{self.runs}"; printf short > .git/{KEY}')
        self.commit(self.repo)
        self.set_base(self.repo)
        r = self.stamp()  # reads the key before the run; the command shortens it during the run
        self.assertNotIn("Traceback", r.stderr)
        self.mark("--findings", "0")
        self.assertEqual(self.count(), 2)  # the stamp does not verify against the replaced key, so --mark ran

    def test_a_stamp_run_on_a_detached_head_is_refused(self):
        self.sh("git", "checkout", "-q", "--detach", cwd=self.repo)
        r = self.stamp()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("detached", r.stderr)
        self.assertEqual(self.count(), 0)

    def test_the_branch_cannot_weaken_the_stamped_command(self):
        self.failing()
        self.write_check(self.repo, "exit 0")  # working tree only; the default branch still runs the counter
        r = self.stamp()
        self.assertEqual(r.returncode, BLOCKED)
        self.assertFalse(os.path.exists(self.git_file(STAMP)))


if __name__ == "__main__":
    unittest.main()
