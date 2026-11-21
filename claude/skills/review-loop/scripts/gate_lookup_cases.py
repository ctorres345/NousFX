"""Test helper (not shipped to projects): the gate lookup cases test_prove.py and test_testfail.py share (NFX-214).

prove.py and testfail.py load the review gate reader first: <home>/.claude/hooks, then the cloud copy in
<git-dir>/nousfx-gate/current/hooks, then, in a NousFX checkout only, the gate next to the skills folder. A project
copy (.claude/skills/review-loop/scripts) never loads its sibling .claude/hooks/review-gate.py, which the PR branch
can edit. The home comes from the user record, so the scripts run through nousfx_testhome's interpreter stand-in
with a temp home: the real ~/.claude is never read.
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))  # the checkout, also when run through a ~/.claude link
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "..", "plugins", "via", "scripts")))
import nousfx_testhome  # noqa: E402

EMPTY_HOME = tempfile.mkdtemp(prefix="nousfx-empty-home-")
atexit.register(shutil.rmtree, EMPTY_HOME, True)

# Runs SCRIPT's load_gate() in the cwd and prints what it got: the gate's MARK, or the refusal.
LOADER = """import importlib.util, sys
spec = importlib.util.spec_from_file_location("script_under_test", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
try:
    print("MARK " + m.load_gate().MARK)
except m.Refused as e:
    print("REFUSED " + str(e))
"""


def python(home=EMPTY_HOME, env=None):
    """argv prefix and environment that run a script with `home` as the user record's home."""
    if os.name == "nt":  # no pwd: the scripts read USERPROFILE
        return [sys.executable, "-B"], {**(env or os.environ), "USERPROFILE": home}
    return [nousfx_testhome.python(home), "-B"], env


def run_script(args, cwd, home=EMPTY_HOME, env=None, timeout=120):
    argv, env = python(home, env)
    return subprocess.run([*argv, *args], cwd=cwd, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=timeout)


class GateLookupCases:
    """Mixin for a unittest.TestCase. Set SCRIPT (path of the script under test) and GATE_NAMES (source that gives a
    stub gate every name the script checks for)."""
    SCRIPT = None
    GATE_NAMES = ""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)
        self.home = os.path.join(self.tmp, "home")
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.home)
        os.makedirs(self.proj)
        subprocess.run(["git", "init", "-q"], cwd=self.proj, check=True)
        self.git_dir = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=self.proj, check=True,
                                      capture_output=True, text=True).stdout.strip()
        self.sentinel = os.path.join(self.tmp, "tampered-gate-ran")
        self.loader = os.path.join(self.tmp, "loader.py")
        with open(self.loader, "w") as f:
            f.write(LOADER)

    def write_gate(self, folder, mark, body=None):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "review-gate.py"), "w") as f:
            f.write(body if body is not None else f"{self.GATE_NAMES}\nMARK = {mark!r}\n")

    def write_tampered(self, folder):
        self.write_gate(folder, "tampered", f"open({self.sentinel!r}, 'w').close()\n{self.GATE_NAMES}\n"
                                            "MARK = 'tampered'\n")

    def copy_script(self, scripts):
        os.makedirs(scripts, exist_ok=True)
        dst = os.path.join(scripts, os.path.basename(self.SCRIPT))
        shutil.copyfile(self.SCRIPT, dst)
        return dst

    def project_copy(self):
        """The layout sync gives a project: .claude/skills/review-loop/scripts, with a tampered .claude/hooks gate."""
        self.write_tampered(os.path.join(self.proj, ".claude", "hooks"))
        return self.copy_script(os.path.join(self.proj, ".claude", "skills", "review-loop", "scripts"))

    def installed(self):
        return os.path.join(self.home, ".claude", "hooks")

    def cloud(self):
        return os.path.join(self.git_dir, "nousfx-gate", "current", "hooks")

    def load(self, script, cwd=None, env=None, home=None):
        r = run_script([self.loader, script], cwd=cwd or self.proj, home=self.home if home is None else home, env=env,
                       timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def assertTamperedNeverRan(self):
        self.assertFalse(os.path.exists(self.sentinel), "the repo's own .claude/hooks/review-gate.py was loaded")

    def test_a_project_copy_loads_the_installed_gate_not_its_sibling(self):
        script = self.project_copy()
        self.write_gate(self.installed(), "installed")
        self.assertEqual(self.load(script), "MARK installed")
        self.assertTamperedNeverRan()

    def test_a_project_copy_in_a_cloud_session_loads_the_extracted_gate(self):
        script = self.project_copy()
        self.write_gate(self.cloud(), "cloud")
        self.assertEqual(self.load(script), "MARK cloud")
        self.assertTamperedNeverRan()

    def test_the_installed_gate_comes_before_the_cloud_copy(self):
        script = self.project_copy()
        self.write_gate(self.installed(), "installed")
        self.write_gate(self.cloud(), "cloud")
        self.assertEqual(self.load(script), "MARK installed")

    def test_a_project_copy_with_no_trusted_gate_refuses_and_names_the_trusted_path(self):
        script = self.project_copy()
        out = self.load(script)
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertIn(os.path.join(self.home, ".claude", "hooks", "review-gate.py"), out)
        self.assertIn(os.path.join("nousfx-gate", "current", "hooks", "review-gate.py"), out)
        self.assertTamperedNeverRan()

    def test_a_trusted_gate_that_cannot_load_refuses_without_falling_back(self):
        script = self.project_copy()
        self.write_gate(self.installed(), "broken", "raise RuntimeError('broken gate')\n")
        self.write_gate(self.cloud(), "cloud")
        out = self.load(script)
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertIn("broken gate", out)
        self.assertTamperedNeverRan()

    def test_a_trusted_gate_without_the_needed_names_is_too_old(self):
        script = self.project_copy()
        self.write_gate(self.installed(), "old", "STAMP_FILE = 'claude-review-check-pass'\nMARK = 'old'\n")
        out = self.load(script)
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertIn("too old", out)
        self.assertIn("re-run the sync", out)

    def test_a_committed_link_into_a_claude_folder_is_still_a_project_copy(self):
        """The branch commits .claude/skills/review-loop as a link into a claude/{skills,hooks} tree it also wrote."""
        fake = os.path.join(self.proj, "vendor", "claude")
        self.copy_script(os.path.join(fake, "skills", "review-loop", "scripts"))
        self.write_tampered(os.path.join(fake, "hooks"))
        os.makedirs(os.path.join(self.proj, ".claude", "skills"))
        os.symlink(os.path.join(fake, "skills", "review-loop"),
                   os.path.join(self.proj, ".claude", "skills", "review-loop"))
        script = os.path.join(self.proj, ".claude", "skills", "review-loop", "scripts", os.path.basename(self.SCRIPT))
        out = self.load(script)
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertTamperedNeverRan()

    def test_the_nousfx_checkout_falls_back_to_its_own_gate(self):
        """In a NousFX checkout (claude/{hooks,skills}) the sibling gate is the trusted source (step 3)."""
        root = os.path.join(self.tmp, "NousFX", "claude")
        script = self.copy_script(os.path.join(root, "skills", "review-loop", "scripts"))
        self.write_gate(os.path.join(root, "hooks"), "checkout")
        self.assertEqual(self.load(script), "MARK checkout")

    def test_a_symlinked_skill_folder_loads_the_installed_gate(self):
        """~/.claude/skills/review-loop links into a NousFX checkout; the gate comes from ~/.claude/hooks."""
        root = os.path.join(self.tmp, "NousFX", "claude")
        self.copy_script(os.path.join(root, "skills", "review-loop", "scripts"))
        self.write_gate(os.path.join(root, "hooks"), "checkout")
        self.write_gate(self.installed(), "installed")
        os.makedirs(os.path.join(self.home, ".claude", "skills"))
        link = os.path.join(self.home, ".claude", "skills", "review-loop")
        os.symlink(os.path.join(root, "skills", "review-loop"), link)
        self.assertEqual(self.load(os.path.join(link, "scripts", os.path.basename(self.SCRIPT))), "MARK installed")

    def test_a_git_dir_variable_cannot_point_at_a_committed_fake_git_dir(self):
        """A committed settings `env` block can set GIT_DIR; a fake git dir in the repo must not supply the gate."""
        script = self.project_copy()
        fake = os.path.join(self.proj, "fake.git")
        for d in ("objects", "refs"):
            os.makedirs(os.path.join(fake, d))
        with open(os.path.join(fake, "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")
        self.write_tampered(os.path.join(fake, "nousfx-gate", "current", "hooks"))
        out = self.load(script, env={**os.environ, "GIT_DIR": fake})
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertTamperedNeverRan()

    def test_outside_a_repo_a_project_copy_refuses(self):
        plain = os.path.join(self.tmp, "plain")
        self.write_tampered(os.path.join(plain, ".claude", "hooks"))
        script = self.copy_script(os.path.join(plain, ".claude", "skills", "review-loop", "scripts"))
        out = self.load(script, cwd=plain)
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertIn("<git-dir>", out)
        self.assertTamperedNeverRan()

    def test_a_linked_worktree_finds_the_cloud_copy_in_its_own_git_dir(self):
        """The project hook extracts into `git rev-parse --absolute-git-dir`, which is per worktree."""
        subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "commit", "-q",
                        "--allow-empty", "-m", "NFX-1: base"], cwd=self.proj, check=True)
        wt = os.path.join(self.tmp, "wt")
        subprocess.run(["git", "worktree", "add", "-q", wt], cwd=self.proj, check=True, capture_output=True)
        wt_git = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=wt, check=True,
                                capture_output=True, text=True).stdout.strip()
        self.write_tampered(os.path.join(wt, ".claude", "hooks"))
        script = self.copy_script(os.path.join(wt, ".claude", "skills", "review-loop", "scripts"))
        self.write_gate(os.path.join(wt_git, "nousfx-gate", "current", "hooks"), "worktree-cloud")
        self.assertEqual(self.load(script, cwd=wt), "MARK worktree-cloud")
        self.assertTamperedNeverRan()

    def test_an_empty_home_is_not_the_current_folder(self):
        """realpath("") is the cwd: an empty home must not turn step 1 into the repo's own .claude/hooks gate."""
        script = self.project_copy()
        out = self.load(script, home="")
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertTamperedNeverRan()

    def test_a_committed_bare_looking_folder_is_not_a_git_dir(self):
        """Run from inside a folder with HEAD, objects/ and refs/: git must not take it as a bare repo's git dir."""
        script = self.project_copy()
        fake = os.path.join(self.proj, "fakebare")
        for d in ("objects", "refs"):
            os.makedirs(os.path.join(fake, d))
        with open(os.path.join(fake, "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")
        self.write_tampered(os.path.join(fake, "nousfx-gate", "current", "hooks"))
        out = self.load(script, cwd=fake)
        self.assertTrue(out.startswith("REFUSED "), out)
        self.assertTamperedNeverRan()
