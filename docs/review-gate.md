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
- It judges the repo in `DIR`: branch named `cesar/<KEY>`, and a review mark for this exact code.
- Bad input blocks. A missing option, a missing folder or a folder that is not a git repo all exit 2.
- `--allow-non-repo` is for agy. A folder that is not a repo is then judged by the command alone, so `cd <repo> && gh pr create` is still checked in that repo. It only covers git's own "not a git repository" answer: a missing git, a missing folder and a pushed commit (`--tree`) still block.
- `--tool` names the parser: `Bash` (default), `PowerShell`, or an MCP tool name holding `create_pull_request`, as the Claude hook passes them.
- The Claude hook parses its JSON payload and then calls the same `evaluate()` function. Behavior of the hook does not change.
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
- The command runs through the shell at the repo top level, with `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and `GIT_COMMON_DIR` removed and `PYTHONDONTWRITEBYTECODE=1`. Its output goes to `claude-review-check.log` in the git dir. A refusal prints the last 40 lines and the path.
- Where it is read from: `origin/HEAD`, else `origin/main`, else `origin/master`. A branch cannot edit its own check to `true`. When that branch has the file, it wins, even if the file has no usable `[check]` (then the mark is refused). Only when the default branch has no `.nousfx/project.toml` at all (the PR that adds it), or no default branch can be found, does the mark use the working-tree file, and it says so. That local fallback is a convenience; CI never uses it. The anchor is the local remote-tracking state (`origin/HEAD` counts only when it points at another `refs/remotes/origin/` ref, and nothing is fetched), so a stale clone or a shell user can still steer the local mark. CI is the real check.
- A refused mark deletes the older one, so a stale good mark cannot carry a failing run through.
- The check must not edit files. If the tree hash changes while it runs, the mark is refused.
- No check configured: the mark is refused with the lines to add. An approved `--mark --skip "<reason>"` still works locally and runs no check; CI still runs it.
- The mark stores `{command, exit, source}`. `check`, `attest` and the pre-push hook treat a clean mark with no passing `check` result as no review. An old mark from before this change blocks, so re-run the review loop.
- The agent still reports only the findings count. It cannot report a pass the gate did not see.
- `sync --project` prints a note when the repo has no check command. It never invents one; the `project-setup` skill proposes one for the stack and asks.

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
- The matcher is `run_command|.*create_pull_request`. A create-PR tool is checked in each folder like a PR create command. I have not seen how agy names an MCP tool, so that part is untested.
- On Windows it tries both the bash and the PowerShell parser (`--tool Bash`, `--tool PowerShell`) and blocks if either finds a create.
- To block, it prints `{"decision":"deny","reason":...}` with the gate's own message. To allow, it prints nothing, so agy's normal permission prompt still applies. An explicit `allow` might skip that prompt.
- It runs the gate with no `GIT_*` variable in the hook's own environment, so an inherited `GIT_DIR` cannot hide the repo. A workspace root that no longer exists is skipped; with none left, it denies. Only whole argument names count as a folder (`Cwd`, `WorkDir`, `Folder`, `Path` ...), so `IsDirect` is ignored. A folder argument that does not exist is a deny only when the command could open a PR.
- A PR create that sets `GIT_DIR` or `GIT_WORK_TREE` inside the command (`GIT_DIR=... gh pr create`, `env GIT_DIR=...`, `export GIT_DIR=...`) is blocked, in the Claude hook too: gh would resolve another repo than the one the gate checked.
- Integrity: sync writes the sha256 of `agy_gate.py`, `review-gate.py` and `pr_create_parser.py` into the hook command in `hooks.json`. The adapter denies when a file does not match, and denies a shell command that names the plugin folder (`plugins/nousfx`). That raises the bar for a model that edits the gate to disarm it. It is not a lock: the plugin folder is writable by the same user, an edited adapter can skip the check, and `hooks.json` (which agy guards) is the only anchor. Every sync run re-tests the installed hook.
- On Windows both parsers run, so a bash `cd "C:\repo" && gh pr create` can be blocked by the PowerShell pass; the reason starts with `[PowerShell parser]`.
- It fails closed: bad JSON, a missing field, no usable workspace, a missing or crashing gate, an overall time limit (25 s for all checks, under the hook's 30 s) all deny.
- I ran it against the real `agy` in a temp repo: `gh pr create` came back as `tool call denied by pre-tool hook: Blocked: no review recorded ...` (`docs/agy-capabilities.md`).
- Known limits, same as the Claude hook: `gh api`, `cmd /c` and `gh -R owner/repo pr create` from a folder that is not a repo are not recognized. An MCP create-PR tool called from a folder that is not a repo is blocked. Opening a PR from a UI or another tool is not seen. CI is the gate for those.
- OpenCode has no adapter yet (NFX-50). Until it does, CI is the only gate there.

## Pre-push hook: advisory

- Template: `claude/githooks/pre-push`. Install it with `cp claude/githooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`, or link it from `.git/hooks`. Point `core.hooksPath` at the folder only when `.git/hooks` holds no other hooks: it switches them off (graphify's `post-checkout` and `post-commit`, for example).
- The gate script runs from outside the repo: `<home>/.claude/hooks/review-gate.py`, with `pr_create_parser.py` beside it. The sync (`python3 -B scripts/nousfx_sync.py --tools claude`) puts both there from this repo's `claude/hooks/`; by hand, copy both. `<home>` comes from the passwd record, not `$HOME`. A copy inside the repo is code under review, and a branch could edit it to pass.
- `REVIEW_GATE_DIR` overrides that folder. It is for tests and for my own shell. The hook refuses it, and blocks the push, when the folder is inside any git work tree (or git cannot say), because code in a work tree is code under review.
- It fails closed. A missing gate script, missing `python3` or any git error blocks the push.
- It reads the ref list git sends on stdin. Deletes, tag pushes and pushes of the default branch (`main`, `master`, or `origin/HEAD`) pass. Any other branch push is judged on the tree of the pushed commit (`local_sha`), not the checked-out branch. That tree needs a valid review mark recorded on the pushed branch name, and that name must be `cesar/<KEY>`. The mark holds the local branch name, so a push to a remote branch with another name (`git push origin cesar/A:cesar/B`) blocks; push under the same name. A sha that is not a commit blocks. It calls `review-gate.py check ... --tree TREE --branch NAME`.
- It is advisory. `git push --no-verify` skips it, and nothing on the client can stop that. I never treat it as the only control.

## CI: the real gate for tools without hooks

- Workflow: `.github/workflows/review-gate.yml`. It runs on open, push, reopen and description edit.
- It takes `review-gate.py` and `pr_create_parser.py` from the base branch (`git show origin/<base>:...`), never from the PR. A PR cannot edit the gate to approve itself.
- The PR body reaches the job through an env var and a file. It is never interpolated into a shell command.
- The job passes only when the PR description or the last commit message holds a `Review-Gate: <tree hash>` line whose hash equals the tree of the PR head commit.
- A skip attestation passes too, and the log shows `skipped: <reason>`.
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
- `tier.py` runs on the diff from the merge base only, with no labels: Jira labels can only raise a tier, and CI does not read Jira. So CI's tier is usually at or below the review loop's. It can be higher in two cases: the base branch's `tier.py` differs from the copy `--mark` ran (main made it stricter, or the installed copy is stale), or the PR targets an older branch (the local mark diffs from the merge base with `origin/HEAD`, CI from the merge base with the PR's base). The fix is the same: merge the base, review again at the CI tier, attest again.
- `verify-tier --tier-json FILE --text-file FILE [--author-association A]` reads each `Review-Tier:` line that starts a line in the PR body. It fails when one is below the computed tier and names the computed tier and its reasons. An equal or higher tier passes. A line it cannot read (`Review-Tier: two`, `4`) fails, even inside a code block, so a PR body that quotes the format must not start a line with it. Only the PR body is read: a `Review-Tier:` line in the last commit message is not checked.
- No `Review-Tier:` line passes, as today. **Known hole:** a PR body that drops the line is not checked. `--mark` without `--tier` still makes such a body, so the check cannot require it yet.
- CI checks out a clean commit, so it never counts untracked files; the local gate does. Same tree, same tier.
- When `tier.py` cannot read the diff, or cannot read `[review] risk_paths`, it says Tier 2 with a `Tier 2: could not ...` reason. That Tier 2 is a floor, not a measurement. When the diff was read (only `risk_paths` failed), CI compares against the tier the diff measured. When the diff was not read, the check fails closed (NFX-94) unless every line claims Tier 3: a diff too big or slow to read (git times out after 60 s) is the Tier 3 case. Re-run the job first; if it fails again, fix the cause or review at Tier 3. No override is honored then, not even an owner's: there is no computed tier to check it against.
- `tier.py` reads `risk_paths` from the default branch (`origin/HEAD`, else `origin/main`, else `origin/master`). If none exists, or it has no `.nousfx/project.toml`, it falls back to the PR's own copy, which a PR could edit. Not the case in this repo (main has the file); a known gap for installs with another default branch.
- A base branch whose gate has no `verify-tier`, or no `tier.py`, fails the step. I merge the PR that introduces it by hand.

**Override decision.** CI honors an attested `override: computed N, <reason>` suffix when its `N` is at least the tier CI computes and the PR author is the repo owner or an org member (`author_association` `OWNER` or `MEMBER`, NFX-95), and prints `Override honored (self-asserted, not verified)` with the reason in the log. An override naming a lower `N` than CI computes fails: the diff grew after the approval, or the line was edited. Why honor it at all: the override exists for a lower tier the user approved, and the `Review-Gate:` line itself is already self-asserted. Rejecting it would make a legitimate override unmergeable without bypassing the whole check. What it buys: lowering a tier now takes a visible, explicit claim in the PR body, not a silent number.

**Who can override (NFX-95).** The workflow passes `github.event.pull_request.author_association` as `verify-tier --author-association`. Anyone who opens a PR can write an override line, so only `OWNER` and `MEMBER` count; `COLLABORATOR`, `CONTRIBUTOR`, `NONE` and the rest fail a below-tier line that carries one, and so does a missing or empty value. `MEMBER` is any member of the org that owns the repo, read-only ones included; on a personal-account repo like this one only `OWNER` applies. An org member with private membership may arrive as `CONTRIBUTOR` and fail; make the membership public. A line at or above the computed tier needs no override and passes for anyone. This narrows overrides only: any author can still drop the line (the known hole) or write a higher tier than the review ran, since both are self-asserted. Requiring the line from non-members would not help (they could write `Review-Tier: 3`) and would fail their skip attestations, which carry no tier. A base gate older than NFX-95 ignores the flag; the workflow prints a warning then. It is the PR author's association, not the editor's: someone else editing the body of an owner's PR keeps the owner's. The workflow file comes from the PR, so a PR that edits it can change what it passes; the CODEOWNERS caveat above applies.

## Attestation: how CI knows a review happened

Review marks live in the git dir and are never committed, so CI cannot see them. Instead:

```
review-loop ──► mark ──► review-gate.py attest ──► "Review-Gate: <tree>" ──► PR body
                                                                              │
CI: git rev-parse HEAD^{tree} == hash in the line? ◄──────────────────────────┘
```

1. Finish the code and run the review loop. It records the mark.
2. Run `python3 -B claude/hooks/review-gate.py attest`. It prints the `Review-Gate:` line (plus a `Review-Tier:` line when the mark has a tier) and exits 0 only when a clean mark (or a recorded skip) covers the current code. Otherwise it exits 2 and prints nothing.
3. Paste the lines into the PR body, each on its own line. A commit trailer on the last commit works too.
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
- Real enforcement needs a human reviewer or a trusted runner that does the review. Branch protection plus this check only makes skipping visible. The check command (above) adds one real proof: the tests ran and passed on this tree.
