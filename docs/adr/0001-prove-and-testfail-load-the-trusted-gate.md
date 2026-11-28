---
status: accepted
---

# prove.py and testfail.py load the gate --mark runs, never a project's own copy

`prove.py` and `testfail.py` sign their `AC-Proof:` and `Tests-Fail-Without:` stamps with the review gate's helpers, so they load the gate that `--mark` runs, never the `.claude/hooks/review-gate.py` beside a project copy or a repo's `.gemini/...` copy: those files are code the branch under review can edit, so the branch could stamp its own proofs (NFX-214, NFX-316). They look in order at the installed gate (`<home>/.claude/hooks`, with home from the passwd record), the agy plugin gate (`<home>/.gemini/config/plugins/nousfx/hooks`, hash-checked against `hooks.json` before import), the default branch's copy that the cloud hook extracts into the git dir, and, only in a NousFX checkout, `claude/hooks`. If none is found, or the first one found fails hash verification, will not load, or is too old, they refuse with exit 2 and never try the next one.

## Considered Options

- Load the gate next to the script, as before NFX-214. It is simpler and works with no sync, but it trusts code under review.

## Consequences

- A project with no installed gate and no cloud copy refuses until the NousFX sync runs or, in a cloud session, until the project hook extracts the default branch's gate.
- In a NousFX checkout with neither an installed gate nor a cloud copy in the git dir, the third place, `claude/hooks`, is the branch's own gate.
- This closes "honest script, tampered gate", not "tampered script": in a project the scripts are repo files too. The details and limits are in `docs/review-gate.md`, "Where prove.py and testfail.py load the gate".
