# Run record and `via_run.py`

Claude makes the design calls. `scripts/via_run.py` (stdlib Python) does the bookkeeping. The run record format is in `assets/findings.schema.json`.

## Order of a run
1. `python3 "<plugin>/scripts/via_run.py" init` makes `.via/runs/<stamp>/` and adds `.via/` to the project's `.gitignore` first. It prints `run_dir`.
2. Put screenshots and the prototype in `run_dir`. Write `run_dir/findings.json` (never write `id`, `status` or `merged_at`).
3. `via_run.py merge <run_dir>` validates the record, gives each finding its ID, updates the ledger in `docs/design/via.md`, and writes the IDs and statuses back into `findings.json`. It prints counts of new, open, fixed, rejected and regressed.
4. When the user rejects a finding: `via_run.py reject <id> --reason "<why>"`. It never returns as new.
5. After the code is final: `via_run.py pass <run_dir>`. It records the pass for the current code state in the git dir (`via-check-ok`). Editing code afterwards makes the record stale.

## Rules
- A finding's ID comes from its screen, rule and file. The line is not part of it. Keep `rule` a short, stable name (`contrast`, `touch-target`), or re-runs will see a new finding.
- A re-run marks a finding fixed only when its screen is in `scope`. Set `scope` to the screens you really covered, or `"all"`.
- Exit 1 means the record or a path is invalid and nothing was written. Exit 2 means a secret-shaped value was found. Fix it and run again.
- `merge` changes `docs/design/via.md`, which is tracked. Run `pass` after it, not before.
