# Review replay (NFX-130)

Question: can Sonnet at `high` effort replace Opus `medium` as the tier-2 reviewer (`reviewer`)?

This file is written **before any run**. Do not edit the rule after seeing results.

## Decision rule

Switch `reviewer` to `role: balanced`, `effort: high` only if Sonnet high, on the whole set:

1. catches **every** real `high` finding;
2. catches **at least 90%** of the real `med` findings;
3. adds **no more than one** extra false positive per case (average over the set);
4. costs **less** than Opus medium on the same set.

The four conditions are measured on **comparable** findings: a past real finding counts only if the Opus-medium replay also catches it on the rebuilt tree. Some trees cannot be rebuilt exactly (files made through Bash are missing from the transcript's Edit/Write calls), and no reviewer can find a bug that is not there. The table shows both the all-past and the comparable numbers. With fewer than 2 comparable real `high` findings the result is inconclusive and Opus stays.

Anything else keeps Opus. Opus medium is run too, to show its own run-to-run spread; a miss that Opus also shows against its own past round counts as noise and is flagged in the PR, but it does not change the rule. Cesar approves any switch after reading the table.

## Cases

One JSON file per case in `cases/` (NousFX diffs only) or in `~/.config/nousfx/review-replay/cases/` (app projects, never committed). Each holds:

- `id`, `source`, `ui` (bool), `exact` (bool: false if the round-1 tree could not be rebuilt exactly)
- `ticket`: the ticket text the reviewer saw
- `base`: the base SHA, and `patch`: the diff against it (a file next to the case)
- `opus_findings`: the round-1 findings of the past Opus `reviewer`
- `judged`: per finding ID, from the main session: `real` (it acted on it), `wrong` (it rejected it as invalid) or `skip` (valid but left alone, so a new finding that matches it counts as neither real nor false). `judged_notes` records how each call was read from the transcript.

The set needs at least 6 cases: at least 2 with a real `high`, at least 1 clean, at least 1 UI.

## Matching a new finding to a past one

A new finding is **caught** when it names the same file, a line within 10 of the past one, and the same defect. The script proposes matches; a human confirms each one. Unmatched new findings are judged `real` or `wrong` by hand; `wrong` ones are false positives.

## Reviewers compared

| Label | Model | Effort |
|---|---|---|
| `opus-medium` | opus | medium |
| `sonnet-high` | sonnet | high |
| `sonnet-medium` | sonnet | medium |

All three use today's review-loop brief, so the comparison is fair even if the brief differs from the one in the original run.
