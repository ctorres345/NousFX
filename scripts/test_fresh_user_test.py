#!/usr/bin/env python3
"""NFX-25: fresh_user_test.py prepare/restore never loses or overwrites a real config."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fresh_user_test as f  # noqa: E402


class SandboxTest(unittest.TestCase):
    """STATE_DIR and both config paths point into a temp folder, so no test can touch the real ones."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.cfg = os.path.join(self.root, "via.json")
        self.legacy = os.path.join(self.root, "legacy-via.json")
        saved = (f.STATE_DIR, f.via_home.default_config, f.via_home.legacy_config)
        f.STATE_DIR = os.path.join(self.root, "state")
        os.makedirs(f.STATE_DIR)
        f.via_home.default_config = lambda: self.cfg
        f.via_home.legacy_config = lambda: self.legacy
        self.addCleanup(lambda: (setattr(f, "STATE_DIR", saved[0]), setattr(f.via_home, "default_config", saved[1]),
                                 setattr(f.via_home, "legacy_config", saved[2])))
        self.bak = os.path.join(f.STATE_DIR, "via.json.bak")

        def no_run(*a, **k):
            raise AssertionError("a test must not run a real command: %r" % (a,))
        saved_run = f.subprocess.run
        f.subprocess.run = no_run
        self.addCleanup(setattr, f.subprocess, "run", saved_run)

    def work(self, marker=True):
        work = tempfile.mkdtemp(prefix="via-fresh-")
        self.addCleanup(lambda: os.path.isdir(work) and __import__("shutil").rmtree(work))
        if marker:
            open(os.path.join(work, f.MARKER), "w").close()
        return work

    def state(self, work, configs):
        with open(f._state_path(), "w") as fh:
            json.dump({"work": work, "home": "", "app": "", "configs": configs}, fh)


class RestoreTest(SandboxTest):
    def test_work_without_marker_is_not_deleted(self):
        work = self.work(marker=False)
        self.state(work, [])
        self.assertEqual(f.restore(), 0)
        self.assertTrue(os.path.isdir(work))

    def test_work_with_marker_is_deleted(self):
        work = self.work()
        self.state(work, [])
        self.assertEqual(f.restore(), 0)
        self.assertFalse(os.path.exists(work))

    def test_foreign_config_path_is_refused(self):
        victim = os.path.join(self.root, "victim.txt")
        open(victim, "w").close()
        work = self.work()
        self.state(work, [{"path": victim, "backup": None, "sha": None}])
        self.assertEqual(f.restore(), 1)
        self.assertTrue(os.path.exists(victim))
        self.assertTrue(os.path.isdir(work))

    def test_backup_goes_back_and_a_second_restore_is_clean(self):
        open(self.cfg, "w").write("real")
        work = self.work()
        self.state(work, [{"path": self.cfg, "backup": self.bak, "sha": None}])
        open(self.bak, "w").write("real")
        os.remove(self.cfg)
        open(self.cfg, "w").write("test run")
        self.state(work, [{"path": self.cfg, "backup": self.bak, "sha": f._sha(self.bak)}])
        self.assertEqual(f.restore(), 0)
        self.assertEqual(open(self.cfg).read(), "real")
        self.assertFalse(os.path.exists(self.bak))
        self.assertFalse(os.path.exists(f._state_path()))
        # a stop after the backup was removed, before the state file was: the same state again must finish cleanly
        self.state(work, [{"path": self.cfg, "backup": self.bak, "sha": f._sha(self.cfg)}])
        self.assertEqual(f.restore(), 0)
        self.assertEqual(open(self.cfg).read(), "real")

    def test_file_the_run_created_is_removed(self):
        open(self.cfg, "w").write("test run")
        self.state(self.work(), [{"path": self.cfg, "backup": None, "sha": None}])
        self.assertEqual(f.restore(), 0)
        self.assertFalse(os.path.exists(self.cfg))

    def test_state_with_missing_keys_is_damaged_not_a_crash(self):
        with open(f._state_path(), "w") as fh:
            json.dump({"configs": [{"path": self.cfg}]}, fh)
        self.assertEqual(f.restore(), 1)

    def test_damaged_state_exits_one_and_names_the_backup(self):
        open(self.bak, "w").write("real")
        open(f._state_path(), "w").write("{not json")
        self.assertEqual(f.restore(), 1)
        self.assertTrue(os.path.exists(self.bak))

    def test_orphan_backup_without_state_exits_one(self):
        open(self.bak, "w").write("real")
        self.assertEqual(f.restore(), 1)

    def test_nothing_prepared_is_ok(self):
        self.assertEqual(f.restore(), 0)


class PrepareTest(SandboxTest):
    def test_prepare_refuses_to_overwrite_an_existing_backup(self):
        open(self.bak, "w").write("real")
        open(self.cfg, "w").write("test config")
        self.assertEqual(f.prepare(), 1)
        self.assertEqual(open(self.bak).read(), "real")
        self.assertEqual(open(self.cfg).read(), "test config")

    def test_prepare_refuses_a_symlinked_config(self):
        target = os.path.join(self.root, "dotfiles-via.json")
        open(target, "w").write("mine")
        os.symlink(target, self.cfg)
        self.assertEqual(f.prepare(), 1)
        self.assertTrue(os.path.islink(self.cfg))

    def test_prepare_refuses_when_a_state_file_exists(self):
        open(f._state_path(), "w").write("{not json")
        self.assertEqual(f.prepare(), 1)


if __name__ == "__main__":
    unittest.main()
