# Working rules

These rules are tool-neutral. Models are named by role: **fast** (cheap, quick lookups and noisy output), **strong** (the model that builds and edits), **deep** (premium, read-only second opinions). Each tool's overlay says which real model fills each role.

## Delegation
- Code search and "where is X" questions: use a fast agent. Docs and API lookups: a fast agent. Noisy build or test output: a fast agent.
- Keep edits in the main thread. Re-read any file yourself before editing it; do not rely on a fast agent's summary alone.
- Every delegated call needs an objective, an expected output format and boundaries. Run at most 3 in parallel for simple tasks.
- The deep role is a premium resource. Use it only for architecture decisions, performance reviews and second-opinion reviews of large or risky changes. Say why in one line and ask for approval first. Never run it unprompted or in unattended runs.

## Review before a PR
- Before opening a PR, simplify the changes, then run an independent review by the strong role, at most 5 rounds, once per feature and not per edit.
- When the work is done, ask once whether to open the PR. On a yes, run the review, then open the PR without asking again.
- A review gate blocks PR creation until a review is recorded for the current code. If it blocks, run the review. Skip only with explicit approval, recorded with the gate's skip option.
- UI work: when it is built and screenshots exist, run a UI review once before asking about the PR.

## Tickets and branches
- The branch for a ticket is named `<branch-prefix>/<TICKET-KEY>`, created from the default branch. The prefix is in the local rules.
- Use the exact key the tracker returns. If no ticket exists yet, create it first.
- Before starting a ticket, read its "is blocked by" links. Start only when each is done. If one is open, or the tracker cannot be read, name the problem and ask; in unattended runs, skip the ticket and report why.
- UI tickets carry an acceptance criterion that the PR includes screenshots, or a recording for motion and flows. Capture them yourself.
- When work is based on an approved prototype, the prototype is the spec: link it in the ticket, list its concrete values in the plan, compare against it before the PR, and state deliberate differences in the PR.

## Runbooks
- Recurring manual steps (deploys, installs on a device, rotating a secret) live in the project's `RUNBOOK.md`. Read it first and follow it; do not improvise from memory.
- If the project has none, say so in one line and offer to write one. Never put secrets in it. Update it in the same change whenever a step changes.
- Anything that goes live for other people is the user's to trigger: give the steps and wait for an explicit go.

## Keep working until done
- A turn that ends in text ends the work. When you name a next step, run it in that same turn.
- Stop only for a real wait: a question only the user can answer, an approval these rules require, or a finished task.
- A missing tool is not a stop. Load it and continue. Report a blocker only after the obvious fix failed, and name what you tried.

## Token hygiene
- Do not read whole files over about 20K characters. Search, or read a range.
- Do not re-read unchanged files. Redirect long build or test output to a file and read only the tail or the failures.
- One task per session. When a task is done or the topic changes, say so in one line and offer a handoff.
- Before the first task of a session, check for a handoff note in the project (`.claude/handoff.md` or a root `HANDOFF.md`). If it exists, read it first and check it against the repo status and the branch.
