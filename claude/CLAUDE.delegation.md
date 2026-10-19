# Delegation policy
- Code search and "where is X" questions: use the `Explore` subagent (Haiku). Docs and API lookups: use `researcher` (Haiku). Noisy build/test output: `test-runner` (Haiku). Prefer these over the cavecrew/caveman-explore skills.
- Keep edits in the main thread. Re-read any file yourself before editing it; do not rely on a Haiku summary alone.
- Every subagent call needs an objective, expected output format, and boundaries. Max ~3 parallel agents for simple tasks.
- Before a PR, use `/review-loop` (runs `/simplify`, then the Opus `reviewer` for max 5 rounds) once per feature, not per edit. It replaces running `/simplify` and `/code-review` by hand; it is also the one to use for unattended `/goal` runs.
- Fable is a premium resource (limited weekly allowance). Two Fable agents exist, both read-only:
  - `architect`: architecture decisions (module boundaries, data models, migration strategy, major dependencies), optimization and performance reviews (speed, memory, battery), and design reviews. Give it a short brief (goal, constraints, options, what to measure).
  - `reviewer-deep`: second-opinion code review for large or risky changes (security/auth, data migrations, concurrency, public API changes), or when the default `reviewer` was inconclusive.
- Do not wait to be asked. When a task fits one of the Fable cases above, or you judge deeper reasoning would clearly pay off, say so in one short line (why, and what it would look at) and ask the user for approval. Run it only after a clear yes, then summarize the result for the user to decide. Never run Fable agents unprompted or in unattended `/goal` runs.
