# Fresh-user test for Via

I ran this on 2026-11-14 for NFX-25 to see what a friend gets on a clean machine. Run it again with `python3 -B scripts/fresh_user_test.py prepare`, then `verify`, then `restore`.

**Result: passed.** The install ran through the real `claude plugin` commands in a temp HOME. On 2026-11-15 (NFX-149) I ran `/via setup` and `/via audit` in a real interactive Claude Code session in that temp HOME, on a git-initialised copy of `samples/web`. The audit flagged both planted flaws, `verify` exited 0 and `restore` put the real `via.json` back with a matching hash.

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
| 8 | Interactive `claude` in the temp HOME, `/login`, `/via setup` (said no to the locked-skills install) | Passed after a keychain fix (finding 6); `/login` used the temp keychain, not the real one. Nothing installed; `via_skills.py verify` printed `DRIFT (missing)` for all 9. |
| 9 | `/via audit` in the sample app folder | **Passed.** 4 findings, 4 shots, run folder `.via/runs/20261115T204142Z`. Both planted flaws reported (see finding 3). Cold capture time: not timed (Cesar: "it wasn't long"). |
| 10 | `verify`, then `restore` | `verify` exited 0: `via.json` present and a `findings.json` under `.via/runs/`. `restore` put back `~/.config/nousfx/via.json` (sha256 `b03fe7ba9659`, matches the backup). |

## Findings

1. **The first capture on a new browser profile is slow.** Chrome's first launch in an empty HOME took 174 s for four shots, over the 60 s default of `VIA_SHOT_TIMEOUT`. A second run took 17 s. A friend's first `/via audit` could stop with exit 4 and no shots. Fixed in NFX-148: the first shot of a run now waits up to 120 s longer (180 s in all by default), with a note on stderr, so nobody has to rerun it by hand. Later shots keep the 60 s limit, and a browser that never answers still exits 4.
2. **`$HOME` does not isolate Via.** Said above. A test must back up and restore the real config.
3. **The audit flags both planted flaws.** Subtitle `#bbbbbb` on white, about 1.9:1 (high, `styles.css:39`), and the Start button at 96x28px, under the 44px target (high, `styles.css:45`). It also reported a top-left layout on desktop (med) and a 12px button label (low), and dismissed a false contrast finding after checking it (Start label passes at about 8:1). The README's claim is true, so its wording is unchanged. I rejected the fix batch so the sample stayed as it was.
4. **`/reload-plugins` reported "1 error during load"** in my own session. I did not find out which plugin it was, and it is not shown to come from Via.
5. **Via installs from GitHub `main`**, not from this branch, so the test shows what is published, not this PR.
6. **A temp HOME has no keychain on macOS.** `/login` failed with "keychain cannot be found" because the temp HOME has no `Library/Keychains`. Fix used: create a separate login keychain inside the temp HOME (`security create-keychain`, `default-keychain -s`, `unlock-keychain`, all with `HOME=<temp>`). With it, `/login` wrote only to that temp keychain, so **it did not change the real session: the real login was still active** (a normal `claude` session opened afterwards had my usual setup). Sharing the real keychain was never tried.
7. **A declined locked-skills install reads as `DRIFT`.** `via_skills.py verify` prints `DRIFT (missing)` for all 9 skills, but setup describes `DRIFT` as "edited or out of date". For a friend who says no, it looks like a problem. Not changed here.
8. **Started from the wrong folder, `/via audit` refuses sensibly.** My first run was from the home folder: it said there was no project, listed the folders in `~/Projects` and asked which to audit. No error, no files written.

## Fallbacks hit

| Missing | What happened |
|---|---|
| Gemini | Doctor reports `missing`. Via fell back to a fresh-context Claude reviewer for the second opinion (Argon was skipped: the sample has no git remote, so it is not on the allow-list). |
| Atlassian | `missing`. Via writes markdown tickets. |
| Figma | `missing`. Only `/via export figma` is affected. |
| Obsidian | `missing`. No note is written. |
| Browser Claude can drive | `missing` (flag `--browser none`). Headless Chrome for capture is a separate thing and was found. |
| Design skills | `missing`. `plan` lists 9 to install, none installed. |
| Chrome or Chromium | With `VIA_CHROME=/nonexistent/chrome` the capture exits 3: "no Chrome or Chromium found ... Visual checks were skipped." |
| Simulators | `xcrun` is present, so the doctor says `present`. No device was tried. |

## Still to do

- Time a real cold capture inside Claude Code against the 180 s limit (this run was not timed).
- Terminal screenshots of the run were not taken.
- Decide whether `prepare` should create the temp keychain itself (finding 6) and whether `verify` should say "not installed" instead of `DRIFT` (finding 7).
