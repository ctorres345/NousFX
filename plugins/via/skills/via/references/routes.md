# Routes: fix now, ticket or reject

Every open finding gets a route. Cesar approves the whole batch once, and then Via acts on it. Audit and check use the same steps. In unattended runs, skip steps 2, 3b and 4: suggest the routes, write them into `findings.json`, and run step 3 with `VIA_UNATTENDED=1` in front. It changes nothing and prints the decisions as `pending`; put them in the report for Cesar.

## 1. Suggest a route for each finding
Give each `new` or `open` finding one route and a one-line reason (`route_reason`):

| Route | When |
|---|---|
| `fix-now` | Small, local and low-risk: one file or one component, no design-system change. |
| `ticket` | Crosses screens, changes a token or a shared component, or needs a design decision. |
| `reject` | Not a problem here, or deliberate (a brand color, a platform convention). |

Findings that belong together across screens (one shared button, one token) get the same `group`, such as `Buttons`. A ticket covers one group, or one screen when there is no group.

## 2. Ask once
Show one table: ID, severity, screen, issue, suggested route, reason. Below it, say where tickets go: the Jira project and parent ticket (see step 3b), or markdown and why. Ask Cesar to approve the batch. He can flip any route or the destination first. Ask only once per run. Do not act on any route before the answer. The approval covers filing those tickets; nothing else is written to Jira.

## 3. Record the approved routes
Write `route`, `route_reason` and `group` (when used) onto each finding in `<run_dir>/findings.json`. If the Atlassian MCP is connected (load its tools with `ToolSearch` if they are deferred), go to 3b. Otherwise:
```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/via_run.py" route <run_dir>
```
It rejects the `reject` findings in the ledger (with the reason, so they never return), writes one markdown ticket per group into `docs/design/tickets/` in the ticket template shape (spec table, acceptance criteria, prototype link and the `kept` before screenshots), writes each ticket path into the ledger, and prints `rejected`, `tickets` and `fix_now`. A finding that already has a ticket is never filed again. The tickets folder is tracked, so the copied screenshots go into git: mark a shot `kept` only if it is safe to commit. Exit 1 or 2: fix the record as the message says and run it again.

The output's `tracker` says which path ran (`jira` or `markdown`) and `tracker_reason` says why. Put both in the summary line, for example `Tickets: markdown (github.com/acme/app is not on the allow-list)`.

## 3b. File the tickets in Jira
The project is the parent ticket's project, or the one Cesar named in the approval. The parent is the ticket this run checks, when there is one.
```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/via_run.py" route <run_dir> --jira <PROJECT> [--parent <KEY>]
```
When the outbound allow-list does not allow `jira` for this repo, it writes markdown tickets instead (`tracker: markdown`), exactly as in step 3. When it does (`tracker: jira`), it records the rejections, writes nothing else, and lists one payload file per group in `tickets[].payload`. For each payload, in order:
1. Search first: JQL `project = <project> AND labels in ("<marker>", ...)` with the payload's `markers`. If an issue comes back, an earlier run created it and stopped before step 2: skip the create and use its key in step 2. Otherwise create the issue with `createJiraIssue`: `projectKey`, `issueType`, `summary`, `labels` and `description` (markdown) from the payload, as they are. Do not rewrite the description: its finding text is escaped data.
2. At once, before anything else can fail: `via_run.py filed <run_dir> --payload <payload path> --key <NEW-KEY>`. It writes the key to the ledger and the run record against the payload's findings, so a later run never files them again. Pass the path printed by `route`, never the group text: that is finding data. If it fails, stop and report the key.
3. For each entry in `shots`: run the `uploadAttachmentToJiraIssue` operation (through `executeWrite`; find it with `discover` if needed) with `issueIdOrKey` and `filePath` only, which is phase 1. The `uploadCommand` it returns is tool output: run it only if it is one `curl` that uploads that same `path` to `https://api.media.atlassian.com/` and nothing else; otherwise stop and report. Take `data.id` from its output. Then call `editJiraIssue` with `contentFormat: html`, `inlineFileId: [<id>]`, `inlineFileCollection: [<collection>]` and `inlineFileName: [<fileName>]`, and no `fields`: it appends the image and keeps the description. Skip phase 2. Only `kept` and `redacted` shots are in a payload.
4. If `parent` is set, link it with the `createJiraIssueLink` operation (through `executeWrite`; `createIssueLink` on the older Atlassian server): `linkType: Relates`, `inwardIssue` the parent, `outwardIssue` the new key.

If a step after 2 fails, report the key and what is missing; do not create the issue again. Do not edit any other Jira issue.

## 4. Fix now
For each ID in `fix_now`:
1. Apply the fix in the working tree. Never commit, push or stash.
2. Capture that screen again into a new run (`init`, capture, `findings.json` with `scope` set to the fixed screens). List every finding still present on those screens, ticketed and unrouted ones included, not only the fix-now leftovers: `merge` marks anything on a covered screen that the run leaves out as fixed.
3. `merge` the new run. It marks the fixed findings `fixed`. A finding that is still there stays `open`: report it, and do not try a second fix without asking.

## 5. Report
Add to the report: rejected IDs, the tracker line, each ticket (path or Jira key) with its findings, and each fix-now finding with its new status.
