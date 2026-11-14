# Fresh-user test for Via

I ran this on 2026-11-14 for NFX-25 to see what a friend gets on a clean machine. Run it again with `python3 -B scripts/fresh_user_test.py prepare`, then `verify`, then `restore`.

**Result: partly passed.** The install ran through the real `claude plugin` commands in a temp HOME, and the `via_setup.py` doctor ran as a script (not the `/via setup` skill) with nothing configured. The capture passed on a retry. **`/via audit` inside Claude Code was not run**, so this is not a full pass of the ticket's first criterion.

## Setup

| | |
|---|---|
| Claude Code | 2.1.325 |
| Via | 0.4.0, from the `nousfx` marketplace on GitHub `main` |
| Machine | macOS, Google Chrome installed, `xcrun` on PATH |
| Temp HOME | new folder; `claude plugin` state lives there |
| Sample app | copy of `samples/web` in a folder with `git init` |
| Environment | `PATH` cut to `/usr/bin:/bin` plus the Python stand-in; no Gemini key, Jira, Figma or Obsidian |

A temp `$HOME` does not move what Via reads from the real home: its config, the locked skills in `~/.agents/skills` and `~/.claude/skills`, and the Gemini and Obsidian detection. Via reads the home from the system user record, so `/via setup` writes the real `~/.config/nousfx/via.json` whatever `$HOME` says. `prepare` backs the config up (and the old `~/.claude/via.json`) and moves it aside, and `restore` puts it back. A person running the manual steps should say no to the locked-skills install, because `restore` does not undo it. For the setup and capture steps I used the stand-in in `plugins/via/scripts/nousfx_testhome.py`, which points the user record at the temp home. After `restore`, the real file's sha256 matched the backup.

## Steps

| Step | Command | Result |
|---|---|---|
| 1 | `claude plugin marketplace add ctorres345/NousFX` | Passed. Cloned over HTTPS. |
| 2 | `claude plugin install via@nousfx` | Passed. `claude plugin list` shows `via@nousfx` 0.4.0, enabled. |
| 3 | `via_setup.py` doctor, as a script under the stand-in (not the `/via setup` skill; `verify` does not apply to a stand-in run) | Passed. Wrote `via.json`; a second run said `unchanged`. |
| 4 | `via_skills.py plan` | Passed. Lists 9 locked skills to install; nothing was installed. |
| 5 | `via_capture.py` on `samples/web`, default timeout | **Failed**, exit 4, `TimeoutExpired` on the first shot. |
| 6 | The same capture with `VIA_SHOT_TIMEOUT=180` | Passed. Took 174 s. Four shots written. |
| 7 | The same capture again | Passed in 17 s. |
| 8 | `/via audit` in Claude Code | **Not run.** I could not get an interactive session to log in inside the temp HOME. |

## Findings

1. **The first capture on a new browser profile is slow.** Chrome's first launch in an empty HOME took 174 s for four shots, over the 60 s default of `VIA_SHOT_TIMEOUT`. A second run took 17 s. A friend's first `/via audit` could stop with exit 4 and no shots. Fixed in NFX-148: the first shot of a run now waits up to 120 s longer (180 s in all by default), with a note on stderr, so nobody has to rerun it by hand. Later shots keep the 60 s limit, and a browser that never answers still exits 4.
2. **`$HOME` does not isolate Via.** Said above. A test must back up and restore the real config.
3. **`/via audit` was not proven.** The audit's findings, second opinion and report were not run in a clean Claude session. The README says the audit looks for the sample's two planted flaws, but this test did not confirm that it reports them.
4. **`/reload-plugins` reported "1 error during load"** in my own session. I did not find out which plugin it was, and it is not shown to come from Via.
5. **Via installs from GitHub `main`**, not from this branch, so the test shows what is published, not this PR.

## Fallbacks hit

| Missing | What happened |
|---|---|
| Gemini | Doctor reports `missing`. Via falls back to a fresh-context Claude reviewer for the second opinion (not exercised, since the audit was not run). |
| Atlassian | `missing`. Via writes markdown tickets. |
| Figma | `missing`. Only `/via export figma` is affected. |
| Obsidian | `missing`. No note is written. |
| Browser Claude can drive | `missing` (flag `--browser none`). Headless Chrome for capture is a separate thing and was found. |
| Design skills | `missing`. `plan` lists 9 to install, none installed. |
| Chrome or Chromium | With `VIA_CHROME=/nonexistent/chrome` the capture exits 3: "no Chrome or Chromium found ... Visual checks were skipped." |
| Simulators | `xcrun` is present, so the doctor says `present`. No device was tried. |

## Still to do

Run `/via setup` and `/via audit` in a real interactive session started with a temp HOME, and add what it shows here.
