# Contributing

Thanks for looking. NousFX is my own setup, shared so friends can use it, so I keep it small. A skill, a fix or a doc correction is welcome. A new feature is best raised as an issue first.

## Set up

```bash
git clone https://github.com/ctorres345/NousFX.git
cd NousFX
python3 -B scripts/run_checks.py
```

`run_checks.py` runs every test suite (about 2 minutes). It must pass before you open a PR. To try the harness on your machine, `./install.sh` shows a dry run first; see [docs/sync.md](docs/sync.md). I write docs in the first person, in short sentences.

## Branches and the review gate

- I track work in Jira and name branches `cesar/<KEY>`. You have no Jira key, so use any branch name and say in the PR what it changes and why. I will pick it up from there.
- Before a PR opens, my review gate wants a review loop to have passed. How it works and what it blocks is in [docs/review-gate.md](docs/review-gate.md). CI runs the same check, so a PR from a fork is reviewed by me by hand.
- A PR that changes UI also needs `/via check` to pass, or a recorded skip with a reason.
- Keep one idea per PR. Do not edit generated files (the ones with a "generated" header); change their source.

## Writing a skill

Skills I write live in `claude/skills/<name>/`. Via's live in `plugins/via/skills/via/`. A third-party skill is not copied in; it is pinned by commit and checksum in `harness/skills.lock.json` (Via's own in `plugins/via/skills.lock.json`).

### Checklist

- [ ] **One job.** The skill does one thing. If you need "and", write two.
- [ ] **Folder and file.** `claude/skills/<name>/SKILL.md`, with `name` equal to the folder name.
- [ ] **Frontmatter.** `name`, a one-line `description`, and `argument-hint` if it takes arguments. The description says what it does and when to use it, with the words a person would type, because it is how Claude decides to load it.
- [ ] **Body for an agent.** Short sentences, steps in order, what to do when a step fails, and a "Do not" list. Put long detail in `references/` and load it only when needed.
- [ ] **Scripts have tests.** Put scripts in `scripts/` next to the skill and tests in `scripts/test_*.py`. `run_checks.py` picks up `claude/skills/*/scripts/test_*.py` on its own.
- [ ] **No secrets.** Never read, print or store a key. A script that sends anything out says so and asks first.
- [ ] **No new always-on rules.** The generated rules file has a byte budget. Put guidance in the skill, not in `CLAUDE.md`.
- [ ] **Via skills get an eval case.** Add a case to `plugins/via/evals/cases.json`, then run `python3 -B plugins/via/scripts/via_eval.py`.
- [ ] **Docs.** One line in the README table or `docs/` if people need to know it exists, and an entry in `CHANGELOG.md`.
- [ ] **Run it.** Try the skill on a real task, then `python3 -B scripts/run_checks.py`.

To see the skill on your machine, run `python3 -B scripts/nousfx_sync.py --dry-run` to see what it would change. The real sync installs my whole harness (rules, agents, hooks, the review gate and a deny list in `~/.claude/settings.json`), not only skill links, so read [docs/sync.md](docs/sync.md) first.

## Reporting a problem

Open an issue with what you ran, what you expected and what happened. For a Via problem, include the `/via setup` table and the exit code of any script that failed. Do not paste keys or private code.
