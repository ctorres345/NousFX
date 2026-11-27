#!/usr/bin/env python3
"""NFX-292: changelog fragments. Run: python3 -B scripts/test_changelog_fragments.py [-k fold|check|guard|replay_191]"""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import changelog_fragments as cf  # noqa: E402

CHANGELOG = """# Changelog

## Unreleased

### Sync
- Sync bullet (NFX-235).

### Review gate
- Gate bullet two (NFX-271).
- Gate bullet one (NFX-223).

### Rules
- Rules bullet (NFX-281).

### Review gate
- Older gate bullet (NFX-210).

## 1.3.2 (2026-11-17)

### Via assets
- Assets bullet (NFX-170).

### Tickets
- Tickets bullet (NFX-169).
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def make_root(tmp, changelog=CHANGELOG, fragments=None):
    write(os.path.join(tmp, "CHANGELOG.md"), changelog)
    write(os.path.join(tmp, "changelog.d", "README.md"), "# Fragments\n")
    for name, text in (fragments or {}).items():
        write(os.path.join(tmp, "changelog.d", name), text)
    return tmp


def git_env(home):
    env = dict(os.environ, HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return env


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=" + os.devnull, *args],
                       cwd=repo, capture_output=True, text=True, env=git_env(repo))
    if check and r.returncode != 0:
        raise AssertionError("git %s failed: %s%s" % (" ".join(args), r.stdout, r.stderr))
    return r


def init_repo(tmp):
    make_root(tmp)
    git(tmp, "init", "-q")
    git(tmp, "add", "-A")
    git(tmp, "commit", "-q", "-m", "base")
    return git(tmp, "rev-parse", "HEAD").stdout.strip()


def commit_all(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


class Fold(unittest.TestCase):
    def test_fold_puts_bullets_under_their_section_newest_first_and_dates_the_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments={
                "NFX-292.md": "### Review gate\n\n- Fragments bullet (NFX-292).\n",
                "NFX-300.md": "### Review gate\n- Newer gate bullet (NFX-300).\n  Continued line.\n",
                "NFX-295.md": "### Sync\n- Board sync bullet (NFX-295).\n",
            })
            cf.fold(tmp, "1.4.0", "2026-11-27")
            text = read(os.path.join(tmp, "CHANGELOG.md"))
            self.assertNotIn("## Unreleased", text)
            self.assertIn("## 1.4.0 (2026-11-27)\n\n### Sync\n- Board sync bullet (NFX-295).\n- Sync bullet (NFX-235).\n", text)
            self.assertIn("### Review gate\n- Newer gate bullet (NFX-300).\n  Continued line.\n"
                          "- Fragments bullet (NFX-292).\n- Gate bullet two (NFX-271).\n", text)
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "changelog.d"))), ["README.md"])

    def test_fold_fills_only_the_first_of_two_same_named_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments={"NFX-292.md": "### Review gate\n- Fragments bullet (NFX-292).\n"})
            cf.fold(tmp, "1.4.0", "2026-11-27")
            text = read(os.path.join(tmp, "CHANGELOG.md"))
            self.assertIn("### Review gate\n- Fragments bullet (NFX-292).\n- Gate bullet two (NFX-271).\n", text)
            self.assertIn("### Review gate\n- Older gate bullet (NFX-210).\n\n## 1.3.2", text)

    def test_fold_keeps_section_order_and_puts_a_section_new_to_the_block_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments={"NFX-301.md": "### Tickets\n- Tickets bullet (NFX-301).\n"})
            cf.fold(tmp, "1.4.0", "2026-11-27")
            text = read(os.path.join(tmp, "CHANGELOG.md"))
            block = text.split("## 1.4.0 (2026-11-27)")[1].split("## 1.3.2")[0]
            heads = [line for line in block.splitlines() if line.startswith("### ")]
            self.assertEqual(heads, ["### Sync", "### Review gate", "### Rules", "### Review gate", "### Tickets"])
            self.assertIn("### Tickets\n- Tickets bullet (NFX-301).\n\n## 1.3.2", text)

    def test_fold_keeps_both_fragments_of_one_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments={"NFX-292.md": "### Sync\n- First PR (NFX-292).\n",
                                      "NFX-292-2.md": "### Sync\n- Second PR (NFX-292).\n"})
            cf.fold(tmp, "1.4.0", "2026-11-27")
            text = read(os.path.join(tmp, "CHANGELOG.md"))
            self.assertIn("### Sync\n- Second PR (NFX-292).\n- First PR (NFX-292).\n- Sync bullet (NFX-235).\n", text)

    def test_fold_makes_a_new_dated_block_when_there_is_no_unreleased_heading(self):
        released = "# Changelog\n\n## 1.3.2" + CHANGELOG.split("## 1.3.2", 1)[1]
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, changelog=released, fragments={"NFX-302.md": "### Tickets\n- New bullet (NFX-302).\n"})
            cf.fold(tmp, "1.4.0", "2026-11-27")
            text = read(os.path.join(tmp, "CHANGELOG.md"))
            self.assertTrue(text.startswith("# Changelog\n\n## 1.4.0 (2026-11-27)\n\n### Tickets\n- New bullet (NFX-302).\n\n## 1.3.2"), text[:200])

    def test_fold_ends_the_file_with_a_newline_when_unreleased_is_the_last_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, "# Changelog\n\n## Unreleased\n\n### Sync\n- Old (NFX-1).\n",
                      {"NFX-2.md": "### Sync\n- New (NFX-2).\n"})
            cf.fold(tmp, "0.1.0", "2026-11-27")
            self.assertEqual(read(os.path.join(tmp, "CHANGELOG.md")),
                             "# Changelog\n\n## 0.1.0 (2026-11-27)\n\n### Sync\n- New (NFX-2).\n- Old (NFX-1).\n")

    def test_fold_matches_a_section_heading_with_trailing_space(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, CHANGELOG.replace("### Sync\n", "### Sync \n", 1), {"NFX-299.md": "### Sync\n- New (NFX-299).\n"})
            cf.fold(tmp, "1.4.0", "2026-11-27")
            text = read(os.path.join(tmp, "CHANGELOG.md"))
            self.assertIn("### Sync \n- New (NFX-299).\n", text)
            self.assertEqual(text.split("## 1.3.2")[0].count("### Sync"), 1)

    def test_fold_refuses_an_unreleased_heading_that_is_not_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            changelog = CHANGELOG.replace("## Unreleased\n", "## Unreleased \n")
            make_root(tmp, changelog, {"NFX-299.md": "### Sync\n- New (NFX-299).\n"})
            with self.assertRaises(cf.FragmentError) as e:
                cf.fold(tmp, "1.4.0", "2026-11-27")
            self.assertIn("## Unreleased", str(e.exception))
            self.assertEqual(read(os.path.join(tmp, "CHANGELOG.md")), changelog)

    def test_fold_writes_nothing_when_a_fragment_is_bad(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments={"NFX-292.md": "### Sync\n- Good (NFX-292).\n", "NFX-293.md": "### Nope\n- Bad.\n"})
            with self.assertRaises(cf.FragmentError):
                cf.fold(tmp, "1.4.0", "2026-11-27")
            self.assertEqual(read(os.path.join(tmp, "CHANGELOG.md")), CHANGELOG)
            self.assertIn("NFX-292.md", os.listdir(os.path.join(tmp, "changelog.d")))

    def test_fold_refuses_a_version_that_already_has_a_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp)
            with self.assertRaises(cf.FragmentError):
                cf.fold(tmp, "1.3.2", "2026-11-27")

    def test_fold_cli_then_release_check_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments={"NFX-292.md": "### Sync\n- Bullet (NFX-292).\n"})
            script = os.path.join(ROOT, "scripts", "changelog_fragments.py")
            r = subprocess.run([sys.executable, "-B", script, "fold", "--version", "1.4.0", "--date", "2026-11-27", "--root", tmp],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([sys.executable, "-B", script, "check", "--release", "--root", tmp], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class Check(unittest.TestCase):
    def errors(self, fragments, release=False):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments=fragments)
            return cf.check(tmp, release=release)

    def test_check_passes_good_fragments_and_ignores_readme_and_gitkeep(self):
        self.assertEqual(self.errors({"NFX-292.md": "### Review gate\n- Bullet.\n", "NFX-292-2.md": "### Sync\n\n- Two.\n- Three.\n",
                                      ".gitkeep": ""}), [])

    def test_check_names_a_fragment_with_an_unknown_section(self):
        errors = self.errors({"NFX-292.md": "### Review gates\n- Bullet.\n"})
        self.assertEqual(len(errors), 1)
        self.assertIn("changelog.d/NFX-292.md", errors[0])
        self.assertIn("unknown section", errors[0])

    def test_check_names_a_fragment_with_no_bullet(self):
        for text in ("### Sync\n", "### Sync\nJust prose.\n", "- Bullet with no section.\n", ""):
            errors = self.errors({"NFX-293.md": text})
            self.assertEqual(len(errors), 1, text)
            self.assertIn("changelog.d/NFX-293.md", errors[0])

    def test_check_refuses_a_second_heading_in_one_fragment(self):
        for second in ("### Review gate\n- y.\n", "## 9.9.9 (2026-01-01)\n- y.\n"):
            errors = self.errors({"NFX-292.md": "### Sync\n- x.\n" + second})
            self.assertEqual(len(errors), 1, second)
            self.assertIn("one section per fragment", errors[0])

    def test_check_skips_hidden_files_and_editor_leftovers(self):
        self.assertEqual(self.errors({".DS_Store": "\0", ".NFX-292.md.swp": "x", "NFX-292.md~": "x",
                                      "NFX-292.md": "### Sync\n- Bullet.\n"}), [])

    def test_check_names_a_fragment_it_cannot_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp)
            with open(os.path.join(tmp, "changelog.d", "NFX-292.md"), "wb") as f:
                f.write("### Sync\n- Bullet.\n".encode("utf-16"))
            os.makedirs(os.path.join(tmp, "changelog.d", "NFX-293.md"))
            errors = cf.check(tmp)
            self.assertEqual(len(errors), 2, errors)
            self.assertTrue(all("cannot read" in e for e in errors), errors)
            self.assertIn("changelog.d/NFX-292.md", errors[0])

    def test_check_reads_a_fragment_saved_with_a_byte_order_mark(self):
        self.assertEqual(self.errors({"NFX-292.md": "﻿### Sync\r\n- Bullet.\r\n"}), [])

    def test_check_names_a_bad_file_name(self):
        errors = self.errors({"notes.md": "### Sync\n- Bullet.\n", "NFX-292.txt": "### Sync\n- Bullet.\n"})
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("file name" in e for e in errors), errors)

    def test_check_release_fails_while_a_fragment_is_left(self):
        errors = self.errors({"NFX-292.md": "### Sync\n- Bullet.\n"}, release=True)
        self.assertEqual(len(errors), 1)
        self.assertIn("changelog.d/NFX-292.md", errors[0])
        self.assertIn("fold", errors[0])

    def test_check_passes_a_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "CHANGELOG.md"), CHANGELOG)
            self.assertEqual(cf.check(tmp, release=True), [])

    def test_check_cli_exits_non_zero_and_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp, fragments={"NFX-292.md": "### Nope\n- Bullet.\n"})
            r = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "scripts", "changelog_fragments.py"), "check", "--root", tmp],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("changelog.d/NFX-292.md", r.stdout + r.stderr)

    def test_check_passes_on_this_repo(self):
        self.assertEqual(cf.check(ROOT), [])


class Guard(unittest.TestCase):
    def test_guard_fails_a_bullet_added_to_the_unreleased_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            text = CHANGELOG.replace("### Sync\n", "### Sync\n- Sneaked in (NFX-299).\n")
            write(os.path.join(tmp, "CHANGELOG.md"), text)
            ok, message = cf.guard(tmp, base)
            self.assertFalse(ok)
            self.assertIn("changelog.d/<KEY>.md", message)
            self.assertIn("Sneaked in", message)

    def test_guard_fails_the_same_bullet_once_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            write(os.path.join(tmp, "CHANGELOG.md"), CHANGELOG.replace("### Sync\n", "### Sync\n- Sneaked in (NFX-299).\n"))
            commit_all(tmp, "edit")
            self.assertFalse(cf.guard(tmp, base)[0])

    def test_guard_cli_takes_the_base_as_an_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            write(os.path.join(tmp, "CHANGELOG.md"), CHANGELOG.replace("### Sync\n", "### Sync\n- Sneaked in (NFX-299).\n"))
            script = os.path.join(ROOT, "scripts", "changelog_fragments.py")
            for args in ([base], ["--base", base]):
                r = subprocess.run([sys.executable, "-B", script, "guard", *args, "--root", tmp], capture_output=True, text=True)
                self.assertEqual(r.returncode, 1, args)
                self.assertIn("changelog.d/<KEY>.md", r.stdout)

    def test_guard_fails_a_changed_bullet_in_the_unreleased_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            write(os.path.join(tmp, "CHANGELOG.md"), CHANGELOG.replace("Rules bullet", "Rules bullet, reworded"))
            self.assertFalse(cf.guard(tmp, base)[0])

    def test_guard_passes_a_fragment_and_an_edit_to_a_released_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            write(os.path.join(tmp, "changelog.d", "NFX-299.md"), "### Sync\n- Bullet (NFX-299).\n")
            write(os.path.join(tmp, "CHANGELOG.md"), CHANGELOG.replace("Assets bullet", "Assets bullet, typo fixed"))
            ok, message = cf.guard(tmp, base)
            self.assertTrue(ok, message)

    def test_guard_skips_the_release_pr_that_dates_the_heading(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            write(os.path.join(tmp, "changelog.d", "NFX-299.md"), "### Sync\n- Bullet (NFX-299).\n")
            commit_all(tmp, "fragment")
            cf.fold(tmp, "1.4.0", "2026-11-27")
            ok, message = cf.guard(tmp, base)
            self.assertTrue(ok, message)
            self.assertIn("release", message)

    def test_guard_fails_a_new_unreleased_block_after_a_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            cf.fold(tmp, "1.4.0", "2026-11-27")
            commit_all(tmp, "release")
            base = git(tmp, "rev-parse", "HEAD").stdout.strip()
            text = read(os.path.join(tmp, "CHANGELOG.md")).replace("# Changelog\n\n", "# Changelog\n\n## Unreleased\n\n### Sync\n- New (NFX-299).\n\n")
            write(os.path.join(tmp, "CHANGELOG.md"), text)
            self.assertFalse(cf.guard(tmp, base)[0])

    def test_guard_fails_a_bullet_under_the_newest_release_once_unreleased_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            cf.fold(tmp, "1.4.0", "2026-11-27")
            commit_all(tmp, "release")
            base = git(tmp, "rev-parse", "HEAD").stdout.strip()
            text = read(os.path.join(tmp, "CHANGELOG.md")).replace("### Sync\n", "### Sync\n- Old way (NFX-299).\n", 1)
            write(os.path.join(tmp, "CHANGELOG.md"), text)
            ok, message = cf.guard(tmp, base)
            self.assertFalse(ok)
            self.assertIn("## 1.4.0 (2026-11-27)", message)

    def test_guard_passes_the_runbook_step_4_hand_fold_of_a_late_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            cf.fold(tmp, "1.4.0", "2026-11-27")
            write(os.path.join(tmp, "changelog.d", "NFX-299.md"), "### Sync\n- Late (NFX-299).\n")
            commit_all(tmp, "release merged with a late fragment")
            base = git(tmp, "rev-parse", "HEAD").stdout.strip()
            path = os.path.join(tmp, "CHANGELOG.md")
            write(path, read(path).replace("### Sync\n", "### Sync\n- Late (NFX-299).\n", 1))
            os.remove(os.path.join(tmp, "changelog.d", "NFX-299.md"))
            ok, message = cf.guard(tmp, base)
            self.assertTrue(ok, message)
            write(path, read(path).replace("- Late (NFX-299).\n", "- Late (NFX-299).\n- Extra.\n", 1))
            self.assertFalse(cf.guard(tmp, base)[0])

    def test_guard_passes_a_hand_fold_that_adds_the_late_fragments_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            cf.fold(tmp, "1.4.0", "2026-11-27")
            write(os.path.join(tmp, "changelog.d", "NFX-299.md"), "﻿### Tickets\n- Late (NFX-299).\n")
            commit_all(tmp, "release merged with a late fragment")
            base = git(tmp, "rev-parse", "HEAD").stdout.strip()
            path = os.path.join(tmp, "CHANGELOG.md")
            write(path, read(path).replace("\n## 1.3.2", "\n### Tickets\n- Late (NFX-299).\n\n## 1.3.2", 1))
            os.remove(os.path.join(tmp, "changelog.d", "NFX-299.md"))
            ok, message = cf.guard(tmp, base)
            self.assertTrue(ok, message)

    def test_guard_skips_the_second_release_after_a_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            cf.fold(tmp, "1.4.0", "2026-11-27")
            write(os.path.join(tmp, "changelog.d", "NFX-299.md"), "### Sync\n- Bullet (NFX-299).\n")
            commit_all(tmp, "release and a fragment")
            base = git(tmp, "rev-parse", "HEAD").stdout.strip()
            cf.fold(tmp, "1.5.0", "2026-12-15")
            ok, message = cf.guard(tmp, base)
            self.assertTrue(ok, message)

    def test_guard_fails_a_release_pr_that_leaves_a_fragment(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            cf.fold(tmp, "1.4.0", "2026-11-27")
            write(os.path.join(tmp, "changelog.d", "NFX-299.md"), "### Sync\n- Late (NFX-299).\n")
            ok, message = cf.guard(tmp, base)
            self.assertFalse(ok)
            self.assertIn("changelog.d/NFX-299.md", message)

    def test_guard_does_not_take_a_reworded_unreleased_heading_for_a_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = init_repo(tmp)
            text = CHANGELOG.replace("## Unreleased\n", "## Unreleased \n").replace("### Sync\n", "### Sync\n- Sneaked in.\n")
            write(os.path.join(tmp, "CHANGELOG.md"), text)
            self.assertFalse(cf.guard(tmp, base)[0])

    def test_guard_skips_with_a_note_when_no_base_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            ok, message = cf.guard(tmp)
            self.assertTrue(ok)
            self.assertIn("skipped", message)

    def test_guard_skips_outside_a_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_root(tmp)
            ok, message = cf.guard(tmp, "HEAD")
            self.assertTrue(ok)
            self.assertIn("skipped", message)

    def test_guard_finds_the_merge_base_with_origin_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = os.path.join(tmp, "origin")
            os.makedirs(origin)
            init_repo(origin)
            clone = os.path.join(tmp, "clone")
            git(tmp, "clone", "-q", origin, clone)
            git(clone, "checkout", "-q", "-b", "cesar/NFX-299")
            write(os.path.join(clone, "CHANGELOG.md"), CHANGELOG.replace("### Sync\n", "### Sync\n- Sneaked in (NFX-299).\n"))
            commit_all(clone, "edit")
            ok, message = cf.guard(clone)
            self.assertFalse(ok, message)

    def test_guard_passes_on_this_branch(self):
        ok, message = cf.guard(ROOT)
        self.assertTrue(ok, message)


class Replay(unittest.TestCase):
    """#191 (NFX-271) merged main twice only to fix CHANGELOG conflicts with #190 (NFX-223) and #192 (NFX-267)."""

    def branch(self, repo, name, files):
        git(repo, "checkout", "-q", "-b", name, "main")
        for path, text in files.items():
            write(os.path.join(repo, path), text)
        commit_all(repo, name)
        git(repo, "checkout", "-q", "main")

    def merge_all(self, repo, names):
        return [git(repo, "merge", "--no-edit", "-q", name, check=False).returncode for name in names]

    def test_replay_191_fragments_merge_cleanly_in_either_order(self):
        tickets = ["NFX-271", "NFX-223", "NFX-267"]
        for order in (tickets, list(reversed(tickets))):
            with tempfile.TemporaryDirectory() as tmp:
                init_repo(tmp)
                for key in tickets:
                    self.branch(tmp, "cesar/" + key, {"changelog.d/%s.md" % key: "### Review gate\n- Entry (%s).\n" % key})
                self.assertEqual(self.merge_all(tmp, ["cesar/" + k for k in order]), [0, 0, 0])
                cf.fold(tmp, "1.4.0", "2026-11-27")
                text = read(os.path.join(tmp, "CHANGELOG.md"))
                self.assertIn("## 1.4.0 (2026-11-27)\n\n### Sync\n- Sync bullet (NFX-235).\n\n### Review gate\n"
                              "- Entry (NFX-271).\n- Entry (NFX-267).\n- Entry (NFX-223).\n- Gate bullet two (NFX-271).\n", text)
                self.assertEqual(os.listdir(os.path.join(tmp, "changelog.d")), ["README.md"])

    def test_replay_191_control_the_old_way_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            init_repo(tmp)
            for key in ("NFX-271", "NFX-223"):
                text = CHANGELOG.replace("### Review gate\n", "### Review gate\n- Entry (%s).\n" % key, 1)
                self.branch(tmp, "cesar/" + key, {"CHANGELOG.md": text})
            codes = self.merge_all(tmp, ["cesar/NFX-271", "cesar/NFX-223"])
            self.assertEqual(codes[0], 0)
            self.assertNotEqual(codes[1], 0)


class Docs(unittest.TestCase):
    def doc(self, *parts):
        return read(os.path.join(ROOT, *parts))

    def test_docs_contributing_asks_for_a_fragment(self):
        text = self.doc("CONTRIBUTING.md")
        self.assertIn("changelog.d/<KEY>.md", text)
        self.assertNotIn("an entry in `CHANGELOG.md`", text)

    def test_docs_runbook_release_folds_and_checks(self):
        text = self.doc("docs", "RUNBOOK.md")
        self.assertIn("python3 -B scripts/changelog_fragments.py fold --version", text)
        self.assertIn("python3 -B scripts/changelog_fragments.py check --release", text)
        self.assertIn('git ls-tree --name-only "$SHA" changelog.d/', text)
        self.assertNotIn("(unreleased)`", text)

    def test_docs_resync_rule_reads_fragments_too(self):
        for parts in (("docs", "sync.md"), ("claude", "skills", "project-setup", "SKILL.md")):
            self.assertIn("`changelog.d/` fragment", self.doc(*parts), parts)

    def test_docs_fragment_folder_explains_the_format(self):
        text = self.doc("changelog.d", "README.md")
        self.assertIn("<KEY>-2.md", text)
        self.assertIn("scripts/changelog_fragments.py", text)


if __name__ == "__main__":
    unittest.main()
