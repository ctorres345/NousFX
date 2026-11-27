# Retro extras (NousFX)

The pinned `retro` skill says how to run a retro. These are our additions; apply them with it.

## Inputs
- The review comments on the wave's PRs, the reviewer's findings, and the `habit:` lines under Habits in each PR body.
- The decision logs: `python3 -B <review-loop skill folder>/scripts/session_report.py render --name <KEY>` output, or `~/Releases/<project>/reports/<date>-<KEY>/`.
- The wave's result files and `followups.md` when present.

## Classes
Before grouping, read the watch ledger: `python3 -B ~/.claude/skills/review-loop/scripts/session_report.py watch list` from the repo (`python` on Windows). Use the installed copy: a project copy may be stale. If it is missing or has no `watch`, say so and go on. When a finding matches a listed slug, count it against that slug: its earlier tickets count toward the class, across projects too.

Group findings into mistake classes. A class counts once it has happened twice, in two different PRs or tickets; a `habit:` line counts the same way. A class seen once stays in the retro file as a watch item.

Add each watch item the retro keeps, and each finding that matched a slug: `python3 -B ~/.claude/skills/review-loop/scripts/session_report.py watch add --slug <kebab> --ticket <KEY> --evidence <pointer> --note "<one line>"`. Pass `--harness` when the fix belongs in NousFX (a script, skill, hook, rule, the sync or an agent habit, so a habit seen once per project still becomes a class), else `--project`; inside NousFX every row is a harness row. Reuse a listed slug for the same mistake; `--new` starts one on purpose.

A byte cap a ticket raised under a name that `CAP_NAMES` in review-loop's `scripts/tier.py` does not list is a finding (NFX-200); its fix is adding the name there.

## Fix order per class
Take the first level that can do the job (idea from pstack `correct`, MIT, github.com/cursor/plugins):
1. Remove the mistake with an API or structure change.
2. Types.
3. A lint or check whose message names the fix.
4. A test.
5. Docs or a prose rule, last, and only for judgement calls.

Each proposed check fails on a real past instance: name the commit or PR the finding came from, and run the check against it. A check that cannot fail there is not proposed.

Each finding says how we tell it worked (a check, an eval or a count). A prose rule also says why a higher level cannot do the job.

## Filing
- Cesar's yes comes first, for each finding. Nothing is filed without it.
- File at most 3 tickets per retro. The rest stay in the retro file.
- Labels: `needs-triage` and `agent-filed`, never `ready-for-agent`.
- Search Jira for a similar open ticket first. On a match, comment there instead of filing.
- Body: `docs/agents/ticket-template.md`, plus the "how we tell it worked" line and the past PR or commit the check would have caught.

## Output
Write the retro to `~/Releases/<project>/waves/<date>/retro.md` for a wave, or `retro.md` in the ticket's report folder for a solo ticket. Never inside the repo. Never edit rules or skills from a retro; an approved finding becomes a ticket.
