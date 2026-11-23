---
name: delete-comments
description: Clean a repo's existing comments the same way every time. Sorts each comment as Tautology, Stale, Excuse or Decision record, acts on it by kind, and proves that only comments changed. Use when asked to clean up, prune or delete comments in a repo or folder, or on a comment cleanup ticket.
argument-hint: "[path] (default: the repo)"
---

Clean the comments under: $ARGUMENTS (default: the git top level).

This runs from Cesar's Mac. The script is `~/.claude/skills/review-loop/scripts/comment_check.py` (`python` on Windows). Work on a ticket branch, as for any ticket.

## 1. Inventory

```bash
python3 -B ~/.claude/skills/review-loop/scripts/comment_check.py --all <path> [<path> ...]
```

One line per comment: `file:line form verdict text`. Form is `docstring`, `inline`, `line` or `shell`. Verdict is `keep:<rule>` or `flag:<kind>`. The last line counts the forms and the lines the cleanup could shed; with several paths, each path gets its own count line and the last line is the total.

The keep list lives only in the script's `KEEP`: print it with `comment_check.py --keep-list`. Never copy it here or into the table. A `keep:` item stays as it is. Leave every module docstring in a file that reads `__doc__` whole: argparse builds the CLI from it.

Save the summary line: it is the "before" count.

## 2. Classify

Read each `flag:` item in its code. Put it in one table:

| file:line | kind | action | reason |
|---|---|---|---|

Kind is one of the four below. The reason is one short line a reviewer can check. A wrong Stale call deletes a true comment, so name what makes it stale (the renamed function, the removed flag).

Read the code before you judge: a comment that looks like a Tautology can carry a constraint the code does not show. That one is a Decision record.

Three flags come with a first guess:

- `flag:test-docstring` (a docstring on a `test_*` method or a `Test*` class's method, in a test file): a Tautology when it only repeats the test name; kept when it says what the name doesn't (what broke, why the test exists).
- `flag:ticket-tag` (a bare ticket key such as `# NFX-100` or `# NFX-114: moved`): Stale, since git log holds the history. A key with its reason is `ticket-why` and stays.
- `flag:banner` (`# --- gh ---`): a Tautology.

## 3. Act on each kind

- **Tautology** (says what the next line already says): delete it.
- **Stale** (no longer true of the code): delete it.
- **Excuse** (covers a hack, a workaround, or a suppression with no reason): replace it with a one-line reason plus a new ticket key; the fix ticket deletes it. File the tickets first, then edit: one ticket per root cause, not per comment, labels `needs-triage` and `agent-filed`, in the project's own tracker (`docs/agents/issue-tracker.md`). Never just delete a tool directive (`# type: ignore`, `# noqa`, `# pragma: no cover`, `# shellcheck`, `@Suppress`): it changes what a tool does.
- **Decision record** (why the code is this way): becomes an ADR when it is hard to reverse, surprising and a real trade-off, all three; otherwise cut it to the reason or delete it. The code keeps at most a pointer with a one-line reason.

ADRs follow the `domain-modeling` skill's ADR-FORMAT: `docs/adr/NNNN-slug.md`, the next free number. A pointer reads `ADR-NNNN` (or `ADR NNNN` where the project writes it so); in NousFX files under `claude/` and `plugins/` (they are copied into other projects) it reads `NousFX ADR-NNNN`. Anything below the three-part bar is not an ADR.

## 4. Verify

```bash
python3 -B ~/.claude/skills/review-loop/scripts/comment_check.py verify [<base>]
```

It compares with `git merge-base <base> HEAD` and prints that commit, so merges that land on `<base>` later never count; with no `<base>`, review-loop's base (origin/HEAD, else origin/main), so a ticket's `Proof:` line runs as written. It checks every changed Python file's AST with docstrings stripped, the module docstring and every docstring read as `name.__doc__` in each file that reads `__doc__` (the `--help` text; it reads scripts and never runs them), each changed shell file's text outside comments and `bash -n`, shebang and encoding lines, and every ADR pointer. Exit 2 names each file that changed more than comments: undo that edit and run it again. In a shell file, delete only the comment and keep the blank lines and spaces around it: verify counts them as code, since a heredoc or an escaped space can hold them. Kotlin, Dart, TypeScript and JavaScript files are compared token by token outside comments, and a difference names its line. In TSX or JSX delete the whole `{/* ... */}` expression: empty braces left behind are code, and a bare `//` between tags is rendered text. In Dart a bare trailing `//` is a `dart format` line-break marker, not a comment: keep it. Keep line breaks and directive comments (`/// <reference>`, `// @dart=`, `@jsx`, `@ts-`): verify counts them as code. Exit 0 is not the end: then run the project's test suite. Other languages rely on the suite alone.

## 5. Report

Run step 1 again for the "after" count. Save the table with the before and after counts outside the repo, at `~/Releases/<project>/reports/<date>-<KEY>/comments.md`, and paste it in the PR body.

## Batch rule

At most ~800 deleted lines per PR (`verify` prints the count and warns above it). A larger cleanup splits by folder into several PRs, one ticket each.
