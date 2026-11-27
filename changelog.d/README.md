# Changelog fragments

Each ticket writes its changelog entry here, in `changelog.d/<KEY>.md`, instead of editing `CHANGELOG.md` (NFX-292). Parallel PRs then add different files and stop conflicting.

A fragment's first line is the section, a `###` heading already used in `CHANGELOG.md`. Then come one or more bullets, written as they would be in `CHANGELOG.md`:

```markdown
### Review gate
- What changed and why (NFX-123). Details.
```

- A second PR for the same ticket writes `<KEY>-2.md`.
- One section per fragment. A ticket that touches two sections writes `<KEY>-2.md` for the second.
- A section name no release has used yet goes in `ALLOWED_NEW_SECTIONS` in `scripts/changelog_fragments.py` first, so a typo still fails the check.
- `python3 -B scripts/changelog_fragments.py check` checks the fragments. The suite runs it, along with a guard that fails a PR that edits `CHANGELOG.md`'s `## Unreleased` block.
- The release folds every fragment into `CHANGELOG.md` and deletes them (`docs/RUNBOOK.md`, Cut a release, step 3).
