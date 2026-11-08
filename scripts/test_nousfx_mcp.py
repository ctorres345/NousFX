#!/usr/bin/env python3
"""Tests for scripts/nousfx_mcp.py. Run: python3 -B scripts/test_nousfx_mcp.py

Temp dirs only. The module computes {abs path: text}; these tests play the part of sync's writer.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import nousfx_mcp as M
from nousfx_sync import SyncError


PATTERNS = [re.compile(p) for p in (r"gh[pousr]_[A-Za-z0-9]{30,}", r"sk-[A-Za-z0-9]{32,}")]
TOKEN = "ghp_" + "A1b2C3d4" * 4 + "wxyz"

BASE = '''
[servers.atlassian]
tools = ["agy"]
url = "https://mcp.example.com/v1/mcp"

[servers.both]
tools = ["claude", "agy"]
command = "npx"
args = ["-y", "thing"]
env = { THING_KEY = "${THING_KEY}" }

[servers.claudeonly]
tools = ["claude"]
url = "https://c.example.com/mcp"
'''


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nfxmcp-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo, self.home, self.state = (os.path.join(self.tmp, d) for d in ("repo", "home", "state"))
        for d in (self.repo, self.home, self.state):
            os.makedirs(d)
        self.claude = os.path.join(self.home, M.CLAUDE_JSON_REL)
        self.agy = os.path.join(self.home, M.AGY_MCP_REL)
        self.owned = os.path.join(self.state, "mcp-owned.json")

    def shared(self, text=BASE):
        put(os.path.join(self.repo, "harness", "mcp.toml"), text)

    def plan(self, tools=("claude", "agy"), project=None):
        return M.plan_mcp(self.repo, self.home, self.state, set(tools), SyncError, PATTERNS, project)

    def apply(self, result):
        for p, t in result.items():
            put(p, t)
        return result

    def fails(self, text, code=1, **kw):
        self.shared(text)
        with self.assertRaises(SyncError) as c:
            self.plan(**kw)
        self.assertEqual(c.exception.code, code)
        self.assertNotIn(TOKEN, str(c.exception))
        return str(c.exception)


class Outputs(Base):
    def test_each_tool_gets_only_its_entries(self):
        self.shared()
        r = self.plan()
        agy = json.loads(r[self.agy])["mcpServers"]
        self.assertEqual(agy, {"atlassian": {"url": "https://mcp.example.com/v1/mcp"},
                               "both": {"command": "npx", "args": ["-y", "thing"],
                                        "env": {"THING_KEY": "${THING_KEY}"}}})
        cl = json.loads(r[self.claude])["mcpServers"]
        self.assertEqual(cl, {"both": {"type": "stdio", "command": "npx", "args": ["-y", "thing"],
                                       "env": {"THING_KEY": "${THING_KEY}"}},
                              "claudeonly": {"type": "http", "url": "https://c.example.com/mcp"}})
        self.assertNotIn("atlassian", cl)

    def test_only_selected_tools_run(self):
        self.shared()
        self.assertEqual(list(self.plan(tools=("agy",))), [self.agy])
        self.assertEqual(set(self.plan(tools=("claude",))), {self.claude, self.owned})

    def test_empty_agy_list_still_writes_file(self):
        self.shared('[servers.c]\ntools = ["claude"]\nurl = "https://x.example.com"\n')
        self.assertEqual(json.loads(self.plan(tools=("agy",))[self.agy]), {"mcpServers": {}})

    def test_empty_args_and_env_omitted(self):
        self.shared('[servers.s]\ntools = ["agy"]\ncommand = "tool"\n')
        self.assertEqual(json.loads(self.plan(tools=("agy",))[self.agy])["mcpServers"]["s"], {"command": "tool"})

    def test_tools_is_required(self):
        self.assertIn("tools is required", self.fails('[servers.s]\nurl = "https://x.example.com"\n'))

    def test_real_repo_file(self):
        got = M.load(ROOT, self.state, SyncError, PATTERNS)
        self.assertEqual(list(got), ["atlassian"])
        self.assertEqual(got["atlassian"]["tools"], ["agy"])
        self.assertEqual(got["atlassian"]["url"], "https://mcp.atlassian.com/v1/mcp")

    def test_state_file_wins(self):
        self.shared()
        put(os.path.join(self.state, "mcp.toml"),
            '[servers.atlassian]\ntools = ["agy"]\nurl = "https://mine.example.com"\n')
        got = M.load(self.repo, self.state, SyncError, PATTERNS)
        self.assertEqual(got["atlassian"]["url"], "https://mine.example.com")
        self.assertIn("both", got)


class Validation(Base):
    def test_plaintext_env_fails_without_leaking(self):
        for val in (TOKEN, "hunter2", "pre${X}", "$X"):
            msg = self.fails(f'[servers.s]\ntools = ["agy"]\ncommand = "t"\nenv = {{ K = "{val}" }}\n')
            self.assertIn("s", msg)
            self.assertIn("K", msg)
            self.assertNotIn(val, msg)

    def test_token_in_url_or_args_or_command_fails(self):
        for body in (f'url = "https://x.example.com/{TOKEN}"',
                     f'command = "t"\nargs = ["--k", "{TOKEN}"]',
                     f'command = "{TOKEN}"'):
            self.assertIn("s", self.fails(f'[servers.s]\ntools = ["agy"]\n{body}\n'))

    def test_url_userinfo_and_query_token_fail(self):
        self.fails('[servers.s]\ntools = ["agy"]\nurl = "https://user:pw@x.example.com/m"\n')
        self.fails('[servers.s]\ntools = ["agy"]\nurl = "https://x.example.com/m?api_key=abcdefgh12345"\n')
        self.fails(f'[servers.s]\ntools = ["agy"]\nurl = "https://x.example.com/m?z={TOKEN}"\n')

    def test_unknown_tool_fails(self):
        self.fails('[servers.s]\ntools = ["gemini"]\nurl = "https://x.example.com"\n')

    def test_url_and_command_fails(self):
        self.fails('[servers.s]\ntools = ["agy"]\nurl = "https://x.example.com"\ncommand = "t"\n')
        self.fails('[servers.s]\ntools = ["agy"]\n')

    def test_bad_name_and_unknown_key_fail(self):
        self.fails('[servers."bad name"]\nurl = "https://x.example.com"\n')
        self.fails('[servers.s]\ntools = ["agy"]\nurl = "https://x.example.com"\nheaders = { A = "b" }\n')

    def test_bad_toml_fails(self):
        self.fails("[servers\n")


class ClaudeMerge(Base):
    def test_second_run_is_identical(self):
        self.shared()
        self.apply(self.plan())
        again = self.plan()
        for p, t in again.items():
            with open(p, encoding="utf-8") as f:
                self.assertEqual(f.read(), t, p)

    def test_unrelated_keys_and_servers_survive(self):
        self.shared()
        put(self.claude, json.dumps({"theme": "dark", "projects": {"/a": {"x": 1}},
                                     "mcpServers": {"mine": {"type": "http", "url": "https://é.example.com"}}}))
        got = json.loads(self.plan(tools=("claude",))[self.claude])
        self.assertEqual(got["theme"], "dark")
        self.assertEqual(got["projects"], {"/a": {"x": 1}})
        self.assertEqual(got["mcpServers"]["mine"], {"type": "http", "url": "https://é.example.com"})
        self.assertIn("claudeonly", got["mcpServers"])

    def test_removal_only_removes_owned(self):
        self.shared()
        put(self.claude, json.dumps({"mcpServers": {"mine": {"url": "u"}}}))
        self.apply(self.plan(tools=("claude",)))
        self.shared('[servers.both]\ntools = ["claude"]\ncommand = "npx"\n')
        got = json.loads(self.plan(tools=("claude",))[self.claude])["mcpServers"]
        self.assertEqual(set(got), {"mine", "both"})
        self.shared('[servers.x]\ntools = ["agy"]\nurl = "https://x.example.com"\n')
        r = self.plan(tools=("claude",))
        self.assertEqual(set(json.loads(r[self.claude])["mcpServers"]), {"mine"})
        self.assertEqual(json.loads(r[self.owned]), {"claude": {}})

    def test_foreign_server_with_same_name_refused(self):
        self.shared()
        put(self.claude, json.dumps({"mcpServers": {"claudeonly": {"url": "theirs"}}}))
        with self.assertRaises(SyncError) as c:
            self.plan(tools=("claude",))
        self.assertEqual(c.exception.code, 1)
        self.assertIn("not mine, left untouched", str(c.exception))

    def test_empty_claude_list_leaves_file_out(self):
        self.shared('[servers.x]\ntools = ["agy"]\nurl = "https://x.example.com"\n')
        self.assertNotIn(self.claude, self.plan())
        put(self.claude, '{"a":1}')
        r = self.plan()
        self.assertNotIn(self.claude, r)
        self.assertNotIn(self.owned, r)

    def test_unchanged_existing_file_returns_same_text(self):
        self.shared('[servers.x]\ntools = ["agy"]\nurl = "https://x.example.com"\n')
        put(self.claude, '{"a":   1}')
        r = self.plan(tools=("claude",))
        self.assertEqual(r.get(self.claude, '{"a":   1}'), '{"a":   1}')

    def test_invalid_json_fails_code_2(self):
        self.shared()
        put(self.claude, "{nope")
        with self.assertRaises(SyncError) as c:
            self.plan(tools=("claude",))
        self.assertEqual(c.exception.code, 2)

    def test_owner_record_written(self):
        self.shared()
        r = self.plan(tools=("claude",))
        got = json.loads(r[self.owned])["claude"][self.claude]
        self.assertEqual(sorted(got), ["both", "claudeonly"])
        servers = json.loads(r[self.claude])["mcpServers"]
        self.assertEqual(got["both"], M._entry_hash(servers["both"]))


class Project(Base):
    def test_project_entries_only_in_project_files(self):
        self.shared()
        proj = os.path.join(self.tmp, "proj")
        put(os.path.join(proj, ".nousfx", "mcp.toml"),
            '[servers.p]\ntools = ["claude", "agy"]\nurl = "https://p.example.com"\n')
        r = self.plan(project=proj)
        pc, pa = os.path.join(proj, ".mcp.json"), os.path.join(proj, ".agents", "plugins", "nousfx", "mcp_config.json")
        rec = M.project_record(proj)
        self.assertEqual(set(r), {pc, pa, rec})  # the state record is not touched (NFX-99)
        self.assertEqual(json.loads(r[pc])["mcpServers"], {"p": {"type": "http", "url": "https://p.example.com"}})
        self.assertEqual(json.loads(r[pa])["mcpServers"], {"p": {"url": "https://p.example.com"}})
        self.assertEqual(list(json.loads(r[rec])["claude"][".mcp.json"]), ["p"])
        self.assertLess(list(r).index(rec), list(r).index(pc))  # record first

    def test_global_entries_never_in_project(self):
        self.shared()
        proj = os.path.join(self.tmp, "proj")
        os.makedirs(proj)
        r = self.plan(project=proj)
        self.assertNotIn(self.claude, r)
        self.assertNotIn(self.agy, r)
        self.assertEqual(json.loads(r[os.path.join(proj, ".agents", "plugins", "nousfx",
                                                   "mcp_config.json")]), {"mcpServers": {}})
        self.assertNotIn(os.path.join(proj, ".mcp.json"), r)


class ProjectRecord(Base):
    """NFX-99: the project ownership record travels with the repo, so a new checkout is not "not mine"."""
    P = '[servers.p]\ntools = ["claude"]\nurl = "https://p.example.com"\n'

    def synced(self, toml=P):
        self.shared()
        self.proj = os.path.join(self.tmp, "proj")
        put(os.path.join(self.proj, ".nousfx", "mcp.toml"), toml)
        self.apply(self.plan(tools=("claude",), project=self.proj))
        self.mcp = os.path.join(self.proj, ".mcp.json")

    def copy(self):
        """A fresh worktree or clone: the committed files, a new path, an empty state home."""
        other = os.path.join(self.tmp, "clone")
        shutil.copytree(self.proj, other)
        shutil.rmtree(self.state)
        os.makedirs(self.state)
        return other

    def changed(self, result):
        return {p: t for p, t in result.items() if not os.path.exists(p) or open(p, encoding="utf-8").read() != t}

    def test_a_copied_project_syncs_clean(self):
        self.synced()
        other = self.copy()
        self.assertEqual(self.changed(self.plan(tools=("claude",), project=other)), {})

    def test_a_stale_committed_file_updates_after_the_list_changed(self):
        self.synced()
        other = self.copy()
        put(os.path.join(other, ".nousfx", "mcp.toml"), self.P.replace("p.example", "q.example"))
        r = self.plan(tools=("claude",), project=other)
        self.assertEqual(json.loads(r[os.path.join(other, ".mcp.json")])["mcpServers"]["p"]["url"],
                         "https://q.example.com")

    def test_without_a_record_anywhere_the_server_is_still_not_mine(self):
        self.synced()
        other = self.copy()
        os.remove(M.project_record(other))
        with self.assertRaises(SyncError) as c:
            self.plan(tools=("claude",), project=other)
        self.assertIn("not mine", str(c.exception))
        self.assertIn("commit that checkout's .nousfx/mcp-owned.json", str(c.exception))

    def test_a_hand_edit_under_an_owned_name_is_still_refused(self):
        self.synced()
        data = json.loads(open(self.mcp, encoding="utf-8").read())
        data["mcpServers"]["p"]["url"] = "https://mine.example.com"
        put(self.mcp, json.dumps(data))
        with self.assertRaises(SyncError) as c:
            self.plan(tools=("claude",), project=self.proj)
        self.assertIn("not what sync wrote", str(c.exception))

    def test_a_state_entry_from_before_moves_into_the_repo(self):
        self.synced()
        rec = M.project_record(self.proj)
        legacy = json.loads(open(rec, encoding="utf-8").read())["claude"][".mcp.json"]
        os.remove(rec)
        put(self.owned, json.dumps({"claude": {self.mcp: legacy, "/other/.mcp.json": legacy}}))
        r = self.plan(tools=("claude",), project=self.proj)
        self.assertEqual(json.loads(r[rec])["claude"], {".mcp.json": legacy})
        self.assertEqual(json.loads(r[self.owned])["claude"], {"/other/.mcp.json": legacy})
        self.assertEqual(list(r)[:2], [rec, self.owned])  # records first
        self.assertEqual(r[self.mcp], open(self.mcp, encoding="utf-8").read())  # .mcp.json already right
        self.apply(r)
        self.assertEqual(self.changed(self.plan(tools=("claude",), project=self.proj)), {})

    def test_record_keys_cover_both_records_for_a_project_file(self):
        self.assertEqual(M.record_keys(self.state, "/r/.mcp.json"),
                         [(self.owned, "/r/.mcp.json"), (os.path.join("/r", ".nousfx", "mcp-owned.json"), ".mcp.json")])
        self.assertEqual(M.record_keys(self.state, self.claude), [(self.owned, self.claude)])


class ReviewFixes(Base):
    """Round 1 review findings: literal secrets in args, odd URLs, record shape and order, unmerge."""

    def entry(self, extra):
        return '[servers.s]\ntools = ["agy"]\n' + extra + "\n"

    def test_secret_flags_must_take_an_env_reference(self):
        for args in ('["--api-key", "abcdef"]', '["--token", "x"]', '["--password=hunter2"]',
                     '["-y", "pkg", "--bearer", "short"]'):
            self.assertRegex(self.fails(self.entry(f'command = "t"\nargs = {args}')),
                             "never a literal|carries a credential")

    def test_secret_flag_with_env_reference_and_plain_flags_pass(self):
        self.shared(self.entry('command = "t"\nargs = ["--api-key", "${K}", "--config", "/p/x.json", "--key-file", "k"]'))
        self.plan()

    def test_url_semicolon_fragment_and_malformed(self):
        self.fails(self.entry('url = "https://x.example.com/m?x=1;token=abcdef"'))
        self.fails(self.entry('url = "https://x.example.com/m#access_token=abcdef"'))
        self.assertIn("not valid", self.fails(self.entry('url = "http://[::1"')))

    def test_bad_record_shape_is_refused(self):
        put(self.owned, json.dumps({"claude": {self.claude: "abc"}}))
        self.shared('[servers.c]\ntools = ["claude"]\nurl = "https://x.example.com"\n')
        with self.assertRaises(SyncError) as c:
            self.plan(tools=("claude",))
        self.assertEqual(c.exception.code, 2)

    def test_record_comes_before_the_file_it_describes(self):
        self.shared('[servers.c]\ntools = ["claude"]\nurl = "https://x.example.com"\n')
        self.assertEqual(list(self.plan(tools=("claude",))), [self.owned, self.claude])

    def test_unmerge_restores_only_named_servers(self):
        saved = json.dumps({"a": 1, "mcpServers": {"mine": {"url": "u"}}})
        live = json.dumps({"a": 2, "later": True, "mcpServers": {"mine": {"url": "u"}, "docs": {"url": "d"}}})
        out = json.loads(M.unmerge(live, saved, ["docs"]))
        self.assertEqual(out, {"a": 2, "later": True, "mcpServers": {"mine": {"url": "u"}}})

    def test_unmerge_brings_back_an_entry_sync_replaced(self):
        saved = json.dumps({"mcpServers": {"docs": {"url": "old"}}})
        live = json.dumps({"mcpServers": {"docs": {"url": "new"}}})
        self.assertEqual(json.loads(M.unmerge(live, saved, ["docs"]))["mcpServers"]["docs"]["url"], "old")

    def test_unmerge_deletes_only_a_file_sync_created_and_nothing_else_is_in(self):
        live = json.dumps({"mcpServers": {"docs": {"url": "d"}}})
        self.assertIsNone(M.unmerge(live, None, ["docs"]))
        kept = json.loads(M.unmerge(json.dumps({"oauth": 1, "mcpServers": {"docs": {}}}), None, ["docs"]))
        self.assertEqual(kept, {"oauth": 1})

    def test_unmerge_refuses_unusable_json(self):
        with self.assertRaises(ValueError):
            M.unmerge("[1]", None, [])
        with self.assertRaises(ValueError):
            M.unmerge("{nope", None, [])

    def test_credential_inside_an_argument_is_refused(self):
        for arg in ("Authorization: Bearer abcd1234efgh", "--header=Authorization: Bearer abcd1234efgh",
                    "X-Api-Key: abcd1234", "token=abcd1234", "Bearer abcd1234efgh"):
            self.assertIn("credential", self.fails(self.entry(f'command = "mcp-remote"\nargs = ["https://x", {json.dumps(arg)}]')))

    def test_credential_written_as_an_env_reference_passes(self):
        self.shared(self.entry('command = "mcp-remote"\nargs = ["https://x", "--header", "Authorization: Bearer ${TOKEN}", "--transport", "http-only"]'))
        self.plan()

    def test_owned_names_refuses_an_unusable_record(self):
        self.assertEqual(M.owned_names(None, "/p"), [])
        self.assertEqual(M.owned_names("{}", "/p"), [])
        for bad in ("[1]", "{nope", '{"claude": 5}'):
            with self.assertRaises(ValueError):
                M.owned_names(bad, "/p")

    def test_ordinary_flags_and_auth_schemes_pass(self):
        self.shared(self.entry('command = "t"\nargs = ["--max-tokens=1000", "--password-store=basic", "--auth-type=oauth", '
                               '"--token-file=/p/t", "--no-auth", "--port", "3000", "--header", "Authorization: token ${GH}", '
                               '"--header", "Authorization: Digest ${D}"]'))
        self.plan()

    def test_cookie_and_key_shapes_are_refused(self):
        for arg in ("Cookie: sid=abcd1234", "X-Access-Key: abcd1234"):
            self.assertIn("credential", self.fails(self.entry(f'command = "t"\nargs = [{json.dumps(arg)}]')))

    def test_key_file_flags_and_prose_pass(self):
        self.shared(self.entry('command = "t"\nargs = ["--private-key", "/etc/tls/key.pem", "--ssh-key", "~/.ssh/id", '
                               '"--sort-key=name", "--partition-key=userId", "--mode=basic mode"]'))
        self.plan()

    def test_underscore_multiword_quoted_and_letters_only_bypasses_are_refused(self):
        for args in ('["--api_key", "sk_live_abcd"]', '["--access_token", "abcd1234"]',
                     '["--personal-access-token", "ghp_abcd1234"]'):
            self.assertRegex(self.fails(self.entry(f'command = "t"\nargs = {args}')), "never a literal")
        for arg in ('--config={"token":"abcd1234"}', "X-Api-Key: SuperSecretWord ${T}", "Authorization: token abcd1234"):
            self.assertIn("credential", self.fails(self.entry(f'command = "t"\nargs = [{json.dumps(arg)}]')))

    def test_env_style_names_with_underscores_and_secret_key_flag(self):
        for arg in ("BRAVE_API_KEY=BSAabcdef1234", "client_secret=abcd1234", "auth_token=abcd1234",
                    "GITLAB_TOKEN=abcd1234", "X_API_KEY: abcd1234"):
            self.assertIn("credential", self.fails(self.entry(f'command = "docker"\nargs = ["-e", {json.dumps(arg)}]')))
        self.assertRegex(self.fails(self.entry('command = "t"\nargs = ["--secret-key", "literal1"]')), "never a literal")
        self.shared(self.entry('command = "docker"\nargs = ["-e", "BRAVE_API_KEY=${BRAVE_API_KEY}"]'))
        self.plan()


class Nfx52(Base):
    """Fable review follow-up: ownership by content, required shared list, record shape, dash values."""

    def one(self, tools=("claude",)):
        self.shared('[servers.foo]\ntools = ["claude"]\nurl = "https://foo.example.com/mcp"\n')
        self.apply(self.plan(tools=tools))

    def test_a_users_own_server_under_an_owned_name_is_not_overwritten(self):
        self.one()
        data = json.loads(open(self.claude, encoding="utf-8").read())
        data["mcpServers"]["foo"] = {"command": "USERS-OWN"}
        put(self.claude, json.dumps(data))
        with self.assertRaises(SyncError) as c:
            self.plan(tools=("claude",))
        self.assertIn("not what sync wrote", str(c.exception))

    def test_a_users_own_server_is_not_deleted_when_the_entry_is_removed(self):
        self.one()
        data = json.loads(open(self.claude, encoding="utf-8").read())
        data["mcpServers"]["foo"] = {"command": "USERS-OWN"}
        put(self.claude, json.dumps(data))
        self.shared('[servers.other]\ntools = ["claude"]\nurl = "https://o.example.com"\n')
        with self.assertRaises(SyncError):
            self.plan(tools=("claude",))

    def test_an_owned_entry_that_sync_wrote_still_updates_and_a_dropped_one_is_re_added(self):
        self.one()
        self.shared('[servers.foo]\ntools = ["claude"]\nurl = "https://foo.example.com/v2"\n')
        r = self.plan(tools=("claude",))
        self.assertEqual(json.loads(r[self.claude])["mcpServers"]["foo"]["url"], "https://foo.example.com/v2")
        put(self.claude, "{}")  # Claude rewrote its file and dropped the entry
        self.assertIn("foo", json.loads(self.plan(tools=("claude",))[self.claude])["mcpServers"])

    def test_an_older_record_without_hashes_still_works(self):
        self.one()
        rec = json.loads(open(self.owned, encoding="utf-8").read())
        rec["claude"][self.claude] = sorted(rec["claude"][self.claude])
        put(self.owned, json.dumps(rec))
        self.assertEqual(self.plan(tools=("claude",))[self.claude], open(self.claude, encoding="utf-8").read())

    def test_missing_shared_list_is_an_error(self):
        with self.assertRaises(SyncError) as c:
            self.plan()
        self.assertEqual(c.exception.code, 2)
        self.assertIn("missing shared list", str(c.exception))

    def test_a_run_stopped_between_record_and_file_is_not_refused(self):
        self.one()
        old_text = open(self.claude, encoding="utf-8").read()
        self.shared('[servers.foo]\ntools = ["claude"]\nurl = "https://foo.example.com/v2"\n')
        r = self.plan(tools=("claude",))
        put(self.owned, r[self.owned])  # the record landed, the file did not
        again = self.plan(tools=("claude",))
        self.assertEqual(json.loads(again[self.claude])["mcpServers"]["foo"]["url"], "https://foo.example.com/v2")
        self.apply(again)
        h = M._entry_hash(json.loads(open(self.claude, encoding="utf-8").read())["mcpServers"]["foo"])
        rec = lambda: json.loads(open(self.owned, encoding="utf-8").read())["claude"][self.claude]["foo"]
        self.assertIn("+", rec())
        self.apply(self.plan(tools=("claude",)))  # the file now holds the new entry: the old hash is dropped
        self.assertEqual(rec(), h)
        self.assertNotIn(self.owned, {p for p, t in self.plan(tools=("claude",)).items()
                                      if t != open(p, encoding="utf-8").read()})
        self.assertNotEqual(old_text, open(self.claude, encoding="utf-8").read())

    def test_state_list_and_project_list_stay_optional(self):
        self.shared()
        self.plan()
        self.plan(project=os.path.join(self.tmp, "no-such-project"))

    def test_owned_names_validates_the_shape(self):
        for bad in ('{"claude": {"p": "abc"}}', '{"claude": {"p": [1]}}', '{"claude": []}', '{"claude": {"p": {"a": 5}}}'):
            with self.assertRaises(ValueError):
                M.owned_names(bad, "p")
        self.assertEqual(M.owned_names('{"claude": {"p": {"a": "h1"}}}', "p"), ["a"])
        self.assertEqual(M.owned_names('{"claude": {"p": ["a"]}}', "p"), ["a"])

    def test_dash_led_literal_after_a_secret_flag_is_refused(self):
        e = '[servers.s]\ntools = ["agy"]\ncommand = "t"\nargs = ["--token", "-NotAnEnvRef1234567890"]\n'
        self.assertRegex(self.fails(e), "never a literal")
        self.assertRegex(self.fails(e.replace("-NotAnEnvRef1234567890", "-abc123def")), "never a literal")
        self.shared('[servers.s]\ntools = ["agy"]\ncommand = "t"\nargs = ["--token", "--port", "3000", "-y"]\n')
        self.plan()

    def test_servers_slice_keeps_only_mcp_servers(self):
        out = json.loads(M.servers_slice(json.dumps({"oauthAccount": {"x": 1}, "mcpServers": {"a": {}}})))
        self.assertEqual(out, {"mcpServers": {"a": {}}})
        self.assertEqual(json.loads(M.servers_slice(json.dumps({"oauthAccount": 1}))), {})
        self.assertEqual(M.servers_slice("{nope"), "{nope")


if __name__ == "__main__":
    unittest.main()
