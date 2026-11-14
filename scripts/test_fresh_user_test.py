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
        saved = f.STATE_DIR
        f.STATE_DIR = self.state_dir
        self.addCleanup(setattr, f, "STATE_DIR", saved)

    def write_state(self, **over):
        st = {"work": self.victim, "home": "", "app": "", "config": f.via_home.default_config(),
              "backup": None, "backup_sha": None, **over}
        with open(f._state_path(), "w") as fh:
            json.dump(st, fh)

    def test_work_outside_tempdir_is_refused(self):
        self.write_state()
        self.assertEqual(f.restore(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.victim, "keep.txt")))

    def test_config_elsewhere_is_refused(self):
        self.write_state(config=os.path.join(self.victim, "keep.txt"))
        self.assertEqual(f.restore(), 1)
        self.assertTrue(os.path.exists(os.path.join(self.victim, "keep.txt")))

    def test_nothing_prepared_is_ok(self):
        self.assertEqual(f.restore(), 0)


if __name__ == "__main__":
    unittest.main()
