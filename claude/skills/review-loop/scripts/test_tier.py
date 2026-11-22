#!/usr/bin/env python3
"""Tests for tier.py (NFX-62), the script that picks the review tier.
Run: python3 -B claude/skills/review-loop/scripts/test_tier.py

Seams: classify() (pure), load_project_risk_paths() (default-branch rule), and the CLI. Git tests use
throwaway repos under a temp dir and never touch the real repo or HOME.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
                     "claude/hooks/git_refs.py",
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
                     "web/src/App.tsx", "web/src/App.vue", "web/styles/main.css", "public/index.html",
                     "lib/components/card.dart", "app/src/main/java/x/components/Card.kt",
                     "Sources/Components/Card.swift", "app/src/main/java/x/ui/theme/Color.kt"):
            r = tier.classify([code(1, path)])
            self.assertTrue(r["ui"], path)
            self.assertGreaterEqual(r["tier"], 1, path)

    def test_viewmodel_under_ui_is_not_ui(self):
        r = tier.classify([f("composeApp/src/commonMain/kotlin/x/ui/ask/AskViewModel.kt",
                             text=["class AskViewModel : ViewModel() {", "    val state = 1", "}"])])
        self.assertFalse(r["ui"])
        self.assertEqual(r["tier"], 0)
        self.assertFalse(any("UI change" in x for x in r["reasons"]), r["reasons"])

    def test_composable_added_under_ui_is_ui(self):
        r = tier.classify([f("x/ui/ask/AskContent.kt", text=["@Composable", "fun AskContent() {}"])])
        self.assertTrue(r["ui"])
        self.assertGreaterEqual(r["tier"], 1)
        self.assertFalse(tier.classify([f("x/ui/ask/AskState.kt", text=["val a = 1"])])["ui"])

    def test_composable_body_edit_is_ui_by_the_whole_file(self):
        x = f("x/ui/ask/AskContent.kt", text=["    Modifier.padding(16.dp)"], removed_text=["    Modifier.padding(8.dp)"])
        self.assertFalse(tier.classify([dict(x)])["ui"])  # the changed lines alone cannot tell
        x["content"] = ["@Composable", "fun AskContent() {", "    Modifier.padding(16.dp)", "}"]
        self.assertTrue(tier.classify([x])["ui"])

    def test_viewmodel_file_content_without_composable_is_not_ui(self):
        x = f("x/ui/ask/AskViewModel.kt", text=["    val state = 2"])
        x["content"] = ["class AskViewModel : ViewModel() {", "    val state = 2", "}"]
        self.assertFalse(tier.classify([x])["ui"])

    def test_qualified_composable_annotation_is_ui(self):
        r = tier.classify([f("x/ui/ask/AskContent.kt", text=["@androidx.compose.runtime.Composable"])])
        self.assertTrue(r["ui"])

    def test_composable_removed_under_ui_is_ui(self):
        r = tier.classify([f("x/ui/ask/AskContent.kt", text=["val a = 1"], removed_text=["@Composable"])])
        self.assertTrue(r["ui"])

    def test_kotlin_ui_folder_with_unknown_lines_fails_closed(self):
        path = "x/ui/ask/AskViewModel.kt"
        r = tier.classify([f(path, text=[], removed_text=[])])
        self.assertTrue(r["ui"])
        self.assertTrue(tier.is_ui(path))

    def test_kotlin_ui_ending_needs_no_composable(self):
        self.assertTrue(tier.classify([f("x/ui/HomeScreen.kt", text=["val a = 1"])])["ui"])

    def test_non_ui_paths(self):
        for path in ("src/app.py", "server/handler.go", "app/data/Repo.kt", "lib/models/user.dart", "README.md",
                     "web/src/components/card.ts", "web/src/components/card.js"):
            self.assertFalse(tier.classify([code(1, path)])["ui"], path)

    def test_ui_lifts_a_tiny_change_to_tier_1_and_says_so(self):
        r = tier.classify([code(3, "web/src/App.tsx")])
        self.assertEqual(r["tier"], 1)
        self.assertTrue(any(x.startswith("Tier 1: UI change") and "web/src/App.tsx" in x for x in r["reasons"]),
                        r["reasons"])

    def test_ui_does_not_lower_a_higher_tier(self):
        self.assertEqual(tier.classify([code(200, "web/src/App.tsx")])["tier"], 2)


CAP_MESSAGE = "cap change: needs Cesar's yes; consider moving text to references/"
SYNC = "scripts/nousfx_sync.py"
SPLIT_TEST = "scripts/test_review_loop_split.py"
# The exact lines the three cap raises of wave 2026-11-19 changed (NFX-200).
PR148 = ("CLAUDE_RULES_BUDGET = 3846  # 50 bytes over the rules at NFX-186 (wave pointer); 3775 at NFX-158, 3339 at "
         "NFX-133",
         "CLAUDE_RULES_BUDGET = 3913  # 50 bytes over the rules at NFX-191 (decision-log pointer); 3846 at NFX-186, "
         "3775 at NFX-158")
PR149 = ("MAX_BYTES = 16100  # ANI-194: was 14900 (NFX-124: was 14500); room for the Open the PR step, inline so every "
         "project copy has it",
         "MAX_BYTES = 16200  # NFX-190: was 16100; ANI-194: was 14900 (NFX-124: was 14500); room for the Open the PR "
         "step, inline so every project copy has it")
PR150 = (PR149[1],
         "MAX_BYTES = 16300  # NFX-192: was 16200 (Cesar chose raising over trimming); NFX-190: was 16100; ANI-194: was "
         "14900 (NFX-124: was 14500); room for the Open the PR step, inline so every project copy has it")


class CapGate(unittest.TestCase):
    """NFX-200: a diff that changes a byte cap reports a human gate; a diff that only reads one does not."""

    def gates(self, files):
        return tier.classify(files)["gates"]

    def assert_gate(self, files, name, path):
        r = tier.classify(files)
        self.assertEqual(len(r["gates"]), 1, r["gates"])
        self.assertIn(CAP_MESSAGE, r["gates"][0])
        self.assertIn(name, r["gates"][0])
        self.assertIn(path, r["gates"][0])
        self.assertIn("Gate: " + r["gates"][0], r["reasons"])  # review-loop prints the reasons

    def test_pr148_rules_budget_raise(self):
        self.assert_gate([f(SYNC, text=[PR148[1]], removed_text=[PR148[0]])], "CLAUDE_RULES_BUDGET", SYNC)

    def test_pr149_max_bytes_raise(self):
        self.assert_gate([f(SPLIT_TEST, text=[PR149[1]], removed_text=[PR149[0]])], "MAX_BYTES", SPLIT_TEST)

    def test_pr150_max_bytes_raise(self):
        self.assert_gate([f(SPLIT_TEST, text=[PR150[1]], removed_text=[PR150[0]])], "MAX_BYTES", SPLIT_TEST)

    def test_rules_budget_change(self):
        self.assert_gate([f(SYNC, text=["RULES_BUDGET = 6500  # x"], removed_text=["RULES_BUDGET = 6400  # x"])],
                         "RULES_BUDGET", SYNC)

    def test_a_lowered_or_deleted_cap_is_a_change_too(self):
        self.assert_gate([f(SYNC, text=["RULES_BUDGET = 6000"], removed_text=["RULES_BUDGET = 6400"])],
                         "RULES_BUDGET", SYNC)
        self.assert_gate([f(SPLIT_TEST, text=[], removed_text=["MAX_BYTES = 16300"])], "MAX_BYTES", SPLIT_TEST)

    def test_augmented_assignment_is_a_change(self):
        self.assert_gate([f(SPLIT_TEST, text=["MAX_BYTES += 100"], removed_text=[])], "MAX_BYTES", SPLIT_TEST)
        self.assert_gate([f(SYNC, text=["    RULES_BUDGET -= 1"], removed_text=[])], "RULES_BUDGET", SYNC)

    def test_a_new_test_file_with_a_cap_is_a_change(self):
        self.assert_gate([f("scripts/test_new.py", text=["MAX_BYTES = 9000"], removed_text=[])], "MAX_BYTES",
                         "scripts/test_new.py")

    def test_reads_of_a_cap_report_no_gate(self):
        files = [f(SPLIT_TEST, text=['        self.assertLess(os.path.getsize(p), MAX_BYTES)']),
                 f("scripts/test_nousfx_sync.py", text=['        self.assertEqual(b, nousfx_sync.CLAUDE_RULES_BUDGET)']),
                 f("scripts/test_rules_single_copy.py", text=["CLAUDE_RULES_BUDGET = nousfx_sync.CLAUDE_RULES_BUDGET"]),
                 f(SYNC, text=["    if size > RULES_BUDGET:", "    budget = CLAUDE_RULES_BUDGET",
                               "    ok = RULES_BUDGET == size"])]
        r = tier.classify(files)
        self.assertEqual(r["gates"], [])
        self.assertFalse(any(x.startswith("Gate:") for x in r["reasons"]), r["reasons"])

    def test_comment_only_edit_of_a_cap_line_is_no_gate(self):
        self.assertEqual(self.gates([f(SYNC, text=["RULES_BUDGET = 6400  # new note"],
                                       removed_text=["RULES_BUDGET = 6400  # old note"])]), [])

    def test_longer_names_and_other_files_do_not_match(self):
        files = [f(SYNC, text=["NOUSFX_RULES_BUDGET = 1", "RULES_BUDGET_X = 2", "MAX_BYTES = 3"]),
                 f(SPLIT_TEST, text=["MAX_BYTES_X = 1", "OTHER_MAX_BYTES = 2", "RULES_BUDGET = 3"]),
                 f("scripts/nousfx_agy.py", text=["MAX_BYTES = 25000"], removed_text=["MAX_BYTES = 24000"]),
                 f("docs/sync.md", text=["RULES_BUDGET = 7000"]),
                 f("other/nousfx_sync.py", text=["RULES_BUDGET = 7000"])]
        self.assertEqual(self.gates(files), [])

    def test_two_caps_in_one_diff_give_two_gates(self):
        files = [f(SYNC, text=[PR148[1], "RULES_BUDGET = 6500"], removed_text=[PR148[0], "RULES_BUDGET = 6400"]),
                 f(SPLIT_TEST, text=[PR150[1]], removed_text=[PR150[0]])]
        self.assertEqual(len(self.gates(files)), 3)

    def test_the_gate_does_not_change_the_tier(self):
        plain = tier.classify([f(SYNC, text=["X = 2"], removed_text=["X = 1"])])
        capped = tier.classify([f(SYNC, text=[PR148[1]], removed_text=[PR148[0]])])
        self.assertEqual(plain["tier"], capped["tier"])

    def test_cap_names_live_in_one_place(self):
        self.assertEqual(dict(tier.CAP_NAMES), {"CLAUDE_RULES_BUDGET": "scripts/nousfx_sync.py",
                                                "RULES_BUDGET": "scripts/nousfx_sync.py", "MAX_BYTES": "test"})

    def test_fallback_has_the_gates_key(self):
        self.assertEqual(tier.fallback("x", [])["gates"], [])


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


class SecuritySkills(unittest.TestCase):
    """NFX-124: which security skills the diff calls for."""

    def skills(self, files):
        return tier.classify(files)["security"]

    def test_plain_diff_calls_for_none(self):
        self.assertEqual(self.skills([code(30)]), [])

    def test_workflow_path_calls_for_the_auditor(self):
        self.assertEqual(self.skills([f(".github/workflows/ci.yml", added=3)]), ["agentic-actions-auditor"])

    def test_workflow_deleted_still_counts(self):
        self.assertEqual(self.skills([f(".github/workflows/ci.yml", removed=5)]), ["agentic-actions-auditor"])

    def test_other_github_paths_do_not(self):
        for path in (".github/CODEOWNERS", ".github/actions/setup/action.yml", ".github/workflows-notes.txt"):
            self.assertEqual(self.skills([f(path, added=2)]), [], path)

    def test_session_keyword_in_added_line_calls_for_sharp_edges(self):
        self.assertEqual(self.skills([f("src/app.py", text=["x = sessionless_mode()"])]), [])  # whole words only
        self.assertEqual(self.skills([f("src/app.py", text=["session = load(request)"])]), ["sharp-edges"])

    def test_keyword_in_path_or_removed_line(self):
        self.assertEqual(self.skills([f("src/jwt_utils.py", added=1)]), ["sharp-edges"])
        self.assertEqual(self.skills([f("src/app.py", added=1, removed_text=["verify_csrf(request)"])]), ["sharp-edges"])

    def test_payment_and_lock_words_are_tier_3_only(self):
        r = tier.classify([f("src/app.py", text=["charge the payment", "mutex.lock()"])])
        self.assertEqual(r["tier"], 3)
        self.assertEqual(r["security"], [])

    def test_docs_and_generated_files_are_skipped(self):
        self.assertEqual(self.skills([f("docs/auth.md", text=["the password flow"])]), [])
        self.assertEqual(self.skills([f("package-lock.json", text=["token"])]), [])

    def test_both_skills_in_a_fixed_order(self):
        files = [f("src/login.py", added=1), f(".github/workflows/ci.yml", added=1)]
        self.assertEqual(self.skills(files), ["agentic-actions-auditor", "sharp-edges"])

    def test_untracked_file_without_removed_lines_is_read(self):
        untracked = {"path": "src/login.py", "added": ["x = 1"], "removed": 0, "hunks": 1}  # as untracked_files() builds it
        self.assertEqual(self.skills([untracked]), ["sharp-edges"])


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

    def test_deleted_workflow_is_found_through_git(self):
        self.write(".github/workflows/ci.yml", "name: ci\n")
        self.commit("add workflow")
        self.set_origin("main")
        self.git("checkout", "-qb", "cesar/X-2")
        self.git("rm", "-q", ".github/workflows/ci.yml")
        self.commit("drop workflow")
        self.assertEqual(self.result()["security"], ["agentic-actions-auditor"])

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

    def test_composable_body_edit_is_ui_through_git(self):
        self.write("ui/ask/AskContent.kt", "@Composable\nfun AskContent() {\n    Modifier.padding(8.dp)\n}\n")
        self.commit("add composable")
        self.branch_with("ui/ask/AskContent.kt", "@Composable\nfun AskContent() {\n    Modifier.padding(16.dp)\n}\n")
        self.assertTrue(self.result()["ui"])

    def test_viewmodel_under_ui_folder_is_not_ui_through_git(self):
        self.branch_with("ui/ask/AskViewModel.kt", "class AskViewModel {\n    val a = 1\n}\n")
        self.assertFalse(self.result()["ui"])

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


CLAUDE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # claude/
GIT_REFS_SRC = os.path.join(CLAUDE_DIR, "hooks", "git_refs.py")


class BaseRef(GitCase):
    """--base-ref NAME: risk_paths and the merge base come from origin/NAME, never from origin/HEAD (NFX-108)."""
    TOML = '[review]\nrisk_paths = ["%s"]\n'

    def stack(self):
        """main trusts a/*; origin/feature (one commit ahead) trusts core/*; HEAD is a commit on top of feature."""
        self.write(".nousfx/project.toml", self.TOML % "a/*")
        self.commit("toml")
        self.set_origin("main")
        self.git("checkout", "-qb", "feature")
        self.write(".nousfx/project.toml", self.TOML % "core/*")
        self.commit("feature toml")
        self.set_origin("feature")
        self.feature_sha = self.git_out("rev-parse", "HEAD")
        self.git("checkout", "-qb", "cesar/X-1")
        self.write("core/x.py", "x = 1\n")
        self.commit("change")

    def git_out(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, text=True).stdout.strip()

    def test_risk_paths_come_from_the_named_branch(self):
        self.stack()
        out = self.result("--base-ref", "feature")
        self.assertEqual(out["tier"], 3, out["reasons"])
        self.assertTrue(any("core/x.py" in r for r in out["reasons"]), out["reasons"])

    def test_without_it_origin_head_or_main_is_used(self):
        self.stack()
        out = self.result("--base", self.feature_sha)
        self.assertLess(out["tier"], 3, out["reasons"])

    def test_it_also_picks_the_merge_base(self):
        self.stack()
        out = self.result("--base-ref", "feature")
        self.assertEqual(out["stats"]["base"], self.feature_sha)
        self.assertEqual(out["stats"]["files"], 1)

    def test_a_missing_branch_is_tier_2_naming_it_and_does_not_fall_back(self):
        self.stack()
        out = self.result("--base-ref", "nope", "--base", self.feature_sha)
        self.assertEqual(out["tier"], 2, out["reasons"])
        self.assertTrue(any("origin/nope" in r for r in out["reasons"]), out["reasons"])

    def test_a_bad_name_is_tier_2(self):
        self.stack()
        out = self.result("--base-ref=-x", "--base", self.feature_sha)
        self.assertEqual(out["tier"], 2, out["reasons"])

    def test_a_missing_branch_without_base_is_tier_2_too(self):
        self.stack()
        out = self.result("--base-ref", "nope")
        self.assertEqual(out["tier"], 2, out["reasons"])
        self.assertTrue(any("origin/nope" in r for r in out["reasons"]), out["reasons"])


class GitRefsLoader(GitCase):
    """tier.py finds git_refs.py next to itself, else under ../../../hooks; missing is a Tier 2 reason."""

    def run_copy(self, script):
        self.set_origin("main")
        self.git("checkout", "-qb", "cesar/X-1")
        self.write("src/app.py", "x = 1\n")
        self.commit("change")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        r = subprocess.run([sys.executable, "-B", script, "--cwd", self.repo], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def place(self, rel_dir):
        d = os.path.join(self.tmp, *rel_dir.split("/"))
        os.makedirs(d, exist_ok=True)
        return d

    def test_alone_it_is_tier_2_and_names_git_refs(self):
        d = self.place("alone")
        shutil.copy(TIER, d)
        out = self.run_copy(os.path.join(d, "tier.py"))
        self.assertEqual(out["tier"], 2)
        self.assertTrue(any("git_refs" in r for r in out["reasons"]), out["reasons"])

    def test_flat_folder_loads(self):
        d = self.place("flat")
        shutil.copy(TIER, d)
        shutil.copy(GIT_REFS_SRC, d)
        out = self.run_copy(os.path.join(d, "tier.py"))
        self.assertFalse(any("git_refs" in r for r in out["reasons"]), out["reasons"])

    def test_repo_layout_loads(self):
        d = self.place("claude/skills/review-loop/scripts")
        shutil.copy(TIER, d)
        shutil.copy(GIT_REFS_SRC, self.place("claude/hooks"))
        out = self.run_copy(os.path.join(d, "tier.py"))
        self.assertFalse(any("git_refs" in r for r in out["reasons"]), out["reasons"])

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_symlinked_skill_loads_through_the_real_path(self):
        d = self.place("claude/skills/review-loop/scripts")
        shutil.copy(TIER, d)
        shutil.copy(GIT_REFS_SRC, self.place("claude/hooks"))
        link = self.place("home/skills")
        os.symlink(os.path.join(d, "tier.py"), os.path.join(link, "tier.py"))
        out = self.run_copy(os.path.join(link, "tier.py"))
        self.assertFalse(any("git_refs" in r for r in out["reasons"]), out["reasons"])


class CapGateCli(GitCase):
    """NFX-200 through git: the #148 raise on a real diff prints the gate in the JSON."""

    def test_rules_budget_raise_in_a_repo(self):
        self.write(SYNC, "import os\n" + PR148[0] + "\n")
        self.commit("cap")
        base = self.git("rev-parse", "HEAD")
        self.write(SYNC, "import os\n" + PR148[1] + "\n")
        out = self.result("--base", base)
        self.assertEqual(len(out["gates"]), 1, out)
        self.assertIn(CAP_MESSAGE, out["gates"][0])


class CommentLines(unittest.TestCase):
    """NFX-252: a keyword on a whole-line comment, added or removed, is not a Tier 3 keyword hit."""

    def tier_of(self, path, added=(), removed=()):
        return tier.classify([f(path, text=list(added), removed_text=list(removed))])

    def test_removed_banner_comment_is_not_a_keyword_hit(self):
        r = self.tier_of("plugins/via/scripts/via_prototype.py", removed=["# --- tokens ---"])
        self.assertEqual(r["tier"], 0, r["reasons"])

    def test_comment_lines_in_each_comment_style_are_skipped(self):
        for path, line in (("src/a.py", "    # check the secret"), ("run.sh", "# the token goes here"),
                           ("src/A.kt", "// the auth flow"), ("src/a.ts", "  /* login first */"),
                           ("src/a.ts", "   * @param token the user's token"), ("src/A.java", " */ "),
                           ("src/a.go", "// password rules"), ("conf/a.yml", "# secrets live in the vault")):
            r = self.tier_of(path, added=[line], removed=[line])
            self.assertFalse([x for x in r["reasons"] if "keyword" in x], (path, line, r["reasons"]))

    def test_code_on_the_line_still_counts(self):
        for path, line in (("src/a.py", "x = token  # the token"), ("src/a.ts", "/* x */ login()"),
                           ("src/a.c", "#define SECRET 1"), ("src/a.c", "*p = secret;"),
                           ("src/a.go", "*p = token"), ("src/a.py", "*args, token = x"),
                           ("src/a.ts", "/* a */ login(); /* b */"), ("src/A.kt", "*/ val token = 1"),
                           ("src/A.kt", "    *tokens,"), ("src/a.js", "  *[Symbol.iterator]() { yield token }")):
            self.assertEqual(self.tier_of(path, added=[line])["tier"], 3, (path, line))
            self.assertEqual(self.tier_of(path, removed=[line])["tier"], 3, (path, line))

    def test_security_skills_still_read_comment_lines(self):
        r = self.tier_of("src/a.py", removed=["# check the secret"])
        self.assertEqual(r["security"], ["sharp-edges"])


class CommentOnlyCap(GitCase):
    """NFX-252: when `comment_check.py verify BASE` proves only comments changed (and leaves no other changed file),
    the tier is capped at 1 and `ui` is false. Anything less than that proof leaves the tier as it was."""

    GATE_FILE = "claude/hooks/review-gate.py"
    UI_FILE = "app/src/main/java/x/ui/GameBoardScreen.kt"

    def setUp(self):
        super().setUp()
        self.write(self.GATE_FILE, "import os\n\n# Check the token here.\nLIMIT = 3\n")
        self.write(self.UI_FILE, "package x\n\n// Draws the board.\n@Composable\nfun Board() {\n    Text(\"a\")\n}\n")
        self.commit("base")
        self.base = self.git("rev-parse", "HEAD")

    def change_comment(self):
        self.write(self.GATE_FILE, "import os\n\n# Check it.\nLIMIT = 3\n")

    def change_code(self):
        self.write(self.GATE_FILE, "import os\n\n# Check it.\nLIMIT = 4\n")

    def assertCapped(self, out):
        self.assertEqual(out["tier"], 1, out["reasons"])
        self.assertEqual(out["reasons"][0], "Tier 1: comments only (verify OK)")
        self.assertFalse(out["ui"])

    def test_comment_only_gate_file_is_capped_at_tier_1(self):
        self.change_comment()
        out = self.result("--base", self.base)
        self.assertCapped(out)

    def test_capped_reasons_keep_what_was_found_without_a_tier_prefix(self):
        self.change_comment()
        out = self.result("--base", self.base)
        self.assertIn("was Tier 3: touches " + self.GATE_FILE, out["reasons"])
        self.assertEqual([r for r in out["reasons"] if r.startswith("Tier ")], ["Tier 1: comments only (verify OK)"])

    def test_one_code_token_changed_stays_tier_3(self):
        self.change_code()
        out = self.result("--base", self.base)
        self.assertEqual(out["tier"], 3)
        self.assertNotIn("Tier 1: comments only (verify OK)", out["reasons"])

    def test_comment_only_ui_file_is_not_ui(self):
        self.write(self.UI_FILE, "package x\n\n// Draws it.\n@Composable\nfun Board() {\n    Text(\"a\")\n}\n")
        out = self.result("--base", self.base)
        self.assertCapped(out)

    def test_code_change_in_a_ui_file_is_still_ui(self):
        self.write(self.UI_FILE, "package x\n\n// Draws the board.\n@Composable\nfun Board() {\n    Text(\"b\")\n}\n")
        self.assertTrue(self.result("--base", self.base)["ui"])

    def test_another_changed_file_verify_cannot_read_means_no_cap(self):
        self.change_comment()
        self.write("README.md", "hello again\n")
        out = self.result("--base", self.base)
        self.assertEqual(out["tier"], 3, out["reasons"])

    def test_deleted_docstring_of_a_file_that_reads_doc_means_no_cap(self):
        self.write(self.GATE_FILE, '"""Usage: gate."""\nimport argparse\n\nHELP = __doc__\n')
        self.commit("doc")
        base = self.git("rev-parse", "HEAD")
        self.write(self.GATE_FILE, "import argparse\n\nHELP = __doc__\n")
        self.assertEqual(self.result("--base", base)["tier"], 3)

    def test_deleted_file_means_no_cap(self):
        """NFX-252: comment_check verify neither flags nor counts a deleted file it cannot type (no extension, no shebang)."""
        self.write("Makefile", "all:\n\ttrue\n")
        self.commit("make")
        base = self.git("rev-parse", "HEAD")
        self.change_comment()
        self.git("rm", "-q", "Makefile")
        self.assertEqual(self.result("--base", base)["tier"], 3)

    def test_verify_timeout_leaves_the_gate_time_for_the_rest(self):
        with open(os.path.join(CLAUDE_DIR, "hooks", "review-gate.py"), encoding="utf-8") as fh:
            text = fh.read()
        for name in ("TIER_TIMEOUT", "DEFAULT_BUDGET"):
            budget = int(re.search(r"^%s = (\d+)" % name, text, re.M).group(1))
            self.assertLessEqual(tier.VERIFY_TIMEOUT * 2, budget, name)

    def test_a_diff_too_big_for_a_cleanup_is_not_verified(self):
        self.write("src/big.py", "".join("# c%d\nx%d = 1\n" % (i, i) for i in range(600)))
        self.commit("big")
        base = self.git("rev-parse", "HEAD")
        self.write("src/big.py", "".join("# d%d\nx%d = 1\n" % (i, i) for i in range(600)))
        with mock.patch.object(tier, "comments_only", side_effect=AssertionError("verify ran")):
            result = tier.run(self.repo, base, [])
        self.assertEqual(result["tier"], 3)

    def test_untracked_code_file_means_no_cap(self):
        self.change_comment()
        self.write("src/new.py", "x = 1\n")
        self.assertEqual(self.result("--base", self.base)["tier"], 3)

    def test_fable_review_label_keeps_tier_3_but_not_ui(self):
        self.write(self.UI_FILE, "package x\n\n// Draws it.\n@Composable\nfun Board() {\n    Text(\"a\")\n}\n")
        out = self.result("--base", self.base, "--labels", "fable-review")
        self.assertEqual(out["tier"], 3)
        self.assertFalse(out["ui"])

    def test_size_l_label_is_still_capped(self):
        self.change_comment()
        self.assertCapped(self.result("--base", self.base, "--labels", "size-L"))

    def test_a_small_comment_only_diff_keeps_its_tier(self):
        self.write("src/a.py", "# one\nx = 1\n")
        self.commit("a")
        base = self.git("rev-parse", "HEAD")
        self.write("src/a.py", "# two\nx = 1\n")
        out = self.result("--base", base)
        self.assertEqual(out["tier"], 0)
        self.assertNotIn("Tier 1: comments only (verify OK)", out["reasons"])

    def run_copy(self, comment_check=None):
        d = os.path.join(self.tmp, "copy")
        os.makedirs(d)
        shutil.copy(TIER, d)
        shutil.copy(GIT_REFS_SRC, d)
        if comment_check is not None:
            with open(os.path.join(d, "comment_check.py"), "w", encoding="utf-8") as fh:
                fh.write(comment_check)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        r = subprocess.run([sys.executable, "-B", os.path.join(d, "tier.py"), "--cwd", self.repo, "--base", self.base],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_missing_comment_check_means_no_cap(self):
        self.change_comment()
        self.assertEqual(self.run_copy()["tier"], 3)

    def test_crashing_comment_check_means_no_cap(self):
        self.change_comment()
        self.assertEqual(self.run_copy("raise SystemExit(1)\n")["tier"], 3)

    def test_exit_0_without_the_ok_line_means_no_cap(self):
        self.change_comment()
        self.assertEqual(self.run_copy("print('comment_check verify: 0 differences')\n")["tier"], 3)

    def test_ok_line_with_other_changed_files_means_no_cap(self):
        self.change_comment()
        fake = ("print('comment_check verify: OK, Python and shell files changed only comments (1 lines deleted); "
                "1 other changed file left to the suite')\n")
        self.assertEqual(self.run_copy(fake)["tier"], 3)

    def test_ok_line_not_last_means_no_cap(self):
        self.change_comment()
        fake = ("print('comment_check verify: OK, Python files changed only comments (1 lines deleted); "
                "0 other changed files left to the suite')\nprint('x.py: deleted')\n")
        self.assertEqual(self.run_copy(fake)["tier"], 3)

    def test_hanging_comment_check_means_no_cap(self):
        self.change_comment()
        fake = os.path.join(self.tmp, "slow.py")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("import time\ntime.sleep(5)\nprint('comment_check verify: OK, Python files changed only comments "
                     "(1 lines deleted); 0 other changed files left to the suite')\n")
        with mock.patch.object(tier, "comment_check_path", return_value=fake):
            self.assertTrue(tier.comments_only(self.repo, self.base, []))
            with mock.patch.object(tier, "VERIFY_TIMEOUT", 1):
                started = time.monotonic()
                self.assertFalse(tier.comments_only(self.repo, self.base, []))
                self.assertLess(time.monotonic() - started, 4)

    def test_ok_from_a_flat_copy_like_ci(self):
        self.change_comment()
        with open(os.path.join(HERE, "comment_check.py"), encoding="utf-8") as fh:
            out = self.run_copy(fh.read())
        self.assertCapped(out)


class SyncedExempt(GitCase):
    """NFX-261: with --synced, a project file byte-equal to its NousFX source (or to the text sync renders now, for
    .githooks/pre-push) adds no path, keyword, line or gate reason. Only the `synced: N files` reason names it."""

    NOUSFX = os.path.realpath(os.path.join(HERE, "..", "..", "..", ".."))
    GATE = ".claude/hooks/review-gate.py"
    HOOK = ".githooks/pre-push"

    def setUp(self):
        super().setUp()
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "cesar/PAP-70")

    def reviewed(self, source):
        """The NousFX file as tier.py compares it: from the default branch, not the working tree."""
        ref = tier.default_ref(self.NOUSFX)
        return tier.git(["show", "%s:%s" % (ref, source)], self.NOUSFX, raw=True).decode("utf-8")

    def copy(self, rel, source, edit=False):
        self.write(rel, self.reviewed(source) + ("# hand edit\n" if edit else ""))

    def rendered_hook(self):
        sys.path.insert(0, os.path.join(self.NOUSFX, "scripts"))
        try:
            import nousfx_project
        finally:
            sys.path.pop(0)
        return nousfx_project.render_hook(self.reviewed("claude/githooks/pre-push"))

    def synced_result(self):
        self.commit("sync")
        return self.result("--base", self.base, "--synced")

    def test_synced_gate_and_copies_get_tier_0_or_1_with_only_the_synced_reason(self):
        self.copy(self.GATE, "claude/hooks/review-gate.py")
        self.copy(".claude/hooks/git_refs.py", "claude/hooks/git_refs.py")
        self.copy(".claude/skills/review-loop/scripts/testfail.py", "claude/skills/review-loop/scripts/testfail.py")
        out = self.synced_result()
        self.assertLessEqual(out["tier"], 1, out["reasons"])
        self.assertEqual(len(out["reasons"]), 1, out["reasons"])
        self.assertTrue(out["reasons"][0].startswith("synced: 3 files"), out["reasons"])
        self.assertEqual(out["gates"], [])
        self.assertEqual(out["security"], [])

    def test_synced_reason_says_the_change_was_reviewed_in_nousfx(self):
        self.copy(self.GATE, "claude/hooks/review-gate.py")
        self.assertIn("reviewed in NousFX", self.synced_result()["reasons"][0])

    def test_synced_hand_edited_gate_keeps_tier_3(self):
        self.copy(self.GATE, "claude/hooks/review-gate.py", edit=True)
        out = self.synced_result()
        self.assertEqual(out["tier"], 3, out["reasons"])
        self.assertIn("Tier 3: touches " + self.GATE, out["reasons"])
        self.assertEqual(out["synced"], [])

    def test_synced_mix_keeps_every_rule_for_the_project_file(self):
        self.copy(self.GATE, "claude/hooks/review-gate.py")
        self.write("src/auth.py", "token = 1\n")
        out = self.synced_result()
        self.assertEqual(out["tier"], 3, out["reasons"])
        self.assertNotIn("Tier 3: touches " + self.GATE, out["reasons"])
        self.assertTrue(any("src/auth.py" in r for r in out["reasons"]), out["reasons"])

    def test_without_synced_flag_the_gate_copy_still_gets_tier_3(self):
        self.copy(self.GATE, "claude/hooks/review-gate.py")
        self.commit("sync")
        out = self.result("--base", self.base)
        self.assertEqual(out["tier"], 3, out["reasons"])
        self.assertIn("Tier 3: touches " + self.GATE, out["reasons"])

    def test_pre_push_rendered_by_sync_counts_as_synced(self):
        self.make_hook(executable=True)
        out = self.synced_result()
        self.assertEqual(out["synced"], [self.HOOK])
        self.assertLessEqual(out["tier"], 1, out["reasons"])
        self.assertEqual(len(out["reasons"]), 1, out["reasons"])

    def test_pre_push_hand_edited_is_counted_in_full(self):
        self.write(self.HOOK, self.rendered_hook() + "echo hand edit\n")
        out = self.synced_result()
        self.assertEqual(out["synced"], [])
        self.assertIn("Tier 3: touches " + self.HOOK, out["reasons"])

    def make_hook(self, executable):
        self.write(self.HOOK, self.rendered_hook())
        os.chmod(os.path.join(self.repo, *self.HOOK.split("/")), 0o755 if executable else 0o644)

    def test_pre_push_rendered_and_executable_counts_as_synced(self):
        self.make_hook(executable=True)
        self.assertEqual(self.synced_result()["synced"], [self.HOOK])

    @unittest.skipIf(os.name == "nt", "no executable bit on Windows")
    def test_pre_push_that_lost_its_executable_bit_is_counted_in_full(self):
        self.make_hook(executable=True)
        self.commit("hook")
        self.base = self.git("rev-parse", "HEAD")
        os.chmod(os.path.join(self.repo, *self.HOOK.split("/")), 0o644)
        out = self.synced_result()
        self.assertEqual(out["synced"], [])
        self.assertIn("Tier 3: touches " + self.HOOK, out["reasons"])

    @unittest.skipIf(os.name == "nt", "no executable bit on Windows")
    def test_pre_push_added_without_executable_bit_is_counted_in_full(self):
        self.make_hook(executable=False)
        out = self.synced_result()
        self.assertEqual(out["synced"], [])
        self.assertIn("Tier 3: touches " + self.HOOK, out["reasons"])

    @unittest.skipIf(os.name == "nt", "no executable bit on Windows")
    def test_synced_copy_keeps_its_own_mode_as_sync_does(self):
        self.copy(self.GATE, "claude/hooks/review-gate.py")
        os.chmod(os.path.join(self.repo, *self.GATE.split("/")), 0o755)
        self.assertEqual(self.synced_result()["synced"], [self.GATE])

    def test_synced_empty_diff_keeps_its_tier_0_reason(self):
        out = self.result("--base", self.base, "--synced")
        self.assertEqual(out["tier"], 0)
        self.assertTrue(out["reasons"] and out["reasons"][0].startswith("Tier 0: no code counted"), out["reasons"])

    def test_pre_push_source_copied_verbatim_is_not_synced(self):
        self.copy(self.HOOK, "claude/githooks/pre-push")
        out = self.synced_result()
        self.assertEqual(out["synced"], [])
        self.assertIn("Tier 3: touches " + self.HOOK, out["reasons"])


if __name__ == "__main__":
    unittest.main()
