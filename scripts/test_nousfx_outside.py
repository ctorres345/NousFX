#!/usr/bin/env python3
"""Tests for the pinned outside skills in nousfx_sync. Run: python3 -B scripts/test_nousfx_outside.py

Seam: the sync CLI as a subprocess against a temp home, temp state home and a temp repo whose
harness/skills.lock.json points at a local fixture git repo (file:// URL). Nothing real is touched.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "via", "scripts"))
import nousfx_agy  # noqa: E402
import via_skills  # noqa: E402
from test_nousfx_skills import SkillsBase  # noqa: E402
from test_nousfx_sync import SYNC, write  # noqa: E402

GOOD = {"SKILL.md": "# {n}\n", "references/a.md": "ref {n}\n"}


class OutsideBase(SkillsBase):
    def setUp(self):
        super().setUp()
        self.remote = os.path.join(self.tmp, "remote")
        self.lock_path = os.path.join(self.repo, "harness", "skills.lock.json")
        self.cache = os.path.join(self.state, "outside-skills")
        root = os.path.dirname(HERE)
        for rel in (os.path.join("overlays", "agy.overlay.md"), os.path.join("agy", "agy_gate.py")):  # for --tools agy
            with open(os.path.join(root, "harness", rel), encoding="utf-8") as f:
                write(os.path.join(self.repo, "harness", rel), f.read())

    def env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["NOUSFX_ALLOW_FILE_URLS"] = "1"
        return env

    def sync(self, *args, repo=None, env_extra=None):
        return subprocess.run([sys.executable, "-B", SYNC, "--home", self.home, "--state-home", self.state,
                               "--repo", repo or self.repo, *args], capture_output=True, text=True,
                              cwd=self.tmp, env={**self.env(), **(env_extra or {})})

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.remote,
                              capture_output=True, text=True, check=True, env=self.env()).stdout.strip()

    def make_remote(self, names, extra=None):
        """A git repo with skills/<name>/... for each name (plus `extra` files in every skill). Returns the commit."""
        os.makedirs(self.remote)
        self.git("init", "-q")
        for n in names:
            for rel, body in {**GOOD, **(extra or {})}.items():
                write(os.path.join(self.remote, "skills", n, rel), body.format(n=n))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def entry(self, name, commit):
        url = "file://" + self.remote
        files = via_skills.stage(url, "skills/" + name, commit, True)
        return {"repo": url, "path": "skills/" + name, "commit": commit, "sha256": via_skills.checksum(files)}

    def write_lock(self, entries):
        write(self.lock_path, json.dumps({"version": 1, "skills": entries}, indent=2))

    def install_two(self):
        commit = self.make_remote(["pa", "pb"])
        self.entries = {n: self.entry(n, commit) for n in ("pa", "pb")}
        self.write_lock(self.entries)
        return commit

    def cached(self, name):
        return os.path.join(self.cache, name + "-" + self.entries[name]["sha256"][:12])


class Install(OutsideBase):
    def test_fetch_link_and_second_run_needs_no_network(self):
        self.install_two()
        r = self.run_ok("--tools", "claude,agy")
        for n in ("pa", "pb"):
            self.assertEqual(self.real(self.agents, n), self.real(self.cached(n)))
            self.assertEqual(self.real(self.claude, n), self.real(self.cached(n)))
            agy_link = os.path.join(nousfx_agy.plugin_dir(self.home), "skills", n)
            self.assertEqual(self.real(agy_link), self.real(self.cached(n)))
            with open(os.path.join(self.agents, n, "references", "a.md")) as f:
                self.assertEqual(f.read(), "ref %s\n" % n)
        self.assertIn(os.path.join(self.claude, "pa"), self.owned())
        shutil.rmtree(self.remote)  # the second run must not fetch
        before = self.snapshot()
        self.assertIn("nothing to change", self.run_ok("--tools", "claude,agy").stdout)
        self.assertEqual(self.snapshot(), before)

    def test_no_lock_file_changes_nothing_for_outside_skills(self):
        self.run_ok()
        self.assertFalse(os.path.exists(self.cache))

    def test_a_name_that_nousfx_ships_is_refused(self):
        self.install_two()
        self.write_lock({"alpha": self.entries["pa"]})
        r = self.sync()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("alpha", r.stderr)
        self.assertFalse(os.path.exists(self.cache))
        self.assertFalse(os.path.lexists(self.agents))


class Refusals(OutsideBase):
    def assert_only_pa(self, r, bad="pb"):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(bad, r.stderr)
        self.assertEqual(self.real(self.agents, "pa"), self.real(self.cached("pa")))
        for base in (self.agents, self.claude):
            self.assertFalse(os.path.lexists(os.path.join(base, bad)), base)
        self.assertEqual(len(os.listdir(self.cache)), 1)
        self.assertNotIn(os.path.join(self.agents, bad), json.dumps(self.owned()))

    def test_checksum_mismatch_refuses_only_that_skill(self):
        self.install_two()
        self.entries["pb"]["sha256"] = "0" * 64
        self.write_lock(self.entries)
        r = self.sync()
        self.assert_only_pa(r)
        self.assertIn("checksum mismatch", r.stderr)

    def test_failed_fetch_refuses_only_that_skill(self):
        self.install_two()
        self.entries["pb"]["commit"] = "1" * 40
        self.write_lock(self.entries)
        self.assert_only_pa(self.sync())


class HandInstalled(OutsideBase):
    def hand_copy(self, name, same):
        for base in (self.agents, self.claude):
            for rel, body in GOOD.items():
                write(os.path.join(base, name, rel), body.format(n=name if same else "other"))

    def test_a_copy_that_differs_is_a_collision_until_confirmed(self):
        self.install_two()
        self.hand_copy("pb", same=False)
        r = self.sync()
        self.assertIn("collision", r.stdout)
        self.assertTrue(os.path.isdir(os.path.join(self.agents, "pb")))
        self.assertFalse(os.path.islink(os.path.join(self.agents, "pb")))
        with open(os.path.join(self.agents, "pb", "SKILL.md")) as f:
            self.assertEqual(f.read(), "# other\n")
        self.assertEqual(self.real(self.agents, "pa"), self.real(self.cached("pa")))
        self.run_ok("--yes-replace", "pb")
        self.assertEqual(self.real(self.agents, "pb"), self.real(self.cached("pb")))
        self.assertEqual(self.real(self.claude, "pb"), self.real(self.cached("pb")))

    def test_a_copy_that_matches_the_pin_is_replaced_and_restore_brings_it_back(self):
        self.install_two()
        self.hand_copy("pb", same=True)
        self.run_ok()
        for base in (self.agents, self.claude):
            self.assertTrue(os.path.islink(os.path.join(base, "pb")), base)
            self.assertEqual(self.real(base, "pb"), self.real(self.cached("pb")))
        self.run_ok("--restore")
        for base in (self.agents, self.claude):
            p = os.path.join(base, "pb")
            self.assertTrue(os.path.isdir(p) and not os.path.islink(p), p)
            with open(os.path.join(p, "SKILL.md")) as f:
                self.assertEqual(f.read(), "# pb\n")


class Reporting(OutsideBase):
    def test_check_and_dry_run_fetch_and_write_nothing(self):
        commit = self.install_two()
        shutil.rmtree(self.remote)  # a fetch here would fail loudly
        before = self.snapshot()
        r = self.sync("--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("drift: outside skill pa missing", r.stdout)
        r = self.sync("--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("would fetch pa@" + commit[:7], r.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(os.path.exists(self.cache))

    def test_check_flags_an_edited_cache_and_is_clean_otherwise(self):
        self.install_two()
        self.run_ok()
        self.assertEqual(self.sync("--check").returncode, 0)
        os.chmod(os.path.join(self.cached("pa"), "SKILL.md"), 0o644)
        write(os.path.join(self.cached("pa"), "SKILL.md"), "# tampered\n")
        r = self.sync("--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("drift: outside skill pa edited", r.stdout)

    def test_an_edited_cache_is_kept_aside_when_the_pin_is_put_back(self):
        self.install_two()
        self.run_ok()
        edited = os.path.join(self.cached("pa"), "SKILL.md")
        write(edited, "# my work\n")
        self.run_ok()
        with open(edited) as f:
            self.assertEqual(f.read(), "# pa\n")
        kept = [os.path.join(b, "old", "SKILL.md") for b in
                (os.path.join(self.cache, d) for d in os.listdir(self.cache) if d.startswith(".edited-"))]
        self.assertEqual(len(kept), 1)
        with open(kept[0]) as f:
            self.assertEqual(f.read(), "# my work\n")
        self.assertEqual(self.sync("--check").returncode, 0)
        self.assertEqual([d for d in os.listdir(self.cache) if d.startswith(".stage-")], [])

    def test_files_the_os_or_a_script_adds_are_not_edits(self):
        self.install_two()
        self.run_ok()
        write(os.path.join(self.cached("pa"), ".DS_Store"), "x")
        write(os.path.join(self.cached("pa"), "scripts", "__pycache__", "m.cpython-312.pyc"), "x")
        before = self.snapshot()
        self.assertIn("nothing to change", self.run_ok().stdout)
        self.assertEqual(self.sync("--check").returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_a_pin_that_really_ships_junk_names_still_matches(self):
        commit = self.make_remote(["pa"], extra={"scripts/__pycache__/x.pyc": "pin {n}\n", ".DS_Store": "d {n}\n"})
        self.entries = {"pa": self.entry("pa", commit)}
        self.write_lock(self.entries)
        self.run_ok()
        before = self.snapshot()
        out = self.run_ok().stdout
        self.assertIn("nothing to change", out)
        self.assertNotIn("edited", out)
        self.assertEqual(self.sync("--check").returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([d for d in os.listdir(self.cache) if d.startswith(".edited-")], [])

    def test_the_run_says_where_an_edited_copy_went(self):
        self.install_two()
        self.run_ok()
        write(os.path.join(self.cached("pa"), "SKILL.md"), "# mine\n")
        r = self.run_ok()
        self.assertIn("pa was edited", r.stdout)
        self.assertIn(".edited-", r.stdout)

    def test_a_name_that_only_differs_by_case_from_a_shipped_skill_is_refused(self):
        self.install_two()
        self.write_lock({"Alpha": self.entries["pa"]})
        r = self.sync()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Alpha", r.stderr)


class Windows(OutsideBase):
    def test_junction_fallback_links_an_outside_skill(self):
        self.install_two()
        bin_dir = os.path.join(self.tmp, "bin")
        log = os.path.join(self.tmp, "junctions.log")
        write(os.path.join(bin_dir, "cmd"),
              '#!/bin/sh\n[ "$1" = /c ] && [ "$2" = mklink ] && [ "$3" = /J ] || exit 9\n'
              'ln -s "$5" "$4" && echo "$4" >> "%s"\n' % log)
        os.chmod(os.path.join(bin_dir, "cmd"), 0o755)
        r = self.sync("--platform", "windows", env_extra={
            "NOUSFX_NO_SYMLINK": "1", "PATH": bin_dir + os.pathsep + os.environ["PATH"]})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(log) as f:
            self.assertIn(os.path.join(self.agents, "pa"), f.read().split())
        self.assertEqual(self.real(self.agents, "pa"), self.real(self.cached("pa")))


if __name__ == "__main__":
    unittest.main()
