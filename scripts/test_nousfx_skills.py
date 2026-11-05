#!/usr/bin/env python3
"""Tests for the skills part of nousfx_sync. Run: python3 -B scripts/test_nousfx_skills.py

Seam: the sync CLI as a subprocess against a temp home, temp state home and a temp repo with
fixture skills. Nothing real is touched.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_nousfx_sync import SYNC, Base, write  # noqa: E402


class SkillsBase(Base):
    def setUp(self):
        super().setUp()
        for name in ("alpha", "beta"):
            write(os.path.join(self.repo, "claude", "skills", name, "SKILL.md"), f"# {name}\n")
        write(os.path.join(self.repo, "plugins", "via", "skills", "via", "SKILL.md"), "# via\n")
        self.agents = os.path.join(self.home, ".agents", "skills")
        self.claude = os.path.join(self.home, ".claude", "skills")
        self.manifest = os.path.join(self.state, "skills-manifest.json")

    def owned(self):
        with open(self.manifest) as f:
            return json.load(f)["links"]

    def src(self, name, via=False):
        base = os.path.join(self.repo, *(("plugins", "via") if via else ("claude",)), "skills")
        return os.path.realpath(os.path.join(base, name))

    def stash(self, name):
        write(os.path.join(self.agents, name, "SKILL.md"), "# someone else's\n")
        return os.path.join(self.agents, name)

    def run_ok(self, *args):
        r = self.sync(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def real(self, *parts):
        return os.path.realpath(os.path.join(*parts))


class Links(SkillsBase):
    def test_links_manifest_and_second_run(self):
        self.run_ok()
        for name, via in (("alpha", False), ("beta", False), ("via", True)):
            self.assertEqual(self.real(self.agents, name), self.src(name, via))
            self.assertTrue(os.path.islink(os.path.join(self.claude, name)))
            self.assertEqual(self.real(self.claude, name), self.src(name, via))
        self.assertEqual(len(self.owned()), 6)
        self.assertEqual(self.owned()[os.path.join(self.claude, "alpha")], os.path.join(self.agents, "alpha"))
        before = self.snapshot()
        self.assertIn("nothing to change", self.run_ok().stdout)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_writes_nothing_and_check_flags_missing(self):
        before = self.snapshot()
        r = self.run_ok("--dry-run")
        self.assertIn("skill create", r.stdout)
        self.assertFalse(os.path.lexists(self.agents))
        self.assertEqual(self.snapshot(), before)
        self.run_ok()
        self.assertEqual(self.sync("--check").returncode, 0)
        os.remove(os.path.join(self.claude, "alpha"))
        self.assertEqual(self.sync("--check").returncode, 1)

    def test_restore_removes_links_and_manifest(self):
        self.run_ok()
        self.run_ok("--restore")
        self.assertFalse(os.path.lexists(os.path.join(self.agents, "alpha")))
        self.assertFalse(os.path.lexists(os.path.join(self.claude, "alpha")))
        self.assertFalse(os.path.exists(self.manifest))

    def test_removed_skill_loses_only_its_owned_link(self):
        self.run_ok()
        other = self.stash("thirdparty")
        os.symlink(other, os.path.join(self.claude, "thirdparty"))
        shutil.rmtree(os.path.join(self.repo, "claude", "skills", "beta"))
        self.run_ok()
        self.assertFalse(os.path.lexists(os.path.join(self.agents, "beta")))
        self.assertFalse(os.path.lexists(os.path.join(self.claude, "beta")))
        self.assertTrue(os.path.exists(os.path.join(self.agents, "alpha", "SKILL.md")))
        self.assertTrue(os.path.exists(os.path.join(other, "SKILL.md")))
        self.assertTrue(os.path.islink(os.path.join(self.claude, "thirdparty")))
        self.assertEqual(len(self.owned()), 4)
        self.run_ok("--restore")
        self.assertTrue(os.path.islink(os.path.join(self.agents, "beta")))
        self.assertEqual(len(self.owned()), 6)

    def test_removed_skill_whose_link_became_a_folder_is_not_deleted(self):
        self.run_ok()
        p = os.path.join(self.claude, "beta")
        os.remove(p)
        write(os.path.join(p, "SKILL.md"), "# mine now\n")
        shutil.rmtree(os.path.join(self.repo, "claude", "skills", "beta"))
        self.run_ok()
        self.assertTrue(os.path.exists(os.path.join(p, "SKILL.md")))
        self.assertNotIn(p, self.owned())

    def test_broken_owned_link_fails_check(self):
        self.run_ok()
        os.remove(os.path.join(self.agents, "alpha"))
        os.symlink(os.path.join(self.tmp, "gone"), os.path.join(self.agents, "alpha"))
        r = self.sync("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("broken skill link", r.stdout)
        self.run_ok()  # a run relinks it
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_repo_moved_relinks_owned_links(self):
        self.run_ok()
        repo2 = os.path.join(self.tmp, "repo2")
        shutil.copytree(self.repo, repo2)
        r = self.sync(repo=repo2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.real(self.claude, "alpha"), self.real(repo2, "claude", "skills", "alpha"))


class Collisions(SkillsBase):
    def test_unowned_folder_untouched_without_confirmation(self):
        mine = self.stash("alpha")
        r = self.run_ok()
        self.assertIn("skill collision", r.stdout)
        self.assertFalse(os.path.islink(mine))
        with open(os.path.join(mine, "SKILL.md")) as f:
            self.assertEqual(f.read(), "# someone else's\n")
        self.assertFalse(os.path.lexists(os.path.join(self.claude, "alpha")))  # whole skill skipped
        self.assertTrue(os.path.islink(os.path.join(self.agents, "beta")))     # others still linked
        self.assertNotIn(mine, self.owned())
        c = self.sync("--check")
        self.assertEqual(c.returncode, 1)
        self.assertIn("skill collision", c.stdout)
        self.assertIn("skill collision", self.run_ok("--dry-run").stdout)

    def test_confirmation_backs_up_then_replaces(self):
        mine = self.stash("alpha")
        self.run_ok("--yes-replace", "alpha")
        self.assertTrue(os.path.islink(mine))
        self.assertEqual(os.path.realpath(mine), self.src("alpha"))
        saved = [os.path.join(b, f) for b, _, fs in os.walk(os.path.join(self.state, "backups"))
                 for f in fs if f == "SKILL.md"]
        self.assertEqual(len(saved), 1)
        with open(saved[0]) as f:
            self.assertEqual(f.read(), "# someone else's\n")
        self.assertIn(mine, self.owned())
        self.assertEqual(self.sync("--check").returncode, 0)
        self.run_ok("--restore")
        self.assertFalse(os.path.islink(mine))
        with open(os.path.join(mine, "SKILL.md")) as f:
            self.assertEqual(f.read(), "# someone else's\n")

    def test_confirmation_only_for_the_named_skill(self):
        a, b = self.stash("alpha"), self.stash("beta")
        self.run_ok("--yes-replace", "alpha")
        self.assertTrue(os.path.islink(a))
        self.assertFalse(os.path.islink(b))

    def test_unowned_link_to_elsewhere_is_replaced_only_when_confirmed(self):
        os.makedirs(self.agents)
        other = os.path.join(self.tmp, "elsewhere")
        write(os.path.join(other, "SKILL.md"), "x")
        os.symlink(other, os.path.join(self.agents, "alpha"))
        self.run_ok()
        self.assertEqual(self.real(self.agents, "alpha"), os.path.realpath(other))
        self.run_ok("--yes-replace", "alpha")
        self.assertEqual(self.real(self.agents, "alpha"), self.src("alpha"))
        self.assertTrue(os.path.exists(os.path.join(other, "SKILL.md")))
        self.run_ok("--restore")
        self.assertEqual(self.real(self.agents, "alpha"), os.path.realpath(other))

    def test_old_install_copy_in_claude_folder_is_a_collision(self):
        old = os.path.join(self.claude, "alpha")
        write(os.path.join(old, "SKILL.md"), "# copied by install.sh\n")
        r = self.run_ok()
        self.assertIn("skill collision", r.stdout)
        self.assertFalse(os.path.islink(old))
        self.assertFalse(os.path.lexists(os.path.join(self.agents, "alpha")))


class OldBackups(SkillsBase):
    STAMP = ".bak-20250101-101010"

    def put(self, *parts):
        path = os.path.join(self.home, *parts)
        write(path, "old")
        return path

    def test_a_normal_run_lists_them_and_moves_nothing(self):
        bak = self.put(".claude", "agents", "x.md" + self.STAMP)
        r = self.run_ok()
        self.assertIn(bak, r.stdout)
        self.assertIn("--clean-backups", r.stdout)
        self.assertTrue(os.path.exists(bak))
        again = self.run_ok()  # nothing else to do, the listing still shows
        self.assertIn("nothing to change", again.stdout)
        self.assertIn(bak, again.stdout)
        self.assertTrue(os.path.exists(bak))
        self.assertIn(bak, self.run_ok("--dry-run").stdout)
        self.assertIn(bak, self.sync("--check").stdout)
        self.assertEqual(self.sync("--check").returncode, 0)

    def test_clean_backups_moves_them_and_restore_brings_them_back(self):
        names = [(".claude", "CLAUDE.md" + self.STAMP), (".claude", "settings.json" + self.STAMP),
                 (".claude", "agents", "x.md" + self.STAMP),
                 (".claude", "hooks", "review-gate.py" + self.STAMP),
                 (".claude", "skills", "mine", "SKILL.md" + self.STAMP)]
        baks = [self.put(*n) for n in names]
        keep = self.put(".claude", "notes.bak")
        self.run_ok()
        self.assertTrue(all(os.path.exists(b) for b in baks))
        r = self.run_ok("--clean-backups")
        for b in baks:
            self.assertIn(b, r.stdout)
            self.assertFalse(os.path.exists(b), b)
        self.assertTrue(os.path.exists(keep))
        self.run_ok("--restore")
        for b in baks:
            with open(b) as f:
                self.assertEqual(f.read(), "old")

    def test_only_what_install_sh_writes_is_in_scope(self):
        outside = [self.put(".claude", "projects", "p", "x.md" + self.STAMP),
                   self.put(".claude", "plugins", "p", "x.md" + self.STAMP),
                   self.put(".claude", "other.md" + self.STAMP),
                   self.put("elsewhere", "x.md" + self.STAMP)]
        r = self.run_ok("--clean-backups")
        for b in outside:
            self.assertTrue(os.path.exists(b), b)
            self.assertNotIn(b, r.stdout)

    def test_a_linked_skill_folder_is_not_walked(self):
        bak = os.path.join(self.repo, "claude", "skills", "alpha", "SKILL.md" + self.STAMP)
        write(bak, "inside the repo")
        self.run_ok("--clean-backups")
        self.assertTrue(os.path.exists(bak))


class Windows(SkillsBase):
    """Windows is tested with --platform windows and a stand-in `cmd` first on the subprocess PATH."""

    def stand_in(self):
        bin_dir = os.path.join(self.tmp, "bin")
        log = os.path.join(self.tmp, "junctions.log")
        write(os.path.join(bin_dir, "cmd"),
              '#!/bin/sh\n[ "$1" = /c ] && [ "$2" = mklink ] && [ "$3" = /J ] || exit 9\n'
              'ln -s "$5" "$4" && echo "$4" >> "%s"\n' % log)
        os.chmod(os.path.join(bin_dir, "cmd"), 0o755)
        return bin_dir, log

    def run_with(self, env_extra, *args):
        env = {**os.environ, **env_extra}
        return subprocess.run([sys.executable, "-B", SYNC, "--home", self.home, "--state-home", self.state,
                               "--repo", self.repo, *args], capture_output=True, text=True, env=env,
                              cwd=self.tmp)

    def test_junction_fallback_runs_the_fixed_mklink_command(self):
        bin_dir, log = self.stand_in()
        r = self.run_with({"NOUSFX_NO_SYMLINK": "1", "PATH": bin_dir + os.pathsep + os.environ["PATH"]},
                          "--platform", "windows")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(log) as f:
            self.assertEqual(len(f.read().split()), 6)
        self.assertEqual(self.real(self.agents, "alpha"), self.src("alpha"))

    def test_the_junction_command_cannot_be_replaced_from_the_environment(self):
        marker = os.path.join(self.tmp, "ran")
        evil = os.path.join(self.tmp, "evil.sh")
        write(evil, "#!/bin/sh\ntouch %s\n" % marker)
        os.chmod(evil, 0o755)
        bin_dir, _ = self.stand_in()
        r = self.run_with({"NOUSFX_NO_SYMLINK": "1", "NOUSFX_JUNCTION_CMD": evil,
                           "PATH": bin_dir + os.pathsep + os.environ["PATH"]}, "--platform", "windows")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(marker))

    def test_posix_without_symlinks_fails_and_changes_no_manifest(self):
        r = self.run_with({"NOUSFX_NO_SYMLINK": "1"})
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
