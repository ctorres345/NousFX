#!/usr/bin/env python3
"""NFX-25: fresh_user_test.py restore refuses a state file that points outside what prepare makes."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fresh_user_test as f  # noqa: E402


class RestoreGuardTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state_dir = tmp.name
        self.victim = os.path.join(tmp.name, "victim")
        os.makedirs(self.victim)
        open(os.path.join(self.victim, "keep.txt"), "w").close()
        self.cfg = os.path.join(tmp.name, "fake-via.json")  # never the real config
        open(self.cfg, "w").write("{}")
        saved = (f.STATE_DIR, f.via_home.default_config)
        f.STATE_DIR = self.state_dir
        f.via_home.default_config = lambda: self.cfg
        self.addCleanup(lambda: (setattr(f, "STATE_DIR", saved[0]), setattr(f.via_home, "default_config", saved[1])))

    def write_state(self, **over):
        st = {"tmp_root": os.path.realpath(tempfile.gettempdir()), "work": self.victim, "home": "", "app": "", "config": self.cfg,
              "backup": None, "backup_sha": None, **over}
        with open(f._state_path(), "w") as fh:
            json.dump(st, fh)

    def test_work_outside_tempdir_is_refused(self):
        self.write_state()
        self.assertEqual(f.restore(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.victim, "keep.txt")))

    def test_config_elsewhere_is_refused(self):
        work = tempfile.mkdtemp(prefix="via-fresh-")  # passes the work guard, so only the config guard can refuse
        self.addCleanup(lambda: os.path.isdir(work) and os.rmdir(work))
        self.write_state(work=work, config=os.path.join(self.victim, "keep.txt"))
        self.assertEqual(f.restore(), 1)
        self.assertTrue(os.path.isdir(work))
        self.assertTrue(os.path.exists(os.path.join(self.victim, "keep.txt")))

    def test_nothing_prepared_is_ok(self):
        self.assertEqual(f.restore(), 0)


if __name__ == "__main__":
    unittest.main()
