#!/usr/bin/env python3
"""Tests for via_outbound.py. Run: python3 -B plugins/via/scripts/test_via_outbound.py

Each test runs the script as a subprocess on a throwaway git repo and a throwaway config file.
Exit 0 means allowed, 20 means local-only, 1 means bad input (fail closed).
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import nousfx_testhome as th

sys.dont_write_bytecode = True

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "via_outbound.py")
DESTINATIONS = ("gemini", "jira", "figma", "artifacts", "obsidian")
KEY = "acme/app"


class ViaOutboundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = self.tempdir()
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)
        self.git("init", "-q")
        os.makedirs(os.path.join(self.tmp, ".config", "nousfx"))
        self.config = os.path.join(self.tmp, ".config", "nousfx", "via.json")  # the only place a config may live
        self.legacy = os.path.join(self.tmp, ".claude", "via.json")  # read for one release

    def env(self, **extra):
        """The user record says home is self.tmp (the script ignores $HOME, so tests steer pwd, see nousfx_testhome)."""
        return {"PATH": os.environ["PATH"], **th.env(self.tmp, **extra)}

    def run_raw(self, *args, env=None):
        cmd = [th.python(self.tmp), "-I", "-B", SCRIPT, *args]
        return subprocess.run(cmd, capture_output=True, text=True, env=env or self.env())

    def tempdir(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def git(self, *args):
        subprocess.run(["git", "-C", self.project, *args], check=True, capture_output=True)

    def remote(self, url):
        self.git("remote", "add", "origin", url)

    def write_config(self, allowlist, path=None):
        os.makedirs(os.path.dirname(path or self.config), exist_ok=True)
        with open(path or self.config, "w") as f:
            json.dump({"version": 1, "outbound_allowlist": allowlist}, f)

    def run_cli(self, *args, config=True):
        cmd = [th.python(self.tmp), "-I", "-B", SCRIPT, *args, "--project", self.project]
        if config:
            cmd += ["--config", self.config]
        return subprocess.run(cmd, capture_output=True, text=True,
                              env=self.env())

    def check(self, dest):
        return self.run_cli("check", "--dest", dest)

    def test_a_project_not_on_the_allow_list_is_local_only_for_every_destination(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({"someone/else": {"destinations": list(DESTINATIONS)}})
        for dest in DESTINATIONS:
            r = self.check(dest)
            self.assertEqual(r.returncode, 20, (dest, r.stderr))
            out = json.loads(r.stdout)
            self.assertFalse(out["allowed"], dest)
            self.assertTrue(out["reason"], dest)

    def test_a_listed_project_is_allowed_only_for_its_listed_destinations(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["jira", "obsidian"]}})
        for dest in DESTINATIONS:
            expected = 0 if dest in ("jira", "obsidian") else 20
            self.assertEqual(self.check(dest).returncode, expected, dest)

    def test_ssh_and_https_remotes_give_the_same_key(self):
        self.write_config({KEY: {"destinations": ["gemini"]}})
        for url in ("git@github.com:%s.git" % KEY, "https://github.com/%s.git" % KEY,
                    "https://github.com/%s" % KEY, "ssh://git@github.com/%s.git" % KEY):
            with self.subTest(url=url):
                self.git("remote", "remove", "origin") if self.has_remote() else None
                self.remote(url)
                self.assertEqual(self.check("gemini").returncode, 0)

    def has_remote(self):
        r = subprocess.run(["git", "-C", self.project, "remote"], capture_output=True, text=True)
        return "origin" in r.stdout.split()

    def test_no_remote_means_local_only(self):
        self.write_config({KEY: {"destinations": ["gemini"]}})
        r = self.check("gemini")
        self.assertEqual(r.returncode, 20, r.stderr)
        self.assertFalse(json.loads(r.stdout)["allowed"])

    def test_a_missing_config_means_local_only(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.assertEqual(self.check("gemini").returncode, 20)

    def test_a_malformed_config_fails_closed_with_exit_1(self):
        self.remote("git@github.com:%s.git" % KEY)
        with open(self.config, "w") as f:
            f.write("{not json")
        r = self.check("gemini")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertNotIn('"allowed": true', r.stdout)

    def test_a_malformed_entry_fails_closed_with_exit_1(self):
        self.remote("git@github.com:%s.git" % KEY)
        for bad in (["gemini"], {"destinations": "gemini"}, {"destinations": ["gemini", 3]},
                    {"destinations": ["gemini"], "gemini_code_paths": "src/"}):
            with self.subTest(bad=bad):
                self.write_config({KEY: bad})
                self.assertEqual(self.check("gemini").returncode, 1)

    def test_an_unknown_destination_is_refused(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}})
        self.assertEqual(self.check("dropbox").returncode, 1)

    def test_a_public_repo_gets_no_exemption(self):
        self.remote("https://github.com/octocat/hello-world.git")
        self.write_config({})
        self.assertEqual(self.check("gemini").returncode, 20)

    def test_paths_prints_the_gemini_code_paths_nul_separated(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"], "gemini_code_paths": ["a/b/", "c/"]}})
        r = self.run_cli("paths", "--dest", "gemini")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "a/b/\0c/\0")

    def test_paths_is_empty_and_exits_20_when_gemini_is_not_allowed(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["jira"], "gemini_code_paths": ["a/"]}})
        r = self.run_cli("paths", "--dest", "gemini")
        self.assertEqual(r.returncode, 20)
        self.assertEqual(r.stdout, "")

    def test_a_config_outside_the_anchor_is_refused_so_a_repo_cannot_allow_list_itself(self):
        self.remote("git@github.com:%s.git" % KEY)
        planted = os.path.join(self.project, "via.json")
        self.write_config({KEY: {"destinations": ["gemini"]}}, planted)
        self.assertEqual(self.run_cli("check", "--dest", "gemini", config=False).returncode, 20)  # default: none found
        r = self.run_raw("check", "--dest", "gemini", "--project", self.project, "--config", planted)
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_a_committed_config_in_the_repo_is_never_read_by_default(self):
        self.remote("git@github.com:%s.git" % KEY)
        for name in ("via.json", os.path.join(".config", "nousfx", "via.json"), os.path.join(".claude", "via.json")):
            self.write_config({KEY: {"destinations": list(DESTINATIONS)}}, os.path.join(self.project, name))
        self.assertEqual(self.check("gemini").returncode, 20)

    def test_environment_variables_cannot_move_the_anchor(self):
        self.remote("git@github.com:%s.git" % KEY)
        planted = os.path.join(self.project, "via.json")
        self.write_config({KEY: {"destinations": ["gemini"]}}, planted)
        elsewhere = os.path.join(self.tempdir(), "nousfx")
        self.write_config({KEY: {"destinations": ["gemini"]}}, os.path.join(elsewhere, "via.json"))
        env = self.env(NOUSFX_HOME=elsewhere, VIA_CONFIG=planted, XDG_CONFIG_HOME=elsewhere,
                       VIA_HOME=elsewhere, CLAUDE_CONFIG_DIR=elsewhere)
        r = self.run_raw("check", "--dest", "gemini", "--project", self.project, env=env)
        self.assertEqual(r.returncode, 20, r.stderr)  # none of them is read; the real anchor is empty
        r = self.run_raw("check", "--dest", "gemini", "--project", self.project, "--config", planted, env=env)
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_a_changed_home_variable_does_not_move_the_anchor(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}})
        decoy = self.tempdir()
        self.write_config({KEY: {"destinations": ["gemini"]}}, os.path.join(decoy, ".config", "nousfx", "via.json"))
        r = self.run_raw("check", "--dest", "gemini", "--project", self.project, env=self.env(HOME=decoy))
        self.assertEqual(r.returncode, 0, r.stderr)
        os.unlink(self.config)  # with the real anchor empty, a planted HOME gives nothing either
        r = self.run_raw("check", "--dest", "gemini", "--project", self.project, env=self.env(HOME=decoy))
        self.assertEqual(r.returncode, 20, r.stderr)

    def test_a_config_folder_inside_a_git_work_tree_is_refused(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}})
        subprocess.run(["git", "-C", self.tmp, "init", "-q"], check=True, capture_output=True)  # the home is now a repo
        for args in (("check", "--dest", "gemini", "--project", self.project),
                     ("check", "--dest", "gemini", "--project", self.project, "--config", self.config)):
            r = self.run_raw(*args)
            self.assertEqual(r.returncode, 1, (args, r.stdout))
            self.assertIn("work tree", r.stderr)

    def test_a_missing_config_folder_inside_a_work_tree_is_refused_too(self):
        self.remote("git@github.com:%s.git" % KEY)
        os.rmdir(os.path.dirname(self.config))
        os.rmdir(os.path.dirname(os.path.dirname(self.config)))
        subprocess.run(["git", "-C", self.tmp, "init", "-q"], check=True, capture_output=True)
        self.assertEqual(self.check("gemini").returncode, 1)

    def test_a_config_folder_that_is_itself_a_symlink_still_works(self):
        self.remote("git@github.com:%s.git" % KEY)
        dots = os.path.join(self.tmp, "dotfiles")
        os.makedirs(dots)
        os.rename(os.path.dirname(self.config), os.path.join(dots, "nousfx"))
        os.symlink(os.path.join(dots, "nousfx"), os.path.dirname(self.config))
        self.write_config({KEY: {"destinations": ["gemini"]}})
        self.assertEqual(self.check("gemini").returncode, 0)

    def test_a_symlinked_config_is_refused(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}})
        link = os.path.join(os.path.dirname(self.config), "link.json")
        os.symlink(self.config, link)
        r = self.run_raw("check", "--dest", "gemini", "--project", self.project, "--config", link)
        self.assertEqual(r.returncode, 1, r.stdout)
        os.unlink(link)
        os.rename(self.config, self.config + ".real")
        os.symlink(self.config + ".real", self.config)  # the default path itself a symlink
        self.assertEqual(self.check("gemini").returncode, 1)

    def test_a_config_that_is_a_directory_is_refused(self):
        self.remote("git@github.com:%s.git" % KEY)
        os.makedirs(self.config)
        self.assertEqual(self.check("gemini").returncode, 1)

    def test_fresh_install_with_no_config_anywhere_is_local_only(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.assertEqual(self.check("gemini").returncode, 20)

    def test_the_old_path_is_read_when_the_new_one_is_absent(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}}, self.legacy)
        self.assertEqual(self.run_cli("check", "--dest", "gemini", config=False).returncode, 0)

    def test_the_new_path_wins_over_the_old_one(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}}, self.legacy)
        self.write_config({}, self.config)
        self.assertEqual(self.check("gemini").returncode, 20)

    def test_a_symlinked_old_config_is_refused(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}}, self.legacy + ".real")
        os.symlink(self.legacy + ".real", self.legacy)
        self.assertEqual(self.run_cli("check", "--dest", "gemini", config=False).returncode, 1)

    def test_a_local_path_or_file_remote_names_no_project(self):
        self.write_config({KEY: {"destinations": ["gemini"]}, "forks/acme/app": {"destinations": ["gemini"]}})
        for url in ("/Users/x/forks/%s" % KEY, "file:///Users/x/forks/%s" % KEY, "../forks/%s" % KEY):
            with self.subTest(url=url):
                if self.has_remote():
                    self.git("remote", "remove", "origin")
                self.remote(url)
                self.assertEqual(self.check("gemini").returncode, 20)

    def test_a_query_or_fragment_on_the_remote_does_not_change_the_key(self):
        self.write_config({KEY: {"destinations": ["gemini"]}})
        self.remote("https://github.com/%s.git?x=1#frag" % KEY)
        self.assertEqual(self.check("gemini").returncode, 0)

    def test_the_old_via_config_variable_is_ignored(self):
        self.remote("git@github.com:%s.git" % KEY)
        self.write_config({KEY: {"destinations": ["gemini"]}})
        os.rename(self.config, self.config + ".moved")
        r = self.run_raw("check", "--dest", "gemini", "--project", self.project,
                         env=self.env(VIA_CONFIG=self.config + ".moved"))
        self.assertEqual(r.returncode, 20, r.stderr)


if __name__ == "__main__":
    unittest.main()
