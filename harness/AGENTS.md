# Working rules

## Delegation
- Keep edits in the main thread; re-read a file before editing, never trust only an agent's summary.
- Give every subagent an objective, output format and boundaries. At most ~3 in parallel for simple tasks.

## Tickets and branches
- Before a ticket, read its `is blocked by` links; start only when all are Done. If one is open or Jira is unreadable, ask (unattended: skip it, report why).
- UI/UX tickets you create or refine get an acceptance criterion for PR screenshots (before/after, light and dark, device sizes), or a recording for motion, flows and bugs over time; capture them yourself.
- Branch `<prefix>/<KEY>` (Jira's exact key; create the ticket first) off the default branch, screenshots `<prefix>/<KEY>-shots`; never a tool prefix or slug, rename only if asked. Once merged, delete both (local, remote) unasked; only yours.

## Runbooks
- Recurring manual steps (deploys, device installs, secret rotation, domains) live in `RUNBOOK.md` (`docs/engineering/` or `docs/`); read and follow it, never improvise.
- No runbook: say so and offer to write one from docs and config; no secrets, update it with any step change.
- Deploys and installs that go live for others are the user's to trigger: give the steps, wait for "deploy it".

## Keep working until done
- A turn that ends in text ends the work: run a named next step in the same turn. Stop only for a question only the user can answer, a needed approval, or a finished task.
