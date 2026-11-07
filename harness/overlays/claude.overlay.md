## Claude Code
- A missing tool is not a stop: load it with `ToolSearch`. Report a blocker only after the obvious fix failed, with what you tried.
- Lookups (Haiku): `Explore` for code, `researcher` for docs and APIs, `test-runner` for noisy build/test output; not the cavecrew/caveman-explore skills.
- When the work is done, ask once whether to open the PR. On yes, run `review-loop`, then `gh pr create`. If the `review-gate.py` hook blocks, run the loop, never work around it; skip only with the user's explicit approval.
- Requests to review or polish UI/UX, design, responsiveness or accessibility: `ui-review` first. Run it once on finished UI work before asking about the PR.
- Token hygiene: Grep or offset/limit for files over ~20K chars; never Read old transcripts in `~/.claude/projects` or re-read unchanged files. Send long output to a file; read the tail.
- One task per session. When done, on a topic change or a large context, offer `/handoff` then `/clear`; `/compact focus on <topic>` at milestones.
- Handoff: before a session's first planning or edit, read `.claude/handoff.md` (or root `HANDOFF.md`) if present, unasked, and check it against `git status` and the branch. Its "Suggested skills" are part of the plan: run each model-invocable one at its step or say why not; name user-only ones for the user. If missing, say nothing.
- Fable agents (`architect`, `reviewer-deep`, read-only) have a weekly limit. When one fits, say why in a line and ask; on a `fable-review` ticket, offer `architect` for the plan. Run only on a clear yes; summarize for the user to decide. Never in unattended or `/goal` runs.
- Work from an approved prototype or design (artifact, Figma, mockup): it is the spec; use `prototype-fidelity`.
