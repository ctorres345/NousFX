# Working rules

These rules are tool-neutral. Models are named by role: **fast** (cheap, quick lookups and noisy output), **strong** (the model that builds and edits), **deep** (premium, read-only second opinions). Each tool's overlay says which real model fills each role.

## Delegation
- Code search and "where is X" questions, docs and API lookups, and noisy build or test output go to a fast agent.
- Keep edits in the main thread. Re-read any file yourself before editing it; do not rely on a Haiku summary alone.
- Every subagent call needs an objective, expected output format, and boundaries. Max ~3 parallel agents for simple tasks.
- The deep role is a premium resource. Use it only for architecture decisions, performance reviews and second-opinion reviews of large or risky changes. Say why in one line and ask for approval first. Never run it unprompted or in unattended runs.

## Review before a PR
- Before opening a PR, simplify the changes, then run an independent review by the strong role, at most 5 rounds, once per feature and not per edit.
- When the work is done, ask once whether to open the PR. On a yes, run the review, then open the PR without asking again.
- A review gate blocks PR creation until a review is recorded for the current code. If it blocks, run the review. Skip only with explicit approval, recorded with the gate's skip option.
- UI work: when it is built and screenshots exist, run a UI review once before asking about the PR.

## Tickets and branches
- The branch for a ticket is named `<branch-prefix>/<TICKET-KEY>`, created from the default branch. The prefix is in the local rules.
- Use the exact key the tracker returns. If no ticket exists yet, create it first.
- Jira blockers (any project): before starting a ticket, read its `is blocked by` links. Start only when each of those tickets is Done. If one is open, or you cannot read Jira, name the problem and ask the user whether to go ahead; in unattended runs, skip the ticket and report why.
- UI/UX tickets (feature or bugfix): when creating or refining the Jira ticket, add an acceptance criterion that the PR includes screenshots (before/after for changes, light and dark where theming applies, relevant device sizes). If the behavior is motion, a flow, or a bug that only shows over time, require a screen recording instead of or besides screenshots. Capture them yourself (simulator, emulator, or browser) rather than handing it to the user, and attach them in the PR description (fill the PR template's Screenshots / recording section).
- When work is based on an approved prototype, the prototype is the spec: link it in the ticket, list its concrete values in the plan, compare against it before the PR, and state deliberate differences in the PR.

## Runbooks
- Each project keeps its recurring manual steps (deploys, installing on a device or simulator, rotating a secret, adding a domain) in a `RUNBOOK.md` (`docs/engineering/RUNBOOK.md` or `docs/RUNBOOK.md`). When the user asks to deploy, redeploy, install on a phone, simulator or emulator, or run another recurring manual step, read that project's runbook first and give or follow its steps; do not improvise from memory.
- If the project has none, say so in one line and offer to write one from its docs and config. Never put secrets in it. Update it in the same change whenever a step changes.
- Deploys and installs that go live for other people are the user's to trigger: give the steps and wait for a "deploy it" before running them.

## Keep working until done
- A turn that ends in text ends the work. When you name a next step, run it in that same turn with a tool call. Stop only for a real wait: a question only the user can answer, an approval these rules require, or a finished task.

## Token hygiene
- Do not read whole files over about 20K characters. Search, or read a range.
- Do not re-read unchanged files. Redirect long build or test output to a file and read only the tail or the failures.
- One task per session. When a task is done or the topic changes, say so in one line and offer a handoff.
- Before the first task of a session, check for a handoff note in the project. If it exists, read it first and check it against the repo status and the branch.
