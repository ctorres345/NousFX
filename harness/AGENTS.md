# Working rules

## Delegation
- Keep edits in the main thread. Re-read any file yourself before editing it; do not rely on a Haiku summary alone.
- Every subagent call needs an objective, expected output format, and boundaries. Max ~3 parallel agents for simple tasks.

## Tickets and branches
- Jira blockers (any project): before starting a ticket, read its `is blocked by` links. Start only when each of those tickets is Done. If one is open, or you cannot read Jira, name the problem and ask the user whether to go ahead; in unattended runs, skip the ticket and report why.
- UI/UX tickets (feature or bugfix): when creating or refining the Jira ticket, add an acceptance criterion that the PR includes screenshots (before/after for changes, light and dark where theming applies, relevant device sizes). If the behavior is motion, a flow, or a bug that only shows over time, require a screen recording instead of or besides screenshots. Capture them yourself (simulator, emulator, or browser) rather than handing it to the user, and attach them in the PR description (fill the PR template's Screenshots / recording section).

## Runbooks
- Each project keeps its recurring manual steps (deploys, installing on a device or simulator, rotating a secret, adding a domain) in a `RUNBOOK.md` (`docs/engineering/RUNBOOK.md` or `docs/RUNBOOK.md`). When the user asks to deploy, redeploy, install on a phone, simulator or emulator, or run another recurring manual step, read that project's runbook first and give or follow its steps; do not improvise from memory.
- If the project has none, say so in one line and offer to write one from its docs and config. Never put secrets in it. Update it in the same change whenever a step changes.
- Deploys and installs that go live for other people are the user's to trigger: give the steps and wait for a "deploy it" before running them.

## Keep working until done
- A turn that ends in text ends the work. When you name a next step, run it in that same turn with a tool call. Stop only for a real wait: a question only the user can answer, an approval these rules require, or a finished task.
