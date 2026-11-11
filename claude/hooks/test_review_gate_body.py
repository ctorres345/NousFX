#!/usr/bin/env python3
"""Tests for the PR body check (NFX-85): a create needs a `Review-Gate: <HEAD tree>` line in its body.
Run: python3 -B claude/hooks/test_review_gate_body.py

Throwaway git repos under a temp dir. BLOCKED is exit 2, ALLOWED exit 0.
"""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_review_gate_check import ALLOWED, BLOCKED, GATE, Base  # noqa: E402


class BodyBase(Base):
    def setUp(self):
        super().setUp()
        self.mark("--findings", "0", "--tier", "2", "--reasons", "test")
        self.head = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo,
                                   capture_output=True, text=True).stdout.strip()
        self.line = "Review-Gate: " + self.head

    def create(self, command, cwd=None):
        return self.check("--cwd", cwd or self.repo, "--command", command)

    def body_file(self, text, name="body.md"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def assert_blocked_with_lines(self, r, why):
        self.assertEqual(r.returncode, BLOCKED, r.stderr)
        self.assertIn(why, r.stderr)
        self.assertIn("    " + self.line + "\n", r.stderr)  # the exact line to paste
        self.assertIn("    Review-Tier: 2 (test)\n", r.stderr)
        self.assertNotIn("no review recorded", r.stderr)


class Body(BodyBase):
    def test_no_body_blocks_and_prints_the_lines(self):
        self.assert_blocked_with_lines(self.create("gh pr create --title x"), "passes no PR body")

    def test_fill_blocks(self):
        for flag in ("--fill", "-f", "--fill-first", "--fill-verbose"):
            with self.subTest(flag=flag):
                self.assert_blocked_with_lines(self.create(f"gh pr create {flag}"), "--fill")

    def test_stale_hash_blocks(self):
        stale = "0" * 40
        r = self.create(f"gh pr create --body 'Review-Gate: {stale}'")
        self.assert_blocked_with_lines(r, "names tree 0000000000")

    def test_body_file_without_the_line_blocks(self):
        path = self.body_file("## Summary\n\nno line here\n")
        self.assert_blocked_with_lines(self.create(f"gh pr create --body-file {path}"), "no `Review-Gate: <HEAD tree>`")

    def test_a_mention_inside_a_sentence_does_not_count(self):
        r = self.create(f"gh pr create --body 'see {self.line}'")
        self.assert_blocked_with_lines(r, "no `Review-Gate: <HEAD tree>`")

    def test_missing_body_file_blocks(self):
        r = self.create(f"gh pr create -F {os.path.join(self.tmp, 'nope.md')}")
        self.assert_blocked_with_lines(r, "cannot read the body file")

    def test_stdin_body_and_bodies_built_at_run_time_block(self):
        for command, why in ((f"printf '{self.line}' | gh pr create --body-file -", "stdin"),
                             (f"gh pr create --body-file - <<EOF\n{self.line}\nEOF", "stdin"),
                             ('gh pr create --body "$BODY"', "built at run time"),
                             ('gh pr create -F "$F"', "built at run time"),
                             ("gh pr create $ARGS", "cannot read this PR create's body"),
                             ("$GH pr create --body x", "no `Review-Gate: <HEAD tree>`"),
                             ("echo 'gh pr create' | sh", "cannot read this PR create's body")):
            with self.subTest(command=command):
                self.assert_blocked_with_lines(self.create(command), why)

    def test_a_body_with_the_head_line_passes_in_every_flag_form(self):
        path = self.body_file(f"## Summary\n\n{self.line}\nReview-Tier: 2 (test)\n")
        rel = os.path.relpath(path, self.repo)
        for command in (f"gh pr create --body '{self.line}'",
                        f"gh pr create -b '{self.line}'",
                        f"gh pr create '-b{self.line}'",
                        f"gh pr create '--body={self.line}'",
                        f"gh pr create --title t --body 'x\n  {self.line}\ny' --draft",
                        f"gh pr create --body-file {path}",
                        f"gh pr create -F {path}",
                        f"gh pr create --body-file={path}",
                        f"gh pr create -F {rel}",  # relative to the folder the create runs in
                        f"gh pr create --fill --body '{self.line}'",
                        f"gh pr create --body \"$(cat <<'EOF'\n## Summary\n\n{self.line}\nEOF\n)\"",
                        f"gh pr create --body \"$(cat <<EOF\nhi $USER\n{self.line}\nEOF\n)\" --base main",
                        f"bash -c \"gh pr create -F {path}\""):
            with self.subTest(command=command):
                r = self.create(command)
                self.assertEqual(r.returncode, ALLOWED, r.stderr)

    def test_the_last_body_flag_wins(self):
        self.assertEqual(self.create(f"gh pr create -b '{self.line}' -b other").returncode, BLOCKED)
        self.assertEqual(self.create(f"gh pr create -b other -b '{self.line}'").returncode, ALLOWED)

    def test_a_relative_body_file_follows_a_cd(self):
        self.body_file(self.line + "\n", name="here.md")
        self.assertEqual(self.create(f"cd {self.repo} && gh pr create -F ../here.md", cwd=self.tmp).returncode,
                         BLOCKED)  # self.tmp is not a repo: bad input
        self.assertEqual(self.check("--cwd", self.tmp, "--command", f"cd {self.repo} && gh pr create -F ../here.md",
                                    "--allow-non-repo").returncode, ALLOWED)

    def test_a_mark_on_uncommitted_changes_does_not_cover_head(self):
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
        self.mark("--findings", "0")  # the working tree is reviewed, HEAD is not
        r = self.create(f"gh pr create -b '{self.line}'")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("HEAD itself was not reviewed", r.stderr)
        self.assertIn("commit or clean first", r.stderr)

    def test_the_value_of_another_flag_is_not_a_body(self):
        r = self.create(f"gh pr create --body x --title '--body={self.line}'")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertEqual(self.create(f"gh pr create -t -bugfix -b '{self.line}'").returncode, ALLOWED)

    def test_a_body_file_wins_over_body(self):
        path = self.body_file("no line\n")
        self.assertEqual(self.create(f"gh pr create -F {path} -b '{self.line}'").returncode, BLOCKED)
        good = self.body_file(self.line + "\n", name="good.md")
        self.assertEqual(self.create(f"gh pr create -b nope -F {good}").returncode, ALLOWED)

    def test_a_dynamic_command_elsewhere_does_not_block_a_readable_create(self):
        for command in (f"\"$EDITOR\" notes.txt; gh pr create --body '{self.line}'",
                        f"$GH pr create --body '{self.line}'"):
            with self.subTest(command=command):
                self.assertEqual(self.create(command).returncode, ALLOWED, self.create(command).stderr)
        self.assertEqual(self.create("\"$EDITOR\" notes.txt; gh pr create").returncode, BLOCKED)

    def test_shell_code_the_gate_cannot_read_may_hide_another_create(self):
        for command in (f"gh pr create --dry-run -b '{self.line}'; eval \"$REAL\"",
                        f"gh pr create -b '{self.line}'; bash -c \"$X\"",
                        f"gh pr create -b '{self.line}'; curl x | sh"):
            with self.subTest(command=command):
                self.assert_blocked_with_lines(self.create(command), "cannot read this PR create's body")

    def test_a_body_file_inside_the_repo_still_gets_the_lines(self):
        path = os.path.join(self.repo, "PR_BODY.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("no line\n")  # untracked: the tree is dirty, HEAD is still reviewed
        r = self.create("gh pr create --body-file PR_BODY.md")
        self.assert_blocked_with_lines(r, "no `Review-Gate: <HEAD tree>`")
        self.assertIn("commit or clean first", r.stderr)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.line + "\n")
        self.assertEqual(self.create("gh pr create --body-file PR_BODY.md").returncode, ALLOWED)

    def test_clustered_shorthands_are_read_as_gh_reads_them(self):
        self.assertEqual(self.create(f"gh pr create -b '{self.line}' -db nothing").returncode, BLOCKED)
        self.assertEqual(self.create(f"gh pr create -db '{self.line}'").returncode, ALLOWED)
        self.assertEqual(self.create(f"gh pr create -df '-b{self.line}'").returncode, ALLOWED)  # -df: two switches
        self.assertEqual(self.create(f"gh pr create -dt -bx -b '{self.line}'").returncode, ALLOWED)  # -t takes -bx
        self.assertEqual(self.create(f"gh pr create -dz -b '{self.line}'").returncode, BLOCKED)  # unknown letter

    def test_an_unknown_program_beside_a_readable_create_counts_one_for_one(self):
        r = self.create(f"R='gh pr create --fill'; gh pr create --dry-run -b '{self.line}'; $R")
        self.assert_blocked_with_lines(r, "cannot read this PR create's body")

    def test_a_flag_name_built_at_run_time_could_be_a_body_flag(self):
        self.assertEqual(self.create(f"gh pr create --$K=f.md -b '{self.line}'").returncode, BLOCKED)

    def test_a_body_file_written_in_the_same_command_blocks(self):
        path = self.body_file(self.line + "\n", name="pr.md")  # an old file that would pass on its own
        r = self.create(f"cat > {path} <<'EOF'\nnew body\nEOF\ngh pr create -F {path}")
        self.assert_blocked_with_lines(r, "Write the body file in a separate call")
        self.assertEqual(self.create(f"gh pr create -F {path}").returncode, ALLOWED)

    def test_web_editor_and_recover_block_even_with_the_line(self):
        for flag in ("--web", "-w", "--editor", "-e", "-dw", "--recover f.json", "--recover=f.json"):
            with self.subTest(flag=flag):
                self.assert_blocked_with_lines(self.create(f"gh pr create {flag} -b '{self.line}'"),
                                               "can change its body after the gate has read it")

    def test_an_argument_built_at_run_time_could_replace_the_body(self):
        r = self.create(f"gh pr create --body '{self.line}' $EXTRA")
        self.assert_blocked_with_lines(r, "cannot read this PR create's body")
        self.assertEqual(self.create(f"gh pr create --label=\"$L\" -t \"$T\" -b '{self.line}'").returncode, ALLOWED)

    def test_crlf_line_ends_count(self):
        self.assertEqual(self.create(f"gh pr create -b 'x\r\n{self.line}\r\nmore'").returncode, ALLOWED)

    def test_a_body_file_that_is_not_a_regular_file_blocks(self):
        self.assert_blocked_with_lines(self.create(f"gh pr create -F {self.tmp}"), "not a regular file")

    def test_every_create_in_a_command_needs_its_body(self):
        r = self.create(f"gh pr create -b '{self.line}' && gh pr create")
        self.assert_blocked_with_lines(r, "passes no PR body")

    def test_a_skip_line_passes(self):
        self.mark("--skip", "docs only")
        self.assertEqual(self.create(f"gh pr create -b '{self.line} skipped: docs only'").returncode, ALLOWED)

    def test_a_committed_change_makes_the_line_stale(self):
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
        self.sh("git", "add", "-A")
        self.sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        self.mark("--findings", "0")
        r = self.create(f"gh pr create -b '{self.line}'")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("names tree " + self.head[:10], r.stderr)

    def test_a_dirty_tree_blocks_with_commit_or_clean_first(self):
        with open(os.path.join(self.repo, "scratch.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        r = self.create("gh pr create")
        self.assertEqual(r.returncode, BLOCKED)
        self.assertIn("commit or clean first", r.stderr)
        self.assertIn(self.head, r.stderr)
        self.assertEqual(self.create(f"gh pr create -b '{self.line}'").returncode, ALLOWED)  # HEAD is reviewed

    def test_hook_reads_an_mcp_create_body(self):
        def hook(body):
            payload = {"tool_name": "mcp__github__create_pull_request", "cwd": self.repo,
                       "tool_input": {"title": "t", **({"body": body} if body is not None else {})}}
            return subprocess.run([sys.executable, "-B", GATE], input=json.dumps(payload), capture_output=True,
                                  text=True, cwd=self.tmp)
        self.assert_blocked_with_lines(hook(None), "passes no PR body")
        self.assert_blocked_with_lines(hook("no line"), "no `Review-Gate: <HEAD tree>`")
        self.assertEqual(hook(f"## Summary\n{self.line}\n").returncode, ALLOWED)


class Template(BodyBase):
    """NFX-135: the block message names the PR template that exists in the repo being gated."""
    GITHUB = ".github/pull_request_template.md"
    SHIPPED = "claude/templates/pull_request_template.md"

    def put(self, rel):
        path = os.path.join(self.repo, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("## Summary\n")

    def blocked(self):
        r = self.create("gh pr create --title x")
        self.assertEqual(r.returncode, BLOCKED, r.stderr)
        return r.stderr

    def test_the_github_template_is_named(self):
        self.put(self.GITHUB)
        self.assertIn("PR template: " + self.GITHUB + "\n", self.blocked())

    def test_the_shipped_template_is_named_when_github_has_none(self):
        self.put(self.SHIPPED)
        err = self.blocked()
        self.assertIn("PR template: " + self.SHIPPED + "\n", err)
        self.assertNotIn(self.GITHUB, err)

    def test_github_wins_when_both_exist(self):
        self.put(self.GITHUB)
        self.put(self.SHIPPED)
        err = self.blocked()
        self.assertIn(self.GITHUB, err)
        self.assertNotIn(self.SHIPPED, err)

    def test_no_template_means_no_template_line(self):
        self.assertNotIn("PR template", self.blocked())

    def test_the_hint_is_one_line_and_every_block_reason_keeps_it(self):
        self.put(self.GITHUB)
        for command in ("gh pr create --fill", "gh pr create --title x --body 'no line here'"):
            with self.subTest(command=command):
                r = self.create(command)
                self.assertEqual(r.returncode, BLOCKED, r.stderr)
                self.assertEqual(r.stderr.count("PR template:"), 1)

    def test_a_subfolder_cwd_finds_the_template_at_the_repo_root(self):
        self.put(self.GITHUB)
        sub = os.path.join(self.repo, "sub")
        os.makedirs(sub)
        r = self.create("gh pr create --title x", cwd=sub)
        self.assertEqual(r.returncode, BLOCKED, r.stderr)
        self.assertIn("PR template: " + self.GITHUB, r.stderr)

    def test_the_shipped_template_path_exists_in_this_repo(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.assertTrue(os.path.isfile(os.path.join(here, "..", "templates", "pull_request_template.md")))
        self.assertIn(self.SHIPPED, open(GATE, encoding="utf-8").read())


class Attest(Base):
    def test_attest_refuses_a_dirty_tree_and_prints_the_head_tree(self):
        self.mark("--findings", "0")
        head = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
        r = subprocess.run([sys.executable, "-B", GATE, "attest"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        self.assertIn("commit or clean first", r.stderr)
        self.assertIn("HEAD^{tree}", r.stderr)
        self.assertIn(head, r.stderr)

    def test_attest_refuses_a_mark_on_the_uncommitted_tree(self):
        with open(os.path.join(self.repo, "f.txt"), "a", encoding="utf-8") as f:
            f.write("more")
        self.mark("--findings", "0")  # covers the working tree, which HEAD does not hold yet
        r = subprocess.run([sys.executable, "-B", GATE, "attest"], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("commit or clean first", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=1)
