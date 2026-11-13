#!/usr/bin/env python3
"""Tests for the CI skip-label check (NFX-110): a `skipped:` attestation passes only when the `review-skip` label
is on the PR and the last person who applied it is the repo owner or an org member.
Run: python3 -B -I scripts/test_review_gate_workflow.py

The block between the NFX-110 markers in review-gate.yml runs under bash with a stub `gh` on PATH.
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "review-gate.yml")
START = "          # NFX-110 skip label check\n"
END = "          # end NFX-110\n"


def read_workflow():
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


def block():
    """The skip-label script, dedented. It reads $out (the verify-attestation output) and the env set by the step."""
    text = read_workflow()
    body = text.split(START)[1].split(END)[0]
    return "\n".join(line[10:] for line in body.splitlines()) + "\n"


def event(actor, actor_type="User"):
    """One line of what the block's `gh api --jq` prints for a labeled review-skip event: login TAB type."""
    return "%s\t%s" % (actor, actor_type)


class SkipLabel(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="gate-ci-skip-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.calls = os.path.join(self.tmp, "gh-calls")
        self.script = os.path.join(self.tmp, "block.sh")
        with open(self.script, "w", encoding="utf-8") as f:
            f.write(block())

    def stub_gh(self, events=None, fail=False, members=()):
        """events: lines the events call prints (already filtered by --jq). members: logins the org call accepts."""
        data = os.path.join(self.tmp, "events.txt")
        with open(data, "w", encoding="utf-8") as f:
            f.write("".join(e + "\n" for e in (events or [])))
        gh = os.path.join(self.bin, "gh")
        cases = "".join("  *orgs/cesar/members/%s*) exit 0 ;;\n" % m for m in members)
        with open(gh, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\necho "$@" >> "%s"\ncase "$*" in\n%s  *orgs/*) exit 1 ;;\nesac\n%s\n'
                    % (self.calls, cases, 'cat "%s"; exit 1' % data if fail else 'cat "%s"' % data))
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)

    def run_block(self, out, label=True, owner="cesar"):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_"))}
        env.update(PATH=self.bin + os.pathsep + env["PATH"], SKIP_LABEL="true" if label else "false", REPO="o/r",
                   PR_NUMBER="7", REPO_OWNER=owner, GH_TOKEN="x")
        wrapper = os.path.join(self.tmp, "wrap.sh")
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write("set -eu\nout=%s\n. %s\n" % (shlex.quote(out), self.script))
        return subprocess.run(["bash", wrapper], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def called(self):
        return os.path.exists(self.calls)

    SKIP = "Review attestation found for tree abc.\nskipped: tests only"
    FULL = "Review attestation found for tree abc."

    def test_a_real_review_never_calls_the_api(self):
        self.stub_gh([])
        r = self.run_block(self.FULL, label=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(self.called())

    def test_skip_without_the_label_fails(self):
        self.stub_gh([event("cesar")])
        r = self.run_block(self.SKIP, label=False)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("::error::", r.stdout)
        self.assertIn("review-skip", r.stdout)

    def test_unreadable_events_fail_closed(self):
        self.stub_gh(fail=True)
        r = self.run_block(self.SKIP)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("::error::", r.stdout)

    def test_a_failed_later_page_does_not_leave_an_earlier_labeler_passing(self):
        self.stub_gh([event("cesar")], fail=True)  # page 1 printed, then gh exits 1
        r = self.run_block(self.SKIP)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("::error::", r.stdout)

    def test_label_with_no_labeled_event_fails(self):
        self.stub_gh([])
        self.assertEqual(self.run_block(self.SKIP).returncode, 1)

    def test_owner_passes(self):
        self.stub_gh([event("cesar")])
        r = self.run_block(self.SKIP)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("review-skip", r.stdout)

    def test_org_member_passes_and_a_stranger_fails(self):
        self.stub_gh([event("ana")], members=["ana"])
        self.assertEqual(self.run_block(self.SKIP).returncode, 0)
        self.stub_gh([event("mallory")], members=["ana"])
        r = self.run_block(self.SKIP)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("::error::", r.stdout)

    def test_an_event_with_no_actor_fails(self):
        self.stub_gh(["\t"], members=["cesar"])  # a null actor prints a lone tab; the org call must not be reached
        self.assertEqual(self.run_block(self.SKIP).returncode, 1)
        self.assertFalse("members" in open(self.calls).read() if self.called() else False)

    def test_a_bot_fails_even_when_it_is_the_owner_login(self):
        self.stub_gh([event("cesar", "Bot")], members=["cesar"])
        self.assertEqual(self.run_block(self.SKIP).returncode, 1)

    def test_only_the_last_labeler_counts(self):
        self.stub_gh([event("cesar"), event("mallory")])
        self.assertEqual(self.run_block(self.SKIP).returncode, 1)
        self.stub_gh([event("mallory"), event("cesar")])
        self.assertEqual(self.run_block(self.SKIP).returncode, 0)

    def test_workflow_wiring(self):
        text = read_workflow()
        self.assertIn("issues: read", text)
        self.assertIn("types: [opened, synchronize, reopened, edited, labeled, unlabeled]", text)
        step = text.split("Verify the review attestation with the base branch's gate")[1].split("\n      - name:")[0]
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'review-skip')", step)
        self.assertIn("GH_TOKEN: ${{ github.token }}", step)
        self.assertIn("gh api --paginate", step)
        run = step.split("run: |")[1]
        self.assertNotIn("${{", run)  # nothing from the PR is interpolated into the shell
        # The label check follows the attestation and runs before the check command.
        self.assertLess(text.index("verify-attestation --tree"), text.index("# NFX-110 skip label check"))
        self.assertLess(text.index("# end NFX-110"), text.index('review-gate.py" run-check'))


class Pins(unittest.TestCase):
    def test_every_action_is_pinned_to_a_full_commit_sha(self):  # NFX-109
        import glob
        import re
        for path in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.y*ml")):
            with open(path, encoding="utf-8") as f:
                uses = re.findall(r"^\s*-?\s*uses:\s*(\S+)", f.read(), re.M)
            self.assertTrue(uses, path)
            for ref in uses:
                if ref.startswith(("./", "docker://")):
                    continue
                self.assertRegex(ref, r"@[0-9a-f]{40}$", f"{os.path.basename(path)}: {ref}")


if __name__ == "__main__":
    unittest.main()
