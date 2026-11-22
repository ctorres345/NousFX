#!/usr/bin/env python3
"""Tests for comment_check.py: the check on comments a diff adds (NFX-196), and the inventory and verify
modes of the delete-comments skill (NFX-228).
Run: python3 -B claude/skills/review-loop/scripts/test_comment_check.py

Seams: the CLI (`comment_check.py <base>`, `--all PATH` and `verify BASE` in a throwaway repo) and the KEEP structure.
Git tests use temp repos and never touch the real repo or HOME.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "comment_check.py")
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import comment_check  # noqa: E402 (sys.path must name this folder first)

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


class Repo:
    """A throwaway repo with a base commit; write() changes the working tree after it."""

    def __init__(self, base_files=None, prefix="comment-check-"):
        self.root = tempfile.mkdtemp(prefix=prefix)
        self.git("init", "-q", "-b", "main")
        for path, text in (base_files or {"README.md": "base\n"}).items():
            self.write(path, text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        env = dict(os.environ, **GIT_ENV)
        return subprocess.run(["git", *args], cwd=self.root, env=env, check=True, capture_output=True,
                              text=True).stdout

    def write(self, path, text):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(textwrap.dedent(text))

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")

    def check(self, base=None, **extra_env):
        return self.run(base or self.base, **extra_env)

    def run(self, *args, **extra_env):
        env = dict(os.environ, **GIT_ENV, **extra_env)
        return subprocess.run([sys.executable, "-B", SCRIPT, *args], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def inventory(self, path="."):
        """(item lines, summary line) of `--all PATH`."""
        result = self.run("--all", path)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        return lines[:-1], lines[-1]

    def verify(self):
        return self.run("verify", self.base)

    def remove(self, path):
        os.remove(os.path.join(self.root, path))

    def flags(self):
        """The flag lines (`file:line kind text`), without the count line."""
        result = self.check()
        assert result.returncode == 0, result.stderr
        return [l for l in result.stdout.splitlines() if not l.startswith("comment_check:")]

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class RepoCase(unittest.TestCase):
    def repo(self, base_files=None, **kwargs):
        repo = Repo(base_files, **kwargs)
        self.addCleanup(repo.cleanup)
        return repo


class PlainComments(RepoCase):
    def test_an_added_explanatory_comment_prints_one_flag_with_file_and_line(self):
        repo = self.repo()
        repo.write("app.py", """\
            x = 1
            # bump x because the loop below is off by one
            x += 1
            """)
        repo.commit()
        result = repo.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            "app.py:2 comment # bump x because the loop below is off by one",
            "comment_check: 1 flag (comment 1)",
        ])

    def test_a_trailing_comment_after_code_is_flagged(self):
        repo = self.repo()
        repo.write("app.ts", "const x = 1; // reset later\n")
        self.assertEqual(repo.flags(), ["app.ts:1 comment // reset later"])

    def test_consecutive_comment_lines_are_one_flag(self):
        repo = self.repo()
        repo.write("app.py", "# first line\n# second line\nx = 1\n")
        self.assertEqual(repo.flags(), ["app.py:1 comment # first line"])

    def test_a_do_not_remove_comment_is_a_guard(self):
        repo = self.repo()
        repo.write("app.kt", "val x = 1 // do not remove: the cache needs it\n")
        self.assertEqual(repo.flags(), ["app.kt:1 guard // do not remove: the cache needs it"])

    def test_count_line_counts_per_kind(self):
        repo = self.repo()
        repo.write("a.py", "x = 1  # one\ny = 2  # two\nz = 3  # noqa\n")
        result = repo.check()
        self.assertEqual(result.stdout.splitlines()[-1], "comment_check: 3 flags (comment 2, suppression 1)")

    def test_no_comments_prints_zero_flags_and_exits_0(self):
        repo = self.repo()
        repo.write("a.py", "x = 1\n")
        result = repo.check()
        self.assertEqual((result.returncode, result.stdout), (0, "comment_check: 0 flags\n"))

    def test_a_bad_base_exits_2(self):
        repo = self.repo()
        result = repo.check(base="not-a-commit")
        self.assertEqual(result.returncode, 2)
        self.assertIn("git failed", result.stderr)

    def test_a_base_that_looks_like_an_option_is_refused(self):
        repo = self.repo()
        result = repo.check(base="--output=out.txt")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(repo.root, "out.txt")))


class KeepList(RepoCase):
    def test_license_shebang_encoding_and_formatter_lines_are_not_flagged(self):
        repo = self.repo()
        repo.write("tool.py", """\
            #!/usr/bin/env python3
            # -*- coding: utf-8 -*-
            # Copyright 2026 Cesar Torres. Licensed under the MIT License.
            # fmt: off
            GRID = [1,0,
                    0,1]
            # fmt: on
            """)
        repo.write("web.js", "// SPDX-License-Identifier: MIT\n// prettier-ignore\nconst m = [1,0, 0,1];\n")
        self.assertEqual(repo.flags(), [])

    def test_public_docstrings_and_doc_comments_are_not_flagged(self):
        repo = self.repo()
        repo.write("lib.py", '''\
            """Parse widget files."""


            class Widget:
                """A widget on the page."""

                def size(self):
                    """Width times height."""
                    return 1


            def load(path):
                """Read a widget file."""
                return path
            ''')
        repo.write("lib.ts", "/** Loads a widget. */\nexport function load() {}\n")
        repo.write("lib.dart", "/// The widget size.\nint size() => 1;\n")
        repo.write("Lib.kt", "/** A widget. */\nclass Widget\n")
        self.assertEqual(repo.flags(), [])

    def test_private_docstrings_and_doc_comments_are_flagged(self):
        repo = self.repo()
        repo.write("lib.py", 'def _helper():\n    """Add one."""\n    return 1\n')
        repo.write("lib.dart", "/// Internal size.\nint _size() => 1;\n")
        repo.write("Lib.kt", "/** Internal. */\nprivate fun helper() = 1\n")
        self.assertEqual(repo.flags(), [
            "Lib.kt:1 docstring /** Internal. */",
            'lib.dart:1 docstring /// Internal size.',
            'lib.py:2 docstring """Add one."""',
        ])

    def test_module_docstring_in_a_file_that_reads_doc_is_kept_by_its_own_rule(self):
        repo = self.repo()
        text = '"""Run the tool.\n\nUsage: tool.py <x>\n"""\nimport argparse\nargparse.ArgumentParser(description=__doc__)\n'
        repo.write("tool.py", text)
        self.assertEqual(repo.flags(), [])
        doc = [c for c in comment_check.comments_in("tool.py", text) if c.style == "docstring"][0]
        self.assertEqual(comment_check.classify(doc), ("module-doc-read", None))

    def test_a_ticket_key_keeps_a_line_only_with_its_reason(self):
        repo = self.repo()
        repo.write("a.py", """\
            x = 1  # NFX-123
            y = 2  # see https://github.com/o/r/issues/7
            z = 3  # NFX-123: the vendor API returns null on empty lists
            """)
        self.assertEqual(repo.flags(), [
            "a.py:1 ticket-tag # NFX-123",
            "a.py:2 ticket # see https://github.com/o/r/issues/7",
        ])

    def test_keep_is_one_importable_structure_that_the_cli_prints(self):
        names = [rule.name for rule in comment_check.KEEP]
        for name in ("shebang", "encoding", "license", "formatter", "public-doc", "module-doc-read", "ticket-why",
                     "suppression-why", "external-quirk"):
            self.assertIn(name, names)
        self.assertEqual(len(names), len(set(names)))
        out = subprocess.run([sys.executable, "-B", SCRIPT, "--keep-list"], capture_output=True, text=True).stdout
        self.assertEqual([l.split(":")[0] for l in out.splitlines()], [f"- {n}" for n in names])
        self.assertIn("(the reviewer judges)", out.splitlines()[names.index("external-quirk")])


class Suppressions(RepoCase):
    def test_a_suppression_without_a_reason_is_flagged_and_with_one_is_not(self):
        repo = self.repo()
        repo.write("a.py", """\
            import os  # noqa: F401
            import sys  # noqa: F401 (re-exported for the plugin loader)
            x = os.foo  # type: ignore[attr-defined]
            y = os.bar  # type: ignore[attr-defined]  stubs miss this attribute
            """)
        repo.write("a.ts", """\
            // eslint-disable-next-line no-console
            console.log(1);
            // eslint-disable-next-line no-console -- the CLI prints to the terminal
            console.log(2);
            // @ts-ignore
            const z: number = "s";
            """)
        repo.write("a.dart", "// ignore: unused_import\nimport 'x.dart';\n// ignore: unused_import, kept for the generated code\nimport 'y.dart';\n")
        repo.write("A.kt", '@Suppress("UNCHECKED_CAST")\nval a = b as List<String>\n'
                           '@Suppress("UNCHECKED_CAST") // the schema checks the type first\nval c = d as List<String>\n')
        self.assertEqual(repo.flags(), [
            'A.kt:1 suppression @Suppress("UNCHECKED_CAST")',
            "a.dart:1 suppression // ignore: unused_import",
            "a.py:1 suppression # noqa: F401",
            "a.py:3 suppression # type: ignore[attr-defined]",
            "a.ts:1 suppression // eslint-disable-next-line no-console",
            "a.ts:5 suppression // @ts-ignore",
        ])

    def test_a_suppress_inside_a_string_is_not_an_annotation(self):
        repo = self.repo()
        repo.write("A.kt", 'val s = "@Suppress(\\"X\\")"\n')
        self.assertEqual(repo.flags(), [])


class Ratchet(RepoCase):
    OLD = """\
        # an old comment that explains the loop
        x = 1  # old trailing comment
        def _old():
            \"\"\"Old private docstring.\"\"\"
            return 1
        """

    def test_an_unchanged_file_full_of_old_comments_prints_no_flags(self):
        repo = self.repo({"old.py": self.OLD, "old.ts": "// old\nconst a = 1; // old too\n"})
        repo.write("new.py", "x = 2\n")
        self.assertEqual(repo.flags(), [])

    def test_editing_code_next_to_old_comments_flags_nothing(self):
        repo = self.repo({"old.py": self.OLD})
        repo.write("old.py", self.OLD.replace("        x = 1", "        x = 2", 1).replace("    return 1", "    return 2"))
        self.assertEqual(repo.flags(), [])

    def test_a_renamed_or_reindented_comment_is_not_new(self):
        repo = self.repo({"old.py": self.OLD, "b.py": "if True:\n    # keep the order: the plugin loads first\n    x = 1\n"})
        repo.git("mv", "old.py", "renamed.py")
        repo.write("b.py", "# keep the order: the plugin loads first\nx = 1\n")
        self.assertEqual(repo.flags(), [])

    def test_an_untracked_file_counts_every_line(self):
        repo = self.repo()
        repo.write("new.sh", "#!/bin/sh\necho hi  # say hi\n")
        self.assertEqual(repo.flags(), ["new.sh:2 comment # say hi"])

    def test_a_staged_change_counts(self):
        repo = self.repo()
        repo.write("a.py", "x = 1  # staged note\n")
        repo.git("add", "a.py")
        self.assertEqual(repo.flags(), ["a.py:1 comment # staged note"])


class FilesAndStrings(RepoCase):
    def test_markdown_and_other_files_are_never_flagged(self):
        repo = self.repo()
        repo.write("README.md", "# Title\n<!-- a comment -->\n// not code\n")
        repo.write("skills/x/SKILL.md", "# Heading\n")
        repo.write("conf.yml", "# a yaml comment\n")
        repo.write("data.json", '{"a": "# no"}\n')
        self.assertEqual(repo.flags(), [])

    def test_comment_markers_inside_strings_are_not_comments(self):
        repo = self.repo()
        repo.write("a.py", 'url = "https://x.org/#frag"\ns = """\n# not a comment\n"""\n')
        repo.write("a.ts", 'const u = "http://x.org"; const t = `// no ${u}`; const r = \'/* no */\';\n')
        repo.write("a.dart", "final s = '''\n// not a comment\n''';\n")
        repo.write("a.sh", 'echo "# no" \'# no\' ${#arr[@]} $#\nx=a#b\n')
        self.assertEqual(repo.flags(), [])

    def test_block_comments_and_extensionless_scripts_are_read(self):
        repo = self.repo()
        repo.write("a.js", "/* a block\n   comment */\nconst a = 1;\n")
        repo.write("bin/tool", "#!/usr/bin/env python3\nx = 1  # note\n")
        self.assertEqual(repo.flags(), ["a.js:1 comment /* a block", "bin/tool:2 comment # note"])


class ReviewRound1(RepoCase):
    """Cases the first review round found (NFX-196)."""

    def test_the_users_diff_prefix_config_does_not_hide_tracked_files(self):
        for key in ("diff.mnemonicPrefix", "diff.noprefix"):
            with self.subTest(key=key):
                repo = self.repo()
                repo.write("a.py", "x = 1  # tracked note\n")
                repo.commit()
                result = repo.check(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0=key, GIT_CONFIG_VALUE_0="true")
                self.assertIn("a.py:1 comment # tracked note", result.stdout)

    def test_a_path_with_a_space_is_read(self):
        repo = self.repo()
        repo.write("a b.py", "x = 1  # plain note\n")
        repo.commit()
        self.assertEqual(repo.flags(), ["a b.py:1 comment # plain note"])

    def test_a_suppress_on_the_declaration_line_needs_its_own_reason(self):
        repo = self.repo()
        repo.write("A.kt", '@Suppress("UNCHECKED_CAST") val c = d as List<String>\n'
                           '@Suppress("UNUSED") fun f(x: Int) {} // the plugin calls it by name\n'
                           '@Suppress("UNUSED") fun g() {} /* reflection calls it by name */\n')
        self.assertEqual(repo.flags(), ['A.kt:1 suppression @Suppress("UNCHECKED_CAST")'])

    def test_a_heredoc_is_text_not_code(self):
        repo = self.repo()
        repo.write("a.sh", "cat <<EOF\nDon't run as root\n# not a comment\nEOF\n# real comment here\necho hi\n"
                           "cat <<-'END'\n\t# not one either\n\tEND\n")
        self.assertEqual(repo.flags(), ["a.sh:5 comment # real comment here"])

    def test_a_line_continuation_keeps_line_numbers(self):
        repo = self.repo({"a.sh": "echo a \\\n  b \\\n  c\necho d\n"})
        repo.write("a.sh", "echo a \\\n  b \\\n  c\necho d  # new note\n")
        self.assertEqual(repo.flags(), ["a.sh:4 comment # new note"])

    def test_a_guard_is_flagged_even_with_a_ticket_or_a_suppression(self):
        repo = self.repo()
        repo.write("a.py", "import os  # noqa: F401 do not remove this import\n"
                           "x = 1  # NFX-12: do not remove, the cache reads this value\n")
        self.assertEqual([l.split(" ")[1] for l in repo.flags()], ["guard", "guard"])

    def test_the_word_copyright_alone_is_not_a_license_header(self):
        repo = self.repo()
        repo.write("a.py", "x = 1  # check the copyright year in the footer before release\n")
        self.assertEqual(repo.flags(), ["a.py:1 comment # check the copyright year in the footer before release"])

    def test_one_removal_covers_one_addition(self):
        repo = self.repo({"a.py": "x = 1  # noqa\n"})
        repo.write("a.py", "x = 1\n")
        repo.write("b.py", "y = 2  # noqa\nz = 3  # noqa\n")
        self.assertEqual(repo.flags(), ["b.py:2 suppression # noqa"])

    def test_a_neighbour_line_is_not_the_reason_of_a_directive(self):
        repo = self.repo()
        repo.write("a.py", "# helper for legacy payloads\n# NFX-123\nx = 1\n# explain the grid\n# fmt: off\nG = [1,0]\n")
        self.assertEqual(repo.flags(), ["a.py:1 comment # helper for legacy payloads", "a.py:2 ticket-tag # NFX-123",
                                        "a.py:4 comment # explain the grid"])

    def test_a_private_constructor_parameter_does_not_make_the_class_private(self):
        repo = self.repo()
        repo.write("W.kt", "/** A public widget. */\nclass Widget(private val id: String)\n")
        self.assertEqual(repo.flags(), [])

    def test_editing_code_before_an_old_trailing_comment_is_not_a_new_comment(self):
        repo = self.repo({"a.ts": 'const u = "https://old.example.com"; // existing comment\n'})
        repo.write("a.ts", 'const u = "https://new.example.com"; // existing comment\n')
        self.assertEqual(repo.flags(), [])

    def test_a_block_that_opens_on_its_own_line_shows_its_first_words(self):
        repo = self.repo()
        repo.write("a.js", "/*\n * Explain the workaround.\n */\nconst a = 1;\n")
        repo.write("b.py", 'def _helper():\n    """\n    Do something internal.\n    """\n')
        self.assertEqual(repo.flags(), ["a.js:1 comment * Explain the workaround.",
                                        "b.py:2 docstring Do something internal."])


class ReviewRound2(RepoCase):
    """Cases the second review round found (NFX-196)."""

    def test_generated_files_are_never_flagged(self):
        repo = self.repo({"lib/a.g.dart": "// old\n", "gen.py": "# @generated by protoc\nx = 1\n"})
        repo.write("lib/a.g.dart", "// GENERATED CODE - DO NOT MODIFY BY HAND\n// part of the model\n")
        repo.write("gen.py", "# @generated by protoc\nx = 1  # a new field\n")
        self.assertEqual(repo.flags(), [])

    def test_a_shift_inside_arithmetic_is_not_a_heredoc(self):
        repo = self.repo()
        repo.write("a.sh", "x=$((a<<b))\n# explain the shift\ny=1\n")
        self.assertEqual(repo.flags(), ["a.sh:2 comment # explain the shift"])

    def test_a_suppress_that_spans_lines_is_one_suppression(self):
        repo = self.repo()
        repo.write("A.kt", '@Suppress(\n    "UNCHECKED_CAST",\n)\nval a = b as List<String>\n')
        self.assertEqual(repo.flags(), ['A.kt:1 suppression @Suppress('])

    def test_a_line_separator_or_crlf_keeps_line_numbers(self):
        repo = self.repo()
        repo.write("a.js", 'const s = "a\u2028b";\nconst t = 1;\r\n// explain t\nconst u = 2;\n')
        repo.write("b.py", 's = "a\u2028b"\ndef _f():\n    """Private helper."""\n')
        self.assertEqual(repo.flags(), ["a.js:3 comment // explain t", 'b.py:3 docstring """Private helper."""'])

    def test_prose_after_ignore_is_not_a_dart_directive(self):
        repo = self.repo()
        repo.write("a.dart", "// ignore: this is a hack around the parser\nvar a = 1;\n")
        self.assertEqual(repo.flags(), ["a.dart:1 comment // ignore: this is a hack around the parser"])


class ReviewRound3(RepoCase):
    """Cases the third review round found (NFX-196)."""

    def test_a_here_string_is_not_a_heredoc(self):
        repo = self.repo()
        repo.write("a.sh", "grep x <<< hello\n# explain the grep\necho hi\n")
        self.assertEqual(repo.flags(), ["a.sh:2 comment # explain the grep"])

    def test_a_standard_name_with_a_number_is_not_a_ticket_key(self):
        repo = self.repo()
        repo.write("a.py", "# Use AES-256 here because the vendor needs it\nx = 1\n")
        self.assertEqual(repo.flags(), ["a.py:1 comment # Use AES-256 here because the vendor needs it"])

    def test_prose_that_mentions_auto_generated_does_not_skip_the_file(self):
        repo = self.repo()
        repo.write("sync.py", '"""Regenerates the auto-generated copies."""\nx = 1  # explain x\n')
        self.assertEqual(repo.flags(), ["sync.py:2 comment # explain x"])

    def test_a_license_header_that_asks_to_keep_the_notice_is_kept(self):
        repo = self.repo()
        repo.write("a.py", "# Copyright 2024 Acme\n# Do not remove this notice.\nx = 1\n")
        self.assertEqual(repo.flags(), [])

    def test_a_dart_ignore_with_a_plain_reason_is_kept(self):
        repo = self.repo()
        repo.write("a.dart", "// ignore: unused_import because the generator needs it\nimport 'x.dart';\n")
        self.assertEqual(repo.flags(), [])

    def test_an_escaped_slash_in_a_regex_literal_is_not_a_comment(self):
        repo = self.repo()
        repo.write("a.js", "const r = /https?:\\/\\//;\n// explain r\n")
        self.assertEqual(repo.flags(), ["a.js:2 comment // explain r"])


class ReviewRound4(RepoCase):
    """Cases the fourth review round found (NFX-196)."""

    def test_a_comment_added_under_an_old_license_header_is_flagged(self):
        repo = self.repo({"a.py": "# Copyright 2024 Acme\nx = 1\n"})
        repo.write("a.py", "# Copyright 2024 Acme\n# sleep 2s because the parser races\nx = 1\n")
        self.assertEqual(repo.flags(), ["a.py:2 comment # sleep 2s because the parser races"])

    def test_a_new_file_with_a_long_license_header_is_kept(self):
        repo = self.repo()
        repo.write("a.py", "# Copyright 2024 Acme\n# Licensed under the Apache License, Version 2.0;\n"
                           "# you may not use this file except in compliance with the License.\nx = 1\n")
        self.assertEqual(repo.flags(), [])

    def test_a_comment_that_starts_with_auto_generated_does_not_skip_the_file(self):
        repo = self.repo()
        repo.write("a.py", "# Auto-generated ids collide, so prefix them\nx = 1  # explain x\n")
        self.assertEqual(repo.flags(), ["a.py:1 comment # Auto-generated ids collide, so prefix them",
                                        "a.py:2 comment # explain x"])

    def test_a_standard_with_a_number_is_not_a_ticket_key(self):
        repo = self.repo()
        repo.write("a.py", "# IEEE-754 rounds this the wrong way for us\nx = 1\n# X86-64 needs the aligned copy here\n")
        self.assertEqual(repo.flags(), ["a.py:1 comment # IEEE-754 rounds this the wrong way for us",
                                        "a.py:3 comment # X86-64 needs the aligned copy here"])

    def test_a_doc_block_on_a_local_statement_is_flagged(self):
        repo = self.repo()
        repo.write("a.js", "function f() {\n  /** sleep because the parser races */\n  const x = 1;\n}\n")
        self.assertEqual(repo.flags(), ["a.js:2 docstring /** sleep because the parser races */"])

    def test_an_escaped_slash_star_in_a_regex_literal_is_not_a_comment(self):
        repo = self.repo()
        repo.write("a.js", "const r = /a\\/*b/;\nconst y = 1;\n// explain y\n")
        self.assertEqual(repo.flags(), ["a.js:3 comment // explain y"])


class ReviewRound5(RepoCase):
    """Cases the fifth review round found (NFX-196)."""

    def test_a_private_def_inside_an_if_or_try_is_flagged(self):
        repo = self.repo()
        repo.write("a.py", """\
            import sys
            if sys.platform == "win32":
                def _helper():
                    \"\"\"Windows only path.\"\"\"
            try:
                import x
            except ImportError:
                def _shim():
                    \"\"\"Fallback when x is missing.\"\"\"
            """)
        self.assertEqual(repo.flags(), ['a.py:4 docstring """Windows only path."""',
                                        'a.py:9 docstring """Fallback when x is missing."""'])

    def test_suppress_lint_is_a_suppression(self):
        repo = self.repo()
        repo.write("A.kt", '@SuppressLint("NewApi")\nfun a() {}\n'
                           '@SuppressLint("NewApi") // minSdk 26 checks the API at the call site\nfun b() {}\n')
        self.assertEqual(repo.flags(), ['A.kt:1 suppression @SuppressLint("NewApi")'])


def items(lines):
    """[(path, line, form, verdict)] from `--all` item lines (`path:line form verdict text`)."""
    out = []
    for l in lines:
        where, form, verdict = l.split(" ")[:3]
        path, line = where.rsplit(":", 1)
        out.append((path, int(line), form, verdict))
    return out


def src(*lines):
    return "\n".join(lines) + "\n"


# NFX-196 retro W1: one row per language, each string, heredoc and regex form above a real comment.
SCANNER_TABLE = [
    ("tool.py", src(
        "#!/usr/bin/env python3",
        '"""Module doc."""',
        'x = "# not a comment"',
        "y = '''",
        "# still a string",
        "'''",
        'z = r"\\d+ # raw"  # trailing note',
        "# a line comment",
        "def _helper():",
        '    """Private doc."""',
        "    return 1"),
     [(1, "line", "keep:shebang"), (2, "docstring", "keep:public-doc"), (7, "inline", "flag:comment"),
      (8, "line", "flag:comment"), (10, "docstring", "flag:docstring")]),
    ("deploy.sh", src(
        "#!/bin/sh",
        "echo \"# not a comment\" '# nor this'",
        "cat <<EOF",
        "# heredoc text",
        "EOF",
        "x=$((1 << 2))  # shift then comment",
        "y=${#PATH}",
        "# real comment"),
     [(1, "shell", "keep:shebang"), (6, "inline", "flag:comment"), (8, "shell", "flag:comment")]),
    ("App.kt", src(
        'val s = "// not a comment"',
        'val r = Regex("a/*b")',
        'val t = """',
        "/* raw string */",
        '"""',
        "/** Public doc. */",
        "fun run() = 1 // trailing",
        "/* block",
        "   comment */",
        '@Suppress("UNUSED") // kept for the reflection API',
        "private val q = 2"),
     [(6, "docstring", "keep:public-doc"), (7, "inline", "flag:comment"), (8, "line", "flag:comment"),
      (10, "inline", "keep:suppression-why")]),
    ("main.dart", src(
        "final s = 'it\\'s // here';",
        'final u = "http://example.com";',
        "/// Public doc.",
        "void main() {}",
        "// ignore: unused_element",
        "void _x() {}"),
     [(3, "docstring", "keep:public-doc"), (5, "line", "flag:suppression")]),
    ("app.ts", src(
        "const re = /a\\/b/; // after regex",
        "const s = `template // ${x} text`;",
        "/* eslint-disable no-console -- logging is the CLI output */"),
     [(1, "inline", "flag:comment"), (3, "line", "keep:suppression-why")]),
    ("lib.js", src(
        "const a = 'x /* not */ y';",
        "function f() {} /* trailing block */",
        "// tidy this later"),
     [(2, "inline", "flag:comment"), (3, "line", "flag:comment")]),
]


class ScannerTable(RepoCase):
    """`--all` per language: strings, heredocs and regex literals are not comments; each real one gets its form."""

    def test_each_language_lists_its_real_comments_with_form_and_verdict(self):
        for path, source, expected in SCANNER_TABLE:
            with self.subTest(path=path):
                repo = self.repo()
                repo.write(path, source)
                lines, _ = repo.inventory(path)
                self.assertEqual(items(lines), [(path, *row) for row in expected])


class Inventory(RepoCase):
    """`--all PATH` on a fixture repo: every comment and docstring, its verdict, and the shed estimate."""

    def fixture(self):
        repo = self.repo()
        repo.write("pkg/tool.py", src(
            '"""Public module doc."""',          # 1  keep:public-doc
            "import sys",
            "",
            "# Add one to x.",                   # 4-5 flag, 2 lines shed
            "# Then return it.",
            "def _bump(x):",
            '    """Bump x.',                    # 7-9 flag:docstring, 3 lines shed
            "",
            '    """',
            "    return x + 1  # plus one",      # 10 inline, sheds nothing
            "# SPDX-License-Identifier: MIT"))   # 11 keep:license
        repo.write("pkg/run.sh", src("#!/bin/sh", "# say hi", "echo hi"))
        repo.write("README.md", "# A heading, not a comment\n")
        repo.write("gen.py", "# @generated by protoc. Do not edit.\n# a comment\n")
        repo.write(".gitignore", "build/\n")
        repo.write("build/out.py", "# ignored output\n")
        return repo

    def test_a_fixture_repo_lists_every_item_with_form_verdict_and_text(self):
        repo = self.fixture()
        lines, _ = repo.inventory()
        self.assertEqual(lines, [
            'pkg/run.sh:1 shell keep:shebang #!/bin/sh',
            'pkg/run.sh:2 shell flag:comment # say hi',
            'pkg/tool.py:1 docstring keep:public-doc """Public module doc."""',
            'pkg/tool.py:4 line flag:comment # Add one to x.',
            'pkg/tool.py:7 docstring flag:docstring """Bump x.',
            'pkg/tool.py:10 inline flag:comment # plus one',
            'pkg/tool.py:11 line keep:license # SPDX-License-Identifier: MIT',
        ])

    def test_the_summary_counts_forms_kept_items_and_shed_lines(self):
        _, summary = self.fixture().inventory()
        self.assertEqual(summary, "comment_check --all: 7 items (docstring 2, inline 1, line 2, shell 2), kept 3, "
                                  "shed 6 lines")

    def test_a_subfolder_or_one_file_can_be_named(self):
        repo = self.fixture()
        lines, summary = repo.inventory("pkg/run.sh")
        self.assertEqual(len(lines), 2)
        self.assertTrue(summary.startswith("comment_check --all: 2 items"))
        self.assertEqual({l.split(":")[0] for l in repo.inventory("pkg")[0]}, {"pkg/run.sh", "pkg/tool.py"})

    def test_a_folder_outside_git_is_walked(self):
        folder = tempfile.mkdtemp(prefix="comment-check-nogit-")
        self.addCleanup(shutil.rmtree, folder, True)
        with open(os.path.join(folder, "a.py"), "w") as f:
            f.write("x = 1  # one\n")
        os.makedirs(os.path.join(folder, ".hidden"))
        with open(os.path.join(folder, ".hidden", "b.py"), "w") as f:
            f.write("# hidden\n")
        result = subprocess.run([sys.executable, "-B", SCRIPT, "--all", "."], cwd=folder, capture_output=True,
                                text=True, env=dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(folder)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "a.py:1 inline flag:comment # one")
        self.assertEqual(len(result.stdout.splitlines()), 2)

    def test_the_folders_above_path_never_trigger_the_ignore_rules(self):
        """`--all ../generated/app` must judge files by their path under PATH, not by the folders above it."""
        repo = self.repo()
        repo.write("generated/app/a.py", "x = 1  # one\n")
        result = subprocess.run([sys.executable, "-B", SCRIPT, "--all", os.path.join(repo.root, "generated", "app")],
                                cwd=repo.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a.py:1 inline flag:comment # one", result.stdout)

    def test_a_tool_directive_sheds_nothing(self):
        """Round 2 low: a suppression is replaced with a reason, never just deleted, so it sheds no line."""
        repo = self.repo()
        repo.write("a.py", "import os\n# noqa: F401\n")
        lines, summary = repo.inventory("a.py")
        self.assertEqual(lines, ["a.py:2 line flag:suppression # noqa: F401"])
        self.assertTrue(summary.endswith("shed 0 lines"), summary)

    def test_a_missing_path_is_refused(self):
        result = self.repo().run("--all", "nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no such path", result.stderr)

    def test_an_ordinary_comment_that_says_generated_is_listed(self):
        """NFX-196 retro W2: a skip rule must not fire on incidental text."""
        repo = self.repo()
        repo.write("a.py", "x = 1\n\n\n\n\n# the generated file lands in build/\n")
        self.assertEqual(items(repo.inventory("a.py")[0]), [("a.py", 6, "line", "flag:comment")])


CLI = src(
    "#!/usr/bin/env python3",
    '"""Greet someone.',
    "",
    "Prints hello.",
    '"""',
    "import argparse",
    "",
    "",
    "# Build the parser.",
    "def main():",
    '    """Parse the arguments."""',
    "    p = argparse.ArgumentParser(description=__doc__)",
    "    p.add_argument('name')  # who to greet",
    "    print('hello', p.parse_args().name)",
    "",
    "",
    "def _quiet():",
    '    """Nothing to say."""',
    "",
    "",
    "if __name__ == '__main__':",
    "    main()")

SHELL = src("#!/bin/sh", "# say hi", 'if [ -n "$1" ]; then  # with a name', '  echo "hi $1"', "fi")


class Verify(RepoCase):
    """`verify BASE`: exit 0 when only comments changed, 2 naming the file on any other difference."""

    def fixture(self, **extra):
        files = {"cli.py": CLI, "run.sh": SHELL, "README.md": "base\n"}
        files.update(extra)
        return self.repo(files)

    def edit(self, repo, path, old, new):
        full = os.path.join(repo.root, path)
        with open(full, encoding="utf-8") as f:
            text = f.read()
        self.assertIn(old, text)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))

    def assert_difference(self, repo, *needles):
        result = repo.verify()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stdout)
        return result

    def test_deleting_comments_and_private_docstrings_exits_0(self):
        repo = self.fixture()
        self.edit(repo, "cli.py", "# Build the parser.\n", "")
        self.edit(repo, "cli.py", "  # who to greet", "")
        self.edit(repo, "cli.py", '    """Parse the arguments."""\n', "")
        self.edit(repo, "cli.py", '    """Nothing to say."""\n', "    pass\n")
        self.edit(repo, "run.sh", "# say hi\n", "")
        self.edit(repo, "run.sh", "  # with a name", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(result.stdout.splitlines()[-1].startswith("comment_check verify: OK"), result.stdout)

    def test_a_docstring_only_body_may_become_empty_of_docstring(self):
        repo = self.fixture(**{"lib.py": src("def _f():", '    """Only a docstring."""')})
        self.edit(repo, "lib.py", '    """Only a docstring."""', "    pass")
        self.assertEqual(repo.verify().returncode, 0)

    def test_a_docstring_only_module_may_become_empty(self):
        """Gemini G1: an emptied module needs no `pass`, unlike a function."""
        repo = self.fixture(**{"pkg/__init__.py": '"""Internal package."""\n'})
        repo.write("pkg/__init__.py", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_main_in_a_comment_does_not_run_help(self):
        """Gemini G2: only a real `if __name__ == "__main__"` block makes verify run the file."""
        marker = os.path.join(tempfile.mkdtemp(prefix="comment-check-ran-"), "ran")
        self.addCleanup(shutil.rmtree, os.path.dirname(marker), True)
        repo = self.fixture(**{"lib.py": src(
            "x = __doc__  # not run as __main__",
            f"open({marker!r}, 'a').write('ran')",
            "y = 1  # old")})
        self.edit(repo, "lib.py", "  # old", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(os.path.exists(marker), "verify ran a file with no __main__ block")

    def test_a_shell_file_named_like_an_option_is_checked(self):
        """Gemini G3."""
        repo = self.fixture(**{"-env.sh": SHELL})
        self.edit(repo, "-env.sh", "# say hi\n", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_changed_statement_exits_2(self):
        repo = self.fixture()
        self.edit(repo, "cli.py", "print('hello'", "print('hi'")
        self.assert_difference(repo, "cli.py:", "code changed")

    def test_a_deleted_doc_docstring_exits_2(self):
        repo = self.fixture()
        self.edit(repo, "cli.py", '"""Greet someone.\n\nPrints hello.\n"""\n', "")
        self.assert_difference(repo, "cli.py:", "module docstring")

    def test_changed_help_text_exits_2(self):
        """A function docstring is stripped from the AST, but this one is the --help text."""
        repo = self.fixture(**{"tool.py": src(
            "import argparse",
            "def run():",
            '    """Run the tool."""',
            "",
            "if __name__ == '__main__':",
            "    argparse.ArgumentParser(description=run.__doc__).parse_args()")})
        self.edit(repo, "tool.py", "Run the tool.", "Run it.")
        self.assert_difference(repo, "tool.py:", "--help")

    def test_verify_never_runs_a_script(self):
        """Round 3: a main block can write files or call out, so verify reads scripts and never runs them."""
        marker = os.path.join(tempfile.mkdtemp(prefix="comment-check-ran-"), "ran")
        self.addCleanup(shutil.rmtree, os.path.dirname(marker), True)
        repo = self.fixture(**{"sync.py": src(
            '"""Sync things."""',
            "if __name__ == '__main__':",
            "    print(__doc__)",
            f"    open({marker!r}, 'a').write('ran')  # old")})
        self.edit(repo, "sync.py", "  # old", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(os.path.exists(marker), "verify ran the script")

    def test_a_docstring_read_by_name_is_help_text_anywhere(self):
        """Round 3: with no __main__ guard, or read through self, a docstring can still be --help text."""
        cases = (("tool.py", src("import argparse, sys",
                                 "def main():",
                                 '    """Run the tool."""',
                                 "    argparse.ArgumentParser(description=main.__doc__).parse_args()",
                                 "sys.exit(main())"), "Run the tool.", "Run it."),
                 ("cmd.py", src("class Cmd:",
                                '    """Show help."""',
                                "    def usage(self):",
                                "        return self.__doc__"), "Show help.", "Help."))
        for path, text, old, new in cases:
            with self.subTest(path=path):
                repo = self.fixture(**{path: text})
                self.edit(repo, path, old, new)
                self.assert_difference(repo, f"{path}:", "--help")

    def test_a_python_file_that_did_not_parse_at_base_is_left_to_the_suite(self):
        """Round 3 low: a broken fixture must not block verify for good."""
        repo = self.fixture(**{"broken.py": "def (:  # old\n"})
        self.edit(repo, "broken.py", "  # old", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("1 other changed file left to the suite", result.stdout)

    def test_an_unreadable_file_is_named(self):
        """Round 4 low: exit 2 naming the file, never a traceback."""
        repo = self.fixture()
        self.edit(repo, "cli.py", "# Build the parser.\n", "")
        full = os.path.join(repo.root, "cli.py")
        os.chmod(full, 0)
        self.addCleanup(os.chmod, full, 0o644)
        result = repo.verify()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cli.py", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_a_nousfx_pointer_in_another_repo_is_not_looked_up(self):
        """Round 5 low: a file synced from NousFX points at NousFX's ADRs, not this repo's."""
        repo = self.fixture(**{"claude/x.py": "# see NousFX ADR-0001 for why\nx = 1  # old\n"})
        self.edit(repo, "claude/x.py", "  # old", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_missing_shell_is_a_difference_not_a_crash(self):
        """Round 5 low: no bash on PATH (Windows) names the file."""
        self.assertIn("cannot run", comment_check.syntax_error("no-such-shell-nfx228", "echo hi\n"))

    def test_a_lost_executable_bit_exits_2(self):
        """Round 4: git records the mode, so verify compares it too, in every language."""
        repo = self.fixture(**{"tool.js": "x = 1\n"})
        for path in ("run.sh", "tool.js"):
            os.chmod(os.path.join(repo.root, path), 0o755)
        repo.git("add", "-A")
        repo.git("commit", "-q", "-m", "exec")
        repo.base = repo.git("rev-parse", "HEAD").strip()
        self.edit(repo, "run.sh", "# say hi\n", "")
        for path in ("run.sh", "tool.js"):
            os.chmod(os.path.join(repo.root, path), 0o644)
        self.assert_difference(repo, "run.sh: mode changed", "tool.js: mode changed")

    def test_a_symlink_swap_exits_2(self):
        """Round 3: git status T (symlink to file or back) is named, never a traceback or OK."""
        for start_link in (True, False):
            with self.subTest(start_link=start_link):
                repo = self.fixture(**{"real.py": "x = 1\n"})
                link = os.path.join(repo.root, "tool.py")
                if start_link:
                    os.symlink("real.py", link)
                else:
                    repo.write("tool.py", "y = 2\n")
                repo.git("add", "-A")
                repo.git("commit", "-q", "-m", "link")
                repo.base = repo.git("rev-parse", "HEAD").strip()
                os.remove(link)
                if start_link:
                    repo.write("tool.py", "x = 1\n")
                else:
                    os.symlink("real.py", link)
                self.assert_difference(repo, "tool.py:", "symlink")

    def test_a_shell_syntax_error_exits_2(self):
        repo = self.fixture()
        self.edit(repo, "run.sh", "fi\n", "")
        self.assert_difference(repo, "run.sh:", "bash -n")

    def test_a_dangling_adr_pointer_exits_2_and_a_resolving_one_passes(self):
        repo = self.fixture(**{"docs/adr/0001-one-keep-list.md": "# One keep list\n"})
        self.edit(repo, "cli.py", "# Build the parser.", "# Built here, not in a helper: see ADR-0001")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.edit(repo, "cli.py", "ADR-0001", "ADR-0002")
        self.assert_difference(repo, "cli.py:", "ADR-0002")

    def test_a_pointer_under_claude_must_name_nousfx(self):
        repo = self.fixture(**{"docs/adr/0001-one-keep-list.md": "# One keep list\n",
                               "scripts/nousfx_sync.py": "x = 1\n", "claude/x.py": "x = 1  # old\n"})
        self.edit(repo, "claude/x.py", "# old", "# kept for the gate: see ADR-0001")
        self.assert_difference(repo, "claude/x.py:", "NousFX ADR-0001")
        self.edit(repo, "claude/x.py", "see ADR-0001", "see NousFX ADR-0001")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_an_adr_number_in_a_string_is_not_a_pointer(self):
        repo = self.fixture(**{"t.py": 'NOTE = "ADR-0042"  # a fixture\n'})
        self.edit(repo, "t.py", "  # a fixture", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_an_added_or_deleted_python_or_shell_file_exits_2(self):
        repo = self.fixture()
        repo.write("new.py", "x = 1\n")
        repo.remove("run.sh")
        self.assert_difference(repo, "new.py: added", "run.sh: deleted")

    def test_a_python_file_that_no_longer_parses_exits_2(self):
        repo = self.fixture()
        self.edit(repo, "cli.py", "def main():", "def main(:")
        self.assert_difference(repo, "cli.py:", "does not parse")

    def test_other_languages_and_markdown_are_left_to_the_suite(self):
        repo = self.fixture(**{"a.go": "var x = 1 // one\n"})
        self.edit(repo, "a.go", "var x = 1", "var x = 2")
        self.edit(repo, "README.md", "base", "changed")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("2 other changed files left to the suite", result.stdout)

    def test_a_plugins_folder_in_another_repo_takes_a_plain_pointer(self):
        """Round 1: the NousFX prefix rule applies only in NousFX (scripts/nousfx_sync.py marks it)."""
        repo = self.fixture(**{"docs/adr/0001-x.md": "# X\n", "plugins/p.py": "x = 1  # old\n"})
        self.edit(repo, "plugins/p.py", "# old", "# see ADR-0001 for why")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_deleted_shebang_or_encoding_line_exits_2(self):
        """Round 1: these comments change how the file runs."""
        for path, text, line in (("a.py", "#!/usr/bin/env python3\nx = 1\n", "#!/usr/bin/env python3\n"),
                                 ("b.py", "# -*- coding: latin-1 -*-\nx = 1\n", "# -*- coding: latin-1 -*-\n"),
                                 ("c.sh", "#!/bin/bash\necho hi\n", "#!/bin/bash\n"),
                                 ("bin/tool", "#!/usr/bin/env python3\nx = 1\n", "#!/usr/bin/env python3\n"),
                                 ("bin/run", "#!/bin/bash\necho hi\n", "#!/bin/bash\n")):
            with self.subTest(path=path):
                repo = self.fixture(**{path: text})
                self.edit(repo, path, line, "")
                self.assert_difference(repo, f"{path}:", "shebang or encoding")

    def test_a_hash_line_inside_a_heredoc_is_data_not_a_comment(self):
        repo = self.fixture(**{"h.sh": src("cat <<EOF", "# printed", "EOF")})
        self.edit(repo, "h.sh", "# printed\n", "")
        self.assert_difference(repo, "h.sh:", "code changed")

    def test_shell_line_structure_counts(self):
        """Round 2: blank lines in a heredoc or string, and a comment line after a continuation, are code."""
        cases = (("cat <<EOF\na\n\nb\nEOF\n", "a\n\nb", "a\nb"),
                 ('echo "x\n\ny"\n', 'x\n\ny', 'x\ny'),
                 ("echo a \\\n# note\necho b\n", "# note\n", ""))
        for text, old, new in cases:
            with self.subTest(text=text):
                repo = self.fixture(**{"s.sh": text})
                self.edit(repo, "s.sh", old, new)
                self.assert_difference(repo, "s.sh:", "code changed")

    def test_a_blank_line_between_commands_is_not_code(self):
        repo = self.fixture(**{"s.sh": "echo a\n\n# note\necho b  # why\n"})
        self.edit(repo, "s.sh", "\n# note\n", "\n")
        self.edit(repo, "s.sh", "  # why", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_shell_file_that_failed_bash_n_at_base_may_lose_a_comment(self):
        repo = self.fixture(**{"bad.sh": src("# note", "if true; then", "  echo hi")})
        self.edit(repo, "bad.sh", "# note\n", "")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_root_with_glob_characters_resolves_pointers(self):
        repo = self.repo({"cli.py": "x = 1  # old\n", "docs/adr/0001-x.md": "# X\n"}, prefix="comment-check-[x]-")
        with open(os.path.join(repo.root, "cli.py"), "w") as f:
            f.write("x = 1  # see ADR-0001 for the reason here\n")
        result = repo.verify()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_bad_base_exits_2(self):
        repo = self.fixture()
        result = repo.run("verify", "not-a-commit")
        self.assertEqual(result.returncode, 2)
        self.assertIn("git failed", result.stderr)

    def test_verify_without_a_base_is_refused(self):
        result = self.fixture().run("verify")
        self.assertEqual(result.returncode, 2)
        self.assertIn("BASE", result.stderr)


KOTLIN = src(
    "package app",
    "",
    "/**",
    " * Greets someone.",
    " */",
    "fun greet(name: String): String {",
    '    val url = "https://example.com/a//b/*c*/"  // the home page',
    "    /* the greeting */",
    '    return "hi $name" + url',
    "}")

DART = src(
    "/// A dog.",
    "class Dog {",
    "  // its name",
    "  final String name;",
    "  Dog(this.name);",
    "",
    "  List<String> tricks() => [",
    "        'sit', //",
    "        'stay', // the hard one",
    "        'https://example.com//x',",
    "      ];",
    "}")

TSX = src(
    "export function Card() {",
    "  return (",
    "    <div>",
    "      {/* the title */}",
    '      <h1 className="a//b">Hi</h1>',
    "    </div>",
    "  );",
    "}")


class VerifyCLike(RepoCase):
    """NFX-250: verify compares Kotlin, Dart, TypeScript and JavaScript token by token outside comments."""

    def edited(self, path, text, old, new):
        repo = self.repo({path: text})
        full = os.path.join(repo.root, path)
        self.assertIn(old, text)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))
        return repo.verify()

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 other changed files left to the suite", result.stdout)

    def assert_changed(self, result, location):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(f"{location} code changed", result.stdout)

    def test_removing_only_comments_passes(self):
        cases = (("a.kt", KOTLIN, "/**\n * Greets someone.\n */\n", ""),
                 ("a.kt", KOTLIN, "  // the home page", ""),
                 ("a.kt", KOTLIN, "    /* the greeting */\n", ""),
                 ("lib/dog.dart", DART, "/// A dog.\n", ""),
                 ("lib/dog.dart", DART, "  // its name\n", ""),
                 ("lib/dog.dart", DART, " // the hard one", ""),
                 ("ui/card.tsx", TSX, "      {/* the title */}\n", ""))
        for path, text, old, new in cases:
            with self.subTest(path=path, old=old):
                self.assert_ok(self.edited(path, text, old, new))

    def test_one_changed_code_token_exits_2_naming_its_line(self):
        cases = (("a.kt", KOTLIN, '"hi $name"', '"hey $name"', "a.kt:9"),
                 ("a.kt", KOTLIN, "a//b/*c*/", "a//b", "a.kt:7"),
                 ("lib/dog.dart", DART, "'stay'", "'go'", "lib/dog.dart:9"),
                 ("lib/dog.dart", DART, "final String", "final Object", "lib/dog.dart:4"),
                 ("ui/card.tsx", TSX, 'className="a//b"', 'className="a"', "ui/card.tsx:5"),
                 ("ui/card.tsx", TSX, "<h1", "<h2", "ui/card.tsx:5"))
        for path, text, old, new, location in cases:
            with self.subTest(path=path, old=old):
                self.assert_changed(self.edited(path, text, old, new), location)

    def test_a_bare_trailing_dart_slash_pair_is_code(self):
        """`dart format` keeps a list split on lines when an element ends in `//`: it is not a comment."""
        repo = self.repo({"lib/dog.dart": DART})
        lines, _ = repo.inventory("lib/dog.dart")
        self.assertFalse([l for l in lines if l.startswith("lib/dog.dart:8 ")], lines)
        self.assertTrue([l for l in lines if l.startswith("lib/dog.dart:9 ")], lines)
        self.assert_changed(self.edited("lib/dog.dart", DART, "'sit', //", "'sit',"), "lib/dog.dart:8")

    def test_the_diff_mode_does_not_flag_a_bare_trailing_dart_slash_pair(self):
        repo = self.repo({"lib/dog.dart": "x"})
        repo.write("lib/dog.dart", DART)
        self.assertFalse([l for l in repo.flags() if l.startswith("lib/dog.dart:8 ")])

    def test_a_bare_slash_pair_in_kotlin_is_still_a_comment(self):
        self.assert_ok(self.edited("a.kt", "val x = listOf(\n    1, //\n)\n", " //", ""))

    def test_tsx_braces_left_behind_are_a_code_change(self):
        self.assert_changed(self.edited("ui/card.tsx", TSX, "{/* the title */}", "{}"), "ui/card.tsx:4")

    def test_empty_braces_in_code_are_never_dropped(self):
        """Only a JSX child is a comment expression: an arrow body or an if block keeps its braces."""
        cases = ("const f = () => {/* nothing */}\n<a/>\n", "if (x) {/* nothing */}\n{y}\n",
                 "try { run() } catch {/* nothing */}\n{ cleanup() }\n",
                 "interface Props {/* nothing */}\n<App />\n",
                 "async function f(): Promise<void> {/* nothing */}\n{ x() }\n",
                 "class A { static {/* nothing */}\n<B/> }\n")
        for text in cases:
            with self.subTest(text=text):
                result = self.edited("a.tsx", text, "{/* nothing */}", "")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertRegex(result.stdout, r"a\.tsx:\d+ code changed")

    def test_jsx_text_around_a_comment_expression(self):
        cases = (("<p>Hello {/* name */}</p>\n", " {/* name */}"),
                 ("<p>{/* note */}Hello</p>\n", "{/* note */}"),
                 ("<p>Hello: {/* name */}</p>\n", "{/* name */}"),
                 ("return <div className={a}>{/* note */}</div>\n", "{/* note */}"))
        for text, old in cases:
            with self.subTest(text=text):
                self.assert_ok(self.edited("a.jsx", text, old, ""))

    def test_raw_strings_end_at_their_quote(self):
        """A backslash escapes nothing in a Dart r'...' or a Kotlin \"\"\"...\"\"\" string."""
        self.assert_ok(self.edited("a.dart", "final p = s.split(r'\\'); // Windows\nx;\n", " // Windows", ""))
        self.assert_ok(self.edited("a.kt", 'val p = """C:\\"""  // dir\nval y = 1\n', "  // dir", ""))

    def test_a_bare_slash_pair_after_a_multiline_dart_string_is_code(self):
        result = self.edited("a.dart", "final s = '''\nhello\n''' //\n'world';\n", "''' //", "'''")
        self.assert_changed(result, "a.dart:3")

    def test_an_added_or_deleted_kotlin_file_exits_2(self):
        repo = self.repo({"a.kt": KOTLIN})
        repo.write("b.ts", "const x = 1\n")
        repo.remove("a.kt")
        result = repo.verify()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("a.kt: deleted", result.stdout)
        self.assertIn("b.ts: added", result.stdout)

    def test_regrouped_operators_and_moved_line_breaks_are_code(self):
        """Spacing between two operators, and line breaks (ASI, Kotlin statements), are code."""
        cases = (("a.ts", "x = i++ + j\n", "i++ + j", "i + ++j"),
                 ("a.ts", "function f() {\n  return a\n}\n", "return a", "return\n  a"),
                 ("a.kt", "val x = a - b\n", "a - b", "a\n- b"))
        for path, text, old, new in cases:
            with self.subTest(text=text):
                result = self.edited(path, text, old, new)
                self.assertEqual(result.returncode, 2, result.stdout)

    def test_comment_spacing_is_not_code(self):
        cases = (("a.ts", "foo(/* x */ a)\n", "/* x */ ", ""),
                 ("a.ts", "foo(/* x */a)\n", "/* x */", ""),
                 ("a.ts", "a = b - /* x */ -c\n", "/* x */ ", ""),
                 ("a.kt", "val x = 1 /* one */\nval y = 2\n", " /* one */", ""),
                 ("a.tsx", "<div>\n  {/* t */} <b/>\n</div>\n", "{/* t */} ", ""))
        for path, text, old, new in cases:
            with self.subTest(text=text):
                self.assert_ok(self.edited(path, text, old, new))

    def test_code_after_a_comment_marker_in_code_is_compared(self):
        """A URL in JSX text, a quote nested in an interpolation and a regex class open no comment."""
        cases = (("a.tsx", "<a>https://x.com</a>; let n = 1\n"),
                 ("a.kt", 'val s = "${if (a) "http://x" else ""}"; val n = 1\n'),
                 ("a.dart", "var s = '${a ? 'http://x' : ''}'; var n = 1;\n"),
                 ("a.ts", "const s = `${a ? `http://${b}` : ''}`; let n = 1\n"),
                 ("a.ts", "const r = /[/*]/;\nlet n = 1;\n// */\n"))
        for path, text in cases:
            with self.subTest(text=text):
                result = self.edited(path, text, "n = 1", "n = 2")
                self.assertEqual(result.returncode, 2, result.stdout)

    def test_a_trailing_comment_after_an_interpolation_or_regex_may_go(self):
        cases = (("a.kt", 'val s = "${if (a) "x" else ""}" // why\n'),
                 ("a.ts", "const r = /a\\/b/g // why\n"),
                 ("a.ts", "const q = a / b // why\n"))
        for path, text in cases:
            with self.subTest(text=text):
                self.assert_ok(self.edited(path, text, " // why", ""))

    def test_a_deleted_compiler_directive_exits_2(self):
        """These comments change how the file compiles."""
        cases = (("src/vite-env.d.ts", '/// <reference types="vite/client" />\nlet n = 1\n'),
                 ("a.dart", "// @dart=2.9\nvar n = 1;\n"),
                 ("a.tsx", "/** @jsxImportSource preact */\nlet n = 1\n"),
                 ("a.ts", "// @ts-nocheck\nlet n = 1\n"))
        for path, text in cases:
            with self.subTest(path=path):
                head = text.split("\n", 1)[0] + "\n"
                result = self.edited(path, text, head, "")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn("compiler directive", result.stdout)

    def test_a_large_file_with_far_apart_edits_finishes_fast(self):
        """Difflib on a whole file is quadratic; verify trims the common ends first."""
        body = "".join(f"const v{i} = f(a, b, c, d, e);\n" for i in range(8000))
        old = "let first = 1\n" + body + "let last = 1\n"
        new = "let first = 2\n" + body + "let last = 2\n"
        start = time.monotonic()
        found = comment_check.c_differences("big.ts", old, new)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual([row for row, _ in found][:1], [1])
        self.assertTrue(found)

    def test_a_spaced_pointer_under_claude_must_name_nousfx_with_a_hyphen(self):
        """NousFX files keep the one form `NousFX ADR-NNNN`."""
        repo = self.repo({"docs/adr/0001-x.md": "# X\n", "scripts/nousfx_sync.py": "x = 1\n",
                          "claude/x.py": "x = 1  # old\n"})
        with open(os.path.join(repo.root, "claude/x.py"), "w") as f:
            f.write("x = 1  # see NousFX ADR 0001 for why\n")
        result = repo.verify()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("NousFX ADR-0001", result.stdout)

    def test_a_division_after_a_non_null_assertion_is_no_regex(self):
        """Kotlin has no regex literals, and a postfix `!` (`a!!`, `x!`) ends an operand."""
        cases = (("a.kt", "val r = a!! / 2 // half\n"), ("a.ts", "const r = x! / 2 // half\n"))
        for path, text in cases:
            with self.subTest(path=path):
                self.assertEqual([c.text for c in comment_check.comments_in(path, text)], ["// half"])
                self.assert_ok(self.edited(path, text, " // half", ""))

    def test_a_comment_in_jsx_text_is_code(self):
        """`//` and `/* */` between tags are rendered text, not comments."""
        cases = (("a.tsx", "<div>\n  Home // Settings\n</div>\n", " // Settings"),
                 ("a.jsx", "<p>a /* b */ c</p>\n", "/* b */ "))
        for path, text, old in cases:
            with self.subTest(text=text):
                result = self.edited(path, text, old, "")
                self.assertEqual(result.returncode, 2, result.stdout)

    def test_a_comment_after_jsx_code_may_go(self):
        cases = ("const a = <b>x</b>; // why\n", "return (\n  <div>\n    {/* note */}\n  </div>\n) // why\n")
        for text in cases:
            with self.subTest(text=text):
                self.assert_ok(self.edited("a.tsx", text, " // why", ""))

    def test_a_nested_block_comment_ends_at_its_own_close(self):
        """Kotlin and Dart nest block comments."""
        for path in ("a.kt", "a.dart"):
            with self.subTest(path=path):
                text = "/* a /* b */ c */\nval n = 1\n"
                self.assertEqual([c.text for c in comment_check.comments_in(path, text)], ["/* a /* b */ c */"])
                result = self.edited(path, text, "/* a /* b */", "")
                self.assertEqual(result.returncode, 2, result.stdout)

    def test_a_deletion_at_the_start_names_the_first_line(self):
        found = comment_check.c_differences("a.ts", "x; y\nz\nw\nv\n", "y\nz\nw\nv\n")
        self.assertEqual([row for row, _ in found][:1], [1])

    def test_an_edit_to_a_comment_in_jsx_text_is_code(self):
        """JSX text that looks like a comment is compared by its text."""
        cases = (("<p>// TODO a</p>\n", "TODO a", "TODO b"), ("<div>\n  /* hi */\n  <b />\n</div>\n", "hi", "bye"),
                 ("<p>// a</p>\n", "</p>", "</div>"))
        for text, old, new in cases:
            with self.subTest(text=text):
                self.assertEqual(self.edited("a.tsx", text, old, new).returncode, 2)

    def test_a_comment_glued_to_brackets_may_go(self):
        """Only operators fuse, so `{/* x */}` and `{}` are the same code."""
        cases = (("a.kt", "fun f() {/* nothing */}\n", "/* nothing */"), ("a.kt", "val a = listOf(/* none */)\n", "/* none */"),
                 ("a.dart", "var a = Foo(/* x */);\n", "/* x */"),
                 ("a.tsx", "useEffect(() => {/* noop */}, [])\n", "/* noop */"))
        for path, text, old in cases:
            with self.subTest(text=text):
                self.assert_ok(self.edited(path, text, old, ""))

    def test_a_regex_after_an_arrow_or_keyword_opens_no_comment(self):
        cases = ("const f = (s) => /[/*]/.test(s)\nlet a = 1\n/* x */\n",
                 "for (const m of /[/*]/g.exec(s)) {}\nlet a = 1\n/* x */\n",
                 "async function f() { await /[/*]/.test(s) }\nlet a = 1\n/* x */\n")
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(self.edited("a.ts", text, "a = 1", "a = 2").returncode, 2)

    def test_a_comment_after_a_colon_outside_jsx_is_a_comment(self):
        """The URL exception is for JSX text only."""
        self.assert_ok(self.edited("a.ts", "const a = x ? 1 ://c\n  2\n", "//c", ""))

    def test_jsx_text_on_the_next_line_is_text(self):
        result = self.edited("a.tsx", "<p>\n  hello // world\n  more\n</p>\n", " // world", "")
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_a_code_comment_between_jsx_elements_may_go(self):
        """An array of elements and a spread attribute are code, not children."""
        cases = ("const a = [\n  <A key=\"a\" />,\n  // admin only\n  <B key=\"b\" />,\n]\n",
                 "const a = <Foo\n  a={1}\n  // forward the rest\n  {...rest}\n/>\n",
                 "const a = cond ? <A /> : null // why\nconst b = <B>x</B>\n// why\nlet c = 1\n")
        for text in cases:
            with self.subTest(text=text):
                self.assert_ok(self.edited("a.tsx", text, "// admin only\n" if "admin" in text else
                                           "// forward the rest\n" if "forward" in text else " // why", ""))

    def test_a_directive_reason_may_change(self):
        """The delete-comments skill rewrites the reason of a bare suppression."""
        text = "// @ts-expect-error: old lib types, see the long story here\nlet n = f()\n"
        self.assert_ok(self.edited("a.ts", text, "old lib types, see the long story here", "old lib types (YUY-12)"))

    def test_a_comment_expression_that_parts_two_text_lines_is_code(self):
        """Without it the two lines join into one text with a space."""
        cases = (("<p>\n  a\n  {/* c */}\n  b\n</p>\n", "  {/* c */}\n"), ("<p>\n  a{/* c */}\n  b\n</p>\n", "{/* c */}"))
        for text, old in cases:
            with self.subTest(text=text):
                self.assertEqual(self.edited("a.tsx", text, old, "").returncode, 2)

    def test_a_comment_object_in_an_array_of_elements_is_code(self):
        result = self.edited("a.tsx", "const a = [<A/>, {/* c */}, <B/>]\n", "{/* c */}", "")
        self.assertEqual(result.returncode, 2, result.stdout)

    def test_an_apostrophe_in_jsx_text_opens_no_string(self):
        text = "const a = <p>Don't go</p>\n// why\nlet b = 1\n"
        self.assertEqual([c.text for c in comment_check.comments_in("a.tsx", text)], ["// why"])
        self.assert_ok(self.edited("a.tsx", text, "// why\n", ""))
        self.assertEqual(self.edited("a.tsx", text, "b = 1", "b = 2").returncode, 2)

    def test_slashes_in_jsx_text_are_not_listed(self):
        self.assertEqual(comment_check.comments_in("a.tsx", "<div>\n  Home // Settings\n</div>\n"), [])

    def test_jsx_in_a_plain_js_file_is_followed(self):
        """CRA and React Native write JSX in .js files."""
        for path in ("a.js", "a.mjs", "a.cjs"):
            with self.subTest(path=path):
                text = "const a = <p>Visit http://example.com today</p>\n"
                self.assertEqual(comment_check.comments_in(path, text), [])
                self.assertEqual(self.edited(path, text, "//example.com today</p>", "").returncode, 2)

    def test_a_hemisphere_pointer_resolves(self):
        """Hemisphere writes `ADR NNNN`, never the hyphen."""
        repo = self.repo({"a.kt": "val x = 1 // old\n", "docs/adr/0002-room.md": "# Room\n"})
        with open(os.path.join(repo.root, "a.kt"), "w") as f:
            f.write("val x = 1 // see ADR 0002 for why\n")
        self.assertEqual(repo.verify().returncode, 0)
        with open(os.path.join(repo.root, "a.kt"), "w") as f:
            f.write("val x = 1 // see ADR 0003 for why\n")
        result = repo.verify()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("0003", result.stdout)


class PilotTuning(RepoCase):
    """NFX-240: the keep list tuned from the NFX-230 pilot in scripts/."""

    def verdicts(self, path, source):
        repo = self.repo()
        repo.write(path, source)
        return [(line, verdict) for _, line, _, verdict in items(repo.inventory(path)[0])]

    def test_a_test_method_docstring_is_flagged_and_a_public_one_is_kept(self):
        self.assertEqual(self.verdicts("test_lib.py", src(
            "import unittest",
            "def load():",
            '    """Read a widget file."""',
            "class Loads(unittest.TestCase):",
            '    """Loading a widget."""',
            "    def test_load(self):",
            '        """load reads a widget file."""',
            "class TestSize:",
            "    def check(self):",
            '        """A helper on a Test class."""',
            "    def test_tagged(self):",
            '        """NFX-1: four or more words here."""',
            "class Testimonial:",
            "    def render(self):",
            '        """Render the card."""')),
            [(3, "keep:public-doc"), (5, "keep:public-doc"), (7, "flag:test-docstring"),
             (10, "flag:test-docstring"), (12, "flag:test-docstring"), (15, "keep:public-doc")])

    def test_test_names_outside_a_test_file_stay_public(self):
        self.assertEqual(self.verdicts("client.py", src(
            "class TestClient:",
            "    def get(self):",
            '        """Send a GET request."""',
            "def test_connection(host):",
            '    """Open and close a socket to host."""')),
            [(3, "keep:public-doc"), (5, "keep:public-doc")])
        self.assertEqual(self.verdicts("tests/helpers.py", src(
            "def test_x():", '    """Checks x."""')), [(2, "flag:test-docstring")])
        self.assertEqual(self.verdicts("app/tests.py", src(
            "def test_x():", '    """Test x."""')), [(2, "flag:test-docstring")])

    def test_a_tests_folder_above_path_does_not_make_a_test_file(self):
        repo = self.repo(prefix="comment-check-")
        repo.write("tests/proj/lib.py", 'def test_connection():\n    """Open a socket."""\n')
        result = subprocess.run([sys.executable, "-B", SCRIPT, "--all", os.path.join(repo.root, "tests", "proj")],
                                cwd=repo.root, capture_output=True, text=True)
        self.assertIn("keep:public-doc", result.stdout)

    def test_a_banner_and_a_bare_ticket_tag_get_their_kind(self):
        self.assertEqual(self.verdicts("a.py", src(
            "# --- gh ---",
            "u = 0",
            "# ---- findings ------------",
            "x = 1  # NFX-100",
            "y = 2  # NFX-114: moved",
            "z = 3  # NFX-114: the moved section",
            "w = 4  # NFX-20: tier.py's base",
            "v = 5  # see https://github.com/o/r/issues/7",
            "# a - b is the gap",
            "t = 0",
            "# --- Section: parsing ---",
            "s = 0",
            "# === WARNING: the vendor API returns UTC, not local time ===",
            "r = 0",
            "# --- keep this order: parse() reads the header first",
            "q = 0",
            "# -*- mode: python -*-",
            "p = 0  # AVX-512",
            "o = 0",
            "# --- do not call twice ---")),
            [(1, "flag:banner"), (3, "flag:banner"), (4, "flag:ticket-tag"), (5, "flag:ticket-tag"),
             (6, "flag:ticket-tag"), (7, "flag:ticket"), (8, "flag:ticket"), (9, "flag:comment"),
             (11, "flag:comment"), (13, "flag:comment"), (15, "flag:comment"),
             (17, "flag:comment"), (18, "flag:comment"), (20, "flag:comment")])

    def test_a_long_banner_like_line_is_judged_quickly(self):
        start = time.monotonic()
        for text in ("# --- " + " " * 2000 + ";", "# " + "-" * 2000 + ";", "#" * 2000 + ";"):
            comment_check.classify(comment_check.Comment(1, 1, text, "line"))
        doc = '"""' + "returns x " * 8000 + '"""'
        comment_check.classify(comment_check.Comment(1, 1, doc, "docstring", "private"))
        self.assertLess(time.monotonic() - start, 1)

    def test_e402_after_a_sys_path_insert_is_kept(self):
        self.assertEqual(self.verdicts("t.py", src(
            "import os",
            "import json  # noqa: E402",
            "import sys",
            "sys.path.insert(0, os.getcwd())",
            "sys.dont_write_bytecode = True",
            "",
            "import tool  # noqa: E402",
            "import other  # noqa: E402",
            "import unused  # noqa: E402, F401",
            "import spaced  # noqa: E402 F401",
            "x = 1  # noqa: E501",
            "if x:",
            "    sys.path.append(os.getcwd())",
            "import guarded  # noqa: E402")),
            [(2, "flag:suppression"), (7, "keep:path-import"), (8, "keep:path-import"), (9, "flag:suppression"),
             (10, "flag:suppression"), (11, "flag:suppression"), (14, "keep:path-import")])

    def test_a_private_docstring_that_states_a_contract_is_kept(self):
        self.assertEqual(self.verdicts("lib.py", src(
            "def _read(p):",
            '    """The text, or None when absent. Raises OSError for anything else."""',
            "def _linked(p):",
            '    """True when the path is a symlink."""',
            "def _bump(x):",
            '    """Add one to x."""',
            "def _find(x):",
            '    """[] if nothing matched; {} on a miss."""',
            "def _load(p):",
            '    """Load p, False for now until NFX-9 lands."""')),
            [(2, "keep:contract"), (4, "keep:contract"), (6, "flag:docstring"), (8, "keep:contract"),
             (10, "keep:ticket-why")])


class PilotBaseline(unittest.TestCase):
    """NFX-230 retro R1: `--all scripts` at 3f2fd78 names 7 banners and 16 ticket tags, and flags no E402.
    NousFX only: it reads that commit from the repo's history."""

    COMMIT = "3f2fd78"

    def test_the_pilot_tree_counts(self):
        root = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
        if not os.path.isfile(os.path.join(root, "scripts", "nousfx_sync.py")):
            self.skipTest("not the NousFX repo")
        if subprocess.run(["git", "cat-file", "-e", f"{self.COMMIT}^{{commit}}"], cwd=root,
                          capture_output=True).returncode:
            self.skipTest(f"{self.COMMIT} is not in this clone (shallow?)")
        folder = tempfile.mkdtemp(prefix="comment-check-pilot-")
        self.addCleanup(shutil.rmtree, folder, True)
        archive = subprocess.run(["git", "archive", self.COMMIT, "scripts"], cwd=root, capture_output=True,
                                 check=True).stdout
        subprocess.run(["tar", "-x", "-C", folder], input=archive, check=True)
        result = subprocess.run([sys.executable, "-B", SCRIPT, "--all", "scripts"], cwd=folder, capture_output=True,
                                text=True, env=dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.dirname(folder)))
        self.assertEqual(result.returncode, 0, result.stderr)
        verdicts = [l.split(" ")[2] for l in result.stdout.splitlines()[:-1]]
        e402 = [l for l in result.stdout.splitlines() if "E402" in l and " flag:" in l]
        self.assertEqual((verdicts.count("flag:banner"), verdicts.count("flag:ticket-tag"), len(e402)), (7, 16, 0))


class DiffModeUnchanged(RepoCase):
    """NFX-228 must not change diff mode: one fixture with every kind, its output pinned byte for byte."""

    def test_diff_mode_output_is_byte_identical(self):
        repo = self.repo()
        repo.write("a.py", src(
            "x = 1  # bump",
            "y = 2  # noqa",
            "# NFX-1",
            "z = 3  # do not remove",
            "def _f():",
            '    """Private."""'))
        result = repo.check()
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(result.stdout, "\n".join([
            "a.py:1 comment # bump",
            "a.py:2 suppression # noqa",
            "a.py:3 ticket-tag # NFX-1",
            "a.py:4 guard # do not remove",
            'a.py:6 docstring """Private."""',
            "comment_check: 5 flags (comment 1, docstring 1, guard 1, suppression 1, ticket-tag 1)",
        ]) + "\n")


if __name__ == "__main__":
    unittest.main()
