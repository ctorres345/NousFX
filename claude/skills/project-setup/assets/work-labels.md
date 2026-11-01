## Work labels

| Label | Meaning | Who runs it |
| --- | --- | --- |
| `env-none` | Tests and builds alone prove it | Queue and cloud |
| `env-emulator` | Needs an emulator, simulator or screenshots | Interactive Mac sessions for now |
| `env-device` | Needs real hardware, a store track or an outside account | Cesar (`ready-for-human`) |
| `fable-review` | Large or risky: security, auth, migrations, concurrency, public API, billing | The PR waits for `reviewer-deep` on Cesar's Mac, after Cesar approves it |

Every non-Epic ticket has exactly one `env-*` label. The nightly queue takes `ready-for-agent` with `env-none`, and skips `fable-review` and tickets with an unfinished blocker.
