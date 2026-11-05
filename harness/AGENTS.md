# Working rules

## Delegation
- Keep edits in the main thread. Re-read any file yourself before editing it; do not rely on a fast agent's summary alone.
- Every subagent call needs an objective, expected output format, and boundaries. Max ~3 parallel agents for simple tasks.

## Tickets and branches
- Jira blockers (any project): before starting a ticket, read its `is blocked by` links. Start only when each of those tickets is Done. If one is open, or you cannot read Jira, name the problem and ask the user whether to go ahead; in unattended runs, skip the ticket and report why.
- UI/UX tickets (feature or bugfix): when creating or refining the ticket, add an acceptance criterion that the PR includes screenshots (before/after, light and dark, relevant device sizes), or a screen recording for motion, flows and bugs that show over time. Capture them yourself; the ticket template has the details.

## Runbooks
- Recurring manual steps (deploys, installs on a device or simulator, rotating a secret, adding a domain) live in the project's `RUNBOOK.md` (`docs/engineering/` or `docs/`). When asked to run one, read it first and follow it; do not improvise from memory.
- If the project has none, say so in one line and offer to write one from its docs and config. Never put secrets in it. Update it in the same change whenever a step changes.
- Deploys and installs that go live for other people are the user's to trigger: give the steps and wait for a "deploy it".

## Keep working until done
- A turn that ends in text ends the work. When you name a next step, run it in that same turn with a tool call. Stop only for a real wait: a question only the user can answer, an approval these rules require, or a finished task.
