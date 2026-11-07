# Working rules

## Delegation
- Keep edits in the main thread. Re-read a file yourself before editing it; never rely only on an agent's summary.
- Give every subagent an objective, an output format and boundaries. At most ~3 in parallel for simple tasks.

## Tickets
- Before starting a ticket (any project), read its `is blocked by` links; start only when all are Done. If one is open or Jira is unreadable, ask the user (unattended: skip it, report why).
- UI/UX tickets: add an acceptance criterion that the PR has screenshots (before/after, light and dark, device sizes), or a recording for motion, flows and bugs over time. Capture them yourself; details in the ticket template.

## Runbooks
- Recurring manual steps (deploys, device installs, secret rotation, domains) live in the project's `RUNBOOK.md` (`docs/engineering/` or `docs/`). Read and follow it; never improvise.
- No runbook: say so and offer to write one from docs and config. No secrets in it; update it in the same change as the step.
- Deploys and installs that go live for others are the user's to trigger: give the steps, wait for "deploy it".

## Keep working until done
- A turn that ends in text ends the work: run a named next step in the same turn. Stop only for a question only the user can answer, a required approval, or a finished task.
