# Review gate for every tool

My review gate asks one question: was this code reviewed before a PR opens? Each tool asks it in its own way.

```
Claude Code hook ──┐
pre-push hook   ───┼──► review-gate.py (evaluate) ──► exit 0 allow / exit 2 block
CI on the PR    ───┘
```

## The plain entry

```
python3 -B claude/hooks/review-gate.py check --cwd DIR --command "gh pr create"
```

- Exit 0 allows. Exit 2 blocks, with the reason on stderr.
- It judges the repo in `DIR`: branch named `<prefix>/<KEY>`, and a review mark for this exact code.
- The prefix is `[branch] prefix` in `~/.config/nousfx/local.toml`, the file sync reads (home from the passwd record, `USERPROFILE` on Windows; sync's `--state-home` does not move it). With a prefix, only `<prefix>/<KEY>` passes. With none (no file, unreadable, invalid, or Python < 3.11 without `tomllib`: cloud, CI, a fresh install), any `[a-z0-9-]+/<KEY>` passes. A broken config never blocks (NFX-119). The exact-prefix check is advisory: `local.toml` is a plain file that sync does not deny to Claude's file tools, and CI checks only the shape.
- Bad input blocks. A missing option, a missing folder or a folder that is not a git repo all exit 2.
- `--allow-non-repo` is for agy. A folder that is not a repo is then judged by the command alone, so `cd <repo> && gh pr create` is still checked in that repo. It only covers git's own "not a git repository" answer: a missing git, a missing folder and a pushed commit (`--tree`) still block.
- `--tool` names the parser: `Bash` (default), `PowerShell`, or an MCP tool name holding `create_pull_request`, as the Claude hook passes them.
- The Claude hook parses its JSON payload and then calls the same `evaluate()` function. Behavior of the hook does not change.
- `--budget SECONDS` bounds every git call the run makes (an adapter passes the time it has left).

### Fail-closed hook (NFX-102)

A PreToolUse hook that times out or exits 0 lets the call through, so the gate never passes by being slow or confused.

- **Budget.** Sync writes one budget into every gate hook: `--budget 55` in the command, `timeout` 60 (the home hook, project-setup's `assets/settings.json` and agy's `hooks.json`). Every git call gets `min(30 s, time left)`. Out of time blocks with "gate timed out; retry or review at Tier 3". A hook config with no `--budget` uses 25 s, under the old 30 s timeout. A huge repo that needs longer blocks, it never passes.
- **One tree per run.** `evaluate()` hashes each folder's working tree and HEAD once, however many checks need them.
- **Payload.** A payload it cannot read (bad JSON, not an object, a non-string `command` or `cwd`) blocks, unless `tool_name` names a tool that cannot open a PR (anything but `Bash`, `PowerShell` and a create-PR MCP tool).
- **API creates.** `gh api`, `curl` and `wget` to a PR collection (`repos/<owner>/<repo>/pulls` or `repositories/<id>/pulls`, after dot segments; a curl glob counts) block when they may create. Fail closed: any method but GET or HEAD (`-X`, `--method`, `--request`, set at run time too), or any data (`-f`, `-F`, `--field`, `--raw-field`, `--input`; curl `-d`, `--data*`, `--json`, `--form`, `-T`; wget `--post-*`, `--body-*`). Only `gh api` with an explicit `--method GET` may send fields (a query); gh's value flags are skipped exactly, so a `-XGET` inside another flag's value does not count. curl reads with data (`-G -d`) block: use `gh api --method GET`. Any of them holding `createPullRequest` (a GraphQL create) blocks too, as does an API create in code the parser cannot read (`eval`, a pipe into a shell, `$CURL ...`). Abbreviated long flags (`--meth=POST`, `--js`) count. The gate cannot read such a body, so these block even after a review, and in any folder (the call names its repo). A GET passes, including `gh api --method GET` with `-f` fields; a gh GET written with `-f` fields and no method is read as a create, and the message names `--method GET`.
- Known limits (as in the parser header): `gh alias`, `python -c` and SDK calls, other HTTP clients (`Invoke-RestMethod`, httpie, and `curl` where PowerShell 5.1 aliases it to `Invoke-WebRequest`), an API URL or GraphQL query built at run time or read from a file (`-F query=@q.graphql`, `--input`), shell functions and `ssh`.
- Marks stay per worktree, in the git dir, keyed by tree hash and branch name. They do not move.
- A mark also names the branch it was recorded on, and covers only that branch. After a fast-forward or squash merge, main's tree equals the last marked tree, so without this a new branch with no commits would pass. A branch renamed after the mark needs the review loop again: the gate cannot tell a rename from a new branch cut from merged main, so it offers no re-mark shortcut. `--mark` refuses on a detached HEAD, and a create from a detached HEAD blocks. `attest` applies the same branch rule. The rule is local and by name only: a branch recreated under the marked name still matches, and the CI attestation (`Review-Gate: <tree>`) stays tree-only. An old mark with no branch blocks with a message to re-run the review loop.

## The check command

A mark now means the tests passed, not only that a reviewer was satisfied. `--mark --findings N` runs the project's check command itself.

```
--mark --findings N ──► read [check] command ──► run it ──► tree unchanged? ──► write the mark (+ command, exit 0)
                         from the default branch   │ fail, timeout, missing, edits files
                                                   └──► refuse, and remove any older mark
```

- Config: `.nousfx/project.toml` in the repo.
  ```toml
  [check]
  command = "python3 -B scripts/run_checks.py"
  timeout = 1800   # seconds, optional
  ```
- The gate never adds `--fail-fast` to `scripts/run_checks.py`, so a stamp or mark always means a full pass.
- The command runs through the shell at the repo top level, with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and `GIT_COMMON_DIR` removed and `PYTHONDONTWRITEBYTECODE=1`. Its output goes to `claude-review-check.log` in the git dir. A refusal prints the last 40 lines and the path.
- Where it is read from: `origin/HEAD`, else `origin/main`, else `origin/master` (one rule, `git_refs.py`, shared with `tier.py`; locally `origin/HEAD` is trusted as `git remote set-head origin -a` set it, and CI is the control, so the rule is not limited to main/master and `develop`/`trunk` repos work). A branch cannot edit its own check to `true`. When that branch has the file, it wins, even if the file has no usable `[check]` (then the mark is refused). Only when the default branch has no `.nousfx/project.toml` at all (the PR that adds it), or no default branch can be found, does the mark use the working-tree file, and it says so. That local fallback is a convenience; CI never uses it. The anchor is the local remote-tracking state (`origin/HEAD` counts only when it points at another `refs/remotes/origin/` ref, and nothing is fetched), so a stale clone or a shell user can still steer the local mark. CI is the real check.
- A refused mark deletes the older one, so a stale good mark cannot carry a failing run through.
- The check must not edit files. If the tree hash changes while it runs, the mark is refused.
- No check configured: the mark is refused with the lines to add. An approved `--mark --skip "<reason>"` still works locally and runs no check; CI still runs it.
- The mark stores `{command, exit, source}`. `check`, `attest` and the pre-push hook treat a clean mark with no passing `check` result as no review. An old mark from before this change blocks, so re-run the review loop.
- The agent still reports only the findings count. It cannot report a pass the gate did not see.
- `sync --project` prints a note when the repo has no check command. It never invents one; the `project-setup` skill proposes one for the stack and asks.

### Pass stamp (NFX-97)

The review loop's last test run and `--mark` used to run the same suite twice. Now the last run goes through the gate and leaves a stamp that `--mark` trusts.

```
run-check --stamp ──► trusted check (as --mark) ──► pass, tree unchanged? ──► write signed stamp
                                                    │ fail, timeout, edits files, detached
                                                    └──► refuse, remove any stamp
--mark --findings N ──► stamp MAC valid and tree, branch, command, source match? ──► yes: no run
                                                                                 └─► no: run the check
```

- `review-gate.py run-check --stamp` reads the check like `--mark` (default branch first), streams its output, and writes `claude-review-check-pass` in the git dir: `{tree, branch, command, source, timeout, exit, time, mac}`.
- The MAC is HMAC-SHA256 over the other fields, with a random key in `claude-review-stamp-key` in the git dir (mode 0600, created on first use). A stamp written by hand or by a test does not verify. The key is read before the run, and both the stamp run and `--mark` remove any stamp the check command wrote, so a failing check cannot leave one for the next mark.
- In a review round with no fixes, `run-check --stamp` is the only full suite run. A round with fixes runs the suite once after the last fix, and the delta round and the post-Argon fix add none (NFX-138).
- review-loop waits for `run-check --stamp` to exit before `--mark`; a mark that starts first finds no stamp and runs the check.
- `--mark` deletes the stamp when it reaches the check, used or not, so one stamp serves one mark. A mark refused before that (a bad `--tier`) leaves it for the next try.
- Any changed, added or removed file that git does not ignore changes the tree, so the stamp no longer matches and `--mark` runs the check. So do another branch, a changed command or timeout on the default branch, or a different source.
- Limit: the key is readable by the same user, so this stops a stray write, not a deliberate forger. The mark itself is a plain file, and CI runs the check on every PR anyway.

### Proof stamp and the AC-Proof line (NFX-105)

`claude/skills/review-loop/scripts/prove.py` runs a ticket's `Proof:` lines and stamps them, so a `/goal` condition can name one line instead of trusting a sentence.

```
prove.py run LIST ──► every `Proof:` line parses? ──► run each proof in order (stop at the first fail)
                       │ no: refuse                     │ fail, timeout, edits files, detached HEAD: refuse, no stamp
                       └─► all pass: write signed claude-proof-pass
prove.py attest LIST ──► stamp MAC, kind, tree, branch and proof hash match? ──► AC-Proof: n/n manual:m <tree> <sha>
```

- LIST is any Markdown file holding the ticket's acceptance criteria. `Proof: \`command\`` is run at the repo top level (default 600 s each, `--timeout`). `Proof: manual: <step>` is listed and never run or counted. Any other `Proof:` line is refused, so a typo cannot drop a criterion. A list with no command proof is refused.
- The stamp is `{kind: "proof", tree, branch, list_sha, passed, total, manual, exit, time, mac}` in the git dir. It reuses the pass stamp's helpers and key (`name=` picks the file), and the `kind` field keeps the two stamps from standing in for each other. `list_sha` is the SHA-256 of the normalized, ordered proofs, not of the file: reworded criteria keep it, a changed, added, removed or reordered proof does not. The list is read once, so the hash and the run use the same bytes.
- A proof that fails, times out, edits any file git does not ignore, or runs on a detached HEAD writes no stamp and removes an older one. The key is read before the run and the stamp is removed after it, as in the pass stamp, because a proof is branch code.
- `attest` never runs anything. It prints the line plus each command and each manual step, so review-loop can compare them with the ticket's criteria. Change the tree, the branch or a proof and `attest` refuses until `run` is repeated.
- The gate hook does not read this stamp, and `review-gate.py attest` is unchanged. Put the `AC-Proof:` line in the PR body; `/goal` can compare its count and `<sha>`.
- What it proves: each command proof exited 0 on this exact tree and branch, with no file edited, for this exact list of proofs.
- What it does not prove: that a proof is a good one. The proofs come from the list the branch author supplies, CI does not re-run them, and the key is same-user readable, so a deliberate forger is not stopped (a `setsid` child can also outlive the process-group kill). A proof weakened to `true` changes the hash, and review-loop compares each proof with its criterion. Manual proofs are not run: the line shows `manual:m` so the goal can require 0.

#### `/goal` condition template (NFX-106)

Paste this as the end of a `/goal`, with N set to the turns you will allow (for example 40). The evaluator reads only the transcript, so each part is something the transcript shows:

```text
Done when the PR body or the last prove.py output shows "AC-Proof: n/n manual:0 <tree> <sha>" with n/n complete and <sha> matching the ticket's proofs,
python3 -B scripts/run_checks.py passes,
and review-loop ran "--mark --findings 0" and its output says "review recorded for tree" without "(skipped:",
at most N turns.
```

- `run_checks.py` is this repo's check command; in another repo, name that project's check command instead.
- "review recorded with 0 open" is the `--mark` call with `--findings 0` that prints `review recorded for tree <tree>`; a refused mark prints an error instead, and a `--skip` adds `(skipped: ...)`, so neither counts.
- `manual:0` means the goal fails while a manual proof is left, so a hands-off run needs command proofs only.
- Every finding then has a disposition (`fix-now`, `follow-up` or `note`, see the review-loop skill); the PR body carries the follow-up drafts and the notes.

### In CI

- `review-gate.py run-check --ref origin/<base>` reads the command from the base branch and runs it in the PR checkout. The workflow takes the gate from the base branch too, so a PR cannot change the command or the script that reads it.
- It runs after `verify-attestation`, always. A skip attestation waives the review, not the tests, so a failing test fails the job even with `skipped:` in the PR body. A local `--mark --skip` still runs no check.
- A failing test fails `review-gate / check` even when the PR body holds a valid `Review-Gate:` line. The command's output goes to the job log. The step also requires the gate's `Check passed.` line, so a base gate that only contains the word `run-check` cannot pass the job by falling through.
- A check command can write a mark itself, since it is branch code. A refused mark deletes the marker again, so nothing it wrote survives.
- If the base branch has no `run-check` or no `[check]` command, the job fails with a clear message. The PR that introduces them is merged by hand, as with attestations.
- The repo's workflow must install whatever the command needs. NousFX needs only Python.

### Limits

- The tests and any script the command calls come from the PR, including `scripts/run_checks.py` here. A branch can change a test or the runner. A human review is what stops that; this only stops it being skipped silently. The runner fails when it finds no suites.
- A timeout kills the command's whole process group (POSIX). On Windows only the shell is killed.
- `.github/workflows/review-gate.yml` also comes from the PR. Require a review on `.github/` (CODEOWNERS) if that matters.
- The check needs Python 3.11 or later (`tomllib`).

## agy adapter

```
agy run_command ──► hooks.json ──► agy_gate.py ──► review-gate.py check --tool T --allow-non-repo ──► deny / silence
```

- `harness/agy/agy_gate.py` is a PreToolUse hook on `run_command`. Sync writes it with `hooks.json` and its own copy of the gate into the nousfx plugin folder, so it needs nothing from `~/.claude`.
- It reads `toolCall.args.CommandLine`, every folder in `workspacePaths` and any folder-like argument (`Cwd`, `WorkingDirectory` ...), and asks the plain `check` entry once per folder. The one call I saw had no folder argument, so I check every folder I can find.
- The matcher is `run_command|.*create_pull_request`. A create-PR tool is checked in each folder like a PR create command, with its `body` (or `Body`) argument passed as `check --body-file` (NFX-85). I have not seen how agy names an MCP tool or its arguments, so that part is untested.
- On Windows it tries both the bash and the PowerShell parser (`--tool Bash`, `--tool PowerShell`) and blocks if either finds a create.
- To block, it prints `{"decision":"deny","reason":...}` with the gate's own message. To allow, it prints nothing, so agy's normal permission prompt still applies. An explicit `allow` might skip that prompt.
- It runs the gate with no `GIT_*` variable in the hook's own environment, so an inherited `GIT_DIR` cannot hide the repo. A workspace root that no longer exists is skipped; with none left, it denies. Only whole argument names count as a folder (`Cwd`, `WorkDir`, `Folder`, `Path` ...), so `IsDirect` is ignored. A folder argument that does not exist is a deny only when the command could open a PR.
- A PR create that sets `GIT_DIR` or `GIT_WORK_TREE` inside the command (`GIT_DIR=... gh pr create`, `env GIT_DIR=...`, `export GIT_DIR=...`) is blocked, in the Claude hook too: gh would resolve another repo than the one the gate checked.
- Integrity: sync writes the sha256 of `agy_gate.py`, `review-gate.py` and `pr_create_parser.py` into the hook command in `hooks.json`. The adapter denies when a file does not match, and denies a shell command that names the plugin folder (`plugins/nousfx`). That raises the bar for a model that edits the gate to disarm it. It is not a lock: the plugin folder is writable by the same user, an edited adapter can skip the check, and `hooks.json` (which agy guards) is the only anchor. Every sync run re-tests the installed hook.
- On Windows both parsers run, so a bash `cd "C:\repo" && gh pr create` can be blocked by the PowerShell pass; the reason starts with `[PowerShell parser]`.
- It fails closed: bad JSON, a missing field, no usable workspace, a missing or crashing gate, an overall time limit (the `--budget` in `hooks.json`, 55 s under its 60 s timeout; 25 s when an old `hooks.json` has none) all deny. Each gate run gets what is left, less a second, as its own `--budget`.
- I ran it against the real `agy` in a temp repo: `gh pr create` came back as `tool call denied by pre-tool hook: Blocked: no review recorded ...` (`docs/agy-capabilities.md`).
- Known limits, same as the Claude hook: `gh alias`, SDK calls, `cmd /c` and `gh -R owner/repo pr create` from a folder that is not a repo are not recognized. An MCP create-PR tool called from a folder that is not a repo is blocked. Opening a PR from a UI or another tool is not seen. CI is the gate for those.
- OpenCode has no adapter yet (NFX-50). Until it does, CI is the only gate there.

## Commit key check (NFX-86)

A commit made on the wrong branch is caught where it happens. In the NFX-10 session a commit `NFX-10: ...` landed on `cesar/NFX-83` after another session switched the shared checkout, and nothing noticed.

The rule: the branch is `<prefix>/<KEY>`, and the subject starts with `KEY:` or `KEY, KEY:` that holds the branch's key.

| Commit | Local | CI |
|---|---|---|
| Subject keys include the branch key (`NFX-86: x`, `NFX-85, NFX-86: x`) | pass | pass |
| Subject names only other keys, or has no key at the start | warn | fail |
| Merge commit | skipped | skipped (`--no-merges`) |
| Subject git writes itself (`Merge `, `Revert "`, `fixup! `, `squash! `, `amend! `) | skipped | a merge is skipped; the others are not judged by the rule |
| Detached HEAD, or a branch with no key | skipped (the branch rule covers it) | the branch step fails first |

- **Git hook.** `claude/githooks/commit-msg` warns on stderr and always exits 0. Install it with `cp claude/githooks/commit-msg .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg` (or put it beside `pre-push` in the folder `core.hooksPath` points at). It skips a merge, rebase, cherry-pick or revert in progress. `git commit --no-verify` skips it. Sync does not install it yet.
- **Claude hook.** The same gate hook reads a literal `git commit -m "..."` subject (also `-am`, `--message=`, and a `$(cat <<'EOF' ...)` heredoc) and adds a warning to the tool call. It never blocks. It stays silent when the subject is not literal (`-F`, an editor, a variable), when the command holds a `cd`, `git -C` or `--git-dir`. A call that also creates a PR is gated first; the warning is added only when the gate allows it.
- **CI.** The step after the branch check copies the base branch's `review-gate.py` and runs `check-commits "$HEAD_REF" --base <merge-base>`. It prints one line per bad commit and fails the job. The `branch-exception` label (OWNER or MEMBER author) skips it, like the branch step. A base gate without `check-commits` (the PR that adds it) gives a warning and no check.
- The check reads subjects only. It does not stop a rewritten history or a key typed to match, so it catches the slip, not a person who means it.

## Pre-push hook: advisory

- Template: `claude/githooks/pre-push`. Install it with `cp claude/githooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`, or link it from `.git/hooks`. Point `core.hooksPath` at the folder only when `.git/hooks` holds no other hooks: it switches them off (graphify's `post-checkout` and `post-commit`, for example).
- The gate script runs from outside the repo: `<home>/.claude/hooks/review-gate.py`, with `pr_create_parser.py` and `git_refs.py` (the default-branch rule, shared with `tier.py`; without it `--mark` refuses) beside it. The sync (`python3 -B scripts/nousfx_sync.py --tools claude`) puts all three there from this repo's `claude/hooks/`; by hand, copy all three. `<home>` comes from the passwd record, not `$HOME`. A copy inside the repo is code under review, and a branch could edit it to pass.
- Repo copies (`.claude/hooks/`, from `project-setup`) are only a fallback for cloud sessions, which have no `~/.claude`; the project's Claude hook (project-setup's `assets/settings.json`) uses one only when `$HOME/.claude/hooks/review-gate.py` is missing (it checks `$HOME`, not the passwd home). Locally the gate is always `~/.claude/hooks/review-gate.py`, for this hook and for review-loop's `--mark`. In the cloud the hook never runs the branch's copy (NFX-107): it reads the gate, the parser and `tier.py` from the default branch with `git show` into `<git-dir>/nousfx-gate/<key>/` (the key names main's hooks folder, so a new main makes a new folder and a running hook never loses its files; `current` points at the latest) and runs that with `python3 -I -B --repo-copy <the repo copy>`. That gate blocks a PR create (`gh pr create` or a create-PR tool) with "the gate copy differs from main" when the repo's gate or parser is not byte for byte main's; other commands still run. The default branch is `origin/HEAD` when it is `main`, `master`, `develop` or `trunk`, else `origin/main` (other names: not covered). Nothing is fetched: when that ref is missing the hook blocks what looks like a PR create (`gh pr create` or `new`, `hub pull-request`, `glab mr create`, a `gh api …/pulls` call, a create-PR tool) with "fetch origin first" and passes everything else. A branch that deletes the copy is still checked, because the gate comes from main. Quotes and backslashes are stripped before that match, so `gh pr cre''ate` is caught; any command with the words `gh` and `pr` (or `createPullRequest`, `pulls`, `pull-request`) is blocked while origin is missing. Limits: main's copy must already hold `--repo-copy` (an older one ignores it and only runs isolated), the PR that first adds the copy is blocked in the cloud when main has none ("no review gate copy to compare with"; open it locally), a branch that commits an edit and restores main's bytes in the working tree only passes this check (CI still judges it), and a branch can still edit `.claude/settings.json` or rewrite the local `refs/remotes/origin/*` refs the compare reads (nothing is fetched or verified against the remote). The extracted gate sits in the git dir, which any command in the session can write; it is reused without re-checking its bytes. The hook is advisory; the protected `main` and CI's base-branch gate are the real check. `nousfx_sync.py --project` refreshes a stale copy (`--check` reports it as drift) and never adds one.
- `REVIEW_GATE_DIR` overrides that folder. It is for tests and for my own shell. The hook refuses it, and blocks the push, when the folder is inside any git work tree (or git cannot say), because code in a work tree is code under review.
- It fails closed. A missing gate script, missing `python3` or any git error blocks the push.
- It reads the ref list git sends on stdin. Deletes, tag pushes and pushes of the default branch (`main`, `master`, or `origin/HEAD`) pass. Any other branch push is judged on the tree of the pushed commit (`local_sha`), not the checked-out branch. That tree needs a valid review mark recorded on the pushed branch name, and that name must be `<prefix>/<KEY>`. The mark holds the local branch name, so a push to a remote branch with another name (`git push origin ana/A:ana/B`) blocks; push under the same name. A sha that is not a commit blocks. It calls `review-gate.py check ... --tree TREE --branch NAME`.
- Screenshots branches (NFX-143). A branch ending in `-shots` first goes to `review-gate.py check-shots NAME --head SHA [--base origin/<default>]`. It passes with no review when the name is `<prefix>/<KEY>-shots` and every commit after the merge base with the default branch (with no base or no merge base, its whole history; at most 100 commits, no merge commit) adds, changes or deletes only files named `shots/<KEY>/…` with a png, jpg, jpeg, gif, webp, mp4, mov or webm extension, each left a regular file (mode 100644). Each commit is judged on its own, so code added in one commit and dropped in the next still fails. Code, a symlink, another key or no change at all falls through to the normal check above, which blocks. The hook lets the push through only on the gate's own "no review needed." line, so an installed gate that predates `check-shots` cannot open it. A shots branch never merges and has no PR, so CI never sees it; `gh pr create` from it stays blocked.
- It is advisory. `git push --no-verify` skips it, and nothing on the client can stop that. I never treat it as the only control.

## CI: the real gate for tools without hooks

- Workflow: `.github/workflows/review-gate.yml`. It runs on open, push, reopen, description edit and label changes.
- Its first step runs the base branch's `review-gate.py check-branch "$GITHUB_HEAD_REF"`: the shape `<prefix>/<KEY>` with any lowercase prefix, since CI does not know whose prefix it is (NFX-119). A bad name fails the job with the expected form. A base gate without `check-branch` (the PR that adds it) gives a warning and no check.
- The `branch-exception` PR label skips that step only when the PR author's `author_association` is OWNER or MEMBER, like a tier override. It checks the author, not who added the label: anyone with triage rights can label an OWNER's PR. No ask rule covers adding it (`gh pr edit --add-label`), so for an agent using my token the rule that it is my call is prose only. Adding the label re-runs the job. Bot and fork branches (`dependabot/...`, a contributor's own name) fail the step and the label cannot help them; an allowance comes when one is needed. Agents never add it; it is the user's call, like `--allow-branch`. The repo owner creates the label once: `gh label create branch-exception`.
- A second step, after the branch check, runs `check-commits` on the PR's commit subjects (see Commit key check, NFX-86).
- `project-setup` does not install this workflow in other repos, so there is no second copy to update.
- It takes `review-gate.py` and `pr_create_parser.py` from the base branch (`git show origin/<base>:...`), never from the PR. A PR cannot edit the gate to approve itself.
- The PR body reaches the job through an env var and a file. It is never interpolated into a shell command.
- The job passes only when the PR description or the last commit message holds a `Review-Gate: <tree hash>` line whose hash equals the tree of the PR head commit.
- A skip attestation passes only with the `review-skip` label (NFX-110), and the log shows `skipped: <reason>`.
- **Skip label (NFX-110).** For a `skipped:` line the attestation step also needs the `review-skip` PR label, applied last by the repo owner or an org member. It lists the PR's events (`gh api --paginate repos/<repo>/issues/<n>/events`, which is why the workflow has `issues: read`), takes the last `labeled` event for `review-skip`, and fails when that actor is a bot, is neither the repo owner nor an org member (`gh api orgs/<owner>/members/<login>`), or when the events cannot be read. The PR author's `author_association` does not count: it says who opened the PR, not who labeled it. Adding or removing the label re-runs the job. On a personal-account repo only the owner passes; the org-member call may need a token that can read members, and when it fails the step fails closed. The check reads who applied the label, not whether a person meant it: an agent that runs `gh pr edit --add-label review-skip` with my token shows up as me and passes, and no ask rule covers that command, so "agents never add the label" is prose only here, like the `branch-exception` label above. The label is not tied to a commit: after I add it, later pushes to the PR keep the waiver. CI also cannot tell a hand-written `Review-Gate: <tree>` line without `skipped:` from a real review (the known hole above); this check covers skip lines that `attest` wrote. The label is my call, like `--allow-branch`. I create it once: `gh label create review-skip`. A real review needs no label.
- Making it required is a manual step in GitHub: Settings, Branches, branch protection, require the `review-gate / check` status.
- First PR that introduces the gate: the base branch has no gate (or none with `verify-attestation`). The job fails with a clear message, it does not pass. I merge that one by hand as the owner.

### CI tier check (NFX-70)

After the attestation and before the check command, the job recomputes the review tier, so a PR opened past the local gate (`--no-verify`, another tool) cannot claim a lower tier than its diff earns.

```
base tier.py --base <merge-base> ──► tier.json ──┐
                                                  ├──► base review-gate.py verify-tier ──► pass / fail
PR body (env var, file) ──► Review-Tier: lines ───┘
```

- It takes `tier.py` from the base branch (`claude/skills/review-loop/scripts/tier.py`), with the gate, so a PR that edits `tier.py` is still checked with the old copy. A change to the tier rules takes effect after it merges.
- `tier.py` runs on the diff from the merge base only, with no labels: Jira labels can only raise a tier, and CI does not read Jira. So CI's tier is usually at or below the review loop's. It can be higher in two cases: the base branch's `tier.py` differs from the copy `--mark` ran (main made it stricter, or the installed copy is stale), or the PR targets an older branch (the local mark diffs from the merge base with `origin/HEAD`, CI from the merge base with the PR's base; run `tier.py --base-ref <that branch>` locally to match CI). The fix is the same: merge the base, review again at the CI tier, attest again.
- `verify-tier --tier-json FILE --text-file FILE [--author-association A]` reads each `Review-Tier:` line that starts a line in the PR body. It fails when one is below the computed tier and names the computed tier and its reasons. An equal or higher tier passes. A line it cannot read (`Review-Tier: two`, `4`) fails, even inside a code block, so a PR body that quotes the format must not start a line with it. Only the PR body is read: a `Review-Tier:` line in the last commit message is not checked.
- No `Review-Tier:` line passes, as today. **Known hole:** a PR body that drops the line is not checked. `--mark` without `--tier` still makes such a body, so the check cannot require it yet.
- CI checks out a clean commit, so it never counts untracked files; the local gate does. Same tree, same tier.
- When `tier.py` cannot read the diff, or cannot read `[review] risk_paths`, it says Tier 2 with a `Tier 2: could not ...` reason. That Tier 2 is a floor, not a measurement. When the diff was read (only `risk_paths` failed), CI compares against the tier the diff measured. When the diff was not read, the check fails closed (NFX-94) unless every line claims Tier 3: a diff too big or slow to read (git times out after 60 s) is the Tier 3 case. Re-run the job first; if it fails again, fix the cause or review at Tier 3. No override is honored then, not even an owner's: there is no computed tier to check it against.
- `tier.py` reads `risk_paths` from the default branch (`origin/HEAD`, else `origin/main`, else `origin/master`); CI passes `--base-ref <the PR base>` (NFX-108), so it reads `origin/<base>` and a stacked PR agrees with the gate. A missing or invalid `--base-ref` branch is Tier 2 with the reason, never a fall back to `origin/HEAD`. If the branch has no `.nousfx/project.toml`, it falls back to the PR's own copy, which a PR could edit (not the case here: main has the file). A base whose `tier.py` predates `--base-ref` runs as before, with a CI warning. `tier.py` loads `git_refs.py` from its own folder, else `../../../hooks`; missing is Tier 2.
- A base branch whose gate has no `verify-tier`, or no `tier.py`, fails the step. I merge the PR that introduces it by hand.

**Override decision.** CI honors an attested `override: computed N, <reason>` suffix when its `N` is at least the tier CI computes and the PR author is the repo owner or an org member (`author_association` `OWNER` or `MEMBER`, NFX-95), and prints `Override honored (self-asserted, not verified)` with the reason in the log. An override naming a lower `N` than CI computes fails: the diff grew after the approval, or the line was edited. Why honor it at all: the override exists for a lower tier the user approved, and the `Review-Gate:` line itself is already self-asserted. Rejecting it would make a legitimate override unmergeable without bypassing the whole check. What it buys: lowering a tier now takes a visible, explicit claim in the PR body, not a silent number.

**Who can override (NFX-95).** The workflow passes `github.event.pull_request.author_association` as `verify-tier --author-association`. Anyone who opens a PR can write an override line, so only `OWNER` and `MEMBER` count; `COLLABORATOR`, `CONTRIBUTOR`, `NONE` and the rest fail a below-tier line that carries one, and so does a missing or empty value. `MEMBER` is any member of the org that owns the repo, read-only ones included; on a personal-account repo like this one only `OWNER` applies. An org member with private membership may arrive as `CONTRIBUTOR` and fail; make the membership public. A line at or above the computed tier needs no override and passes for anyone. This narrows overrides only: any author can still drop the line (the known hole) or write a higher tier than the review ran, since both are self-asserted. Requiring the line from non-members would not help (they could write `Review-Tier: 3`) and would fail their skip attestations, which carry no tier. A base gate older than NFX-95 ignores the flag; the workflow prints a warning then. It is the PR author's association, not the editor's: someone else editing the body of an owner's PR keeps the owner's. The workflow file comes from the PR, so a PR that edits it can change what it passes; the CODEOWNERS caveat above applies.

## PR body check (NFX-85)

The local gate now asks the CI question too, before the PR opens: does the PR body hold a `Review-Gate:` line for HEAD's tree?

```
gh pr create ──► review check (mark, branch, findings, tests) ──► body check ──► allow
                                                                   │ no line, stale hash, unreadable body
                                                                   └──► block, print the attest lines to paste
```

- It runs after the review check, for every create in the command, in the folder that create runs in. Outside a repo there is nothing to judge, as before.
- Where it reads the body: `--body`, `-b`, `--body=`, `-bTEXT`, `--body-file`, `-F`, `--body-file=` (relative to the create's folder), and the `body` field of a create-PR MCP tool. A body file wins over `--body`, as in gh; among flags of one kind the last wins. The value of another flag (`--title '-bx'`) is skipped. The plain `check` entry takes `--body-file F` for an MCP tool's body; the agy adapter writes the tool's `body` (or `Body`) argument there.
- The line must name HEAD's tree, and the review mark must be for that tree. A mark on uncommitted changes does not cover HEAD: commit them, then attest.
- A heredoc inside `--body "$(cat <<'EOF' ... EOF)"` works: the gate reads the literal text, and the hash is literal. Any other body built at run time (`--body "$BODY"`, `-F "$F"`, `$ARGS`) blocks unless its literal text already holds the line.
- It blocks: no body, `--fill` (and `-f`, `--fill-first`, `--fill-verbose`) without a body, `--web`, `--editor` or `--recover` (even with a body: it can change after the hook runs), `--body-file -` (stdin), a missing body file, shell code the parser cannot read next to any create (`eval "$X"`, `bash -c "$X"`, a pipe into a shell), since it may hide another create, an argument built at run time outside a flag's value (`--body '<line>' $EXTRA`), a body file that is not a regular file, a line inside a sentence, and a line whose hash is not HEAD's tree. CI also accepts the line in the last commit message; the local gate reads only the body, so `--fill` is not enough here.
- The block message prints the exact lines for HEAD. An untracked body file in the repo does not hide them: the PR only needs HEAD reviewed, so the message adds that `attest` itself wants a clean tree. When there are no lines for HEAD, it prints why instead (for example, "commit or clean first").
- CRLF line ends count as line ends. An unknown program next to a readable create (`"$EDITOR" notes.txt; gh pr create -b ...`) gets the review check, and only the readable create's body is judged.
- `attest` now needs a clean tree: the working tree must equal HEAD (CI compares `HEAD^{tree}` only), and the mark must be for that tree. Otherwise it exits 2 with "commit or clean first" and the HEAD tree. Untracked files that `.gitignore` does not cover count.
- The pre-push hook is unchanged: a push has no PR body.
- Clustered shorthands are read as gh reads them (`-db TEXT` is `-d -b TEXT`); a cluster with a letter the parser does not know blocks. A body file the same command also names (`cat > b.md <<EOF ... gh pr create -F b.md`) blocks, because the hook reads it before the command runs: write it in a separate call. An unknown program (`$R`) counts as explained only one for one by readable creates.
- Not read, so blocked: `--body-file - <<EOF` (stdin). Not seen: an unquoted `$T` that splits into more flags.
- Limits: the hash is HEAD's tree at the time of the create. A push gh makes afterwards, or a create that names another head branch (`--head`, an MCP tool's `head`), is not compared. CI is still the real check.
- Hermes (unattended runs) gets this only if it runs the Claude PreToolUse hook or the plain `check` entry before it opens a PR; git hooks alone never see a PR body. I have not confirmed which one hermes uses.

## Attestation: how CI knows a review happened

Review marks live in the git dir and are never committed, so CI cannot see them. Instead:

```
review-loop ──► mark ──► review-gate.py attest ──► "Review-Gate: <tree>" ──► PR body
                                                                              │
CI: git rev-parse HEAD^{tree} == hash in the line? ◄──────────────────────────┘
```

1. Finish the code and run the review loop. It records the mark.
2. Commit everything, then run `python3 -B claude/hooks/review-gate.py attest`. It prints the `Review-Gate:` line (plus a `Review-Tier:` line when the mark has a tier) and exits 0 only when the working tree equals HEAD and a clean mark (or a recorded skip) covers it. Otherwise it exits 2 and prints nothing on stdout.
3. Paste the lines into the PR body, each on its own line. The local gate blocks `gh pr create` without them (PR body check above). For CI, a commit trailer on the last commit works too.
4. Any later change to the tree (a new commit, an amended file) changes the hash. Run the review again, run `attest` again, edit the body.

Line formats:

- `Review-Gate: <40 or 64 hex chars>`
- `Review-Gate: <hash> skipped: <reason>` for a recorded skip. The reason is one line.

- `Review-Tier: <0-3> (<reasons>)` on its own line after it, from `--mark --tier N --reasons "..."`. The review loop passes the tier that `tier.py` picked. It tells a reader how deep the review was. `verify-attestation` ignores it; `verify-tier` checks it (see CI tier check).
- `Review-Tier: <0-3> (<reasons>) override: computed <N>, <reason>` when the tier is below what `tier.py` computes and `--tier-override "<reason>"` was given.

`--mark --tier N` runs `tier.py` (from `skills/review-loop/scripts/`, next to the hook's folder, so the same path works in the repo, in `~/.claude` and in a project `.claude`) on the diff and refuses a `N` below the computed tier, naming it. The computed tier uses the diff only; Jira labels can only raise a tier in the review loop, so the gate's number can be lower than the loop's. A `--tier` at or above it passes. `--tier-override "<reason>"` records a lower tier on purpose; use it only when the user approves, as with `--skip`. A missing, failing or slow `tier.py` counts as Tier 2, with a note on stderr, so only Tier 0 or 1 needs an override then. A skip has no tier and runs nothing. A mark without `--tier` records no tier and runs nothing, so its PR body has no `Review-Tier:` line; that gap is visible to a reader but the gate does not refuse it, because older callers of `--mark` pass no tier. Old marks without a tier still attest.

This is a guardrail against a mistaken or lazy agent, not against someone editing the marker file: the override reason is self-asserted, like a skip reason. CI recomputes the tier (CI tier check above) but cannot check the reason.

`verify-attestation --tree HASH --text-file FILE [--text-file FILE]` is what CI runs. The line must start its own line. A mention inside a sentence does not count.

### What it does and does not guarantee

- It is an honor-system claim, not proof of a review.
- It proves one thing: the claim was written for exactly this code. Change the code and the claim goes stale.
- It does not prove the review ran. Anyone who can edit the PR body can type a line with the right hash.
- It does not prove the review was good, or that findings were fixed beyond the mark's count.
- A skip passes. The log shows the reason so a human can see it.
- The gate script comes from the base branch, so a PR cannot weaken the check on itself. A change to the gate takes effect only after it merges.
- Locally, sync makes the honor system harder to fake, not impossible (NFX-101). Deny rules keep Claude's file tools off the marker, the pass stamp, the proof stamp, the stamp key and the branch-name exception (`claude-review-ok`, `claude-review-check-pass`, `claude-proof-pass`, `claude-review-stamp-key`, `claude-branch-ok`, in any git dir). Ask rules make `--skip`, `--tier-override` and `--allow-branch` prompt you, even in auto or bypass mode. Block and usage messages no longer print a ready-made waiver command (`--skip`, `--tier-override`, `--allow-branch`). Known limits: the ask rules match command text, so `python3 -c`, a copy of the gate, `eval`, a variable holding the flag, quotes inside it or a glob for the path get past them, and the stamp key is same-user 0600, so a signature on the marker would prove nothing. The control is CI plus branch protection (on this repo, `main` requires `check`, admins included, since 2026-11-08).
- Real enforcement needs a human reviewer or a trusted runner that does the review. Branch protection plus this check only makes skipping visible. The check command (above) adds one real proof: the tests ran and passed on this tree.
