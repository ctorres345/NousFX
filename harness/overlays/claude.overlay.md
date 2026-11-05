# Claude Code specifics

## Model roles
| Role | Claude Code |
|---|---|
| fast | the `Explore` subagent for code search, `researcher` for docs lookups, `test-runner` for build and test output |
| strong | the main model, and the `reviewer` subagent for the review loop |
| deep | the `architect` and `reviewer-deep` subagents |

## Tools and skills
- Run the review with the `review-loop` skill through the Skill tool. Never wait for the user to type it. It is also the review for unattended goal runs.
- A PreToolUse hook (`review-gate.py`) blocks `gh pr create` until a review is recorded. Skip only with `--mark --skip "<reason>"`, and only with explicit approval.
- UI review: invoke the `ui-review` skill first for any request to review or polish UI, UX, visual design, responsiveness or accessibility.
- User-only slash commands cannot be run through the Skill tool. Name the fitting one in a single line and let the user type it.
- In unattended goal runs, do not ask questions and do not run the deep subagents.
- Use `/compact focus on <topic>` at milestones, and `/handoff` then `/clear` between tasks.
