---
name: after-merge
description: Use when a PR for your own ticket is merged (you merged it, the user says it is merged, or a check shows it MERGED, also a merge made in the GitHub UI). Deletes its branches, moves the Jira ticket to Done, then runs /handoff.
---

Run this once a PR for your own ticket is confirmed merged. A merge made in the GitHub UI is only seen when you next look, so check first.

1. **Confirm the merge.** `gh pr view <n or branch> --json state,mergedAt,headRefName,headRefOid,baseRefName`. Stop unless `state` is `MERGED`, `baseRefName` is the default branch and `headRefName` is your `<prefix>/<KEY>` for the ticket in hand.
2. **Delete the branches.** Only the ones you made: `<prefix>/<KEY>` and `<prefix>/<KEY>-shots`. If `git status --porcelain` is not empty, skip this step, report it and go on to step 3. Otherwise switch to the default branch and pull. Check each branch and each side (local, remote) alone; skip only the one that fails, report it, and go on:
   - `<prefix>/<KEY>`: a side that is missing or already gone counts as deleted. Delete a side only when its tip equals `headRefOid` (local: `git rev-parse`, remote: `git ls-remote origin <branch>`); a different tip means a commit landed after the merge, so keep it. A squash merge makes `git branch -d` refuse, so use `-D`.
   - `<prefix>/<KEY>-shots`: no such branch means nothing to do. It never merges, so use `-D`, and delete a local one only when its remote is gone with nothing ahead of the default branch (`git log origin/<default>..<branch>` empty) or its tip equals `git ls-remote origin <branch>`.
   - Remote delete: `git push origin --delete <branch>`.
3. **Move the ticket to Done.** Use the tracker in `docs/agents/issue-tracker.md`; without that file, use the Jira MCP with the branch's key, and if the tracker is unreadable, skip and say so. A ticket with several PRs stays open until the last one merges: list open PRs with `gh pr list --state open --limit 200 --json title,headRefName` and keep it open if one has a head of `<prefix>/<KEY>` or a title whose key list contains `<KEY>` (`NFX-85, NFX-86: x` counts for both); name it in the report.
4. **Run `/handoff`.** Its own prompts stay in charge; do not write the note yourself. Unattended, skip it and say so.

Do all four unasked. Report what each step did, or why it stopped.
