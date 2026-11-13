"""Tests for git_refs.py: the one default-branch rule the gate and tier.py share (NFX-108)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import git_refs  # noqa: E402


class Timeout(RuntimeError):
    pass


def fake_git(head=None, refs=(), fail_on=None):
    """A git(args, cwd) double. `head` is what origin/HEAD points at (None: no symbolic ref); `refs` are the full
    ref names that resolve to a commit; `fail_on` is an exception raised by the named subcommand."""
    calls = []

    def git(args, cwd):
        calls.append(list(args))
        if fail_on and args[0] == fail_on[0]:
            raise fail_on[1]
        if args[0] == "symbolic-ref":
            if head is None:
                raise RuntimeError("not a symbolic ref")
            return head
        if args[0] == "rev-parse":
            ref = args[-1][:-len("^{commit}")]
            if ref in refs:
                return "abc"
            raise RuntimeError("bad ref")
        if args[0] == "check-ref-format":
            if ".." in args[-1]:
                raise RuntimeError("bad name")
            return args[-1]
        raise AssertionError(args)

    git.calls = calls
    return git


class DefaultRef(unittest.TestCase):
    def test_origin_head_wins(self):
        git = fake_git("refs/remotes/origin/develop", ["refs/remotes/origin/develop", "refs/remotes/origin/main"])
        self.assertEqual(git_refs.default_ref(git, "."), "refs/remotes/origin/develop")

    def test_origin_head_pointing_at_itself_is_ignored(self):
        git = fake_git("refs/remotes/origin/HEAD", ["refs/remotes/origin/main"])
        self.assertEqual(git_refs.default_ref(git, "."), "refs/remotes/origin/main")

    def test_origin_head_outside_origin_is_ignored(self):
        git = fake_git("refs/heads/cesar/X-1", ["refs/heads/cesar/X-1", "refs/remotes/origin/main"])
        self.assertEqual(git_refs.default_ref(git, "."), "refs/remotes/origin/main")

    def test_dangling_origin_head_falls_through(self):
        git = fake_git("refs/remotes/origin/gone", ["refs/remotes/origin/master"])
        self.assertEqual(git_refs.default_ref(git, "."), "refs/remotes/origin/master")

    def test_main_before_master(self):
        git = fake_git(None, ["refs/remotes/origin/main", "refs/remotes/origin/master"])
        self.assertEqual(git_refs.default_ref(git, "."), "refs/remotes/origin/main")

    def test_none_when_nothing_resolves(self):
        self.assertIsNone(git_refs.default_ref(fake_git(None, []), "."))

    def test_passthrough_from_symbolic_ref(self):
        git = fake_git(fail_on=("symbolic-ref", Timeout("slow")))
        with self.assertRaises(Timeout):
            git_refs.default_ref(git, ".", (Timeout,))

    def test_passthrough_from_rev_parse(self):
        git = fake_git("refs/remotes/origin/main", fail_on=("rev-parse", Timeout("slow")))
        with self.assertRaises(Timeout):
            git_refs.default_ref(git, ".", (Timeout,))

    def test_without_passthrough_a_failure_reads_as_missing(self):
        git = fake_git(None, fail_on=("rev-parse", Timeout("slow")))
        self.assertIsNone(git_refs.default_ref(git, "."))


class BranchRef(unittest.TestCase):
    def test_resolves_the_named_branch(self):
        git = fake_git(None, ["refs/remotes/origin/feature"])
        self.assertEqual(git_refs.branch_ref(git, ".", "feature"), "refs/remotes/origin/feature")

    def test_rejects_bad_names_before_asking_git(self):
        for bad in ("", "-x", "--upload-pack=x", "a..b"):
            git = fake_git(None, ["refs/remotes/origin/" + bad])
            with self.assertRaises(ValueError, msg=bad):
                git_refs.branch_ref(git, ".", bad)
            self.assertFalse([c for c in git.calls if c[0] == "rev-parse"], bad)

    def test_missing_ref_raises_and_never_falls_back(self):
        git = fake_git("refs/remotes/origin/main", ["refs/remotes/origin/main"])
        with self.assertRaises(RuntimeError) as cm:
            git_refs.branch_ref(git, ".", "feature")
        self.assertIn("origin/feature", str(cm.exception))

    def test_passthrough(self):
        git = fake_git(fail_on=("rev-parse", Timeout("slow")))
        with self.assertRaises(Timeout):
            git_refs.branch_ref(git, ".", "feature", (Timeout,))


if __name__ == "__main__":
    unittest.main()
