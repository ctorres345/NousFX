# Review replay results (NFX-130, 2026-11-10)

Six past tier-2 round-1 reviews (five NousFX, one private Animus UI case that returned no findings), replayed with today's review-loop brief. Rule: `README.md`.

| | opus-medium | sonnet-high | sonnet-medium |
|---|---|---|---|
| real high caught, all past | 2/3 | 2/3 | 2/3 |
| real med caught, all past | 7/10 | 6/10 | 5/10 |
| real high caught, comparable | 2/2 | 2/2 | 2/2 |
| real med caught, comparable | 7/7 | 4/7 | 4/7 |
| new findings judged | 0 | 0 | 0 |
| unjudged new findings | 29 | 24 | 12 |
| tokens | 1,688,563 | 1,481,057 | 1,162,097 |
| cost (est. $) | 2.15 | 0.94 | 0.71 |

Comparable = past real findings that the Opus-medium replay also caught on the rebuilt tree.

## Decision

**Keep Opus.** Sonnet high catches every comparable real high, but only 4 of 7 comparable real mediums (rule: at least 90%). The three misses are real: no Sonnet finding names the same defect (`CLAUDE.delegation.md` unattended ask, and two in `via_run.py`: rejected-suffix shift, symlinked via merge). Sonnet is cheaper ($0.94 against $2.15), which the rule's cost condition allows, but it does not make up for the misses.

## Limits

- The new findings were not judged one by one (65 in total), so the false-positive condition is not measured. It does not change the result, because the medium condition fails first.
- One Opus run: its run-to-run spread is not measured. Opus catches all comparable findings by construction.
- Three trees are incomplete: files made through Bash are not in the transcript's Edit/Write calls. The comparable numbers drop findings the Opus replay could not reproduce, and one past high (of 3) was not reproducible.
- Only 6 cases and 2 comparable highs.
