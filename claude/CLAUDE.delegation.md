# Delegation policy
- Code search and "where is X" questions: use the `Explore` subagent (Haiku). Docs and API lookups: use `researcher` (Haiku). Noisy build/test output: `test-runner` (Haiku). Prefer these over the cavecrew/caveman-explore skills.
- Keep edits in the main thread. Re-read any file yourself before editing it; do not rely on a Haiku summary alone.
- Every subagent call needs an objective, expected output format, and boundaries. Max ~3 parallel agents for simple tasks.
- Before a PR, use `/review-loop` (runs `/simplify`, then the Opus `reviewer` for max 5 rounds) once per feature, not per edit. It replaces running `/simplify` and `/code-review` by hand; it is also the one to use for unattended `/goal` runs.
- `reviewer-deep` (Fable) is a one-off second opinion. Suggest it only when a change is large or risky (security/auth, data migrations, concurrency, public API changes) or the default reviewer was inconclusive. Always ask the user first and wait for approval; never run it unprompted or in unattended runs.
