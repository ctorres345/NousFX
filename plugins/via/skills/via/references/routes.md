# Routes: fix now, ticket or reject

Every open finding gets a route. Cesar approves the whole batch once, and then Via acts on it. Audit and check use the same steps. In unattended runs, skip steps 2 and 4: suggest the routes, write them into `findings.json`, and run step 3 with `VIA_UNATTENDED=1` in front. It changes nothing and prints the decisions as `pending`; put them in the report for Cesar.

## 1. Suggest a route for each finding
Give each `new` or `open` finding one route and a one-line reason (`route_reason`):

| Route | When |
|---|---|
| `fix-now` | Small, local and low-risk: one file or one component, no design-system change. |
| `ticket` | Crosses screens, changes a token or a shared component, or needs a design decision. |
| `reject` | Not a problem here, or deliberate (a brand color, a platform convention). |

Findings that belong together across screens (one shared button, one token) get the same `group`, such as `Buttons`. A ticket covers one group, or one screen when there is no group.

## 2. Ask once
Show one table: ID, severity, screen, issue, suggested route, reason. Ask Cesar to approve the batch. He can flip any route first. Ask only once per run. Do not act on any route before the answer.

## 3. Record the approved routes
Write `route`, `route_reason` and `group` (when used) onto each finding in `<run_dir>/findings.json`, then:
```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/via_run.py" route <run_dir>
```
It rejects the `reject` findings in the ledger (with the reason, so they never return), writes one markdown ticket per group into `docs/design/tickets/` in the ticket template shape (spec table, acceptance criteria, prototype link and the `kept` before screenshots), writes each ticket path into the ledger, and prints `rejected`, `tickets` and `fix_now`. A finding that already has a ticket is never filed again. The tickets folder is tracked, so the copied screenshots go into git: mark a shot `kept` only if it is safe to commit. Exit 1 or 2: fix the record as the message says and run it again.

## 4. Fix now
For each ID in `fix_now`:
1. Apply the fix in the working tree. Never commit, push or stash.
2. Capture that screen again into a new run (`init`, capture, `findings.json` with `scope` set to the fixed screens). List every finding still present on those screens, ticketed and unrouted ones included, not only the fix-now leftovers: `merge` marks anything on a covered screen that the run leaves out as fixed.
3. `merge` the new run. It marks the fixed findings `fixed`. A finding that is still there stays `open`: report it, and do not try a second fix without asking.

## 5. Report
Add to the report: rejected IDs, each ticket path with its findings, and each fix-now finding with its new status.
