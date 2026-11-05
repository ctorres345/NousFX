## Antigravity CLI (agy)
- Skills are not loaded by a tool here. When a skill fits the task, read its `SKILL.md` (under `~/.agents/skills/<name>/`) and follow it.
- Before a PR, follow the `review-loop` skill inline: simplify the changes, then review them and fix findings, max 5 rounds, once per feature. Then ask the user before running `gh pr create`, because no gate blocks it yet.
- Any request to review, critique, polish, or optimize UI/UX, visual design, responsiveness, or accessibility: follow the `ui-review` skill first.
- Token hygiene (long contexts are the main cost): do not read whole files over ~20K chars (search, or read a line range). Redirect long build or test output to a file and read only the tail or the failures. Do not re-read unchanged files in a session.
- One task per session. When a task is done or the topic changes, tell the user in one line and offer to start a new session.
- Model choice and delegation to other agents are decided by the gemini-orchestrator plugin rules. Do not override them here.
