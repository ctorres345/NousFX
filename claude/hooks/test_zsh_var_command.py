#!/usr/bin/env python3
"""Tests for the zsh `$VAR args` PreToolUse warning (NFX-247).
Run: python3 -B claude/hooks/test_zsh_var_command.py [-k NAME]
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "review-gate.py")


def load_gate():
    spec = importlib.util.spec_from_file_location("review_gate_zsh_var_test", GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ZshVarCommandRule(unittest.TestCase):
    """Unit tests for zsh_var_command_warning(command, shell=...)."""

    @classmethod
    def setUpClass(cls):
        cls.gate = load_gate()

    def warn(self, command, shell="/bin/zsh"):
        return self.gate.zsh_var_command_warning(command, shell=shell)

    def test_ac1_multiword_var_command_warns(self):
        msg = self.warn('P="python3 -B s.py decide K"; $P --why x')
        self.assertIsNotNone(msg)
        self.assertIn("zsh does not word-split `$P`", msg)
        self.assertIn("${=P}", msg)
        self.assertIn("shell function", msg)

    def test_ac1_quoted_var_and_command_substitution_do_not_warn(self):
        self.assertIsNone(self.warn('PY=/usr/bin/python3; "$PY" s.py'))
        self.assertIsNone(self.warn('"$PY" s.py'))
        self.assertIsNone(self.warn("'${PY}' s.py"))
        self.assertIsNone(self.warn("$(which python3) s.py"))
        self.assertIsNone(self.warn("$(cmd) --flag"))

    def test_ac2_past_instance_ani_206_and_ani_207(self):
        ani_206 = 'P="python3 -B ~/.claude/skills/review-loop/scripts/session_report.py decide ANI-206"; $P --decision "use flat layout" --why "simpler"'
        msg = self.warn(ani_206)
        self.assertIsNotNone(msg)
        self.assertIn("zsh does not word-split `$P`", msg)

        bare_ani_206 = '$P --decision "use flat layout" --why "simpler"'
        msg2 = self.warn(bare_ani_206)
        self.assertIsNotNone(msg2)
        self.assertIn("zsh does not word-split `$P`", msg2)

        ani_207 = 'A="adb -s emulator-5554"; $A shell am start -n com.example/.Main'
        msg3 = self.warn(ani_207)
        self.assertIsNotNone(msg3)
        self.assertIn("zsh does not word-split `$A`", msg3)

    def test_braced_var_warns_and_zsh_word_split_does_not(self):
        msg = self.warn('P="python3 -B s.py"; ${P} --why x')
        self.assertIsNotNone(msg)
        self.assertIn("zsh does not word-split `$P`", msg)
        self.assertIsNone(self.warn('P="python3 -B s.py"; ${=P} --why x'))

    def test_all_segment_separators_flag(self):
        for cmd in (
            "$P --flag",
            "true; $P --flag",
            "true && $P --flag",
            "false || $P --flag",
            "echo hi | $P --flag",
            "true\n$P --flag",
        ):
            msg = self.warn(cmd)
            self.assertIsNotNone(msg, cmd)
            self.assertIn("`$P`", msg, cmd)

    def test_failure_mode_single_word_assignment_in_same_command_skipped(self):
        for cmd in (
            "PY=/usr/bin/python3; $PY s.py",
            'PY="/opt/homebrew/bin/python3" && $PY s.py',
            "PY='/usr/bin/python3' && ${PY} s.py",
            "export PY=/usr/bin/python3; $PY s.py",
            "P=(python3 -B s.py); $P --why x",
        ):
            self.assertIsNone(self.warn(cmd), cmd)

    def test_array_elements_are_not_commands(self):
        self.assertIsNone(self.warn("P=($X -B s.py); $P --why x"))
        self.assertIsNone(self.warn("P=(a $(cmd) b); $P --why x"))

    def test_command_inside_substitution_warns(self):
        self.assertIn("`$P`", self.warn('P="python3 s.py"; out=$($P --why x)'))

    def test_append_keeps_unknown_var_unsafe(self):
        self.assertIsNotNone(self.warn("P+=x; $P --flag"))
        self.assertIsNone(self.warn("P=a; P+=b; $P --flag"))
        self.assertIsNotNone(self.warn('P=a; P+=" b"; $P --flag'))

    def test_arithmetic_does_not_hide_a_later_command(self):
        self.assertIn("`$P`", self.warn("n=$(( $N + 1 )); $P --flag"))

    def test_unbalanced_or_bad_input_is_silent(self):
        self.assertIsNone(self.warn("echo $(( 1 + ; $P --flag"))
        self.assertIsNone(self.warn('echo "unclosed; $P --flag'))

    def test_reassignment_uses_latest_value(self):
        self.assertIsNotNone(self.warn('P=/bin/echo; P="python3 -B s.py"; $P --flag'))
        self.assertIsNone(self.warn('P="python3 -B s.py"; P=/usr/bin/python3; $P s.py'))

    def test_non_zsh_shells_are_silent(self):
        cmd = 'P="python3 -B s.py decide K"; $P --why x'
        for sh in ("/bin/bash", "/usr/local/bin/bash", "/bin/sh", ""):
            self.assertIsNone(self.warn(cmd, shell=sh), sh)

    def test_no_args_comments_quotes_heredocs_and_paths_are_silent(self):
        for cmd in (
            'P="python3 -B s.py"; $P',
            "echo '; $P --flag'",
            'echo "; $P --flag"',
            "# $P --flag",
            "cat <<'EOF'\n$P --flag\nEOF",
            "$ROOT/bin/tool --flag",
            "\\$P --flag",
            "echo $(($X + 1)); n=$((N - 1))",
            "for i in 1 2; do echo $((i * 2)); done",
        ):
            self.assertIsNone(self.warn(cmd), cmd)


class ZshVarCommandHook(unittest.TestCase):
    """End-to-end PreToolUse hook subprocess tests: warns on zsh, never blocks."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = os.path.realpath(tmp.name)

    def run_hook(self, command, shell="/bin/zsh", tool="Bash"):
        payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}, "cwd": self.tmp})
        env = {**os.environ, "SHELL": shell}
        r = subprocess.run([sys.executable, "-I", "-B", GATE], input=payload, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_hook_warns_and_never_blocks_on_zsh(self):
        out = self.run_hook('P="python3 -B s.py decide K"; $P --why x', shell="/bin/zsh")
        data = json.loads(out)
        text = data["systemMessage"]
        self.assertEqual(data["hookSpecificOutput"], {"hookEventName": "PreToolUse", "additionalContext": text})
        self.assertIn("zsh does not word-split `$P`", text)
        self.assertNotIn("permissionDecision", out)

    def test_hook_is_silent_on_bash_and_powershell(self):
        cmd = 'P="python3 -B s.py decide K"; $P --why x'
        self.assertEqual(self.run_hook(cmd, shell="/bin/bash"), "")
        self.assertEqual(self.run_hook(cmd, shell="/bin/zsh", tool="PowerShell"), "")
        self.assertEqual(self.run_hook(cmd, shell="/bin/zsh", tool="Read"), "")


if __name__ == "__main__":
    unittest.main()
