#!/usr/bin/env python3
"""Tests for via_setup.py. Run: python3 plugins/via/scripts/test_via_setup.py

Each test runs the script as a subprocess with a throwaway HOME and a PATH holding only fake binaries.
"""
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

import nousfx_testhome as th

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "via_setup.py")
KEY_LIKE = re.compile(r"(AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{20,})")
INTEGRATIONS = {"gemini", "atlassian", "figma", "obsidian", "simulators", "browser", "pillow", "skills"}
PILLOW_VERSION = re.search(r'PILLOW_VERSION = "([^"]+)"', open(SCRIPT).read()).group(1)
FAKE_KEY = "AQ.FAKEKEYFAKEKEYFAKEKEYFAKEKEY99"


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


class ViaSetupTest(unittest.TestCase):
    def setUp(self):
        self.home = self.tempdir()
        self.bin = self.tempdir()
        os.symlink(shutil.which("git"), os.path.join(self.bin, "git"))  # the config check asks git about work trees
        self.config = os.path.join(self.home, ".config", "nousfx", "via.json")
        self.legacy = os.path.join(self.home, ".claude", "via.json")

    def tempdir(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        return d.name

    def fake_gemini(self, source="keyring", env_source=None, creds=None):
        path = os.path.join(self.home, ".local", "bin", "gemini-api")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        def doc(src):
            return json.dumps(creds if creds is not None else
                              {"credentials": {"api-key": {"source": src, "value": FAKE_KEY[:2] + "******99"}}})
        script = "#!/bin/sh\n"
        if env_source:
            script += 'if [ -n "$GEMINI_API_KEY" ]; then echo \'%s\'; exit 0; fi\n' % doc(env_source)
        with open(path, "w") as f:
            f.write(script + "echo '%s'\n" % doc(source))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def run_setup(self, *args, **extra_env):
        env = {"PATH": self.bin, **th.env(self.home), **extra_env}
        return subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, *args], env=env, capture_output=True, text=True)

    def report(self, *args):
        r = self.run_setup(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_detection_uses_the_user_record_home_not_the_home_variable(self):
        self.fake_gemini()
        elsewhere = self.tempdir()  # HOME points at an empty folder, the user record at the real one
        r = self.run_setup(HOME=elsewhere)
        self.assertEqual(r.returncode, 0, r.stderr)
        rep = json.loads(r.stdout)
        self.assertEqual(rep["integrations"]["gemini"]["status"], "present")

    def test_a_config_outside_the_anchor_is_refused(self):
        elsewhere = os.path.join(self.tempdir(), "via.json")
        r = self.run_setup("--config", elsewhere)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("nousfx", r.stderr)
        self.assertFalse(os.path.exists(elsewhere))
        r = self.run_setup(VIA_CONFIG=elsewhere)  # no longer read: setup writes to the anchor instead
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(self.config))

    def test_nothing_configured_lists_every_integration_as_missing_with_a_fix_link(self):
        rep = self.report()
        self.assertEqual(set(rep["integrations"]), INTEGRATIONS)
        for name, item in rep["integrations"].items():
            self.assertEqual(item["status"], "missing", name)
            self.assertTrue(item["why"], name)
            self.assertTrue(item["fix_url"].startswith("https://"), name)
        self.assertIn("summary", rep)

    def test_config_is_written_with_the_required_fields_and_no_secrets(self):
        self.fake_gemini()
        self.report()
        text = read(self.config)
        cfg = json.loads(text)
        for field in ("outbound_allowlist", "obsidian", "figma"):
            self.assertIn(field, cfg)
        self.assertEqual(cfg["outbound_allowlist"], {})
        self.assertIn("vault_path", cfg["obsidian"])
        self.assertIn("state", cfg["figma"])
        self.assertIsNone(KEY_LIKE.search(text))
        self.assertEqual(stat.S_IMODE(os.stat(self.config).st_mode), 0o600)

    def test_rerun_with_no_changes_leaves_the_config_untouched(self):
        self.fake_gemini()
        first = self.report()
        self.assertEqual(first["config"], "written")
        before = (read(self.config, "rb"), os.stat(self.config).st_mtime_ns)
        second = self.report()
        self.assertEqual(second["config"], "unchanged")
        self.assertEqual(before, (read(self.config, "rb"), os.stat(self.config).st_mtime_ns))

    def test_user_set_values_survive_a_rerun(self):
        self.report()
        cfg = json.loads(read(self.config))
        vault = os.path.join(self.home, "vault")
        os.makedirs(os.path.join(vault, ".obsidian"))
        cfg["obsidian"]["vault_path"] = vault
        cfg["outbound_allowlist"] = {"acme/app": ["gemini"]}
        with open(self.config, "w") as f:
            json.dump(cfg, f)
        rep = self.report()
        after = json.loads(read(self.config))
        self.assertEqual(after["obsidian"]["vault_path"], vault)
        self.assertEqual(after["outbound_allowlist"], {"acme/app": ["gemini"]})
        self.assertEqual(rep["integrations"]["obsidian"]["status"], "present")

    def test_gemini_key_in_keyring_is_present_and_never_echoed(self):
        self.fake_gemini("keyring")
        r = self.run_setup()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["integrations"]["gemini"]["status"], "present")
        self.assertNotIn("******", r.stdout + read(self.config))

    def test_gemini_cli_without_a_key_is_reported_as_missing_key(self):
        self.fake_gemini("unset")
        self.assertEqual(self.report()["integrations"]["gemini"]["status"], "no-key")

    def test_flags_from_claude_set_the_mcp_browser_and_skill_integrations(self):
        rep = self.report("--atlassian", "present", "--figma", "needs-auth", "--browser", "chrome",
                          "--skills", "material-3:material-3,adaptive")
        i = rep["integrations"]
        self.assertEqual(i["atlassian"]["status"], "present")
        self.assertEqual(i["figma"]["status"], "needs-auth")
        self.assertEqual(i["browser"]["status"], "present")
        self.assertEqual(i["skills"]["status"], "partial")
        self.assertEqual(json.loads(read(self.config))["figma"]["state"], "needs-auth")

    def test_gemini_key_from_an_env_var_counts_as_present(self):
        self.fake_gemini("unset", env_source="env")
        r = self.run_setup(GEMINI_API_KEY="set-in-env")
        self.assertEqual(json.loads(r.stdout)["integrations"]["gemini"]["status"], "present")

    def test_odd_whoami_output_does_not_crash_the_doctor(self):
        for creds in ({"credentials": None}, {"credentials": ["x"]}, {"credentials": "x"}, {"other": 1}):
            self.fake_gemini(creds=creds)
            self.assertEqual(self.report()["integrations"]["gemini"]["status"], "no-key")

    def test_a_non_object_section_in_the_config_stops_with_a_message(self):
        os.makedirs(os.path.dirname(self.config))
        with open(self.config, "w") as f:
            json.dump({"obsidian": "oops"}, f)
        r = self.run_setup()
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be an object", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_vault_path_with_a_tilde_is_expanded_and_a_non_string_is_ignored(self):
        os.makedirs(os.path.join(self.home, "Vault", ".obsidian"))
        os.makedirs(os.path.dirname(self.config))
        for value, expected in (("~/Vault", "present"), (["x"], "missing"), (5, "missing")):
            with open(self.config, "w") as f:
                json.dump({"obsidian": {"vault_path": value}}, f)
            self.assertEqual(self.report()["integrations"]["obsidian"]["status"], expected, value)

    def test_an_empty_skills_flag_is_accepted(self):
        self.assertEqual(self.report("--skills")["integrations"]["skills"]["status"], "missing")

    def test_a_bare_config_filename_works(self):
        env = {"PATH": self.bin, **th.env(self.home)}
        folder = os.path.join(self.home, ".config", "nousfx")  # a config must live inside the anchor
        os.makedirs(folder)
        r = subprocess.run([th.python(self.home), "-I", "-B", SCRIPT, "--config", "via.json"], env=env, cwd=folder,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(folder, "via.json")))

    def test_an_unwritable_config_folder_exits_1_without_a_traceback(self):
        os.makedirs(os.path.dirname(self.config))
        os.chmod(os.path.dirname(self.config), 0o500)
        self.addCleanup(os.chmod, os.path.dirname(self.config), 0o700)
        r = self.run_setup()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_symlinked_config_is_refused(self):
        os.makedirs(os.path.dirname(self.config))
        target = os.path.join(self.home, "elsewhere.json")
        with open(target, "w") as f:
            f.write("{}")
        os.symlink(target, self.config)
        r = self.run_setup()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(read(target), "{}")

    def test_setup_writes_to_the_new_home_and_never_to_the_old_path(self):
        self.report()
        self.assertTrue(os.path.isfile(self.config))
        self.assertFalse(os.path.exists(self.legacy))

    def test_an_old_config_is_migrated_once_and_kept_in_place(self):
        os.makedirs(os.path.dirname(self.legacy))
        with open(self.legacy, "w") as f:
            json.dump({"outbound_allowlist": {"acme/app": {"destinations": ["gemini"]}}}, f)
        first = self.report()
        self.assertEqual(first["migration"], "migrated")
        self.assertIn("acme/app", json.loads(read(self.config))["outbound_allowlist"])
        self.assertTrue(os.path.exists(self.legacy))
        mid = read(self.config)
        with open(self.legacy, "w") as f:
            f.write('{"outbound_allowlist": {"other/repo": {"destinations": ["jira"]}}}')
        second = self.report()  # run twice: the new file is never overwritten by the old one
        self.assertEqual(second["migration"], "none")
        self.assertEqual(read(self.config), mid)

    def test_a_fresh_install_has_nothing_to_migrate(self):
        self.assertEqual(self.report()["migration"], "none")

    def test_a_symlinked_old_config_is_not_migrated(self):
        os.makedirs(os.path.dirname(self.legacy))
        target = os.path.join(self.home, "elsewhere.json")
        with open(target, "w") as f:
            f.write("{}")
        os.symlink(target, self.legacy)
        self.assertNotEqual(self.run_setup().returncode, 0)
        self.assertFalse(os.path.exists(self.config))

    def test_a_home_inside_a_git_work_tree_is_refused_and_nothing_is_written(self):
        subprocess.run(["git", "-C", self.home, "init", "-q"], check=True, capture_output=True)
        r = self.run_setup()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("work tree", r.stderr)
        self.assertFalse(os.path.exists(self.config))

    def test_a_secret_shaped_value_is_never_written(self):
        r = self.run_setup("--skills", FAKE_KEY)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(self.config))

    # Pillow environment

    def venv_dir(self):
        return os.path.join(self.home, ".local", "share", "nousfx", "via", "venv")

    def fake_wheels(self):
        """A tiny local Pillow wheel so the create flag runs offline."""
        folder = self.tempdir()
        v = PILLOW_VERSION
        info = "Pillow-%s.dist-info" % v
        with zipfile.ZipFile(os.path.join(folder, "Pillow-%s-py3-none-any.whl" % v), "w") as z:
            z.writestr("PIL/__init__.py", '__version__ = "%s"\n' % v)
            z.writestr(info + "/METADATA", "Metadata-Version: 2.1\nName: Pillow\nVersion: %s\n" % v)
            z.writestr(info + "/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            z.writestr(info + "/RECORD", "")
        return {"PIP_NO_INDEX": "1", "PIP_FIND_LINKS": folder}

    def create_pillow(self):
        r = self.run_setup("--create-pillow", **self.fake_wheels())
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_a_missing_pillow_env_is_reported_with_the_create_command_and_not_created(self):
        item = self.report()["integrations"]["pillow"]
        self.assertEqual(item["status"], "missing")
        self.assertIn("--create-pillow", item["create_command"])
        # PATH here holds no python3, so the running interpreter is named by its own path
        self.assertRegex(item["create_command"], r"^(\S+|'[^']*') -I -B .*via_setup\.py'? --create-pillow$")
        self.assertFalse(os.path.exists(self.venv_dir()))
        self.assertIsNone(json.loads(read(self.config))["assets"]["python"])

    def test_create_pillow_builds_the_env_offline_and_reports_present(self):
        rep = self.create_pillow()
        self.assertEqual(rep["integrations"]["pillow"]["status"], "present")
        self.assertNotIn("create_command", rep["integrations"]["pillow"])
        py = os.path.join(self.venv_dir(), "bin", "python")
        r = subprocess.run([py, "-I", "-B", "-c", "import PIL"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.report()["integrations"]["pillow"]["status"], "present")

    def test_a_broken_pillow_env_is_reported_broken_and_create_repairs_it(self):
        self.create_pillow()
        py = os.path.join(self.venv_dir(), "bin", "python")
        os.remove(py)
        with open(py, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(py, 0o755)
        item = self.report()["integrations"]["pillow"]
        self.assertEqual(item["status"], "broken")
        self.assertIn("--create-pillow", item["create_command"])
        self.assertTrue(item["fix_url"].startswith("https://"))
        self.assertEqual(self.create_pillow()["integrations"]["pillow"]["status"], "present")

    def test_a_failed_create_exits_1_but_a_plain_run_does_not(self):
        r = self.run_setup("--create-pillow", PIP_NO_INDEX="1", PIP_FIND_LINKS=self.tempdir())
        self.assertEqual(r.returncode, 1)
        self.assertIn("Pillow environment not ready", r.stderr)
        self.assertNotEqual(json.loads(r.stdout)["integrations"]["pillow"]["status"], "present")
        self.assertEqual(self.run_setup().returncode, 0)

    def test_config_records_the_pillow_status_and_python_path_only(self):
        self.create_pillow()
        text = read(self.config)
        cfg = json.loads(text)
        self.assertEqual(cfg["integrations"]["pillow"], "present")
        self.assertEqual(cfg["assets"]["python"], os.path.join(os.path.realpath(self.venv_dir()), "bin", "python"))
        self.assertIsNone(KEY_LIKE.search(text))

    def test_create_pillow_names_an_interpreter_that_is_too_old_and_builds_nothing(self):
        import importlib.util
        import io
        from contextlib import redirect_stderr
        spec = importlib.util.spec_from_file_location("via_setup_under_test", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.PILLOW_MIN_PYTHON = (99, 0)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertFalse(mod.create_pillow(self.home, "missing"))
        self.assertIn("needs Python 99.0 or newer", err.getvalue())
        self.assertNotIn("network", err.getvalue())
        self.assertFalse(os.path.exists(self.venv_dir()))

    def load_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("via_setup_under_test", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def fake_python(self, name, code, folder=None):
        """A stand-in interpreter. It exits `code` only when run as the version check (-c ... sys.version_info)."""
        d = folder or self.tempdir()
        path = os.path.join(d, name)
        with open(path, "w") as f:
            f.write('#!/bin/sh\ncase "$*" in *"-I -B -c"*sys.version_info*) exit %d;; esac\nexit 3\n' % code)
        os.chmod(path, 0o755)
        return d, path

    def test_a_too_old_running_python_gets_a_verified_newer_one_in_the_command(self):
        from unittest import mock
        mod = self.load_module()
        mod.PILLOW_MIN_PYTHON = (99, 0)
        d, path = self.fake_python("python3.12", 0)
        with mock.patch.dict(os.environ, {"PATH": d}):
            cmd = mod.pillow_command()
        self.assertTrue(cmd.startswith("%s -I -B " % shlex.quote(path)), cmd)
        self.assertTrue(cmd.endswith(" --create-pillow"))

    def test_no_qualifying_python_gives_no_command_and_the_report_asks_for_one(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        mod = self.load_module()
        mod.PILLOW_MIN_PYTHON = (99, 0)
        d, _ = self.fake_python("python3.12", 1)
        for path_env in (d, ""):
            with mock.patch.dict(os.environ, {"PATH": path_env}):
                self.assertIsNone(mod.pillow_command())
        out = io.StringIO()
        # main() runs in this process, where the home comes from the real user record: fake that record too,
        # or it reads and rewrites the real ~/.config/nousfx/via.json.
        with mock.patch.dict(os.environ, {**th.env(self.home), "PATH": d + os.pathsep + self.bin}), \
                th.patched_home(self.home), \
                mock.patch.object(sys, "argv", ["via_setup.py"]), redirect_stdout(out):
            self.assertEqual(mod.main(), 0)
        item = json.loads(out.getvalue())["integrations"]["pillow"]
        self.assertEqual(item["needs_python"], "99.0")
        self.assertNotIn("create_command", item)

    def test_the_newest_qualifying_python_on_path_wins_and_a_failed_check_is_skipped(self):
        from unittest import mock
        mod = self.load_module()
        mod.PILLOW_MIN_PYTHON = (99, 0)
        d, _ = self.fake_python("python3.14", 1)  # fails the check: skipped
        _, p12 = self.fake_python("python3.12", 0, d)
        _, p11 = self.fake_python("python3.11", 0, d)
        with mock.patch.dict(os.environ, {"PATH": d}):
            cmd = mod.pillow_command()
        self.assertTrue(cmd.startswith("%s -I -B " % shlex.quote(p12)), cmd)  # not 3.14 (failed), not 3.11 (older)

    def test_setup_mode_doc_covers_needs_python_and_the_pip_note(self):
        doc = read(os.path.join(os.path.dirname(SCRIPT), "..", "skills", "via", "modes", "setup.md"))
        self.assertIn("`needs_python`", doc)
        self.assertIn("`PIP_*`", doc)

    def test_a_qualifying_running_python_keeps_the_plain_command(self):
        from unittest import mock
        mod = self.load_module()
        mod.PILLOW_MIN_PYTHON = (3, 0)  # any host: the running Python qualifies
        with mock.patch.object(mod.shutil, "which", lambda name: sys.executable if name == "python3" else None):
            cmd = mod.pillow_command()
        self.assertTrue(cmd.startswith("python3 -I -B "), cmd)
        self.assertIn(shlex.quote(os.path.realpath(SCRIPT)), cmd)
        with mock.patch.object(mod.shutil, "which", lambda name: None):  # PATH's python3 is not this interpreter
            self.assertTrue(mod.pillow_command().startswith(shlex.quote(sys.executable) + " -I -B "))

    # Windows layout (NFX-166): the platform is faked through via_setup.windows(), so no Windows is needed.

    def windows_module(self):
        from unittest import mock
        mod = self.load_module()
        patcher = mock.patch.object(mod, "windows", lambda: True)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mod

    def test_windows_detect_and_config_use_scripts_python_exe(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        mod = self.windows_module()
        exe = os.path.join(self.venv_dir(), "Scripts", "python.exe")
        self.assertEqual(mod.pillow_python(self.home), exe)
        self.assertEqual(mod.detect_pillow(self.home), "missing")
        os.makedirs(os.path.dirname(exe))
        with open(exe, "w") as f:  # a stand-in interpreter that answers the Pillow version check
            f.write("#!/bin/sh\necho %s\n" % PILLOW_VERSION)
        os.chmod(exe, 0o755)
        self.assertEqual(mod.detect_pillow(self.home), "present")
        os.makedirs(os.path.join(self.venv_dir(), "bin"))
        with open(os.path.join(self.venv_dir(), "bin", "python"), "w") as f:  # a POSIX interpreter is not looked at
            f.write("#!/bin/sh\nexit 1\n")
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**th.env(self.home), "PATH": self.bin}), th.patched_home(self.home), \
                mock.patch.object(sys, "argv", ["via_setup.py"]), redirect_stdout(out):
            self.assertEqual(mod.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["integrations"]["pillow"]["status"], "present")
        self.assertEqual(json.loads(read(self.config))["assets"]["python"],
                         os.path.join(os.path.realpath(self.venv_dir()), "Scripts", "python.exe"))

    def test_windows_create_builds_the_env_with_copies_and_installs_the_pin(self):
        import io
        from contextlib import redirect_stderr
        from unittest import mock
        mod = self.windows_module()
        real_create, seen = mod.venv.create, {}

        def create(env, **kw):
            # This host lays out bin/python; Windows lays out Scripts\python.exe. Add the Windows name.
            seen.update(kw)
            real_create(env, **{**kw, "symlinks": True})
            os.makedirs(os.path.join(env, "Scripts"), exist_ok=True)
            os.symlink(os.path.join(env, "bin", "python"), os.path.join(env, "Scripts", "python.exe"))

        err = io.StringIO()
        with mock.patch.object(mod.venv, "create", create), mock.patch.dict(os.environ, self.fake_wheels()), \
                redirect_stderr(err):
            self.assertTrue(mod.create_pillow(self.home, "missing"), err.getvalue())
        self.assertIs(seen["symlinks"], False)  # Windows venvs copy the interpreter
        self.assertNotIn("not supported", err.getvalue())
        r = subprocess.run([mod.pillow_python(self.home), "-I", "-B", "-c", "import PIL; print(PIL.__version__)"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), PILLOW_VERSION, r.stderr)

    def test_windows_create_command_uses_the_py_launcher_and_windows_quoting(self):
        from unittest import mock
        mod = self.windows_module()
        script = os.path.realpath(SCRIPT)
        exact = "-%d.%d" % sys.version_info[:2]
        with mock.patch.object(mod.shutil, "which", lambda name: r"C:\Windows\py.exe" if name == "py" else None):
            with mock.patch.object(mod, "qualifies", lambda prefix, timeout=20: prefix[1] == exact):
                self.assertEqual(mod.pillow_command(), " ".join(["py", exact, "-I", "-B", script, "--create-pillow"]))
            with mock.patch.object(mod, "qualifies", lambda prefix, timeout=20: False):  # launcher lacks this Python
                self.assertEqual(mod.pillow_command().split(" -I -B ")[0], mod.win_quote(sys.executable))
            mod.PILLOW_MIN_PYTHON = (99, 0)  # this Python is too old: probe the launcher, newest first
            asked = []
            with mock.patch.object(mod, "qualifies",
                                   lambda prefix, timeout=20: asked.append((prefix[1], timeout)) or prefix[1] == "-3.12"):
                self.assertTrue(mod.pillow_command().startswith("py -3.12 -I -B "))
            self.assertEqual(asked, [("-3.14", 10), ("-3.13", 10), ("-3.12", 10)])  # short probes, newest first
            with mock.patch.object(mod, "qualifies", lambda prefix, timeout=20: prefix[1] == "-3"):
                self.assertTrue(mod.pillow_command().startswith("py -3 -I -B "))
            with mock.patch.object(mod, "qualifies", lambda prefix, timeout=20: False):
                self.assertIsNone(mod.pillow_command())
        mod.PILLOW_MIN_PYTHON = (3, 0)
        with mock.patch.object(mod.shutil, "which", lambda name: None):  # no launcher: this interpreter's path
            self.assertEqual(mod.pillow_command().split(" -I -B ")[0], mod.win_quote(sys.executable))
        # Git Bash eats backslashes: forward slashes, and double quotes (not POSIX single quotes) around spaces.
        self.assertEqual(mod.quote_command(["py", r"C:\Program Files\via_setup.py", r"C:\x\y.py"]),
                         'py "C:/Program Files/via_setup.py" C:/x/y.py')
        # An apostrophe, &, ;, $ or backtick in a path stays inside one bash word, taken literally.
        line = mod.quote_command(["py", "C:\\Users\\O'Neil\\a&b;c\\$x`y.py"])
        self.assertEqual(line, 'py "C:/Users/O\'Neil/a&b;c/\\$x\\`y.py"')
        bash = shutil.which("bash")
        if bash:
            r = subprocess.run([bash, "-c", "printf '%s\\n' " + line.split(" ", 1)[1]], capture_output=True, text=True)
            self.assertEqual(r.stdout, "C:/Users/O'Neil/a&b;c/$x`y.py\n")

    def test_the_platform_is_read_in_one_place(self):
        """The Windows tests fake windows(); any other os.name check would escape them."""
        self.assertEqual(read(SCRIPT).count("os.name"), 1, "use windows(), not os.name")

    def test_create_pillow_keeps_the_other_recorded_integrations(self):
        self.run_setup("--atlassian", "present", "--figma", "authorized", "--browser", "chrome")
        before = json.loads(read(self.config))
        self.create_pillow()
        after = json.loads(read(self.config))
        self.assertEqual(after["figma"]["state"], "authorized")
        for key in ("atlassian", "figma", "browser", "skills"):
            self.assertEqual(after["integrations"][key], before["integrations"][key], key)
        self.assertEqual(after["integrations"]["pillow"], "present")

    def test_an_env_with_a_dead_interpreter_is_broken_and_create_repairs_it(self):
        self.create_pillow()
        py = os.path.join(self.venv_dir(), "bin", "python")
        os.remove(py)
        os.symlink(os.path.join(self.home, "gone", "python"), py)
        self.assertEqual(self.report()["integrations"]["pillow"]["status"], "broken")
        self.assertEqual(self.create_pillow()["integrations"]["pillow"]["status"], "present")

    def test_no_via_script_imports_pillow_except_assets_scripts(self):
        here = os.path.dirname(SCRIPT)
        for folder, dirs, files in os.walk(here):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if not name.endswith(".py") or name.startswith("test_") or name.startswith("via_assets"):
                    continue
                path = os.path.join(folder, name)
                text = read(path)
                self.assertIsNone(re.search(r"^\s*(import\s[^#\n]*\bPIL\b|from\s+PIL\b)", text, re.M), path)
                self.assertIsNone(re.search(r"(import_module|__import__)\(\s*[\"']PIL", text), path)


if __name__ == "__main__":
    unittest.main()
