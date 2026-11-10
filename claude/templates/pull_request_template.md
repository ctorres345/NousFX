## Ticket
<!-- Jira key and link, e.g. MOB-23. Branch is <prefix>/<KEY>. -->

## What and why
<!-- One or two sentences: the change and the reason. -->

## How to test
<!-- Steps or commands a reviewer can run. -->

## Acceptance criteria
<!-- One row per criterion in the ticket: its proof (command or manual step) and what it showed. -->

| Acceptance criterion | Proof | Result |
|---|---|---|
|  |  |  |

## Screenshots / recording
<!-- Required for any UI/UX change (feature or bugfix): before and after, light and dark, relevant device sizes.
     Use a screen recording for motion, flows, or bugs that only show over time. Write "N/A (no UI change)" otherwise. -->

| Before | After |
|---|---|
|  |  |

## What I checked myself
<!-- Two or three sentences in your own words: what you ran or looked at yourself, and what you did not. -->

## Checklist
- [ ] Tests and lint pass (commands in the project's CLAUDE.md)
- [ ] `/review-loop` ran on the final diff
- [ ] Docs updated if behavior, architecture, or security posture changed
- [ ] No secrets or credentials in the diff

## Review
<!-- Replace the two placeholder lines below with the output of `review-gate.py attest` (commit everything first).
     The gate blocks `gh pr create` without a valid Review-Gate line for HEAD's tree. Keep each line at the start of its own line. -->
<Review-Gate line from review-gate.py attest>
<Review-Tier line from review-gate.py attest>
