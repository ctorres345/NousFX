#!/usr/bin/env python3
"""Run a ticket's proofs, stamp them, and print an AC-Proof line (NFX-105).

Python only, standard library only; runs the same on macOS and Windows.

    python3 -B prove.py run LIST [--timeout SECONDS] [--base HASH]
    python3 -B prove.py attest LIST
    python3 -B prove.py lint LIST

LIST is a Markdown file taken from the ticket's acceptance criteria (a PR table's cells have no `Proof:` prefix, so
they are not read). Every line with `Proof:` is one proof:
    Proof: `command`        run at the repo top level; must exit 0 and leave the working tree unchanged
    Proof: guard: `command` the same, for a no-regression check that may pass without the change: `--base` never
                            flags it (NFX-213; NFX-299 owns attest's guard count and the reviewer's exemption)
    Proof: manual: <step>   listed by attest, never run and never counted
    Proof: manual: <step> Backup: <other proof>
                            the same, with a backup that counts when the step needs a quota-limited or paid service
                            that is unavailable (NFX-177); attest lists both, and the PR's proof table says which ran
                            ` Backup:` is reserved in a manual step: an older step that uses it is split there
Any other `Proof:` line is refused, so a typo cannot drop a criterion. Prose such as a `Proof:` in backticks is not
a proof line.

`lint` (NFX-208) runs nothing and needs no repo: with the same parser, it prints every refused line as
"line N: why" and exits 2, else exits 0. A list with only manual proofs passes, since `run`'s no-command rule is
about the list, not a line.

A command proof that is exactly the trusted `[check]` command is not run again when a valid `run-check --stamp` stamp
matches the tree, branch and check, as --mark matches it (NFX-218, reusable_check): `run` prints
"proof N: reused check stamp <tree>" and counts it as passed. In every other case the proof runs.

`run --base HASH` (NFX-213) then runs each command proof in a temporary clone at HASH and prints one line per proof:
"passes without the change" (flagged: a proof that cannot fail), "fails without the change", or "unknown" when the
base run timed out or the proof also fails in a clean clone of the change tree (the control testfail.py runs too:
the clone lacks gitignored setup). Guards and the trusted [check] command get no base run. Flags are print-only: the
exit code and the stamp are those of the normal run; review-loop's Record step turns each flag into a `med` finding.
A HASH that is not a commit exits 2 before any proof runs.

`run` stops at the first failing proof (exit 2, no stamp). When all pass, it writes a signed stamp in the git dir
(claude-proof-pass) that binds the working-tree hash, the branch and a hash of the normalized proofs. `attest`
never runs anything: it checks that stamp against the current tree and list, then prints
    AC-Proof: <passed>/<total> manual:<m> <tree> <sha of the proofs>
Exit 0 prints the line; exit 2 prints a reason to stderr and nothing to stdout.

Proofs are self-asserted. They come from a list the branch author controls and CI does not re-run them; see
docs/review-gate.md. The stamp helpers are the review gate's, loaded from the gate --mark runs: ~/.claude/hooks, else
the agy plugin's gate (~/.gemini/config/plugins/nousfx/hooks, hash-checked against hooks.json), else the cloud copy in
the git dir, else (in a NousFX checkout only) claude/hooks next to the skills folder. A project's own .claude/hooks or
.gemini/... copy is never loaded (NFX-214, NFX-316; gate_paths, and "Where prove.py and testfail.py load the gate" in
docs/review-gate.md).
"""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

DEFAULT_TIMEOUT = 600
PROOF_LINE = re.compile(r"(?<![-`\w])Proof:[ \t]*(.*?)[ \t]*$")
COMMAND = re.compile(r"`([^`]*)`\.?")
MANUAL = "manual:"
GUARD = "guard:"
RUNS = ("command", "guard")  # the kinds `run` runs; a guard is never flagged by --base (NFX-213, NFX-299)
BACKUP = re.compile(r"\s+Backup:\s*")
KIND = "proof"


class Refused(Exception):
    """A reason to stop with exit 2."""


def fail(message):
    print(f"Blocked: {message}", file=sys.stderr)
    return 2


def home_dir():
    """The home from the user record, not $HOME: a repo's committed settings `env` block can set HOME (NFX-214).
    Windows has no pwd, so there it is USERPROFILE, which that block can also set; so can PYTHONPATH, on every OS,
    for a script run as `python3 -B` (a stated limit in docs/review-gate.md). "" when unknown: never the cwd."""
    try:
        import pwd
        home = pwd.getpwuid(os.getuid()).pw_dir
    except ImportError:
        home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.realpath(home) if home and os.path.isabs(home) else ""


def gate_paths(cwd):
    """Where the review gate may come from, reader first (NFX-214; the rule via_run.py follows, NFX-207), as
    (path, kind) pairs, plus the trusted paths a refusal names. The gate --mark runs comes first:
      installed  <home>/.claude/hooks/review-gate.py
      agy        <home>/.gemini/config/plugins/nousfx/hooks/review-gate.py, hash-checked against hooks.json (NFX-316)
      cloud      <git-dir>/nousfx-gate/current/hooks/review-gate.py, the default branch's copy the project hook
                 extracts when there is no ~/.claude. git runs with every GIT_* variable dropped: a committed
                 settings `env` block could point GIT_DIR at a fake git dir in the repo.
      checkout   only in a NousFX checkout: the gate next to the skills folder, abspath then realpath. A project copy
                 sits in .claude/skills/review-loop/scripts, and its sibling .claude/hooks/review-gate.py is a repo
                 file the branch under review can edit, so it is never a candidate. The checkout is told by the
                 invoked path: the folder holding skills/ and hooks/ is named `claude` (NousFX's source layout) for
                 both the abspath and the realpath. Projects, ~/.claude and anything else are `.claude` or another
                 name; a committed link from .claude/skills into a claude/ folder still has a `.claude` abspath."""
    home = home_dir()
    installed = os.path.join(home or "<home>", ".claude", "hooks", "review-gate.py")
    agy = os.path.join(home or "<home>", ".gemini", "config", "plugins", "nousfx", "hooks", "review-gate.py")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # A committed folder that looks like a bare repo is not a git dir: --show-toplevel fails inside any git dir,
    # and safe.bareRepository=explicit (git 2.38+) refuses bare discovery as a second layer.
    try:
        r = subprocess.run(["git", "-c", "safe.bareRepository=explicit", "rev-parse", "--absolute-git-dir",
                            "--show-toplevel"], cwd=cwd, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=30)
        git_dir = (r.stdout.splitlines() + [""])[0] if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        git_dir = ""  # not a repo, or no git: no cloud copy to find
    cloud = os.path.join(git_dir or "<git-dir>", "nousfx-gate", "current", "hooks", "review-gate.py")
    paths = ([(installed, "installed"), (agy, "agy")] if home else []) + ([(cloud, "cloud")] if git_dir else [])
    roots = [os.path.normpath(os.path.join(os.path.dirname(me), "..", "..", ".."))
             for me in (os.path.abspath(__file__), os.path.realpath(__file__))]
    if all(os.path.basename(root) == "claude" for root in roots):
        # normpath, so the `..` steps are taken in the path as written: the OS would walk them through a symlink
        paths += [(os.path.join(root, "hooks", "review-gate.py"), "checkout") for root in roots]
    return paths, (installed, agy, cloud)


def find_gate(cwd):
    """The first gate in gate_paths() that exists, as (path, kind). None found: refuse, naming the trusted paths."""
    paths, (installed, agy, cloud) = gate_paths(cwd)
    found = next(((p, kind) for p, kind in paths if os.path.isfile(p)), None)
    if found is None:
        raise Refused(f"no trusted review gate: looked for {installed}, {agy} and {cloud}. The repo's own "
                      ".claude/hooks/review-gate.py and .gemini/... copies are never loaded: they are code under "
                      "review. Re-run the NousFX sync (in a cloud session, any Bash call lets the project hook "
                      "extract the default branch's gate)")
    return found


def verify_agy_gate(path):
    """Refuse the agy plugin gate unless hooks.json names its sha256 (and pr_create_parser.py's) in --expect
    (NFX-316). agy file tools are not hooked yet, so a model could edit the plugin gate; hooks.json is guarded."""
    hooks_dir = os.path.dirname(path)
    hooks_json = os.path.join(os.path.dirname(hooks_dir), "hooks.json")
    sync_hint = "Run `python3 -B scripts/nousfx_sync.py --tools agy` to refresh it."
    try:
        with open(hooks_json, encoding="utf-8") as f:
            command = json.load(f)["nousfx-review-gate"]["PreToolUse"][0]["hooks"][0]["command"]
        if not isinstance(command, str):
            raise TypeError("hook command is not a string")
        argv = [a.strip('"') for a in shlex.split(command, posix=os.name != "nt")]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as e:
        raise Refused(f"cannot verify agy review gate {path}: {hooks_json} is missing or malformed ({e}). "
                      f"{sync_hint}") from None
    expect = None
    for i, arg in enumerate(argv):
        if arg == "--expect" and i + 1 < len(argv):
            expect = argv[i + 1]
            break
        if arg.startswith("--expect="):
            expect = arg.split("=", 1)[1]
            break
    want = expect.lower().split(",") if expect else []
    if len(want) < 3 or any(len(h) != 64 or any(c not in "0123456789abcdef" for c in h) for h in want[1:3]):
        raise Refused(f"cannot verify agy review gate {path}: {hooks_json} has a missing or malformed --expect "
                      f"hash list. {sync_hint}")
    for target, expected in ((path, want[1]), (os.path.join(hooks_dir, "pr_create_parser.py"), want[2])):
        try:
            with open(target, "rb") as f:
                actual = hashlib.sha256(f.read()).hexdigest()
        except OSError as e:
            raise Refused(f"cannot verify agy review gate {target} against {hooks_json}: {e}. {sync_hint}") from None
        if actual != expected:
            raise Refused(f"agy review gate {target} does not match the hash in {hooks_json} "
                          f"(the plugin file may have been edited). {sync_hint}")
    for shadow in ("pr_create_parser", "__pycache__"):
        shadow_path = os.path.join(hooks_dir, shadow)
        if os.path.exists(shadow_path):
            raise Refused(f"agy review gate refused: unexpected {shadow_path} could shadow a verified plugin "
                          f"module. {sync_hint}")


def too_old_hint(kind):
    if kind == "cloud":
        return "the default branch's gate is older than this script; merge the gate change first"
    return "re-run the sync"


def load_gate(cwd=None):
    """The trusted review gate module (gate_paths). A gate that exists but cannot load, or lacks the proof stamp,
    refuses: the next candidate is never tried."""
    path, kind = find_gate(cwd or os.getcwd())
    if kind == "agy":
        verify_agy_gate(path)
    spec = importlib.util.spec_from_file_location("review_gate_for_prove", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise Refused(f"cannot load {path}: {e}") from None
    if not hasattr(module, "PROOF_STAMP_FILE"):
        raise Refused(f"{path} is too old for prove.py (no proof stamp); {too_old_hint(kind)}")
    return module


def read_text(path):
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise Refused(f"cannot read the proof list {path}: {e}") from None


def parse(text):
    """(proofs, problems): the one parser `run`, `attest` and `lint` share (NFX-208). A proof is ("command", text),
    ("guard", text) or ("manual", text), in file order; a problem is "line N: why" for a `Proof:` line that is none."""
    proofs, problems = [], []
    for number, line in enumerate(text.splitlines(), 1):
        m = PROOF_LINE.search(line)
        if not m:
            continue
        rest = m.group(1)
        if rest.startswith(MANUAL):
            step = rest[len(MANUAL):].strip()
            parts = BACKUP.split(" " + step)
            if not parts[0].strip():
                problems.append(f"line {number}: `Proof: manual:` names no step")
            elif len(parts) > 2 or (len(parts) == 2 and not parts[1].strip()):
                problems.append(f"line {number}: a manual proof takes one `Backup:` that names the backup proof")
            else:
                proofs.append(("manual", step))
            continue
        kind = "command"
        if rest.startswith(GUARD):
            kind, rest = "guard", rest[len(GUARD):].strip()
        c = COMMAND.fullmatch(rest)
        if not c or not c.group(1).strip():
            form = "`Proof: guard:` takes a `command`" if kind == "guard" else \
                "a Proof must be `command` or manual: <step>"
            problems.append(f"line {number}: {form}, got: {rest or '(nothing)'}")
            continue
        proofs.append((kind, c.group(1).strip()))
    return proofs, problems


def read_list(path):
    """(proofs, sha): the list is read once, and the hash comes from the proofs parsed from that read.
    Refused at the first problem, or when there is no command proof to run."""
    proofs, problems = parse(read_text(path))
    if problems:
        raise Refused(problems[0])
    if not any(kind in RUNS for kind, _ in proofs):
        raise Refused("the list has no `command` proof, so there is nothing to run")
    return proofs, proofs_sha(proofs)


def split(proofs):
    """(commands, manual): the texts of the proofs `run` runs (guards included) and of the manual ones."""
    return ([t for kind, t in proofs if kind in RUNS], [t for kind, t in proofs if kind == "manual"])


def proofs_sha(proofs):
    """Hash of the normalized, ordered proofs: the wording around them can change, a proof cannot."""
    body = "\n".join(f"{kind}:{text}" for kind, text in proofs)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def prepare(gate, cwd):
    """(branch, tree) for a repo on a branch, else Refused."""
    try:
        branch = gate.current_branch(cwd)
        if branch == "HEAD":
            raise Refused("HEAD is detached, and a proof stamp covers only the branch it was recorded on")
        return branch, gate.worktree_tree(cwd)
    except Refused:
        raise
    except Exception as e:
        raise Refused(f"cannot read the repo here: {e}") from None


def reusable_check(gate, cwd, tree, branch):
    """The trusted [check] command when a valid `run-check --stamp` stamp matches this tree, branch and check, else
    None (NFX-218). The match is --mark's own: the gate's trusted_check, stamp_fields and valid_stamp. Read once,
    before any proof runs, since a proof is branch code. Anything missing or failing (a gate that predates
    stamp_fields, no git_refs.py next to it, no [check]) means None: the proof runs as before. The stamp is left in
    place for --mark."""
    try:
        check, _ = gate.trusted_check(cwd)
        if check and gate.valid_stamp(cwd, gate.stamp_fields(tree, branch, check), gate.STAMP_FILE):
            return check["command"]
    except Exception:
        pass
    return None


def check_command(gate, cwd):
    """The trusted [check] command, stamped or not, else None: the project's own suite passes at the base whenever
    the change adds tests, so --base never runs it there."""
    try:
        check, _ = gate.trusted_check(cwd)
        return check["command"] if check else None
    except Exception:
        return None


def git(args, cwd, extra=None):
    """git's stdout; Refused on a non-zero exit. Every GIT_* variable is dropped (gate_paths), then `extra` added."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", **(extra or {})})
    try:
        r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise Refused(f"git {args[0]} failed: {e}") from None
    if r.returncode != 0:
        raise Refused(f"git {args[0]} failed: {r.stderr.strip() or r.returncode}")
    return r.stdout


def base_commit(cwd, base):
    """The full hash of --base, else Refused before any proof runs."""
    try:
        return git(["rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"], cwd).strip()
    except Refused:
        raise Refused(f"--base {base} is not a commit here") from None


def base_statuses(gate, cwd, base, tree, branch, proofs, suite, timeout):
    """[(n, flagged, text)] for each proof `run` runs, numbered as `run` numbers them, from a temporary clone that is
    always removed (NFX-213). First every proof runs at the base: a pass there is flagged, a timeout is unknown, never
    good. Then each proof that failed at the base runs again on a clean copy of the change tree, the control
    testfail.py also runs: a pass there means it fails without the change, a fail means the clone lacks something git
    does not track, so the result is unknown. A guard is never checked, and the [check] command (`suite`) gets no
    base run."""
    scratch = tempfile.mkdtemp(prefix="prove-base-")
    copy = os.path.join(scratch, "repo")
    log = os.path.join(scratch, "proof.log")
    nohooks = os.path.join(scratch, "no-hooks")
    os.mkdir(nohooks)
    # The user's global core.hooksPath would run its hooks on the clone's checkouts; point it at an empty folder.
    quiet = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": nohooks}
    check = {"timeout": timeout, "source": "proof list"}
    statuses, controls = {}, []
    try:
        head = git(["rev-parse", "HEAD"], cwd).strip()
        git(["clone", "-q", "--shared", "--no-checkout", git(["rev-parse", "--show-toplevel"], cwd).strip(), copy],
            scratch, quiet)

        def fresh_checkout(commit, files=None):
            git(["checkout", "-q", "-f", "-B", branch, commit], copy, quiet)
            if files:
                git(["read-tree", "--reset", "-u", files], copy, quiet)
            git(["clean", "-q", "-ffdx"], copy, quiet)

        for i, (kind, command) in enumerate([p for p in proofs if p[0] in RUNS], 1):
            if kind == "guard":
                statuses[i] = (False, "guard, not checked at the base")
            elif command == suite:
                statuses[i] = (False, "the [check] command, not run at the base")
            else:
                print(f"  base run, proof {i}", flush=True)
                fresh_checkout(base)
                ok, why = gate.run_check(copy, {**check, "command": command}, log)
                if ok:
                    statuses[i] = (True, f"passes without the change (base {base[:10]})")
                elif why.startswith("did not finish"):
                    statuses[i] = (False, f"unknown, the base run {why}")
                else:
                    controls.append((i, command))
        for i, command in controls:
            print(f"  control run, proof {i}: on a clean clone of the change", flush=True)
            fresh_checkout(head, tree)
            ok, why = gate.run_check(copy, {**check, "command": command}, log)
            if ok:
                statuses[i] = (False, "fails without the change")
            elif why.startswith("did not finish"):
                statuses[i] = (False, f"unknown, the run on a clean clone of the change {why}")
            else:
                statuses[i] = (False, f"unknown, it also fails in a clean clone of the change ({why}); it may need "
                               "files git does not track")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return [(i, *statuses[i]) for i in sorted(statuses)]


def report_base(gate, cwd, base, tree, branch, proofs, timeout):
    """Print the base check after the stamp: it is advice for the reviewer, so a clone that cannot be made never
    takes the stamp of proofs that passed."""
    print(f"Base check against {base[:10]}:", flush=True)
    try:
        statuses = base_statuses(gate, cwd, base, tree, branch, proofs, check_command(gate, cwd), timeout)
    except Exception as e:
        print(f"  base check not run: {e}")
        return
    for i, _, text in statuses:
        print(f"  proof {i}: {text}")
    flagged = [f"proof {i}" for i, bad, _ in statuses if bad]
    if flagged:
        print(f"{len(flagged)} proof(s) pass without the change: {', '.join(flagged)}. Each is a `med` finding: "
              "rewrite it so it fails at the base, or mark a no-regression check `guard:`.")
    else:
        print("No proof passes without the change.")


def run(args):
    cwd = os.getcwd()
    gate = load_gate()
    name = gate.PROOF_STAMP_FILE
    try:
        gate.remove_stamp(cwd, name)  # first: any refusal below must not leave an older stamp behind
    except Exception as e:
        raise Refused(f"cannot prepare the proofs: {e}") from None
    if args.timeout < 1:
        raise Refused("--timeout must be at least 1 second")
    proofs, sha = read_list(args.list)
    branch, tree = prepare(gate, cwd)
    base = None if args.base is None else base_commit(cwd, args.base)
    try:
        key = gate.stamp_key(cwd)  # before the run, so a key a proof replaces never signs the stamp
    except Exception as e:
        raise Refused(f"cannot read the stamp key: {e}") from None
    commands, manual = split(proofs)
    stamped = reusable_check(gate, cwd, tree, branch)
    try:
        for i, command in enumerate(commands, 1):
            print(f"Proof {i}/{len(commands)}: {command}", flush=True)
            if command == stamped:
                print(f"proof {i}: reused check stamp {tree[:10]}", flush=True)
                continue
            check = {"command": command, "timeout": args.timeout, "source": "proof list"}
            ok, why = gate.run_verified(cwd, check, tree)
            if not ok:
                gate.remove_stamp(cwd, name)
                raise Refused(f"proof {i} ({command}) {why}. No stamp recorded.")
        gate.remove_stamp(cwd, name)  # a proof is branch code: it may have written a stamp itself
        fields = {"kind": KIND, "tree": tree, "branch": branch, "list_sha": sha, "passed": len(commands),
                  "total": len(commands), "manual": len(manual), "exit": 0,
                  "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
        gate.write_stamp(cwd, fields, key, name)
    except Refused:
        raise
    except Exception as e:
        with contextlib.suppress(Exception):
            gate.remove_stamp(cwd, name)
        raise Refused(f"the proofs could not finish: {e}. No stamp recorded.") from None
    note = f", {len(manual)} manual not run" if manual else ""
    print(f"{len(commands)}/{len(commands)} proofs passed{note}. Stamp recorded for tree {tree[:10]}: "
          "`prove.py attest` prints the AC-Proof line until a file or the list changes.")
    if base:
        report_base(gate, cwd, base, tree, branch, proofs, args.timeout)
    return 0


def attest(args):
    cwd = os.getcwd()
    gate = load_gate()
    proofs, sha = read_list(args.list)
    branch, tree = prepare(gate, cwd)
    stamp = gate.valid_stamp(cwd, {"kind": KIND, "tree": tree, "branch": branch, "list_sha": sha},
                             gate.PROOF_STAMP_FILE)
    if not stamp:
        raise Refused("no valid proof stamp for this tree, branch and proof list; run `prove.py run` again")
    commands, manual = split(proofs)
    if not (stamp.get("passed") == stamp.get("total") == len(commands) > 0) or stamp.get("manual") != len(manual):
        raise Refused("the proof stamp does not match the list; run `prove.py run` again")
    print(f"AC-Proof: {len(commands)}/{len(commands)} manual:{len(manual)} {tree} {sha}")
    for text in commands:
        print(f"  proof: {text}")
    for text in manual:
        step, *backup = BACKUP.split(text, 1)
        print(f"  manual: {step} (not run)")
        if backup:
            print(f"    backup: {backup[0]}")
    return 0


def lint(args):
    """Every `Proof:` line `run` would refuse, before any code exists (NFX-208). Needs no repo and runs nothing.
    A list with no command proof passes: a ticket whose proofs are all manual is valid."""
    proofs, problems = parse(read_text(args.list))
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} Proof: line(s) prove.py cannot read; write each as `command`, guard: `command` or manual: <step>",
              file=sys.stderr)
        return 2
    print(f"{len(proofs)} Proof: line(s) OK")
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # ticket text echoed to a cp1252 pipe on Windows must not crash lint
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="prove.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="run the proofs and write the stamp")
    p.add_argument("list")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per proof (default 600)")
    p.add_argument("--base", help="also run each command proof in a clone at this commit and flag the ones that "
                   "pass there (NFX-213)")
    p.set_defaults(fn=run)
    p = sub.add_parser("attest", help="print the AC-Proof line for a valid stamp")
    p.add_argument("list")
    p.set_defaults(fn=attest)
    p = sub.add_parser("lint", help="list the Proof: lines run would refuse; runs nothing")
    p.add_argument("list")
    p.set_defaults(fn=lint)
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except Refused as e:
        return fail(e)


if __name__ == "__main__":
    sys.exit(main())
