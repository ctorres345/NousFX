## Claude Code
- A missing tool is not a stop: load it with `ToolSearch`. Report a blocker only after the obvious fix failed, with what you tried.
- Lookups (Haiku): `Explore` for code, `researcher` for docs and APIs, `test-runner` for noisy build/test output, over cavecrew/caveman-explore.
- When done, ask once whether to open the PR; on yes, run `review-loop` yourself, then `gh pr create`. If the `review-gate.py` hook blocks, run the loop, never work around it; skip only with the user's explicit approval.
- UI work: `/via spec` at kickoff, `/via check` before asking about the PR, unasked. Requests to review, critique, polish or optimize UI/UX, design, responsiveness or accessibility: `/via audit`.
- Token hygiene: Grep or offset/limit for files over ~20K chars; never Read old transcripts or unchanged files again. Send long output to a file; read the tail.
- One task per session. When done, on a topic change or large context, offer `/handoff` then `/clear`; `/compact focus on <topic>` at milestones.
- Handoff: before a session's first planning or edit, read `.claude/handoff.md` (or root `HANDOFF.md`) if present, even if unrelated, and check it against `git status` and the branch. Its "Suggested skills" are part of the plan: run each model-invocable one at its step or say why not; name user-only ones for the user.
- Fable agents (`architect`, `reviewer-deep`, read-only) have a weekly limit. When one fits, say why in a line and ask; on a `fable-review` ticket, offer `architect` for the plan. Run only on a clear yes; summarize for the user to decide. Never offer or run them unattended or in `/goal`.
- An approved prototype or design (artifact, Figma, mockup) is the spec: use `prototype-fidelity`.
