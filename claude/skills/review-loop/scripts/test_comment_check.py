#!/usr/bin/env python3
"""Tests for comment_check.py (NFX-196), the warn-only check on comments a diff adds.
Run: python3 -B claude/skills/review-loop/scripts/test_comment_check.py

Seams: the CLI (`comment_check.py <base>` in a throwaway repo, one commit per case) and the KEEP structure
NFX-228 imports. Git tests use temp repos and never touch the real repo or HOME.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
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

    def __init__(self, base_files=None):
        self.root = tempfile.mkdtemp(prefix="comment-check-")
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
        env = dict(os.environ, **GIT_ENV, **extra_env)
        return subprocess.run([sys.executable, "-B", SCRIPT, base or self.base], cwd=self.root, env=env,
                              capture_output=True, text=True)

    def flags(self):
        """The flag lines (`file:line kind text`), without the count line."""
        result = self.check()
        assert result.returncode == 0, result.stderr
        return [l for l in result.stdout.splitlines() if not l.startswith("comment_check:")]

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class RepoCase(unittest.TestCase):
    def repo(self, base_files=None):
        repo = Repo(base_files)
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
            "a.py:1 ticket # NFX-123",
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
        self.assertEqual(repo.flags(), ["a.py:1 comment # helper for legacy payloads", "a.py:2 ticket # NFX-123",
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


if __name__ == "__main__":
    unittest.main()
