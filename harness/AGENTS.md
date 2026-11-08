# Working rules

## Delegation
- Keep edits in the main thread. Re-read a file before editing; never trust only an agent's summary.
- Give every subagent an objective, an output format and boundaries. At most ~3 in parallel for simple tasks.

## Tickets
- Before starting a ticket, read its `is blocked by` links; start only when all are Done. If one is open or Jira is unreadable, ask the user (unattended: skip it, report why).
- UI/UX tickets: when creating or refining one, add an acceptance criterion that the PR has screenshots (before/after, light and dark, device sizes), or a recording for motion, flows and bugs over time. Capture them yourself.
- After a PR merges, delete its branch (local, remote) and screenshot branches you made, unasked; only yours.

## Runbooks
- Recurring manual steps (deploys, device or simulator installs, secret rotation, domains) live in `RUNBOOK.md` (`docs/engineering/` or `docs/`). To run one, read it first and follow it; never improvise.
- No runbook: say so and offer to write one from docs and config. No secrets in it; update it in the same change as any step change.
- Deploys and installs that go live for others are the user's to trigger: give the steps, wait for "deploy it".

## Keep working until done
- A turn that ends in text ends the work: run a named next step in the same turn. Stop only for a question only the user can answer, a needed approval, or a finished task.
