#!/usr/bin/env python3
"""Via in agy (NFX-59). Run: python3 -B plugins/via/scripts/test_via_agy.py

agy loads Via through the shared skills link that sync makes (NFX-42):
  <home>/.gemini/config/plugins/nousfx/skills/via -> <home>/.agents/skills/via -> <repo>/plugins/via/skills/via
These tests rebuild that chain in a throwaway home and run the Via scripts the way SKILL.md tells agy to:
through the plugin folder resolved from the skill folder, with no Claude Code variables set. The outbound
allow-list must answer exactly as it does in Claude Code, and a project that is not listed sends nothing.
"""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

import nousfx_testhome as th

sys.dont_write_bytecode = True

PLUGIN = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # <repo>/plugins/via
SKILL = os.path.join(PLUGIN, "skills", "via")
DESTINATIONS = ("gemini", "jira", "figma", "artifacts", "obsidian")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
STUB = '#!/bin/sh\necho "$@" > "$HOME/gemini-called.log"\necho \'{"text": "", "usage": {}}\'\n'


class ViaInAgyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = os.path.join(tmp.name, "home")
        os.makedirs(os.path.join(self.home, ".agents", "skills"))
        os.makedirs(os.path.join(self.home, ".gemini", "config", "plugins", "nousfx", "skills"))
        os.makedirs(os.path.join(self.home, ".config", "nousfx"))
        os.symlink(SKILL, os.path.join(self.home, ".agents", "skills", "via"))
        self.agy_skill = os.path.join(self.home, ".gemini", "config", "plugins", "nousfx", "skills", "via")
        os.symlink(os.path.join(self.home, ".agents", "skills", "via"), self.agy_skill)
        self.listed = self.project(tmp.name, "listed", "git@github.com:acme/app.git")
        self.unlisted = self.project(tmp.name, "unlisted", "https://github.com/acme/other.git")
        with open(os.path.join(self.home, ".config", "nousfx", "via.json"), "w") as f:
            json.dump({"version": 1, "integrations": {"gemini": "present"},
                       "outbound_allowlist": {"github.com/acme/app": {"destinations": ["gemini"]}}}, f)
        stub = os.path.join(self.home, ".local", "bin", "gemini-api")
        os.makedirs(os.path.dirname(stub))
        with open(stub, "w") as f:
            f.write(STUB)
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IXUSR)

    def project(self, base, name, remote):
        path = os.path.join(base, name)
        os.makedirs(path)
        for args in (("init", "-q"), ("remote", "add", "origin", remote)):
            subprocess.run(["git", "-C", path, *args], check=True, capture_output=True)
        return path

    def env(self):
        """What a command started by agy sees: no CLAUDE_* variables, so no ${CLAUDE_PLUGIN_ROOT}."""
        env = {"PATH": os.environ["PATH"], **th.env(self.home)}
        self.assertFalse([k for k in env if k.startswith("CLAUDE")])
        return env

    def resolved_root(self):
        """The plugin folder, resolved with the command SKILL.md gives agy."""
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as f:
            command = re.search(r'`(python3 -c "import os;[^`]+")`', f.read())
        self.assertIsNotNone(command, "SKILL.md lost the command that resolves the plugin folder")
        # agy's gate denies any command naming its plugin folder (harness/agy/agy_gate.py, PLUGIN_FOLDER)
        self.assertNotRegex(command.group(1), r"plugins[/\\]nousfx")
        r = subprocess.run(command.group(1), shell=True, capture_output=True, text=True, env=self.env(), check=True)
        return r.stdout.strip()

    def run_script(self, root, name, *args, cwd):
        cmd = [th.python(self.home), "-I", "-B", os.path.join(root, "scripts", name), *args]
        return subprocess.run(cmd, capture_output=True, text=True, env=self.env(), cwd=cwd)

    def test_skill_text_fits_agys_24_kb_limit(self):
        """agy cuts a SKILL.md body over 24 KB. Modes are read on demand, but keep them under it too."""
        for folder, _, files in os.walk(SKILL):
            for name in files:
                if name.endswith(".md"):
                    self.assertLess(os.path.getsize(os.path.join(folder, name)), 24000, name)

    def test_the_skills_link_resolves_to_the_via_plugin_folder(self):
        self.assertEqual(self.resolved_root(), os.path.realpath(PLUGIN))

    def test_an_unlisted_project_is_local_only_for_every_destination_from_agy(self):
        for dest in DESTINATIONS:
            r = self.run_script(self.resolved_root(), "via_outbound.py", "check", "--dest", dest, cwd=self.unlisted)
            self.assertEqual(r.returncode, 20, (dest, r.stdout, r.stderr))
            self.assertFalse(json.loads(r.stdout)["allowed"])

    def test_a_listed_project_gets_the_same_answer_as_in_claude_code(self):
        r = self.run_script(self.resolved_root(), "via_outbound.py", "check", "--dest", "gemini", cwd=self.listed)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["project"], "github.com/acme/app")
        r = self.run_script(self.resolved_root(), "via_outbound.py", "check", "--dest", "jira", cwd=self.listed)
        self.assertEqual(r.returncode, 20, r.stderr)

    def test_an_unresolved_path_through_the_link_still_runs_the_scripts(self):
        """An agent that skips the resolve step and writes <skill folder>/../.. still reaches the real scripts,
        and their imports work (they find their folder with realpath, not abspath). The real interpreter is
        used because the test shim normalizes the path first; --help reads no config."""
        root = os.path.join(self.agy_skill, "..", "..")
        for name in ("via_outbound.py", "via_prototype.py", "via_capture_native.py", "via_skills.py"):
            r = subprocess.run([sys.executable, "-I", "-B", os.path.join(root, "scripts", name), "--help"],
                               capture_output=True, text=True, env=self.env(), cwd=self.unlisted)
            self.assertEqual(r.returncode, 0, (name, r.stderr))

    def test_the_second_opinion_sends_nothing_for_an_unlisted_project(self):
        run_dir = os.path.join(self.unlisted, ".via", "runs", "20261107T000000Z")
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "home-light-phone.png"), "wb") as f:
            f.write(PNG)
        with open(os.path.join(run_dir, "findings.json"), "w") as f:
            json.dump({"version": 1, "mode": "audit", "scope": "all", "platforms": ["web"], "findings": [],
                       "screenshots": [{"path": "home-light-phone.png", "screen": "home", "theme": "light",
                                        "size": "phone", "redacted": True, "review": "kept"}]}, f)
        r = self.run_script(self.resolved_root(), "via_second_opinion.py", "--run-dir", run_dir,
                            "--shot", os.path.join(run_dir, "home-light-phone.png"), "--brief", "a home page",
                            cwd=self.unlisted)
        self.assertEqual(r.returncode, 10, r.stderr)
        self.assertIn("not on the allow-list", json.loads(r.stdout)["reason"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "gemini-called.log")), "gemini-api was called")


if __name__ == "__main__":
    unittest.main()
