---
status: accepted
---

# Test runs share machine-wide suite slots

Parallel sessions on one machine share N = max(1, CPUs // 4) suite slots (`suite_slot.py`), and `run_checks.py`, every command the installed gate runs through `run_check` (the check, and each prove.py command proof) and testfail.py's clone runs wait for a free slot. So at most N of them run at once, and a pile-up of sessions no longer decides a stamp or a test-fail verdict (NFX-217; on the 2026-11-20 overnight wave, load reached about 210 on 12 CPUs and a testfail run was killed). The installed gate starts its `[check] timeout` only once the slot is held, so a wait there never fails a check.

## Considered Options

- A lock folder with PID reclaim: rejected, because two reclaimers can race. A `flock` frees itself when the holder dies in any way.
- Start the `[check]` timeout before the wait: rejected, because a busy machine would fail a check that never ran.

## Consequences

- A session waits with no upper limit while live runs hold every slot. It prints "waiting for a suite slot" once, then every 5 minutes. Even a one-line proof can queue behind full suites.
- A child inside a held slot takes no second one.
- The CI and cloud gate copies have no `suite_slot.py` beside them, so they take no slot themselves. `run_checks.py` still takes one, after their timeout has started.
- There is no limit, only a note, on Windows, or for a project copy of testfail.py with no `suite_slot.py` next to it. The rules are in `docs/review-gate.md`, "Suite slots".
