#!/usr/bin/env python3
"""Tests for tier.py (NFX-62), the script that picks the review tier.
Run: python3 -B claude/skills/review-loop/scripts/test_tier.py

Seams: classify() (pure), load_project_risk_paths() (default-branch rule), and the CLI. Git tests use
throwaway repos under a temp dir and never touch the real repo or HOME.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True

import tier  # noqa: E402


def f(path, added=0, removed=0, hunks=1, text=None, removed_text=None):
    """A changed file. `text` overrides the added lines, `removed_text` gives the removed lines (lists of strings)."""
    lines = text if text is not None else ["x = 1"] * added
    gone = removed_text if removed_text is not None else ["x = 0"] * removed
    return {"path": path, "added": lines, "removed": len(gone), "removed_lines": gone, "hunks": hunks}


def code(n, path="src/app.py", hunks=1):
    return f(path, added=n, hunks=hunks)


class SizeBands(unittest.TestCase):
    def tier_of(self, files, **kw):
        return tier.classify(files, **kw)["tier"]

    def test_docs_only_is_tier_0_whatever_the_size(self):
        files = [f("README.md", added=300), f("docs/guide.md", added=100)]
        self.assertEqual(self.tier_of(files), 0)

    def test_text_file_extensions_count_as_docs(self):
        for path in ("notes.txt", "a.rst", "b.adoc", "docs/diagram.png.txt"):
            self.assertEqual(self.tier_of([f(path, added=50)]), 0, path)

    def test_20_code_lines_in_1_or_2_files_is_tier_0(self):
        self.assertEqual(self.tier_of([code(20)]), 0)
        self.assertEqual(self.tier_of([code(10, "a.py"), code(10, "b.py")]), 0)

    def test_21_code_lines_is_tier_1(self):
        self.assertEqual(self.tier_of([code(21)]), 1)

    def test_three_files_with_few_lines_is_tier_1(self):
        files = [code(2, "a.py"), code(2, "b.py"), code(2, "c.py")]
        self.assertEqual(self.tier_of(files), 1)

    def test_removed_lines_count_as_changed_lines(self):
        self.assertEqual(self.tier_of([f("a.py", added=10, removed=11)]), 1)

    def test_150_lines_and_5_files_is_tier_1(self):
        files = [code(30, "f%d.py" % i) for i in range(5)]
        self.assertEqual(self.tier_of(files), 1)

    def test_151_lines_is_tier_2(self):
        self.assertEqual(self.tier_of([code(151)]), 2)

    def test_6_files_is_tier_2(self):
        files = [code(2, "f%d.py" % i) for i in range(6)]
        self.assertEqual(self.tier_of(files), 2)

    def test_400_lines_and_15_files_is_still_tier_2(self):
        self.assertEqual(self.tier_of([code(400)]), 2)
        files = [code(2, "f%d.py" % i) for i in range(15)]
        self.assertEqual(self.tier_of(files), 2)

    def test_over_400_lines_is_tier_3(self):
        result = tier.classify([code(401)])
        self.assertEqual(result["tier"], 3)
        self.assertTrue(any("401" in r and "400" in r for r in result["reasons"]), result["reasons"])

    def test_over_15_files_is_tier_3(self):
        files = [code(1, "f%d.py" % i) for i in range(16)]
        result = tier.classify(files)
        self.assertEqual(result["tier"], 3)
        self.assertTrue(any("16 files" in r for r in result["reasons"]), result["reasons"])

    def test_generated_snapshot_and_screenshot_lines_are_not_counted(self):
        files = [code(10),
                 f("app/build/generated/Foo.kt", added=900),
                 f("src/api.g.dart", added=900),
                 f("tests/__snapshots__/a.snap", added=900),
                 f("docs/shot.png", added=900),
                 f("shots/home.webp", added=900)]
        result = tier.classify(files)
        self.assertEqual(result["stats"]["lines"], 10)
        self.assertEqual(result["stats"]["files"], 1)
        self.assertEqual(result["tier"], 0)  # 10 lines in 1 file

    def test_a_handwritten_build_folder_is_code_but_build_output_is_not(self):
        self.assertEqual(tier.classify([code(300, "src/build/Deploy.kt")])["stats"]["lines"], 300)
        for path in ("build/classes/A.class.kt", "app/build/generated/Foo.kt", "app/build/outputs/x.kt",
                     "app/build/intermediates/y.kt"):
            self.assertEqual(tier.classify([code(300, path)])["stats"]["lines"], 0, path)

    def test_code_in_a_snapshots_or_screenshots_folder_is_code(self):
        for path in ("src/snapshots/SnapshotService.kt", "src/screenshots/Capture.kt"):
            self.assertEqual(tier.classify([code(30, path)])["stats"]["lines"], 30, path)
        self.assertEqual(tier.classify([code(30, "src/snapshots/a.snap")])["stats"]["lines"], 0)
        self.assertEqual(tier.classify([code(30, "src/screenshots/a.png")])["stats"]["lines"], 0)

    def test_code_under_docs_is_code(self):
        r = tier.classify([f("docs/tool.py", text=["password = 1"] * 30)])
        self.assertEqual(r["stats"]["lines"], 30)
        self.assertEqual(r["tier"], 3)
        self.assertEqual(tier.classify([f("docs/guide.md", added=300)])["tier"], 0)
        self.assertEqual(tier.classify([f("docs/notes", added=300)])["tier"], 0)

    def test_only_ignored_files_get_an_honest_reason(self):
        r = tier.classify([f("app/build/generated/Foo.kt", added=50)])
        self.assertEqual(r["tier"], 0)
        self.assertNotIn("docs or text only", r["reasons"][0])

    def test_lock_file_lines_are_not_counted_but_the_file_is_a_risk_path(self):
        result = tier.classify([code(10), f("yarn.lock", added=900), f("poetry.lock", added=900)])
        self.assertEqual(result["stats"]["lines"], 10)
        self.assertEqual(result["tier"], 3)
        self.assertIn("Tier 3: touches yarn.lock", result["reasons"])

    def test_hunk_limits(self):
        self.assertEqual(self.tier_of([code(10, hunks=3)]), 0)
        self.assertEqual(self.tier_of([code(10, hunks=4)]), 1)
        self.assertEqual(self.tier_of([code(30, hunks=10)]), 1)
        self.assertEqual(self.tier_of([code(30, hunks=11)]), 2)

    def test_empty_diff_is_tier_0(self):
        self.assertEqual(self.tier_of([]), 0)


class Risk(unittest.TestCase):
    def one(self, path, text=("x = 1",), **kw):
        return tier.classify([f(path, text=list(text))], **kw)

    def test_risk_path_is_tier_3_and_named(self):
        for path in (".github/workflows/ci.yml", "claude/hooks/review-gate.py", "claude/hooks/pr_create_parser.py",
                     ".githooks/pre-push", "claude/githooks/pre-push", ".husky/pre-commit",
                     "app/build.gradle.kts", "gradle/libs.versions.toml", "package.json", "package-lock.json",
                     "pubspec.yaml", "ios/Podfile", "Package.swift", "pyproject.toml", "requirements-dev.txt",
                     "Cargo.toml", "go.mod", "app/src/main/AndroidManifest.xml", "db/migrations/001_init.py",
                     "db/create.sql", "app/schema.graphql"):
            r = self.one(path)
            self.assertEqual(r["tier"], 3, path)
            self.assertIn("Tier 3: touches " + path, r["reasons"], path)

    def test_keyword_in_a_folder_or_file_name_is_tier_3_and_named(self):
        for path, word in (("src/auth/session.py", "auth"), ("ui/LoginScreen.kt", "login"),
                           ("src/payment_service.py", "payment")):
            r = self.one(path)
            self.assertEqual(r["tier"], 3, path)
            self.assertIn('Tier 3: keyword "%s" in the path %s' % (word, path), r["reasons"])

    def test_risk_path_wins_over_tiny_size(self):
        self.assertEqual(self.one("claude/hooks/review-gate.py")["tier"], 3)

    def test_ignored_file_still_checked_for_risk(self):
        self.assertEqual(self.one("package-lock.json")["tier"], 3)

    def test_plain_path_is_not_risky(self):
        for path in ("src/author.py", "src/authority/x.py", "README.md", "src/oauthor.py", "src/tokenizer.py"):
            self.assertEqual(self.one(path)["tier"], 0, path)

    def test_keyword_on_added_code_line_is_tier_3_and_named(self):
        r = self.one("src/api.py", ["headers = {'Authorization': token}"])
        self.assertEqual(r["tier"], 3)
        self.assertIn('Tier 3: keyword "token" in src/api.py', r["reasons"])

    def test_author_does_not_trigger_auth(self):
        r = self.one("src/meta.py", ["author = 'Cesar'", "# authored by", "authority = 1"])
        self.assertEqual(r["tier"], 0)

    def test_keyword_inside_identifiers(self):
        for line in ("val authToken = 1", "def get_auth_header():", "class PaymentService", "x.encrypt(data)",
                     "AUTH_URL = 1", "use Mutex", "synchronized (lock) {"):
            self.assertEqual(self.one("src/x.py", [line])["tier"], 3, line)

    def test_keyword_on_a_removed_code_line_is_tier_3_and_says_removed(self):
        r = tier.classify([f("src/x.py", text=["x = 1"], removed_text=["    require_auth(request)"])])
        self.assertEqual(r["tier"], 3)
        self.assertIn('Tier 3: keyword "auth" removed in src/x.py', r["reasons"])
        self.assertEqual(tier.classify([f("src/x.py", removed_text=["author = 1"])])["tier"], 0)

    def test_keyword_in_doc_lines_does_not_count(self):
        doc = tier.classify([f("docs/auth.md", text=["How the token works"], removed_text=["the token"])])
        self.assertEqual(doc["tier"], 0)

    def test_project_risk_paths(self):
        r = self.one("app/billing_v2/x.py", project_paths=["app/billing_v2/*"])
        self.assertEqual(r["tier"], 3)
        self.assertIn("Tier 3: touches app/billing_v2/x.py", r["reasons"])
        self.assertEqual(self.one("app/other/x.py", project_paths=["app/billing_v2/*"])["tier"], 0)

    def test_project_folder_names_match_everything_below_them(self):
        for entry in ("core", "core/", "app/core"):
            r = self.one("app/core/engine.py" if entry == "app/core" else "core/engine.py", project_paths=[entry])
            self.assertEqual(r["tier"], 3, entry)
        self.assertEqual(self.one("corex/engine.py", project_paths=["core"])["tier"], 0)

    def test_globstar_entry_also_matches_at_the_repo_root(self):
        for path in ("secrets.py", "a/secrets.py", "a/b/secrets.py"):
            self.assertEqual(self.one(path, project_paths=["**/secrets.py"])["tier"], 3, path)
        self.assertEqual(self.one("a/other.py", project_paths=["**/secrets.py"])["tier"], 0)

    def test_build_files_written_as_text_are_risk_paths(self):
        for path in ("CMakeLists.txt", "native/CMakeLists.txt", "cmake/Find.cmake", "Makefile", "pom.xml"):
            self.assertEqual(self.one(path)["tier"], 3, path)

    def test_plural_and_past_tense_forms(self):
        for line in ("refreshTokens = []", "hashPasswords(x)", "encrypted = True", "decrypted = x",
                     "authenticated = True", "authorized = True"):
            self.assertEqual(self.one("src/x.py", [line])["tier"], 3, line)

    def test_project_risk_path_can_mark_a_doc_risky(self):
        self.assertEqual(self.one("docs/policy.md", project_paths=["docs/policy.md"])["tier"], 3)

    def test_windows_separators(self):
        self.assertEqual(self.one(".github\\workflows\\ci.yml")["tier"], 3)


class Labels(unittest.TestCase):
    def test_fable_review_label_is_tier_3(self):
        r = tier.classify([code(5)], labels=["fable-review"])
        self.assertEqual(r["tier"], 3)
        self.assertIn("Tier 3: label fable-review", r["reasons"])

    def test_size_l_raises_one_tier(self):
        self.assertEqual(tier.classify([code(5)], labels=["size-L"])["tier"], 1)
        self.assertEqual(tier.classify([code(30)], labels=["size-L"])["tier"], 2)
        r = tier.classify([code(151)], labels=["size-L"])
        self.assertEqual(r["tier"], 3)
        self.assertIn("+1 tier: label size-L", r["reasons"])

    def test_size_l_is_capped_at_3(self):
        self.assertEqual(tier.classify([code(500)], labels=["size-L"])["tier"], 3)

    def test_other_labels_change_nothing(self):
        self.assertEqual(tier.classify([code(5)], labels=["size-M", "env-none", "harness"])["tier"], 0)


class Ui(unittest.TestCase):
    def test_ui_paths_by_platform(self):
        for path in ("app/src/main/java/x/ui/HomeScreen.kt", "app/src/main/res/layout/a.xml",
                     "app/src/main/res/drawable/a.xml", "lib/widgets/card.dart", "lib/screens/home.dart",
                     "Sources/Views/Home.swift", "Sources/HomeView.swift", "Base.lproj/Main.storyboard",
                     "web/src/App.tsx", "web/src/App.vue", "web/styles/main.css", "public/index.html"):
            r = tier.classify([code(1, path)])
            self.assertTrue(r["ui"], path)
            self.assertGreaterEqual(r["tier"], 1, path)

    def test_non_ui_paths(self):
        for path in ("src/app.py", "server/handler.go", "app/data/Repo.kt", "lib/models/user.dart", "README.md"):
            self.assertFalse(tier.classify([code(1, path)])["ui"], path)

    def test_ui_lifts_a_tiny_change_to_tier_1_and_says_so(self):
        r = tier.classify([code(3, "web/src/App.tsx")])
        self.assertEqual(r["tier"], 1)
        self.assertTrue(any(x.startswith("Tier 1: UI change") and "web/src/App.tsx" in x for x in r["reasons"]),
                        r["reasons"])

    def test_ui_does_not_lower_a_higher_tier(self):
        self.assertEqual(tier.classify([code(200, "web/src/App.tsx")])["tier"], 2)


TIER = os.path.join(HERE, "tier.py")
HINT_PARTS = ("git remote set-head origin -a", "git fetch --unshallow")


class GitCase(unittest.TestCase):
    """A throwaway repo on branch main whose 'origin' refs are made by hand (no network, no real remote)."""

    def setUp(self):
        patcher = mock.patch.dict(os.environ)  # restores the environment after the test
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in [k for k in os.environ if k.startswith("GIT_")]:
            del os.environ[name]
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="tier-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.write("README.md", "hello\n")
        self.commit("init")

    def git(self, *args):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.repo,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def write(self, rel, text):
        path = os.path.join(self.repo, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def set_origin(self, branch="main", ref=None):
        """Make refs/remotes/origin/<branch> point at `ref` (default HEAD)."""
        self.git("update-ref", "refs/remotes/origin/" + branch, ref or "HEAD")

    def run_tier(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        return subprocess.run([sys.executable, "-B", TIER, "--cwd", cwd or self.repo, *args], capture_output=True,
                              text=True, env=env)

    def result(self, *args):
        r = self.run_tier(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)


class ProjectRiskPaths(GitCase):
    TOML = '[review]\nrisk_paths = ["%s"]\n'

    def put(self, text):
        self.write(".nousfx/project.toml", text)

    def load(self):
        return tier.load_project_risk_paths(self.repo)

    def test_origin_head_is_used_when_it_points_at_another_origin_ref(self):
        self.put(self.TOML % "trunk/*")
        self.commit("toml")
        self.set_origin("trunk")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.put(self.TOML % "working/*")
        self.assertEqual(self.load(), ["trunk/*"])

    def test_origin_main_is_the_fallback(self):
        self.put(self.TOML % "main/*")
        self.commit("toml")
        self.set_origin("main")
        self.put(self.TOML % "working/*")
        self.assertEqual(self.load(), ["main/*"])

    def test_origin_master_is_the_last_fallback(self):
        self.put(self.TOML % "master/*")
        self.commit("toml")
        self.set_origin("master")
        self.put(self.TOML % "working/*")
        self.assertEqual(self.load(), ["master/*"])

    def test_default_branch_file_wins_over_the_working_tree_and_the_branch(self):
        self.put(self.TOML % "base/*")
        self.commit("toml")
        self.set_origin("main")
        self.git("checkout", "-qb", "cesar/X-1")
        self.put(self.TOML % "branch/*")
        self.commit("branch edit")
        self.put(self.TOML % "working/*")
        self.assertEqual(self.load(), ["base/*"])

    def test_default_branch_file_without_review_table_means_no_project_paths(self):
        self.put('[check]\ncommand = "true"\n')
        self.commit("toml")
        self.set_origin("main")
        self.put(self.TOML % "working/*")
        self.assertEqual(self.load(), [])

    def test_working_tree_is_read_when_the_default_branch_has_no_file(self):
        self.set_origin("main")
        self.put(self.TOML % "working/*")
        self.assertEqual(self.load(), ["working/*"])

    def test_working_tree_is_read_when_there_is_no_default_branch(self):
        self.put(self.TOML % "working/*")
        self.assertEqual(self.load(), ["working/*"])

    def test_no_file_anywhere_means_no_project_paths(self):
        self.assertEqual(self.load(), [])

    def test_bad_value_is_an_error(self):
        for text in ('[review]\nrisk_paths = "src/*"\n', "[review]\nrisk_paths = [1]\n", "[review\n"):
            self.put(text)
            with self.assertRaises(Exception):
                self.load()


class Cli(GitCase):
    def branch_with(self, rel, text, name="cesar/X-1"):
        self.set_origin("main")
        self.git("checkout", "-qb", name)
        self.write(rel, text)
        self.commit("change")

    def test_no_merge_base_exits_2_with_the_hint_and_prints_no_json(self):
        r = self.run_tier()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, "")
        for part in HINT_PARTS:
            self.assertIn(part, r.stderr)

    def test_branch_diff_is_taken_from_the_merge_base(self):
        self.branch_with("src/app.py", "x = 1\n" * 30)
        out = self.result()
        self.assertEqual(out["tier"], 1)
        self.assertEqual(out["stats"]["lines"], 30)
        self.assertEqual(out["stats"]["files"], 1)
        self.assertEqual(out["stats"]["base"], self.git("rev-parse", "main"))
        self.assertFalse(out["ui"])

    def test_removed_security_line_is_found_through_git(self):
        self.write("src/app.py", "x = 1\nrequire_auth(request)\n")
        self.commit("with check")
        self.set_origin("main")
        self.git("checkout", "-qb", "cesar/X-1")
        self.write("src/app.py", "x = 1\n")
        self.commit("drop check")
        out = self.result()
        self.assertEqual(out["tier"], 3)
        self.assertIn('Tier 3: keyword "auth" removed in src/app.py', out["reasons"])

    def test_origin_head_is_tried_before_origin_main(self):
        self.set_origin("trunk")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
        self.git("checkout", "-qb", "cesar/X-1")
        self.write("src/app.py", "x = 1\n")
        self.commit("change")
        self.assertEqual(self.result()["stats"]["base"], self.git("rev-parse", "main"))

    def test_untracked_and_uncommitted_changes_are_counted(self):
        self.branch_with("src/a.py", "x = 1\n")
        self.write("src/b.py", "y = 2\n" * 40)
        self.write("src/a.py", "x = 1\nz = 3\n")
        out = self.result()
        self.assertEqual(out["stats"]["files"], 2)
        self.assertEqual(out["stats"]["lines"], 42)
        self.assertEqual(out["tier"], 1)

    def test_base_option_is_used(self):
        self.branch_with("src/app.py", "x = 1\n")
        first = self.git("rev-parse", "HEAD")
        self.write("src/more.py", "y = 2\n")
        self.commit("more")
        out = self.result("--base", first)
        self.assertEqual(out["stats"]["base"], first)
        self.assertEqual(out["stats"]["files"], 1)

    def test_deleted_lines_and_files_are_counted(self):
        self.write("src/old.py", "a\nb\nc\n")
        self.commit("old")
        self.set_origin("main")
        self.git("checkout", "-qb", "cesar/X-1")
        os.remove(os.path.join(self.repo, "src", "old.py"))
        self.commit("rm")
        out = self.result()
        self.assertEqual(out["stats"]["lines"], 3)

    def test_labels_are_read_from_the_option(self):
        self.branch_with("src/app.py", "x = 1\n")
        self.assertEqual(self.result("--labels", "fable-review,size-M")["tier"], 3)
        self.assertEqual(self.result("--labels", "size-L")["tier"], 1)

    def test_risk_keyword_on_an_added_line_is_found_through_git(self):
        self.branch_with("src/app.py", "headers['Authorization'] = token\n")
        out = self.result()
        self.assertEqual(out["tier"], 3)
        self.assertIn('Tier 3: keyword "token" in src/app.py', out["reasons"])

    def test_project_risk_paths_come_from_the_default_branch(self):
        self.write(".nousfx/project.toml", '[review]\nrisk_paths = ["core/*"]\n')
        self.commit("toml")
        self.branch_with("core/engine.py", "x = 1\n")
        self.assertEqual(self.result()["tier"], 3)

    def test_ui_flag_is_printed(self):
        self.branch_with("web/src/App.tsx", "export const A = 1\n")
        out = self.result()
        self.assertTrue(out["ui"])
        self.assertEqual(out["tier"], 1)

    def test_other_git_error_is_tier_2_with_the_reason(self):
        self.branch_with("src/app.py", "x = 1\n")
        out = self.result("--base", "not-a-commit")
        self.assertEqual(out["tier"], 2)
        self.assertTrue(out["reasons"][0].startswith("Tier 2:"), out["reasons"])

    def test_not_a_git_repo_is_tier_2_not_a_stop(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        r = self.run_tier(cwd=plain)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["tier"], 2)

    def test_error_results_have_the_same_keys_as_normal_results(self):
        normal = self.result("--base", "HEAD")
        error = self.result("--base", "not-a-commit")
        self.assertEqual(sorted(error), sorted(normal))
        self.assertEqual(sorted(error["stats"]), sorted(normal["stats"]))

    def test_untracked_lock_file_is_a_risk_path_but_is_not_read(self):
        self.set_origin("main")
        self.write("yarn.lock", "x\n" * 900)
        out = self.result()
        self.assertEqual(out["tier"], 3)
        self.assertEqual(out["stats"]["lines"], 0)

    def test_gitattributes_cannot_hide_the_lines(self):
        self.set_origin("main")
        self.git("checkout", "-qb", "cesar/X-1")
        self.write(".gitattributes", "* -diff\n")
        self.write("src/app.py", "password = token\n" * 30)
        self.commit("hide")
        out = self.result()
        self.assertEqual(out["stats"]["lines"], 31)  # 30 source lines plus the .gitattributes line
        self.assertIn('Tier 3: keyword "token" in src/app.py', out["reasons"])

    def test_diff_noprefix_config_cannot_mangle_paths(self):
        self.git("config", "diff.noprefix", "true")
        self.branch_with(".github/workflows/ci.yml", "on: push\n")
        self.assertIn("Tier 3: touches .github/workflows/ci.yml", self.result()["reasons"])

    def test_gitattributes_file_is_a_risk_path(self):
        self.branch_with(".gitattributes", "*.png binary\n")
        self.assertIn("Tier 3: touches .gitattributes", self.result()["reasons"])

    def test_error_never_lowers_a_tier_that_labels_raise(self):
        out = self.result("--base", "not-a-commit", "--labels", "fable-review")
        self.assertEqual(out["tier"], 3)

    def test_bad_review_table_is_tier_2_on_a_tiny_change(self):
        self.write(".nousfx/project.toml", '[review]\nrisk_paths = "core/*"\n')
        self.commit("toml")
        self.branch_with("src/app.py", "x = 1\n")
        out = self.result()
        self.assertEqual(out["tier"], 2)
        self.assertTrue(any("risk_paths" in r for r in out["reasons"]), out["reasons"])

    def test_bad_review_table_does_not_lower_a_risky_change(self):
        self.write(".nousfx/project.toml", '[review]\nrisk_paths = "core/*"\n')
        self.commit("toml")
        self.branch_with(".github/workflows/ci.yml", "on: push\n")
        self.assertEqual(self.result()["tier"], 3)


class GitFailures(GitCase):
    def test_default_ref_does_not_hide_a_timeout_as_no_default_branch(self):
        with mock.patch.object(tier, "git", side_effect=tier.GitUnavailable("timed out")):
            with self.assertRaises(tier.GitUnavailable):
                tier.default_ref(self.repo)

    def test_an_unexpected_error_in_classify_is_tier_2_not_a_traceback(self):
        self.set_origin("main")
        self.git("checkout", "-qb", "cesar/X-1")
        self.write("src/app.py", "x = 1\n")
        with mock.patch.object(tier, "classify", side_effect=ValueError("boom")):
            out = tier.run(self.repo, None, [])
        self.assertEqual(out["tier"], 2)
        self.assertIn("boom", out["reasons"][0])

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_untracked_symlink_still_goes_to_the_risk_check(self):
        self.set_origin("main")
        os.makedirs(os.path.join(self.repo, ".github"))
        os.symlink(os.path.join(self.repo, "README.md"), os.path.join(self.repo, ".github", "wf.yml"))
        self.assertIn("Tier 3: touches .github/wf.yml", tier.run(self.repo, None, [])["reasons"])

    def test_timeout_and_missing_program_become_git_unavailable(self):
        for err in (subprocess.TimeoutExpired("git", 1), FileNotFoundError("git")):
            with mock.patch.object(tier.subprocess, "run", side_effect=err):
                with self.assertRaises(tier.GitUnavailable):
                    tier.git(["status"], self.repo)

    def test_merge_base_does_not_hide_a_timeout_as_no_merge_base(self):
        with mock.patch.object(tier, "git", side_effect=tier.GitUnavailable("timed out")):
            with self.assertRaises(tier.GitUnavailable):
                tier.merge_base(self.repo)

    def test_run_answers_tier_2_when_git_is_unavailable_for_the_merge_base(self):
        with mock.patch.object(tier, "merge_base", side_effect=tier.GitUnavailable("timed out")):
            out = tier.run(self.repo, None, ["fable-review"])
        self.assertEqual(out["tier"], 3)
        self.assertTrue(out["reasons"][0].startswith("Tier 2:"), out["reasons"])
        with self.assertRaises(tier.NoMergeBase):  # no origin refs: a plain 'no merge base' still stops
            tier.run(self.repo, None, [])


class FindGit(unittest.TestCase):
    def test_only_absolute_path_entries_are_searched(self):
        real = shutil.which("git")
        self.assertTrue(real)
        found = tier.find_git(path=os.pathsep.join([".", "relative/bin", os.path.dirname(real)]))
        self.assertTrue(os.path.isabs(found))

    def test_the_current_folder_is_skipped_even_when_absolute(self):
        with tempfile.TemporaryDirectory() as folder:
            name = "git.exe" if os.name == "nt" else "git"
            fake = os.path.join(folder, name)
            with open(fake, "w") as fh:
                fh.write("#!/bin/sh\n")
            os.chmod(fake, 0o755)
            old = os.getcwd()
            os.chdir(folder)
            try:
                self.assertIsNone(tier.find_git(path=os.path.realpath(folder)))
            finally:
                os.chdir(old)

    def test_relative_entries_alone_find_nothing(self):
        self.assertIsNone(tier.find_git(path=os.pathsep.join([".", "relative/bin"])))


if __name__ == "__main__":
    unittest.main()
