# Delegation policy
- Code search and "where is X" questions: use the `Explore` subagent (Haiku). Docs and API lookups: use `researcher` (Haiku). Noisy build/test output: `test-runner` (Haiku). Prefer these over the cavecrew/caveman-explore skills.
- Keep edits in the main thread. Re-read any file yourself before editing it; do not rely on a Haiku summary alone.
- Every subagent call needs an objective, expected output format, and boundaries. Max ~3 parallel agents for simple tasks.
- Use `/review-loop` (reviewer subagent, max 5 rounds) for review passes once per feature, not per edit.
- Ask the user before using a different model or effort level than configured (per-call model overrides, `/effort`, switching agents' models). Wait for approval.
