#!/usr/bin/env python3
"""Tests for `check-shots` (NFX-143): a `<prefix>/<KEY>-shots` branch may be pushed without a review mark only when
everything it adds or changes is a regular media file under shots/<KEY>/.
Run: python3 -B claude/hooks/test_review_gate_shots.py

Throwaway git repos and a fake home under a temp dir. The gate is loaded in this process with user_home patched.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_review_gate_branch import load_gate  # noqa: E402
from test_review_gate_check import Base  # noqa: E402

BRANCH = "cesar/DEM-1-shots"


class CheckShots(Base):
    def setUp(self):
        super().setUp()
        self.gate = load_gate()
        home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(home, ".config", "nousfx"))
        with open(os.path.join(home, ".config", "nousfx", "local.toml"), "w", encoding="utf-8") as f:
            f.write('[branch]\nprefix = "cesar"\n')
        patcher = mock.patch.object(self.gate, "user_home", return_value=home)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sh("git", "branch", "base")  # the default branch the hook compares against

    def commit(self, files, branch=BRANCH, orphan=False, remove=()):
        """Check out `branch` (from base, or an empty orphan), write files {path: text | ("link", target)}, commit."""
        self.sh("git", "checkout", "-q", *(["--orphan", branch] if orphan else ["-b", branch, "base"]))
        if orphan:
            self.sh("git", "rm", "-rfq", ".")
        for path in remove:
            self.sh("git", "rm", "-q", path)
        for path, content in files.items():
            full = os.path.join(self.repo, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if isinstance(content, tuple):
                os.symlink(content[1], full)
            else:
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "DEM-1: shots")
        return self.rev("HEAD")

    def more(self, files, remove=()):
        """Another commit on the checked-out branch."""
        for path in remove:
            self.sh("git", "rm", "-q", path)
        for path, content in files.items():
            full = os.path.join(self.repo, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if isinstance(content, tuple):
                os.symlink(content[1], full)
            else:
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "DEM-1: more", "--allow-empty")
        return self.rev("HEAD")

    def test_a_leading_space_does_not_hide_a_symlink_or_a_path(self):
        sha = self.commit({" shots/DEM-1/a.png": ("link", "/etc/passwd")})
        code, err = self.run_check(sha)
        self.assertEqual(code, 1)
        self.assertIn("shots/DEM-1/a.png", err)
        other = self.commit({" shots/DEM-1/b.png": "x"}, branch="cesar/DEM-1-shots-sp")
        self.assertEqual(self.run_check(other)[0], 1)  # a regular file, but the path is outside shots/

    def test_code_added_and_dropped_in_later_commits_fails(self):
        self.commit({"shots/DEM-1/a.png": "x", "evil.py": "x"})
        sha = self.more({}, remove=["evil.py"])  # the net diff against base is media only
        self.assertEqual(self.run_check(sha)[0], 1)

    def test_a_code_file_deleted_then_added_back_fails(self):
        self.commit({"shots/DEM-1/a.png": "x"}, remove=["f.txt"])
        sha = self.more({"f.txt": "x"})  # net change none, but the history holds the delete and the add
        self.assertEqual(self.run_check(sha)[0], 1)
        changed = self.more({"f.txt": "changed"})
        self.assertEqual(self.run_check(changed)[0], 1)

    def test_a_merge_commit_fails(self):
        self.commit({"shots/DEM-1/a.png": "x"})
        self.sh("git", "checkout", "-q", "-b", "side", "base")
        self.more({"shots/DEM-1/b.png": "x"})
        self.sh("git", "checkout", "-q", BRANCH)
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "--no-ff", "side", "-m", "DEM-1: merge")
        self.assertEqual(self.run_check(self.rev("HEAD"))[0], 1)

    def test_several_media_commits_pass_and_a_deleted_screenshot_passes(self):
        self.commit({"shots/DEM-1/a.png": "x"})
        self.more({"shots/DEM-1/b.png": "x"})
        sha = self.more({}, remove=["shots/DEM-1/a.png"])
        self.assertEqual(self.run_check(sha)[0], 0)

    def fresh(self):
        """A branch name no earlier call in this test used (the same name would make `checkout -b` fail)."""
        self.counter = getattr(self, "counter", 0) + 1
        return f"cesar/DEM-1-shots-t{self.counter}"

    def test_a_gitlink_is_not_hidden_by_a_submodule_ignore_setting(self):
        with open(os.path.join(self.repo, ".gitmodules"), "w", encoding="utf-8") as f:
            f.write('[submodule "x"]\n\tpath = vendor/x\n\turl = ./x\n\tignore = all\n')
        self.sh("git", "checkout", "-q", "-b", BRANCH, "base")
        os.makedirs(os.path.join(self.repo, "shots", "DEM-1"))
        with open(os.path.join(self.repo, "shots", "DEM-1", "a.png"), "w", encoding="utf-8") as f:
            f.write("x")
        self.sh("git", "add", "shots")
        self.sh("git", "update-index", "--add", "--cacheinfo", f"160000,{self.rev('base')},vendor/x")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "DEM-1: shots")
        code, err = self.run_check(self.rev("HEAD"))
        self.assertEqual(code, 1)
        self.assertIn("vendor/x", err)

    def rev(self, ref):
        import subprocess
        return subprocess.run(["git", "rev-parse", ref], cwd=self.repo, check=True, capture_output=True,
                              text=True).stdout.strip()

    def run_check(self, sha, branch=BRANCH, base="base"):
        argv = ["review-gate.py", "check-shots", branch, "--head", sha, "--cwd", self.repo]
        if base:
            argv += ["--base", base]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = self.gate.check_shots(argv)
        return code, err.getvalue()

    def test_media_only_branch_passes(self):
        sha = self.commit({"shots/DEM-1/a.png": "x", "shots/DEM-1/light/home.JPG": "x", "shots/DEM-1/f.mp4": "x"})
        self.assertEqual(self.run_check(sha)[0], 0)

    def test_an_orphan_branch_with_no_merge_base_passes(self):
        sha = self.commit({"shots/DEM-1/a.png": "x"}, orphan=True)
        self.assertEqual(self.run_check(sha)[0], 0)

    def test_without_a_base_the_whole_tree_must_be_media(self):
        orphan = self.commit({"shots/DEM-1/a.png": "x"}, orphan=True)
        self.assertEqual(self.run_check(orphan, base=None)[0], 0)
        full = self.commit({"shots/DEM-1/a.png": "x"}, branch="cesar/DEM-1-shots-b")  # carries the whole repo
        self.assertEqual(self.run_check(full, branch="cesar/DEM-1-shots", base=None)[0], 1)

    def test_a_name_that_is_not_the_prefix_and_key_and_shots_fails(self):
        sha = self.commit({"shots/DEM-1/a.png": "x"})
        for name in ("ana/DEM-1-shots", "cesar/DEM-1", "cesar/dem-1-shots", "cesar/DEM-1-shots-x", "cesar/DEM-1-shot"):
            with self.subTest(name=name):
                self.assertEqual(self.run_check(sha, branch=name)[0], 1)

    def test_a_path_outside_shots_key_fails(self):
        for path in ("shots/DEM-2/a.png", "src/a.png", "a.png", "shots/a.png", "other/shots/DEM-1/a.png"):
            with self.subTest(path=path):
                sha = self.commit({path: "x"}, branch=self.fresh())
                self.assertEqual(self.run_check(sha)[0], 1)

    def test_a_file_that_is_not_media_fails(self):
        for path in ("shots/DEM-1/run.sh", "shots/DEM-1/a.png.py", "shots/DEM-1/noext", "shots/DEM-1/.png"):
            with self.subTest(path=path):
                sha = self.commit({path: "x"}, branch=self.fresh())
                self.assertEqual(self.run_check(sha)[0], 1)

    def test_a_symlink_named_like_media_fails(self):
        sha = self.commit({"shots/DEM-1/a.png": ("link", "/etc/passwd")})
        self.assertEqual(self.run_check(sha)[0], 1)

    def test_one_bad_file_among_good_ones_fails(self):
        sha = self.commit({"shots/DEM-1/a.png": "x", "hooks/evil.py": "x"})
        code, err = self.run_check(sha)
        self.assertEqual(code, 1)
        self.assertIn("hooks/evil.py", err)

    def test_deleting_a_code_file_fails(self):
        sha = self.commit({"shots/DEM-1/a.png": "x"}, remove=["f.txt"])
        self.assertEqual(self.run_check(sha)[0], 1)

    def test_a_branch_that_changes_nothing_fails(self):
        self.sh("git", "branch", BRANCH, "base")
        self.assertEqual(self.run_check(self.rev("base"))[0], 1)

    def test_git_errors_fail_closed(self):
        sha = self.commit({"shots/DEM-1/a.png": "x"})
        self.assertEqual(self.run_check(sha, base="no-such-ref")[0], 1)
        self.assertEqual(self.run_check("0" * 40)[0], 1)
        self.assertEqual(self.gate.check_shots(["review-gate.py", "check-shots", BRANCH]), 1)  # no --head

    def test_the_command_is_wired_into_the_gate(self):
        import subprocess
        sha = self.commit({"shots/DEM-1/a.png": "x"})
        r = subprocess.run([sys.executable, "-B", os.path.join(HERE, "review-gate.py"), "check-shots", BRANCH,
                            "--base", "base", "--head", sha, "--cwd", self.repo], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1))  # 1 only when this machine's prefix is not cesar
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
