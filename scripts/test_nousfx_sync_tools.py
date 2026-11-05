#!/usr/bin/env python3
"""CLI tests for --tools and --project of scripts/nousfx_sync.py (agy plugin and MCP files).
Run: python3 -B -m unittest test_nousfx_sync_tools   (from scripts/)

Same seam as test_nousfx_sync.py: the CLI as a subprocess against a temp home, state home and repo.
"""
import json
import os
import shutil
import unittest

import nousfx_agy
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
        write(os.path.join(self.repo, "claude", "skills", "demo", "SKILL.md"), "---\nname: demo\n---\nx\n")
        self.plugin = os.path.join(self.home, ".gemini", "config", "plugins", "nousfx")
        self.agy_rules = os.path.join(self.plugin, "rules", "AGENTS.md")
        self.agy_mcp = os.path.join(self.plugin, "mcp_config.json")
        self.claude_json = os.path.join(self.home, ".claude.json")

    def ok(self, *args):
        r = self.sync(*args)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r


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
        before = self.snapshot()
        self.assertIn("nothing to change", self.ok("--project", self.proj, "--tools", "claude,agy").stdout)
        self.assertEqual(self.snapshot(), before)
        self.ok("--restore")
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".mcp.json")))
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".agents", "plugins", "nousfx", "mcp_config.json")))

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

    def test_project_without_a_list_gets_no_agy_files(self):
        os.remove(os.path.join(self.proj, ".nousfx", "mcp.toml"))
        r = self.sync("--project", self.proj, "--tools", "agy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".agents")))


if __name__ == "__main__":
    unittest.main()
