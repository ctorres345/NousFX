# Run record and `via_run.py`

The agent (Claude Code or agy) makes the design calls. `scripts/via_run.py` (stdlib Python) does the bookkeeping. The run record format is in `assets/findings.schema.json`.

## Order of a run
1. `python3 "<plugin>/scripts/via_run.py" init` makes `.via/runs/<stamp>/` and adds `.via/` to the project's `.gitignore` first. It prints `run_dir`.
2. Put screenshots and the prototype in `run_dir`. On web, `via_capture.py` prints the `screenshots` entries for the record; on Compose and Flutter, `via_capture_native.py` prints `screenshots`, `coverage` and sometimes `warnings` (see `platforms.md`). Write `run_dir/findings.json` (never write `id`, `status` or `merged_at`).
3. `via_run.py merge <run_dir>` validates the record, gives each finding its ID, updates the ledger in `docs/design/via.md`, and writes the IDs and statuses back into `findings.json`. It prints counts of new, open, fixed, rejected and regressed.
   After `merge`, `via_prototype.py build <run_dir>` makes `prototype.html` and adds `prototype` links to the record (see `prototype.md`). Run `merge` once more so the ledger gets the anchors.
4. When the user rejects a finding: `via_run.py reject <id> --reason "<why>"`. It never returns as new.
5. After the code is final: `via_run.py pass <run_dir>`. It records the pass for the current code state in the git dir (`via-check-ok`). Editing code afterwards makes the record stale.

## Screenshots and what may leave the run folder
- Each screenshot entry has `redacted` (true when capture masked it) and `review`. After you look at a shot, set `review` to `kept` or `dropped`. Drop any that still shows personal data, tokens, emails or account details. A shot with no `review` is treated as not cleared.
- Native shots also carry `platform`, `state` (`default`, `empty`, `loading`, `error`, `offline`, `long`), `font_scale` (`default` or `largest`), `direction` (`ltr` or `rtl`) and `source` (`preview`, or `device` for the emulator fallback). Copy them into the record as printed. They have `redacted: false`, so `review` is the only gate.
- `coverage` is a list of `{platform, screen, entry, kind, captured, reason}` from native capture (`kind` is `state`, `matrix`, `preview` or `note`): what was shot and why the rest was not. Copy it into the record and report the gaps. `warnings` is for the report, not the record.
- Pick shots for a second opinion default-state first (phone, light and dark), then the rest, up to 8. The prototype's before image does the same on its own.
- `via_run.py outbound-shots <run_dir>` prints the shots marked `kept` as absolute paths, and counts the dropped and unreviewed ones. `via_second_opinion.py` refuses any other file.
- `token_cost` is a number, or an object. After a second opinion, set `token_cost.second_opinion` to `{"who": "argon", "input_tokens": n, "output_tokens": n}` from the wrapper's `usage` (use `null` when a count is unknown, and `"claude"` for the fallback reviewer).

## Rules
- Second-opinion findings need a verdict, and `wrong` ones become `dismissed` (kept here, never ledgered). See `second-opinion.md`.
- A finding's ID comes from its screen, rule and file. The line is not part of it. Keep `rule` a short, stable name (`contrast`, `touch-target`), or re-runs will see a new finding.
- A re-run marks a finding fixed only when its screen is in `scope`. Set `scope` to the screens you really covered, or `"all"`.
- Exit 1 means the record or a path is invalid and nothing was written. Exit 2 means a secret-shaped value was found. Fix it and run again.
- `merge` changes `docs/design/via.md`, which is tracked. Run `pass` after it, not before.
