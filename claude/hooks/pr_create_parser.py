"""Find the `gh pr create` calls in a shell command and the directory each runs in.

Shared by the review gate and the Via gate, which carry identical copies of this file.
create_dirs() parses the shell text instead of matching it, so quoted text and heredocs that only
mention the command pass, while wrappers (`if`, `timeout`, `eval`, `bash -c`, `$(...)`) are seen.
It is a guardrail against mistakes, not a sandbox. Known limits: script files, `gh api`, shell
functions, `find -exec`, `cmd /c`, `iex`, `trap` and `ssh host "..."` are not looked into.
"""
import collections
import os
import re
import subprocess

Word = collections.namedtuple("Word", "text dynamic")  # dynamic: holds a $ or backtick expansion

KEYWORDS = {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "coproc"}
WRAPPERS = {"env", "command", "builtin", "exec", "time", "nohup", "timeout", "gtimeout", "nice", "ionice",
            "sudo", "doas", "xargs", "stdbuf", "setsid", "caffeinate", "unbuffer", "flock", "watch",
            "noglob", "nocorrect", "direnv", "op", "uv", "mise"}
PRINTERS = {"echo", "printf"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish", "pwsh", "powershell"}
CD_COMMANDS = {"cd", "pushd", "popd", "chdir", "set-location", "sl", "pop-location"}
BRANCH_KEYWORDS = {"then", "elif", "else", "do"}
ASSIGNMENT = re.compile(r"^\w+\+?=")
SHELL_C_FLAG = re.compile(r"^(?:-[a-zA-Z]*c[a-zA-Z]*|-command)$", re.I)
SHELL_S_FLAG = re.compile(r"^-[a-zA-Z]*s[a-zA-Z]*$")
CD_MENTION = re.compile(r"\b(?:cd|pushd|popd|chdir|set-location|sl|pop-location)\b", re.I)
HEREDOC_DELIM = re.compile(r"""'([^']*)'|"([^"]*)"|(\\?)([^\s;&|()<>]+)""")
TOPLEVEL = re.compile(r"\$\(\s*git rev-parse --show-toplevel\s*\)|`\s*git rev-parse --show-toplevel\s*`")


def read_heredoc(s, i):
    """Read the `<<` operator at i. Return ((delim, literal, strip) or None, index after it)."""
    i += 2
    strip = s[i:i + 1] == "-"
    i += strip
    while s[i:i + 1] in (" ", "\t"):
        i += 1
    m = HEREDOC_DELIM.match(s, i)
    if not m:
        return None, i
    literal = m.group(1) is not None or m.group(2) is not None or bool(m.group(3))
    delim = next(g for g in (m.group(1), m.group(2), m.group(4)) if g is not None)
    return (delim, literal, strip), m.end()


def close_paren(s, i):
    """Index of the ) matching a ( opened just before i. ValueError if unbalanced (the gate then
    falls back to the raw-text check, so text it cannot read never hides a create)."""
    depth, heredocs = 1, []
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 1
        elif c == "'":
            i = close_ansi_quote(s, i + 1) if s[i - 1] == "$" else s.find("'", i + 1)
            if i < 0:
                raise ValueError("unclosed quote")
        elif c == '"':
            i = close_double_quote(s, i + 1)
        elif c == "#" and s[i - 1] in " \t\n;&|(":
            j = s.find("\n", i)
            i = len(s) if j < 0 else j - 1
        elif c == "<" and s[i + 1:i + 2] == "<" and s[i + 2:i + 3] != "<":
            heredoc, j = read_heredoc(s, i)
            if heredoc:
                heredocs.append(heredoc)
            i = j - 1
        elif c == "\n":
            for delim, _, strip in heredocs:
                found = heredoc_end(s, i + 1, delim, strip)
                if found:
                    i = found[1]
            heredocs = []
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unclosed $(")


def close_ansi_quote(s, i):
    """Index of the ' closing a $' opened just before i, where \\' does not close it. ValueError if unbalanced."""
    while i < len(s):
        if s[i] == "\\":
            i += 2
        elif s[i] == "'":
            return i
        else:
            i += 1
    raise ValueError("unclosed quote")


def close_double_quote(s, i):
    """Index of the " closing one opened just before i. ValueError if unbalanced."""
    while i < len(s):
        if s[i] == "\\":
            i += 2
        elif s[i] == '"':
            return i
        elif s.startswith("$(", i):
            i = close_paren(s, i + 2) + 1
        elif s[i] == "`":
            i = close_backtick(s, i + 1) + 1
        else:
            i += 1
    raise ValueError("unclosed quote")


def close_backtick(s, i):
    """Index of the backtick closing one opened just before i. ValueError if unbalanced."""
    while i < len(s) and s[i] != "`":
        i += 2 if s[i] == "\\" else 1
    if i >= len(s):
        raise ValueError("unclosed backtick")
    return i


def heredoc_end(s, start, delim, strip):
    """(body end, terminator line end) for a heredoc body starting at start, or None if unterminated."""
    k = start
    while k <= len(s):
        e = s.find("\n", k)
        e = len(s) if e < 0 else e
        line = s[k:e]
        if (line.lstrip("\t") if strip else line) == delim:
            return k, e
        k = e + 1
    return None


def parse(s, ps=False):
    """Split shell text into events: a list of Words per simple command, "(" and ")" around subshells,
    and the separators between commands (; & | && ||, a newline counts as ;).

    Substitutions come out before the command holding them, since they run first. ps: PowerShell,
    where backtick is the escape and backslash is a plain path character.
    """
    events, words, heredocs = [], [], []
    buf, quoted, dynamic, in_dq, drop_next = [], False, False, False, False
    escape = "`" if ps else "\\"

    def end_word():
        nonlocal buf, quoted, dynamic, drop_next
        if buf or quoted:
            if drop_next:  # a redirect target, not an argument
                drop_next = False
            else:
                words.append(Word("".join(buf), dynamic))
        buf, quoted, dynamic = [], False, False

    def end_command():
        nonlocal words, drop_next
        end_word()
        drop_next = False
        if words:
            events.append(words)
        words = []

    def nested(inner):
        events.extend(["(", *parse(inner, ps), ")"])

    def substitutions(body):
        k = 0
        while k < len(body):
            if body[k] == "\\":
                k += 2
            elif body.startswith("$(", k):
                j = close_paren(body, k + 2)
                nested(body[k + 2:j])
                k = j + 1
            elif body[k] == "`":
                j = close_backtick(body, k + 1)
                nested(body[k + 1:j])
                k = j + 1
            else:
                k += 1

    i, n = 0, len(s)
    while i < n:
        c = s[i]
        nxt = s[i + 1] if i + 1 < n else ""
        if c == escape:
            if nxt == "\n":
                i += 2
            elif in_dq and not ps and nxt not in '$`"\\':
                buf.append(c)
                i += 1
            else:
                buf.append(nxt)
                i += 2
        elif c == "'" and not in_dq:
            j = s.find("'", i + 1)
            if j < 0:
                raise ValueError("unclosed quote")
            buf.append(s[i + 1:j])
            quoted = True
            i = j + 1
        elif c == '"':
            in_dq = not in_dq
            quoted = True
            i += 1
        elif c == "`":
            j = close_backtick(s, i + 1)
            nested(s[i + 1:j].replace("\\`", "`"))
            buf.append(s[i:j + 1])
            dynamic = True
            i = j + 1
        elif c == "$" and nxt == "(":
            j = close_paren(s, i + 2)
            nested(s[i + 2:j])
            buf.append(s[i:j + 1])
            dynamic = True
            i = j + 1
        elif c == "$" and nxt == "'" and not in_dq and not ps:
            j = close_ansi_quote(s, i + 2)
            buf.append(s[i + 2:j].replace("\\'", "'"))
            quoted = True
            i = j + 1
        elif c == "$" and nxt == '"' and not in_dq:
            i += 1  # $"...": the quote branch takes it from here
        elif c == "$":
            if nxt and (nxt.isalnum() or nxt in "_{@*#?!-"):
                dynamic = True
            buf.append(c)
            i += 1
        elif in_dq:
            buf.append(c)
            i += 1
        elif c == "#" and not buf and not quoted:
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif c == "<" and nxt == "<" and s[i + 2:i + 3] != "<":
            end_word()
            heredoc, i = read_heredoc(s, i)
            if heredoc:
                heredocs.append(heredoc)
        elif c in "<>" or (c == "&" and nxt == ">"):
            if "".join(buf).isdigit() and not quoted:  # the 2 in 2>&1
                buf = []
            end_word()
            i += 1
            while s[i:i + 1] and s[i:i + 1] in "<>&|":
                i += 1
            drop_next = True
        elif c in ";&|()\n":
            end_command()
            if c in "()":
                events.append(c)
            elif c in "&|" and nxt == c:
                events.append(c + c)
                i += 1
            elif c != "\n":
                events.append(c)
            elif not (events and events[-1] in ("&&", "||", "&", "|")):  # those carry over a newline
                events.append(";")
            i += 1
            if c == "\n":
                for delim, literal, strip in heredocs:
                    found = heredoc_end(s, i, delim, strip)
                    if found:  # unterminated: leave the lines to be read as commands
                        if not literal:
                            substitutions(s[i:found[0]])
                        i = found[1] + 1
                heredocs = []
        elif c.isspace():
            end_word()
            i += 1
        else:
            buf.append(c)
            i += 1
    if in_dq:
        raise ValueError("unclosed quote")
    end_command()
    return events


def command_name(text):
    name = re.split(r"[/\\]", text)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def is_pr_create(args):
    """Whether the arguments after `gh` mean `pr create`. An expansion could be anything, so it counts."""
    want = ["pr", {"create", "new"}]
    after_flag = False
    for w in args:
        if after_flag and w.dynamic:  # `-R "$REPO"`: the flag's value
            after_flag = False
        elif w.text.startswith("-"):  # also `--repo="$R"`
            after_flag = True
        elif w.dynamic:
            return True
        elif w.text == want[0] or (isinstance(want[0], set) and w.text in want[0]):
            want.pop(0)
            if not want:
                return True
            after_flag = False
        elif after_flag:  # the flag's value
            after_flag = False
        else:
            return False
    return False


def change_dir(args, cwd):
    """Directory after a cd: the target, cwd if it does not exist, None if it cannot be worked out."""
    args = [a for a in args if not (a.text.startswith("-") and len(a.text) > 1)]
    if not args:
        return os.path.expanduser("~")
    target = args[0].text
    if target == "-":
        return None
    if args[0].dynamic:
        if TOPLEVEL.search(target):
            if cwd is None:
                return None
            try:
                root = git_toplevel(cwd)
            except Exception:
                return None
            target = TOPLEVEL.sub(lambda _: root, target)
        target = os.path.expandvars(target)
        if "$" in target or "`" in target:
            return None
    target = os.path.expanduser(target)
    if not os.path.isabs(target) and cwd is None:
        return None
    new = os.path.normpath(os.path.join(cwd or "", target))
    return new if os.path.isdir(new) else cwd


def mentions_create(text):
    """Raw-text check, only for text run in a way the parser cannot follow (`$CMD`, piped into a shell)."""
    for part in re.split(r"[;&|\n]", text.replace("\\\n", " ")):
        at = 0
        for word in (r"\bgh\b", r"\bpr\b", r"\b(?:create|new)\b"):
            m = re.compile(word).search(part, at)
            if not m:
                break
            at = m.end()
        else:
            return True
    return False


def create_dirs(command, cwd, ps=False):
    """The directory each `gh pr create` in command runs in. None: it cannot be worked out."""
    found = []

    def opaque(cwd):
        if mentions_create(command):
            found.append(cwd)

    def walk(events, cwd):
        stack = []
        chained = False  # the current command runs only if an && or || before it allowed it
        unsure = False  # a cd that may not have run: true only to the end of this && chain
        case_depth = 0
        for k, event in enumerate(events):
            if event == "(":
                stack.append((cwd, chained, unsure))
                chained = unsure = False
            elif event == ")":
                if stack:
                    cwd, chained, unsure = stack.pop()
            elif event in ("&&", "||"):
                chained = True
            elif isinstance(event, str):
                if unsure:
                    cwd = None
                chained = unsure = False
            else:
                head = next((w.text for w in event if w.text not in KEYWORDS), "")
                case_depth = max(0, case_depth + (head == "case") - (head == "esac"))
                before = cwd
                cwd = visit(event, cwd)
                if cwd != before:
                    if k + 1 < len(events) and events[k + 1] in ("&", "|"):  # ran in a subshell
                        cwd = before
                    elif chained or case_depth > 0 or event[0].text in BRANCH_KEYWORDS:
                        unsure = True
        return None if unsure else cwd

    def visit(words, cwd):
        """Handle one simple command; return the directory the next one runs in."""
        while True:
            while words and (words[0].text in KEYWORDS or ASSIGNMENT.match(words[0].text)):
                words = words[1:]
            if not words:
                return cwd
            if command_name(words[0].text) not in WRAPPERS:
                break
            rest = words[1:]
            if command_name(words[0].text) == "command" and rest and rest[0].text in ("-v", "-V"):
                return cwd  # only looks the name up
            # A cd counts only as the wrapped command itself: in `time make cd` it is an argument.
            first = next((k for k, w in enumerate(rest) if not w.text.startswith("-")), None)
            words = next((rest[k:] for k, w in enumerate(rest)
                          if w.dynamic or command_name(w.text) in {"gh", "eval"} | SHELLS
                          or (k == first and command_name(w.text) in CD_COMMANDS)), [])
        name, args = command_name(words[0].text), words[1:]
        if name == "gh":
            if is_pr_create(args):
                found.append(cwd)
        elif name == "eval":
            if any(a.dynamic for a in args):
                opaque(cwd)
            return walk(parse(" ".join(a.text for a in args), ps), cwd)
        elif name in SHELLS:
            for k, a in enumerate(args):
                if SHELL_C_FLAG.match(a.text) and k + 1 < len(args):
                    script = [w for w in args[k + 1:] if w.text != "--"]
                    is_ps = name in ("pwsh", "powershell")  # takes the rest as the script; others one word
                    script = script if is_ps else script[:1]
                    if any(w.dynamic for w in script):
                        opaque(cwd)
                    walk(parse(" ".join(w.text for w in script), is_ps), cwd)
                    return cwd
            if all(a.text.startswith("-") for a in args) or any(SHELL_S_FLAG.match(a.text) for a in args):
                opaque(cwd)  # reads its script from stdin
        elif name in ("popd", "pop-location"):
            return None
        elif name in CD_COMMANDS:
            return change_dir(args, cwd)
        elif words[0].dynamic:  # `$CMD ...`: unknown command. `$VENV/bin/x` names a program.
            if "/" not in words[0].text:
                opaque(cwd)
            if is_pr_create(args):
                found.append(cwd)
            carried_shell(args, cwd)
        elif name not in PRINTERS:
            carried_shell(args, cwd)
        return cwd

    def carried_shell(args, cwd):
        """A runner this gate does not know, such as `docker exec c sh -c "..."`."""
        for k, a in enumerate(args[:-1]):
            if command_name(a.text) in SHELLS and SHELL_C_FLAG.match(args[k + 1].text):
                visit(args[k:], cwd)
                return

    walk(parse(command, ps), cwd)
    return found


def git_toplevel(cwd):
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True,
                       text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()
