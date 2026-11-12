#!/usr/bin/env python3
"""CLI tests for --tools and --project of scripts/nousfx_sync.py (agy plugin and MCP files).
Run: python3 -B -m unittest test_nousfx_sync_tools   (from scripts/)

Same seam as test_nousfx_sync.py: the CLI as a subprocess against a temp home, state home and repo.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

import nousfx_agy
import nousfx_sync
from test_nousfx_sync import ROOT, Base, read, write

def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


TOKEN = "ghp_" + "a1B2c3D4e5" * 4  # looks like a GitHub token; built so the file itself holds no secret


class Fixture(Base):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.repo, "harness", "overlays", "agy.overlay.md"), "## agy overlay\n- agy only line\n")
        shutil.copy(os.path.join(ROOT, "harness", "mcp.toml"), os.path.join(self.repo, "harness", "mcp.toml"))
        write(os.path.join(self.repo, "harness", "agy", "agy_gate.py"),
              read(os.path.join(ROOT, "harness", "agy", "agy_gate.py")))
        write(os.path.join(self.repo, "claude", "skills", "demo", "SKILL.md"), "---\nname: demo\n---\nx\n")
        write(os.path.join(self.repo, "claude", "githooks", "pre-push"),
              read(os.path.join(ROOT, "claude", "githooks", "pre-push")))
        self.plugin = os.path.join(self.home, ".gemini", "config", "plugins", "nousfx")
        self.agy_rules = os.path.join(self.plugin, "rules", "AGENTS.md")
        self.agy_mcp = os.path.join(self.plugin, "mcp_config.json")
        self.claude_json = os.path.join(self.home, ".claude.json")

    def ok(self, *args):
        r = self.sync(*args)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r


class AgyGate(Fixture):
    def test_adapter_files_written_and_second_run_changes_nothing(self):
        r = self.ok("--tools", "claude,agy")
        self.assertIn("agy gate self-test passed", r.stdout)
        for rel in ("hooks.json", os.path.join("hooks", "agy_gate.py"), os.path.join("hooks", "review-gate.py"),
                    os.path.join("hooks", "pr_create_parser.py")):
            self.assertTrue(os.path.isfile(os.path.join(self.plugin, rel)), rel)
        self.assertIn("nothing to change", self.ok("--tools", "claude,agy").stdout)
        self.assertEqual(self.sync("--tools", "claude,agy", "--check").returncode, 0)

    def test_hand_edit_of_the_adapter_is_drift(self):
        self.ok("--tools", "claude,agy")
        with open(os.path.join(self.plugin, "hooks", "agy_gate.py"), "a", encoding="utf-8") as f:
            f.write("# edited\n")
        self.assertEqual(self.sync("--tools", "claude,agy", "--check").returncode, 1)

    def test_broken_adapter_fails_the_run_and_restore_undoes_it(self):
        write(os.path.join(self.repo, "harness", "agy", "agy_gate.py"), "import sys\nsys.exit(0)\n")  # allows everything
        r = self.sync("--tools", "claude,agy")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("agy gate self-test failed", r.stderr)
        self.assertEqual(self.sync("--restore").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.plugin, "hooks.json")))

    def test_a_second_run_does_not_hide_a_broken_adapter(self):
        write(os.path.join(self.repo, "harness", "agy", "agy_gate.py"), "import sys\nsys.exit(0)\n")
        self.assertEqual(self.sync("--tools", "claude,agy").returncode, 1)
        r = self.sync("--tools", "claude,agy")  # the broken files are already installed: nothing to write
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("agy gate self-test failed", r.stderr)

    def test_claude_only_run_has_no_agy_self_test(self):
        self.assertNotIn("agy gate", self.ok().stdout)


class Tools(Fixture):
    def test_default_writes_nothing_for_agy(self):
        self.ok()
        self.assertFalse(os.path.exists(os.path.join(self.home, ".gemini")))
        self.assertFalse(os.path.exists(self.claude_json))

    def test_agy_files_and_rules_content(self):
        write(os.path.join(self.state, "local.claude.md"), "- claude-only personal line\n")
        write(os.path.join(self.state, "local.agy.md"), "- agy-only personal line\n")
        self.ok("--tools", "claude,agy")
        rules = read(self.agy_rules)
        self.assertIn("Fixture rules", rules)
        self.assertIn("agy only line", rules)
        self.assertIn("agy-only personal line", rules)
        self.assertIn("demo/KEY-1", rules)  # the neutral local.md
        self.assertNotIn("claude-only personal line", rules)
        self.assertEqual(nousfx_agy.claude_only_terms(rules), [])
        claude = read(self.rules)
        self.assertIn("claude-only personal line", claude)
        self.assertNotIn("agy-only personal line", claude)
        self.assertEqual(jload(os.path.join(self.plugin, "plugin.json"))["name"], "nousfx")
        self.assertEqual(jload(self.agy_mcp)["mcpServers"]["atlassian"]["url"],
                         "https://mcp.atlassian.com/v1/mcp")
        link = os.path.join(self.plugin, "skills", "demo")
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.path.exists(os.path.join(link, "SKILL.md")))

    def test_atlassian_is_agy_only(self):
        self.ok("--tools", "claude,agy")
        self.assertFalse(os.path.exists(self.claude_json))

    def test_users_own_agy_files_untouched(self):
        own_rules = os.path.join(self.home, ".gemini", "config", "rules", "AGENTS.md")
        own_mcp = os.path.join(self.home, ".gemini", "config", "mcp_config.json")
        write(own_rules, "# my own gemini rules\n")
        write(own_mcp, '{"mcpServers": {"mine": {"url": "https://mine.example.com"}}}\n')
        self.ok("--tools", "claude,agy")
        self.assertEqual(read(own_rules), "# my own gemini rules\n")
        self.assertEqual(read(own_mcp), '{"mcpServers": {"mine": {"url": "https://mine.example.com"}}}\n')

    def test_second_run_changes_nothing_and_check_is_clean(self):
        self.ok("--tools", "claude,agy")
        before = self.snapshot()
        self.assertIn("nothing to change", self.ok("--tools", "claude,agy").stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.sync("--tools", "claude,agy", "--check").returncode, 0)

    def test_check_flags_agy_drift_and_dry_run_writes_nothing(self):
        self.ok("--tools", "claude,agy")
        write(self.agy_rules, "hand edited\n")
        self.assertEqual(self.sync("--tools", "claude,agy", "--check").returncode, 1)
        before = self.snapshot()
        r = self.ok("--tools", "claude,agy", "--dry-run")
        self.assertIn(self.agy_rules, r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_restore_removes_everything_it_created(self):
        self.ok("--tools", "claude,agy")
        self.ok("--restore")
        left = [os.path.join(b, n) for b, _, fs in os.walk(self.home) for n in fs]
        left += [os.path.join(b, n) for b, ds, _ in os.walk(self.home) for n in ds if os.path.islink(os.path.join(b, n))]
        self.assertEqual(left, [])

    def test_local_md_with_claude_term_warns_for_agy_only(self):
        write(os.path.join(self.state, "local.md"), "- use the Skill tool\n")
        self.assertNotIn("warning", self.ok().stderr)
        r = self.ok("--tools", "claude,agy")
        self.assertIn("warning", r.stderr)
        self.assertIn("local.claude.md", r.stderr)

    def test_bad_tools_value_exits_2(self):
        self.assertEqual(self.sync("--tools", "claude,gemini").returncode, 2)
        self.assertEqual(self.sync("--tools", "").returncode, 2)

    def test_symlinked_plugin_folder_is_refused(self):
        os.makedirs(os.path.join(self.tmp, "elsewhere"))
        os.makedirs(os.path.dirname(self.plugin))
        os.symlink(os.path.join(self.tmp, "elsewhere"), self.plugin)
        r = self.sync("--tools", "claude,agy")
        self.assertEqual(r.returncode, 2)
        self.assertIn("symlink", r.stderr)
        self.assertEqual(os.listdir(os.path.join(self.tmp, "elsewhere")), [])

    def test_agy_only_run_does_not_touch_claude(self):
        self.ok("--tools", "agy")
        self.assertTrue(os.path.isdir(self.plugin))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))
        self.assertEqual(self.sync("--tools", "agy", "--check").returncode, 0)


class Mcp(Fixture):
    def mcp_toml(self, text):
        write(os.path.join(self.state, "mcp.toml"), text)

    def test_claude_entry_merged_and_other_keys_kept(self):
        write(self.claude_json, json.dumps({"numStartups": 7, "oauth": TOKEN,
                                            "mcpServers": {"mine": {"type": "http", "url": "https://m.example.com"}}}))
        self.mcp_toml('[servers.docs]\ntools = ["claude", "agy"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok("--tools", "claude,agy")  # the user's own token-looking value must not trip the secret scan
        data = jload(self.claude_json)
        self.assertEqual(data["numStartups"], 7)
        self.assertEqual(data["mcpServers"]["mine"]["url"], "https://m.example.com")
        self.assertEqual(data["mcpServers"]["docs"], {"type": "http", "url": "https://docs.example.com/mcp"})
        self.assertIn("docs", jload(self.agy_mcp)["mcpServers"])
        before = self.snapshot()
        self.assertIn("nothing to change", self.ok("--tools", "claude,agy").stdout)
        self.assertEqual(self.snapshot(), before)

    def test_claude_json_untouched_when_nothing_targets_claude(self):
        text = json.dumps({"numStartups": 7}, indent=1)
        write(self.claude_json, text)
        self.ok("--tools", "claude,agy")
        self.assertEqual(read(self.claude_json), text)

    def test_removed_entry_leaves_only_what_sync_added(self):
        write(self.claude_json, json.dumps({"mcpServers": {"mine": {"type": "http", "url": "https://m.example.com"}}}))
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        self.mcp_toml("")
        self.ok()
        self.assertEqual(list(jload(self.claude_json)["mcpServers"]), ["mine"])

    def test_plaintext_token_fails_and_writes_nothing(self):
        self.mcp_toml(f'[servers.bad]\ntools = ["agy"]\ncommand = "x"\nenv = {{ K = "{TOKEN}" }}\n')
        before = self.snapshot()
        r = self.sync("--tools", "claude,agy")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn(TOKEN, r.stderr + r.stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))

    def test_foreign_server_with_same_name_is_refused(self):
        write(self.claude_json, json.dumps({"mcpServers": {"docs": {"type": "http", "url": "https://x.example.com"}}}))
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        r = self.sync()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not mine", r.stderr)
        self.assertEqual(jload(self.claude_json)["mcpServers"]["docs"]["url"], "https://x.example.com")

    def test_restore_brings_back_claude_json(self):
        original = json.dumps({"numStartups": 1})
        write(self.claude_json, original)
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        self.assertNotEqual(read(self.claude_json), original)
        self.ok("--restore")
        self.assertEqual(jload(self.claude_json), json.loads(original))

    def test_restore_keeps_what_claude_wrote_since(self):
        write(self.claude_json, json.dumps({"numStartups": 1, "mcpServers": {"mine": {"type": "http", "url": "https://m.example.com"}}}))
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        data = jload(self.claude_json)
        data["numStartups"] = 99  # Claude Code keeps writing its own state
        data["oauthAccount"] = {"x": 1}
        data["mcpServers"]["later"] = {"type": "http", "url": "https://later.example.com"}
        write(self.claude_json, json.dumps(data))
        self.ok("--restore")
        back = jload(self.claude_json)
        self.assertEqual(back["numStartups"], 99)
        self.assertEqual(back["oauthAccount"], {"x": 1})
        self.assertEqual(sorted(back["mcpServers"]), ["later", "mine"])

    def test_restore_never_deletes_a_claude_json_that_holds_other_content(self):
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        self.assertEqual(list(jload(self.claude_json)["mcpServers"]), ["docs"])
        data = jload(self.claude_json)
        data["oauthAccount"] = {"x": 1}
        write(self.claude_json, json.dumps(data))
        self.ok("--restore")
        self.assertEqual(jload(self.claude_json), {"oauthAccount": {"x": 1}})

    def test_restore_removes_a_claude_json_sync_created_and_nothing_else_wrote(self):
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        self.ok("--restore")
        self.assertFalse(os.path.exists(self.claude_json))

    def test_dry_run_prints_server_names_not_the_users_file(self):
        write(self.claude_json, json.dumps({"private": "do-not-print-me"}))
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        r = self.ok("--dry-run")
        self.assertIn("+docs", r.stdout)
        self.assertNotIn("do-not-print-me", r.stdout)

    def test_restore_with_an_unusable_record_changes_nothing_and_says_so(self):
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        before = read(self.claude_json)
        write(os.path.join(self.state, "mcp-owned.json"), "[1]")
        r = self.sync("--restore")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not usable", r.stderr)
        self.assertEqual(read(self.claude_json), before)
        self.assertEqual(read(os.path.join(self.state, "mcp-owned.json")), "[1]")

    def test_backup_of_claude_json_keeps_only_the_servers_and_is_private(self):
        write(self.claude_json, json.dumps({"oauthAccount": {"email": "private-account-data"},
                                            "mcpServers": {"mine": {"type": "http", "url": "https://m.example.com"}}}))
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        backups = os.path.join(self.state, "backups")
        self.assertEqual(oct(os.stat(backups).st_mode & 0o777), "0o700")
        saved = [os.path.join(d, f) for d, _, fs in os.walk(backups) for f in fs]
        self.assertTrue(saved)
        for f in saved:
            self.assertNotIn("private-account-data", read(f))
        self.ok("--restore")
        back = jload(self.claude_json)
        self.assertEqual(back["oauthAccount"], {"email": "private-account-data"})
        self.assertEqual(list(back["mcpServers"]), ["mine"])

    def test_restore_does_not_recreate_a_vanished_claude_json(self):
        write(self.claude_json, json.dumps({"numStartups": 1}))
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        os.remove(self.claude_json)
        r = self.ok("--restore")
        self.assertFalse(os.path.exists(self.claude_json))
        self.assertIn("no longer exists", r.stderr)

    def test_an_older_backups_folder_is_made_private(self):
        os.makedirs(os.path.join(self.state, "backups"), mode=0o755)
        os.chmod(os.path.join(self.state, "backups"), 0o755)
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        self.assertEqual(oct(os.stat(os.path.join(self.state, "backups")).st_mode & 0o777), "0o700")

    def test_sync_refuses_to_overwrite_a_users_server_under_an_owned_name(self):
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        data = jload(self.claude_json)
        data["mcpServers"]["docs"] = {"command": "USERS-OWN"}
        write(self.claude_json, json.dumps(data))
        r = self.sync()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not what sync wrote", r.stderr)
        self.assertEqual(jload(self.claude_json)["mcpServers"]["docs"], {"command": "USERS-OWN"})

    def test_missing_shared_list_stops_the_run_and_changes_nothing(self):
        self.mcp_toml('[servers.docs]\ntools = ["claude"]\nurl = "https://docs.example.com/mcp"\n')
        self.ok()
        before = read(self.claude_json)
        os.remove(os.path.join(self.repo, "harness", "mcp.toml"))
        r = self.sync()
        self.assertEqual(r.returncode, 2)
        self.assertIn("missing shared list", r.stderr)
        self.assertEqual(read(self.claude_json), before)


class Project(Fixture):
    def setUp(self):
        super().setUp()
        self.proj = os.path.join(self.tmp, "proj")
        write(os.path.join(self.proj, ".nousfx", "mcp.toml"),
              '[servers.p]\ntools = ["claude", "agy"]\nurl = "https://p.example.com/mcp"\n'
              '[servers.q]\ntools = ["agy"]\nurl = "https://q.example.com/mcp"\n')

    def test_project_entries_land_only_in_project_files(self):
        self.ok("--project", self.proj, "--tools", "claude,agy")
        mcp = jload(os.path.join(self.proj, ".mcp.json"))["mcpServers"]
        self.assertEqual(list(mcp), ["p"])
        agy = os.path.join(self.proj, ".agents", "plugins", "nousfx")
        self.assertEqual(sorted(jload(os.path.join(agy, "mcp_config.json"))["mcpServers"]), ["p", "q"])
        self.assertEqual(jload(os.path.join(agy, "plugin.json"))["name"], "nousfx")
        self.assertEqual(os.listdir(self.home), [])  # nothing global: no rules, no skills, no atlassian
        self.assertNotIn("atlassian", read(os.path.join(agy, "mcp_config.json")))

    def test_project_second_run_and_restore(self):
        self.ok("--project", self.proj, "--tools", "claude,agy")
        rec = os.path.join(self.proj, ".nousfx", "mcp-owned.json")
        self.assertIn("p", jload(rec)["claude"][".mcp.json"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "mcp-owned.json")))
        self.assertTrue(os.stat(rec).st_mode & 0o077)  # a repo file, not a private one
        before = self.snapshot()
        self.assertIn("nothing to change", self.ok("--project", self.proj, "--tools", "claude,agy").stdout)
        self.assertEqual(self.snapshot(), before)
        self.ok("--restore")
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".mcp.json")))
        self.assertFalse(os.path.exists(rec))
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".agents", "plugins", "nousfx", "mcp_config.json")))

    def test_project_without_a_check_command_gets_a_note(self):
        out = self.ok("--project", self.proj, "--tools", "claude,agy").stdout
        self.assertIn("no check command", out)
        self.assertIn(".nousfx/project.toml", out)

    def test_a_fresh_checkout_of_a_synced_project_checks_clean(self):
        self.ok("--project", self.proj, "--tools", "claude")
        clone = os.path.join(self.tmp, "clone")
        shutil.copytree(self.proj, clone)
        shutil.rmtree(self.state)
        r = self.sync("--project", clone, "--tools", "claude", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_restore_after_migration_puts_both_records_back(self):
        self.ok("--project", self.proj, "--tools", "claude")
        rec = os.path.join(self.proj, ".nousfx", "mcp-owned.json")
        mcp = os.path.join(self.proj, ".mcp.json")
        legacy = jload(rec)["claude"][".mcp.json"]
        os.remove(rec)
        state_rec = os.path.join(self.state, "mcp-owned.json")
        write(state_rec, json.dumps({"claude": {mcp: legacy}}))
        write(os.path.join(self.proj, ".nousfx", "mcp.toml"),
              '[servers.p]\ntools = ["claude"]\nurl = "https://p2.example.com/mcp"\n')
        self.ok("--project", self.proj, "--tools", "claude")
        self.assertEqual(jload(mcp)["mcpServers"]["p"]["url"], "https://p2.example.com/mcp")
        self.assertEqual(jload(state_rec)["claude"], {})
        self.ok("--restore")
        self.assertEqual(jload(mcp)["mcpServers"]["p"]["url"], "https://p.example.com/mcp")
        self.assertEqual(jload(state_rec)["claude"], {mcp: legacy})
        self.assertFalse(os.path.exists(rec))

    def test_restore_keeps_the_repo_mode_of_mcp_json(self):  # NFX-100
        self.ok("--project", self.proj, "--tools", "claude")
        mcp = os.path.join(self.proj, ".mcp.json")
        os.chmod(mcp, 0o644)
        write(os.path.join(self.proj, ".nousfx", "mcp.toml"),
              '[servers.p]\ntools = ["claude"]\nurl = "https://p2.example.com/mcp"\n')
        self.ok("--project", self.proj, "--tools", "claude")
        self.ok("--restore")
        self.assertEqual(jload(mcp)["mcpServers"]["p"]["url"], "https://p.example.com/mcp")
        self.assertEqual(os.stat(mcp).st_mode & 0o777, 0o644)

    def test_restore_refuses_a_repo_record_without_hashes(self):  # NFX-100
        self.ok("--project", self.proj, "--tools", "claude")
        write(os.path.join(self.proj, ".nousfx", "mcp.toml"),
              '[servers.p]\ntools = ["claude"]\nurl = "https://p2.example.com/mcp"\n')
        self.ok("--project", self.proj, "--tools", "claude")
        mcp = os.path.join(self.proj, ".mcp.json")
        before = open(mcp, encoding="utf-8").read()
        write(os.path.join(self.proj, ".nousfx", "mcp-owned.json"), json.dumps({"claude": {".mcp.json": ["p"]}}))
        r = self.sync("--restore")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is not usable", r.stdout + r.stderr)
        self.assertEqual(open(mcp, encoding="utf-8").read(), before)

    def test_restore_works_with_a_corrupt_state_record_when_the_repo_has_one(self):  # NFX-100
        self.ok("--project", self.proj, "--tools", "claude")
        write(os.path.join(self.state, "mcp-owned.json"), "{not json")
        write(os.path.join(self.proj, ".nousfx", "mcp.toml"),
              '[servers.p]\ntools = ["claude"]\nurl = "https://p2.example.com/mcp"\n')
        self.ok("--project", self.proj, "--tools", "claude")
        self.ok("--restore")
        self.assertEqual(jload(os.path.join(self.proj, ".mcp.json"))["mcpServers"]["p"]["url"],
                         "https://p.example.com/mcp")

    def test_project_with_a_check_command_gets_no_note(self):
        write(os.path.join(self.proj, ".nousfx", "project.toml"), '[check]\ncommand = "npm test"\n')
        out = self.ok("--project", self.proj, "--tools", "claude,agy").stdout
        self.assertNotIn("no check command", out)

    def test_project_with_an_empty_check_command_gets_the_note(self):
        write(os.path.join(self.proj, ".nousfx", "project.toml"), '[check]\ncommand = ""\n')
        self.assertIn("no check command", self.ok("--project", self.proj, "--tools", "claude,agy").stdout)

    def test_project_must_be_a_folder(self):
        self.assertEqual(self.sync("--project", os.path.join(self.tmp, "nope")).returncode, 2)

    def test_symlink_planted_inside_the_project_is_refused(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.proj, ".agents"))
        r = self.sync("--project", self.proj, "--tools", "claude,agy")
        self.assertEqual(r.returncode, 2)
        self.assertIn("symlink", r.stderr)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_a_linked_state_folder_does_not_block_project_mode(self):
        real = os.path.join(self.tmp, "real")
        os.makedirs(real)
        os.symlink(real, os.path.join(self.tmp, "dotfiles-link"))
        self.state = os.path.join(self.tmp, "dotfiles-link", "nousfx")
        write(os.path.join(self.state, "local.md"), "x\n")
        self.ok("--project", self.proj, "--tools", "claude")
        self.assertTrue(os.path.exists(os.path.join(self.proj, ".mcp.json")))

    def files(self, *rel):
        return os.path.join(self.proj, *rel)

    def test_a_new_repo_gets_the_expected_files_and_a_second_run_changes_nothing(self):
        write(self.files(".claude", "skills", "mine", "SKILL.md"), "---\nname: mine\n---\nx\n")
        self.ok("--project", self.proj, "--tools", "claude,agy")
        self.assertEqual(read(self.files("CLAUDE.md")).split()[0], "@AGENTS.md")
        self.assertTrue(os.path.isfile(self.files("AGENTS.md")))
        hook = self.files(".githooks", "pre-push")
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertEqual(read(hook).splitlines()[0], "#!/bin/sh")
        self.assertIn("generated by nousfx_sync", read(hook).splitlines()[1])
        link = self.files(".agents", "skills")
        self.assertEqual(os.readlink(link), os.path.join("..", ".claude", "skills"))
        self.assertTrue(os.path.isfile(os.path.join(link, "mine", "SKILL.md")))
        for base, _, names in os.walk(self.proj):  # the gate is never copied into the repo, nothing personal is written
            for n in names:
                self.assertNotIn(n, ("review-gate.py", "agy_gate.py"))
                if not os.path.islink(os.path.join(base, n)):
                    self.assertNotIn(self.home, read(os.path.join(base, n)), n)
        before = self.snapshot()
        self.assertIn("nothing to change", self.ok("--project", self.proj, "--tools", "claude,agy").stdout)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.sync("--project", self.proj, "--tools", "claude,agy", "--check").returncode, 0)

    def test_restore_removes_what_a_run_created(self):
        write(self.files(".claude", "skills", "mine", "SKILL.md"), "x\n")
        self.ok("--project", self.proj, "--tools", "claude")
        self.ok("--restore")
        for rel in (("CLAUDE.md",), ("AGENTS.md",), (".githooks", "pre-push"), (".agents", "skills")):
            self.assertFalse(os.path.lexists(self.files(*rel)), rel)

    def test_dry_run_writes_nothing_and_lists_the_files(self):
        r = self.ok("--project", self.proj, "--dry-run")
        self.assertIn("CLAUDE.md", r.stdout)
        self.assertIn("pre-push", r.stdout)
        self.assertFalse(os.path.exists(self.files("CLAUDE.md")))
        self.assertFalse(os.path.exists(self.files(".githooks")))

    def test_hand_written_claude_file_without_the_import_is_kept_and_the_diff_shown(self):
        write(self.files("CLAUDE.md"), "# My rules\nUse tabs.\n")
        r = self.ok("--project", self.proj, "--tools", "claude")
        self.assertEqual(read(self.files("CLAUDE.md")), "# My rules\nUse tabs.\n")
        self.assertIn("+@AGENTS.md", r.stdout)
        self.assertIn("--yes-replace CLAUDE.md", r.stdout)
        self.ok("--project", self.proj, "--tools", "claude", "--yes-replace", "CLAUDE.md")
        self.assertEqual(read(self.files("CLAUDE.md")), "@AGENTS.md\n\n# My rules\nUse tabs.\n")

    def test_claude_file_that_already_imports_agents_is_never_touched(self):
        write(self.files("CLAUDE.md"), "@AGENTS.md\n\n# Claude only\n")
        write(self.files("AGENTS.md"), "# Mine\n")
        r = self.ok("--project", self.proj, "--tools", "claude")
        self.assertEqual(read(self.files("CLAUDE.md")), "@AGENTS.md\n\n# Claude only\n")
        self.assertEqual(read(self.files("AGENTS.md")), "# Mine\n")
        self.assertNotIn("hand-written", r.stdout)

    def test_hand_written_pre_push_hook_is_kept_until_confirmed(self):
        write(self.files(".githooks", "pre-push"), "#!/bin/sh\necho mine\n")
        r = self.ok("--project", self.proj, "--tools", "claude")
        self.assertEqual(read(self.files(".githooks", "pre-push")), "#!/bin/sh\necho mine\n")
        self.assertIn("-echo mine", r.stdout)
        self.assertEqual(self.sync("--project", self.proj, "--tools", "claude", "--check").returncode, 0)
        self.ok("--project", self.proj, "--tools", "claude", "--yes-replace", ".githooks/pre-push")
        self.assertIn("generated by nousfx_sync", read(self.files(".githooks", "pre-push")))

    def test_check_exits_non_zero_on_a_hand_edited_generated_file(self):
        self.ok("--project", self.proj, "--tools", "claude")
        hook = self.files(".githooks", "pre-push")
        with open(hook, "a", encoding="utf-8") as f:
            f.write("# edited\n")
        r = self.sync("--project", self.proj, "--tools", "claude", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"drift: {hook}", r.stdout)
        self.ok("--project", self.proj, "--tools", "claude")
        self.assertNotIn("# edited", read(hook))

    def test_check_flags_a_hook_without_the_executable_bit(self):
        self.ok("--project", self.proj, "--tools", "claude")
        hook = self.files(".githooks", "pre-push")
        os.chmod(hook, 0o644)
        r = self.sync("--project", self.proj, "--tools", "claude", "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not executable", r.stdout)
        self.ok("--project", self.proj, "--tools", "claude")
        self.assertTrue(os.access(hook, os.X_OK))

    def test_dry_run_counts_a_hook_that_only_needs_its_executable_bit(self):
        self.ok("--project", self.proj, "--tools", "claude")
        os.chmod(self.files(".githooks", "pre-push"), 0o644)
        self.assertIn("1 file(s) would change", self.ok("--project", self.proj, "--dry-run").stdout)

    def test_a_planted_agents_link_is_refused_before_the_skills_link_is_made(self):
        write(self.files(".claude", "skills", "mine", "SKILL.md"), "x\n")
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, self.files(".agents"))
        r = self.sync("--project", self.proj, "--tools", "claude")
        self.assertEqual(r.returncode, 2)
        self.assertIn("symlink", r.stderr)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_restore_removes_a_skills_link_whose_target_is_gone(self):
        write(self.files(".claude", "skills", "mine", "SKILL.md"), "x\n")
        self.ok("--project", self.proj, "--tools", "claude")
        shutil.rmtree(self.files(".claude"))
        self.ok("--restore")
        self.assertFalse(os.path.lexists(self.files(".agents", "skills")))

    def test_new_repo_files_are_not_private_and_a_replaced_file_keeps_its_mode(self):
        self.ok("--project", self.proj, "--tools", "claude")
        self.assertEqual(os.stat(self.files("CLAUDE.md")).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(self.files(".githooks", "pre-push")).st_mode & 0o777, 0o755)
        write(self.files("CLAUDE.md"), "# mine\n")
        os.chmod(self.files("CLAUDE.md"), 0o664)
        self.ok("--project", self.proj, "--tools", "claude", "--yes-replace", "CLAUDE.md")
        self.assertEqual(os.stat(self.files("CLAUDE.md")).st_mode & 0o777, 0o664)

    def test_unreadable_repo_files_end_in_a_clean_error(self):
        os.makedirs(self.files(".githooks", "pre-push"))  # a folder where the hook should be
        r = self.sync("--project", self.proj, "--tools", "claude")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        shutil.rmtree(self.files(".githooks"))
        with open(self.files("CLAUDE.md"), "wb") as f:
            f.write(b"\xff\xfe not utf-8")
        r = self.sync("--project", self.proj, "--tools", "claude")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_claude_file_linked_to_agents_is_already_satisfied(self):
        write(self.files("AGENTS.md"), "# Mine\n")
        os.symlink("AGENTS.md", self.files("CLAUDE.md"))
        r = self.ok("--project", self.proj, "--tools", "claude")
        self.assertNotIn("hand-written", r.stdout)
        self.assertTrue(os.path.islink(self.files("CLAUDE.md")))

    def test_dry_run_names_the_hook_it_would_make_executable(self):
        self.ok("--project", self.proj, "--tools", "claude")
        os.chmod(self.files(".githooks", "pre-push"), 0o644)
        self.assertIn("would make", self.ok("--project", self.proj, "--dry-run").stdout)

    def test_restore_keeps_a_stub_the_user_has_since_written(self):
        self.ok("--project", self.proj, "--tools", "claude")
        write(self.files("AGENTS.md"), "# My real rules\n")
        r = self.ok("--restore")
        self.assertEqual(read(self.files("AGENTS.md")), "# My real rules\n")
        self.assertIn("changed since", r.stderr)
        self.assertFalse(os.path.exists(self.files(".githooks", "pre-push")))  # untouched files are still removed

    def test_restore_leaves_a_folder_that_replaced_the_skills_link(self):
        write(self.files(".claude", "skills", "mine", "SKILL.md"), "x\n")
        self.ok("--project", self.proj, "--tools", "claude")
        os.remove(self.files(".agents", "skills"))
        write(self.files(".agents", "skills", "keep.txt"), "k\n")
        r = self.sync("--restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(self.files(".agents", "skills", "keep.txt")))

    def test_mcp_files_in_a_project_are_not_private_either(self):
        self.ok("--project", self.proj, "--tools", "claude,agy")
        self.assertEqual(os.stat(self.files(".mcp.json")).st_mode & 0o777, 0o644)

    def test_a_claude_symlink_to_somewhere_else_is_left_alone_without_reading_it(self):
        secret = os.path.join(self.tmp, "outside.md")
        write(secret, "OUTSIDE-CONTENT\n")
        os.symlink(secret, self.files("CLAUDE.md"))
        r = self.ok("--project", self.proj, "--tools", "claude")
        self.assertNotIn("OUTSIDE-CONTENT", r.stdout)
        self.assertIn("symlink", r.stdout)

    def test_a_failed_link_gives_the_restore_hint_not_a_traceback(self):
        write(self.files(".claude", "skills", "mine", "SKILL.md"), "x\n")
        os.makedirs(self.files(".agents"))  # a folder sync cannot write into: the link step fails
        os.chmod(self.files(".agents"), 0o555)
        try:
            r = self.sync("--project", self.proj, "--tools", "claude")
        finally:
            os.chmod(self.files(".agents"), 0o755)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("--restore", r.stderr)

    def test_a_hook_that_is_a_link_is_never_read(self):
        secret = os.path.join(self.tmp, "credentials")
        write(secret, "TOP-SECRET-VALUE\n")
        os.makedirs(self.files(".githooks"))
        os.symlink(secret, self.files(".githooks", "pre-push"))
        r = self.ok("--project", self.proj, "--tools", "claude", "--dry-run")
        self.assertNotIn("TOP-SECRET-VALUE", r.stdout + r.stderr)
        self.assertIn("symlink", r.stdout)
        os.remove(self.files(".githooks", "pre-push"))
        os.rmdir(self.files(".githooks"))
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        write(os.path.join(elsewhere, "pre-push"), "TOP-SECRET-VALUE\n")
        os.symlink(elsewhere, self.files(".githooks"))
        r = self.ok("--project", self.proj, "--tools", "claude", "--dry-run")
        self.assertNotIn("TOP-SECRET-VALUE", r.stdout + r.stderr)

    def test_restore_does_not_crash_on_a_stub_that_became_binary(self):
        self.ok("--project", self.proj, "--tools", "claude")
        with open(self.files("AGENTS.md"), "wb") as f:
            f.write(b"\xff\xfe\x00 binary")
        r = self.sync("--restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue(os.path.exists(self.files("AGENTS.md")))

    def test_existing_skills_link_or_folder_is_left_alone(self):
        write(self.files(".claude", "skills", "mine", "SKILL.md"), "x\n")
        write(self.files(".agents", "skills", "other", "SKILL.md"), "y\n")
        self.ok("--project", self.proj, "--tools", "claude")
        self.assertFalse(os.path.islink(self.files(".agents", "skills")))
        self.assertTrue(os.path.isfile(self.files(".agents", "skills", "other", "SKILL.md")))

    def add_copy_sources(self):
        """review-loop and the agents, as NousFX ships them (the gate is already in the fixture repo)."""
        for rel in (("claude", "skills", "review-loop", "SKILL.md"),
                    ("claude", "skills", "review-loop", "scripts", "tier.py"),
                    ("claude", "skills", "review-loop", "references", "fable.md"),
                    ("claude", "skills", "review-loop", "references", "gemini.md"), ("harness", "models.toml")):
            write(os.path.join(self.repo, *rel), read(os.path.join(ROOT, *rel)))
        shutil.copytree(os.path.join(ROOT, "harness", "agents"), os.path.join(self.repo, "harness", "agents"))

    COPIES = ((".claude", "hooks", "review-gate.py"), (".claude", "hooks", "pr_create_parser.py"),
              (".claude", "agents", "reviewer.md"), (".claude", "skills", "review-loop", "SKILL.md"),
              (".claude", "skills", "review-loop", "scripts", "tier.py"),
              (".claude", "skills", "review-loop", "references", "fable.md"),
              (".claude", "skills", "review-loop", "references", "gemini.md"))

    def test_stale_copies_are_drift_and_a_run_refreshes_them(self):
        self.add_copy_sources()
        for rel in self.COPIES:
            write(self.files(*rel), "stale\n")
        write(self.files(".claude", "agents", "mine.md"), "repo only\n")
        r = self.sync("--project", self.proj, "--tools", "claude", "--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for rel in self.COPIES:
            self.assertIn(f"drift: {self.files(*rel)}\n", r.stdout)
        self.assertNotIn("mine.md", r.stdout)
        self.assertIn("+++", self.ok("--project", self.proj, "--tools", "claude", "--dry-run").stdout)
        self.assertEqual(read(self.files(*self.COPIES[0])), "stale\n")  # the dry run wrote nothing
        self.ok("--project", self.proj, "--tools", "claude")
        self.assertEqual(read(self.files(".claude", "hooks", "review-gate.py")),
                         read(os.path.join(ROOT, "claude", "hooks", "review-gate.py")))
        self.assertEqual(read(self.files(".claude", "skills", "review-loop", "SKILL.md")),
                         read(os.path.join(ROOT, "claude", "skills", "review-loop", "SKILL.md")))
        self.assertIn("generated by nousfx_sync", read(self.files(".claude", "agents", "reviewer.md")))
        self.assertEqual(read(self.files(".claude", "agents", "mine.md")), "repo only\n")
        self.assertFalse(os.path.exists(self.files(".claude", "agents", "test-runner.md")))  # never created
        r = self.sync("--project", self.proj, "--tools", "claude", "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.ok("--restore")
        self.assertEqual(read(self.files(".claude", "hooks", "review-gate.py")), "stale\n")

    def test_a_repo_without_copies_gets_none(self):
        self.add_copy_sources()
        self.ok("--project", self.proj, "--tools", "claude")
        for rel in self.COPIES:
            self.assertFalse(os.path.lexists(self.files(*rel)), rel)
        self.assertEqual(self.sync("--project", self.proj, "--tools", "claude", "--check").returncode, 0)

    def test_the_skill_copy_brings_its_references(self):
        """NFX-114: a repo that keeps SKILL.md gets the two reference files too, so the stubs never dangle."""
        self.add_copy_sources()
        write(self.files(".claude", "skills", "review-loop", "SKILL.md"), "stale\n")
        r = self.sync("--project", self.proj, "--tools", "claude", "--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.ok("--project", self.proj, "--tools", "claude")
        for name in ("fable.md", "gemini.md"):
            rel = ("claude", "skills", "review-loop", "references", name)
            self.assertEqual(read(self.files(".claude", *rel[1:])), read(os.path.join(ROOT, *rel)))
        self.assertFalse(os.path.lexists(self.files(".claude", "skills", "review-loop", "scripts", "tier.py")))
        self.assertEqual(self.sync("--project", self.proj, "--tools", "claude", "--check").returncode, 0)

    def test_a_symlinked_copy_is_left_alone_and_the_run_goes_on(self):
        self.add_copy_sources()
        elsewhere = os.path.join(self.tmp, "elsewhere")
        write(os.path.join(elsewhere, "review-gate.py"), "outside\n")
        os.makedirs(self.files(".claude"))
        os.symlink(elsewhere, self.files(".claude", "hooks"))
        r = self.ok("--project", self.proj, "--tools", "claude")
        self.assertIn("left alone (the copy or a folder above it is a symlink", r.stdout)
        self.assertEqual(read(os.path.join(elsewhere, "review-gate.py")), "outside\n")
        self.assertTrue(os.path.exists(self.files(".mcp.json")))  # the rest of the run was applied
        self.assertEqual(self.sync("--project", self.proj, "--tools", "claude", "--check").returncode, 0)

    def test_an_old_project_hook_gets_a_note_and_the_new_one_none(self):
        settings = self.files(".claude", "settings.json")
        old = 'f="$CLAUDE_PROJECT_DIR/.claude/hooks/review-gate.py"; [ ! -f "$f" ] || python3 "$f"'
        write(settings, json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": old}]}]}}))
        self.assertIn("a hook from before NFX-98", self.ok("--project", self.proj, "--tools", "claude").stdout)
        shutil.copy(os.path.join(ROOT, "claude", "skills", "project-setup", "assets", "settings.json"), settings)
        self.assertNotIn("a hook from before NFX-98", self.ok("--project", self.proj, "--tools", "claude").stdout)

    def test_a_hook_that_runs_the_copy_itself_gets_the_nfx_107_note(self):
        settings = self.files(".claude", "settings.json")
        mid = ('f="$CLAUDE_PROJECT_DIR/.claude/hooks/review-gate.py"; [ -f "$HOME/.claude/hooks/review-gate.py" ] '
               '|| [ ! -f "$f" ] || python3 "$f" --budget 55')
        write(settings, json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": mid}]}]}}))
        out = self.ok("--project", self.proj, "--tools", "claude").stdout
        self.assertIn("a hook from before NFX-107", out)
        self.assertNotIn("a hook from before NFX-98", out)
        shutil.copy(os.path.join(ROOT, "claude", "skills", "project-setup", "assets", "settings.json"), settings)
        self.assertNotIn("a hook from before NFX-107", self.ok("--project", self.proj, "--tools", "claude").stdout)

    def test_project_without_a_list_gets_no_agy_files(self):
        os.remove(os.path.join(self.proj, ".nousfx", "mcp.toml"))
        r = self.sync("--project", self.proj, "--tools", "agy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".agents")))


class ProjectSetupSkill(Base):
    """project-setup is prose, so this pins the two calls it must make into project mode."""
    def test_setup_and_audit_call_project_mode(self):
        text = read(os.path.join(ROOT, "claude", "skills", "project-setup", "SKILL.md"))
        self.assertIn("nousfx_sync.py --project <repo> --check", text)
        self.assertIn("nousfx_sync.py --project <repo> --dry-run", text)
        self.assertIn("--yes-replace", text)
        sync = self.sync("--help").stdout
        for flag in ("--project", "--check", "--dry-run", "--yes-replace"):
            self.assertIn(flag, sync)

    def hook_command(self):
        asset = jload(os.path.join(ROOT, "claude", "skills", "project-setup", "assets", "settings.json"))
        return asset["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    @staticmethod
    def git_in(proj):
        return lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=proj,
                                         capture_output=True, text=True, check=True)

    def cloud_repo(self, origin_head=None, fetched=True):
        """A repo whose committed gate copy is NousFX's; origin/main (and origin/HEAD) point at that commit."""
        proj, home = os.path.join(self.tmp, "proj"), os.path.join(self.tmp, "h")
        for rel in ("hooks/review-gate.py", "hooks/pr_create_parser.py", "skills/review-loop/scripts/tier.py"):
            write(os.path.join(proj, ".claude", *rel.split("/")), read(os.path.join(ROOT, "claude", *rel.split("/"))))
        git = self.git_in(proj)
        git("init", "-q", "-b", "work")
        git("add", "-A")
        git("commit", "-q", "-m", "x")
        if fetched:
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            if origin_head:
                git("update-ref", "refs/remotes/origin/" + origin_head, "HEAD")
                git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/" + origin_head)
        return proj, home

    def run_hook(self, proj, home, command, tool="Bash"):
        env = {"PATH": os.environ["PATH"], "HOME": home, "CLAUDE_PROJECT_DIR": proj}
        payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}, "cwd": proj})
        return subprocess.run(["sh", "-c", self.hook_command()], env=env, input=payload, capture_output=True,
                              text=True, cwd=self.tmp)

    def test_project_hook_runs_the_repo_gate_only_without_a_global_one(self):
        """NFX-98: the repo copy is the cloud fallback, so the project hook skips it when ~/.claude has a gate."""
        proj, home = self.cloud_repo()
        gate = os.path.join(proj, ".claude", "hooks", "review-gate.py")
        write(gate, "print('repo gate')\n")  # edited, uncommitted: must never run
        self.assertNotIn("repo gate", self.run_hook(proj, home, "ls").stdout)
        write(os.path.join(home, ".claude", "hooks", "review-gate.py"), "print('global')\n")
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))  # local: the global hook judges

    def test_edited_gate_copy_blocks_a_pr_and_never_runs(self):
        """NFX-107 AC1: cloud sessions judge with main's gate, which blocks a PR when the repo copy differs."""
        proj, home = self.cloud_repo()
        gate = os.path.join(proj, ".claude", "hooks", "review-gate.py")
        write(gate, read(gate) + "\nprint('EDITED COPY RAN')\nraise SystemExit(0)\n")
        for command in ("gh pr create --title x", "git status && gh pr create"):
            r = self.run_hook(proj, home, command)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("the gate copy differs from main", r.stderr)
            self.assertNotIn("EDITED COPY RAN", r.stdout + r.stderr)
        r = self.run_hook(proj, home, "x", tool="mcp__github__create_pull_request")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("the gate copy differs from main", r.stderr)
        r = self.run_hook(proj, home, "ls -la")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))  # other commands keep working

    def test_a_copy_that_is_not_a_regular_file_blocks_instead_of_hanging(self):
        proj, home = self.cloud_repo()
        gate = os.path.join(proj, ".claude", "hooks", "review-gate.py")
        os.remove(gate)
        os.mkfifo(gate)
        r = subprocess.run(["sh", "-c", self.hook_command()], env={"PATH": os.environ["PATH"], "HOME": home,
                           "CLAUDE_PROJECT_DIR": proj}, input=json.dumps({"tool_name": "Bash", "tool_input": {
                               "command": "gh pr create"}, "cwd": proj}), capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("the gate copy differs from main", r.stderr)
        os.remove(gate)
        os.symlink("/dev/zero", gate)
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("the gate copy differs from main", r.stderr)

    def test_edited_parser_copy_blocks_a_pr_too(self):
        proj, home = self.cloud_repo()
        parser = os.path.join(proj, ".claude", "hooks", "pr_create_parser.py")
        write(parser, read(parser) + "\n# edited\n")
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("the gate copy differs from main", r.stderr)

    def test_unchanged_copy_is_judged_normally(self):
        proj, home = self.cloud_repo()
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)  # no review recorded: the gate's own block
        self.assertNotIn("differs from main", r.stderr)
        self.assertNotIn("fetch origin", r.stderr)

    def test_unfetched_origin_blocks_a_pr_create_only(self):
        proj, home = self.cloud_repo(fetched=False)
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("fetch origin first", r.stderr)
        r = self.run_hook(proj, home, "x", tool="mcp__github__create_pull_request")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = self.run_hook(proj, home, "ls")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_origin_head_picks_the_default_branch_from_a_short_list(self):
        """origin/HEAD counts when it is main, master, develop or trunk; any other name falls back to origin/main."""
        for head, read_ref, other_ref in (("develop", "develop", "main"), ("trunk", "trunk", "main"),
                                          ("elsewhere", "main", "elsewhere")):
            with self.subTest(origin_head=head):
                shutil.rmtree(os.path.join(self.tmp, "proj"), ignore_errors=True)
                proj, home = self.cloud_repo(origin_head=head)
                git = self.git_in(proj)
                gate = os.path.join(proj, ".claude", "hooks", "review-gate.py")
                write(gate, read(gate) + "\n# a newer gate\n")
                git("commit", "-q", "-a", "-m", "newer gate")
                git("update-ref", "refs/remotes/origin/" + read_ref, "HEAD~1")  # old gate: differs from the copy
                git("update-ref", "refs/remotes/origin/" + other_ref, "HEAD")  # same as the copy: would not block
                r = self.run_hook(proj, home, "gh pr create")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("differs from main", r.stderr)

    def test_a_deleted_gate_copy_still_blocks_a_pr(self):
        """A branch that deletes the copy must not switch the gate off: main still has one to compare with."""
        proj, home = self.cloud_repo()
        os.remove(os.path.join(proj, ".claude", "hooks", "review-gate.py"))
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("the gate copy differs from main", r.stderr)

    def test_a_failed_extract_blocks_even_when_the_branch_deleted_its_copy(self):
        """Main has a gate but its parser is missing, so the extract fails: a deleted copy must not pass."""
        proj, home = self.cloud_repo()
        git = self.git_in(proj)
        git("rm", "-q", ".claude/hooks/pr_create_parser.py")
        git("commit", "-q", "-m", "no parser")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        shutil.rmtree(os.path.join(proj, ".claude"))
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("could not be extracted", r.stderr)
        self.assertEqual(self.run_hook(proj, home, "ls").returncode, 0)

    def test_other_ways_to_open_a_pr_are_blocked_when_origin_is_unfetched(self):
        proj, home = self.cloud_repo(fetched=False)
        for command in ("gh pr new", "gh pr cre''ate --title x", "X=create; gh pr $X", "hub pull-request -m x",
                        "gh api graphql -f query='mutation { createPullRequest(input:{}) { pullRequest { url } } }'", "gh api repos/o/r/pulls -f title=x", "glab mr create"):
            with self.subTest(command=command):
                r = self.run_hook(proj, home, command)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("fetch origin first", r.stderr)

    def test_unrelated_words_are_not_blocked_when_origin_is_unfetched(self):
        proj, home = self.cloud_repo(fetched=False)
        for command in ("cat high_priority.txt", 'git commit -m "fix right-hand problem"', "ls"):
            with self.subTest(command=command):
                r = self.run_hook(proj, home, command)
                self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_main_without_a_gate_copy_blocks_only_where_the_branch_adds_one(self):
        """The PR that first adds the copy cannot be judged in the cloud; a repo with no copy at all is left alone."""
        proj, home = self.cloud_repo()
        git = self.git_in(proj)
        git("rm", "-q", "-r", "--cached", ".claude")
        git("commit", "-q", "-m", "drop the copy")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        r = self.run_hook(proj, home, "gh pr create")  # the working tree still has a copy: it is being added
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no review gate copy to compare with", r.stderr)
        shutil.rmtree(os.path.join(proj, ".claude"))
        r = self.run_hook(proj, home, "gh pr create")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_parallel_hooks_on_a_fresh_session_all_pass(self):
        """Eight hooks racing to extract the gate must not delete each other's files."""
        proj, home = self.cloud_repo()
        env = {"PATH": os.environ["PATH"], "HOME": home, "CLAUDE_PROJECT_DIR": proj}
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": proj})
        procs = [subprocess.Popen(["sh", "-c", self.hook_command()], env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
        for pr in procs:
            out, err = pr.communicate(payload)
            self.assertEqual((pr.returncode, err), (0, ""), out)

    def test_the_extracted_gate_is_reused_while_main_is_unchanged(self):
        proj, home = self.cloud_repo()
        gate = os.path.join(subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=proj, text=True,
                                           capture_output=True).stdout.strip(), "nousfx-gate", "current", "hooks", "review-gate.py")
        self.run_hook(proj, home, "ls")
        first = os.stat(gate).st_ino
        self.run_hook(proj, home, "ls")
        self.assertEqual(os.stat(gate).st_ino, first)  # parallel hooks never delete the gate another one is running

    def test_project_hook_runs_isolated(self):
        """NFX-107 AC2: python3 -I -B, and the copy it runs is main's, outside the working tree."""
        command = self.hook_command()
        self.assertIn('exec python3 -I -B "$d/hooks/review-gate.py"', command)
        self.assertNotIn('python3 "$f"', command)

    def test_skill_and_gate_doc_agree_on_the_repo_gate_copy(self):
        """NFX-98: locally the global gate counts; the repo copy is only the cloud fallback."""
        skill = read(os.path.join(ROOT, "claude", "skills", "project-setup", "SKILL.md"))
        self.assertNotIn("show-toplevel)/.claude/hooks/review-gate.py", skill)
        self.assertIn("Do not point its gate at the repo copy", skill)
        doc = read(os.path.join(ROOT, "docs", "review-gate.md"))
        self.assertIn("only a fallback for cloud sessions", doc)
        self.assertIn("never adds one", doc)


class GateBudget(unittest.TestCase):
    """NFX-102: every installed gate hook passes --budget, and its timeout leaves room for the gate to block."""

    def assert_agrees(self, hook):
        m = re.search(r"--budget (\d+)", hook["command"])
        self.assertTrue(m, hook["command"])
        budget = int(m.group(1))
        self.assertEqual(budget, nousfx_sync.GATE_BUDGET)
        self.assertEqual(hook["timeout"], nousfx_sync.GATE_TIMEOUT)
        self.assertGreater(hook["timeout"], budget)

    def test_home_hook(self):
        for win in (False, True):
            with self.subTest(win=win):
                cfg = json.loads(nousfx_sync.merge_settings("", "/h/.claude/hooks/review-gate.py", win, prefix="me"))
                self.assert_agrees(cfg["hooks"]["PreToolUse"][0]["hooks"][0])

    def test_project_asset(self):
        cfg = jload(os.path.join(ROOT, "claude", "skills", "project-setup", "assets", "settings.json"))
        self.assert_agrees(cfg["hooks"]["PreToolUse"][0]["hooks"][0])

    def test_agy_hook(self):
        cfg = json.loads(nousfx_agy.hooks_text("/a/agy_gate.py", "python3", ["a" * 64] * 3))
        self.assert_agrees(cfg["nousfx-review-gate"]["PreToolUse"][0]["hooks"][0])


if __name__ == "__main__":
    unittest.main()
